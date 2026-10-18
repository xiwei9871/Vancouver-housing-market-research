from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup

from .models import SourceType
from .sources import (
    SourceRunStatus,
    SourceSnapshot,
    SourceRunResult,
    _BaseSource,
)


_MLS_RE = re.compile(r"\b[A-Z]{1,3}\d{6,8}\b", re.IGNORECASE)
_NON_LISTING_JSONLD_TYPES = {"article", "newsarticle", "blogposting", "blog"}
_NUMBER_RE = re.compile(r"[-+]?\d[\d,]*(?:\.\d+)?")
_DIMENSIONS_RE = re.compile(
    r"^\s*([-+]?\d[\d,]*(?:\.\d+)?)\s*(?:x|×)\s*"
    r"([-+]?\d[\d,]*(?:\.\d+)?)\s*(?:ft|feet|')?\s*$",
    re.IGNORECASE,
)
_AREA_UNIT_RE = re.compile(r"(?:sq\.?\s*ft\.?|sqft|square\s+feet|ft2|ft²)", re.IGNORECASE)
_MAP_CARD_COORDINATES_RE = re.compile(
    r"_(?P<latitude>-?\d+\.\d+)_\s*(?P<longitude>-?\d+\.\d+)(?:_|$)"
)
_EXPLICIT_H1_ADDRESS_RE = re.compile(
    r"^\s*\d+[A-Za-z]?(?:[-/]\d+[A-Za-z]?)?\s+.+\b"
    r"(?:street|st|avenue|ave|road|rd|drive|dr|lane|ln|boulevard|blvd|"
    r"way|court|ct|place|pl|crescent|cres|terrace|ter|trail|highway|hwy|"
    r"parkway|pkwy)\b",
    re.IGNORECASE,
)
_DETAIL_CIVIC_ADDRESS_RE = re.compile(
    r"^\s*\d+[A-Za-z]?(?:[-/]\d+[A-Za-z]?)?\s+"
    r"(?=[^,\s]*[^\W\d_])\S",
    re.IGNORECASE,
)
_CHALLENGE_MARKERS = (
    "captcha",
    "incapsula",
    "verify you are human",
    "just a moment",
    "request unsuccessful",
    "access denied",
    "you have been blocked",
    "enable javascript and cookies to continue",
    "cloudflare challenge",
)


@dataclass(frozen=True)
class PublicFeedResponse:
    """Small transport-neutral HTTP response used by the public adapter."""

    url: str
    status_code: int
    headers: Mapping[str, str]
    content: bytes
    method: str = "GET"


class _PartialParseError(ValueError):
    def __init__(self, rows: list[dict[str, Any]], errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.rows = rows
        self.errors = errors


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _header(headers: Mapping[str, str], name: str) -> str:
    wanted = name.lower()
    return next((str(value) for key, value in headers.items() if str(key).lower() == wanted), "")


def _content_type(response: PublicFeedResponse) -> str:
    return _header(response.headers, "content-type").split(";", 1)[0].strip().lower()


def _number(value: Any) -> int | float | None:
    if value in (None, "", [], {}):
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    match = _NUMBER_RE.search(str(value).replace("$", ""))
    if not match:
        return None
    raw = match.group(0).replace(",", "")
    try:
        parsed = float(raw)
    except ValueError:
        return None
    return int(parsed) if parsed.is_integer() else parsed


def _price(value: Any) -> int | float | None:
    return _number(value)


def _lot_dimensions(value: Any) -> tuple[int | float, int | float] | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    match = _DIMENSIONS_RE.match(str(value).replace(",", ""))
    if not match:
        return None
    first, second = (float(item) for item in match.groups())
    return (
        int(first) if first.is_integer() else first,
        int(second) if second.is_integer() else second,
    )


def _lot_area_sqft(value: Any) -> int | float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    if value in (None, ""):
        return None
    text = str(value).strip()
    if _lot_dimensions(text):
        return None
    if _AREA_UNIT_RE.search(text) or re.fullmatch(r"[-+]?\d[\d,]*(?:\.\d+)?", text):
        return _number(text)
    return None


def _lot_measurements(raw_lot: Any, raw_frontage: Any) -> dict[str, int | float]:
    measurements: dict[str, int | float] = {}
    dimensions = _lot_dimensions(raw_lot)
    if dimensions:
        explicit_frontage = _number(raw_frontage)
        measurements["frontage_ft"] = explicit_frontage if explicit_frontage is not None else dimensions[0]
        measurements["depth_ft"] = dimensions[1]
    else:
        area = _lot_area_sqft(raw_lot)
        if area is not None:
            measurements["lot_area_sqft"] = area
        frontage = _number(raw_frontage)
        if frontage is not None:
            measurements["frontage_ft"] = frontage
    return measurements


def _reconcile_lot_area_with_dimensions(
    explicit_area: int | float | None,
    dimensions: tuple[int | float, int | float] | None,
) -> tuple[int | float | None, str | None, dict[str, int | float]]:
    """Keep an explicit lot area unless it is an obvious parser outlier.

    Listing pages occasionally expose a placeholder such as ``1`` alongside
    a real ``33 x 122 ft`` dimension.  Dimensions are not a substitute for a
    survey, so only an extreme inconsistency is repaired here.  The original
    value and the consistency ratio remain in the row for audit/review.
    """

    if dimensions is None:
        return explicit_area, None, {}
    expected = round(float(dimensions[0]) * float(dimensions[1]), 2)
    if expected <= 0:
        return explicit_area, None, {}
    if explicit_area is None:
        return expected, "DERIVED_FROM_LISTING_DIMENSIONS", {}
    ratio = float(explicit_area) / expected
    if ratio < 0.5:
        return (
            expected,
            "CALCULATED_FROM_DIMENSIONS_CONFLICT_REPAIRED",
            {
                "lot_area_original_sqft": explicit_area,
                "lot_area_derived_sqft": expected,
                "lot_area_consistency_ratio": round(ratio, 6),
            },
        )
    return explicit_area, "EXPLICIT_DETAIL", {}


def _map_card_coordinates(value: Any) -> dict[str, float]:
    if value in (None, ""):
        return {}
    match = _MAP_CARD_COORDINATES_RE.search(str(value))
    if not match:
        return {}
    return {
        "latitude": float(match.group("latitude")),
        "longitude": float(match.group("longitude")),
    }


def _text(node: Any, selector: str) -> str | None:
    found = node.select_one(selector)
    if not found:
        return None
    value = " ".join(found.get_text(" ").split())
    return value or None


def _first(node: Any, selectors: Iterable[str]) -> str | None:
    for selector in selectors:
        value = _text(node, selector)
        if value:
            return value
    return None


def _mls(value: Any) -> str | None:
    if value in (None, ""):
        return None
    match = _MLS_RE.search(str(value))
    return match.group(0).upper() if match else None


def _absolute_url(value: Any, requested_url: str) -> str | None:
    if value in (None, ""):
        return None
    return urljoin(requested_url, str(value).strip())


def _split_address(value: Any) -> tuple[str | None, str | None]:
    if value in (None, ""):
        return None, None
    text = " ".join(str(value).replace("|", "|").split())
    if "|" not in text:
        return text, None
    street, locality = text.split("|", 1)
    municipality = locality.split(",", 1)[0].strip() or None
    return street.strip() or None, municipality


def _address_parts(value: Any) -> tuple[str | None, str | None]:
    if isinstance(value, Mapping):
        street = value.get("AddressText") or value.get("streetAddress") or value.get("StreetAddress")
        locality = value.get("City") or value.get("city") or value.get("addressLocality")
        if isinstance(street, Mapping):
            street = street.get("AddressText") or street.get("streetAddress")
        if street and "|" in str(street):
            return _split_address(street)
        return (str(street).strip() if street else None, str(locality).strip() if locality else None)
    return _split_address(value)


def _realtor_row(item: Mapping[str, Any], requested_url: str) -> dict[str, Any]:
    property_data = item.get("Property") if isinstance(item.get("Property"), Mapping) else {}
    address_data = property_data.get("Address") or item.get("Address")
    building = item.get("Building") if isinstance(item.get("Building"), Mapping) else {}
    address, municipality = _address_parts(address_data)
    relative_url = item.get("RelativeURLEn") or item.get("ListingURL") or item.get("url")
    canonical_url = _absolute_url(relative_url, requested_url)
    result: dict[str, Any] = {
        "listing_id": str(item.get("Id") or item.get("ListingKey") or "") or None,
        "mls_number": _mls(item.get("MlsNumber") or item.get("MLSNumber") or item.get("ReferenceNumber")),
        "address": address,
        "municipality": municipality,
        "neighbourhood": (
            item.get("Neighbourhood")
            or property_data.get("Neighbourhood")
            or property_data.get("CommunityName")
        ),
        "property_type": property_data.get("Type") or item.get("PropertyType") or item.get("PropertySubType"),
        "ownership_type": property_data.get("OwnershipType") or item.get("CommonInterest"),
        "listing_status": item.get("StandardStatus") or property_data.get("StandardStatus"),
        "asking": _price(
            property_data.get("PriceUnformattedValue")
            or property_data.get("Price")
            or item.get("ListPrice")
        ),
        "original_asking": _price(
            item.get("OriginalPrice")
            or item.get("OriginalListPrice")
            or property_data.get("OriginalPrice")
        ),
        "listing_date": item.get("ListingDate") or item.get("DateListed") or property_data.get("ListingDate"),
        "dom": _number(item.get("DaysOnMarket") or item.get("DOM") or property_data.get("DaysOnMarket")),
        "cumulative_dom": _number(
            item.get("CumulativeDaysOnMarket")
            or item.get("CumulativeDOM")
            or property_data.get("CumulativeDaysOnMarket")
        ),
        "relist_count": _number(item.get("RelistCount") or property_data.get("RelistCount")),
        "bedrooms": _number(building.get("Bedrooms") or item.get("Bedrooms")),
        "bathrooms": _number(building.get("BathroomTotal") or item.get("BathroomTotal")),
        "finished_sqft": _number(
            building.get("FloorAreaMeasurements", [{}])[0].get("Area")
            if isinstance(building.get("FloorAreaMeasurements"), list)
            and building.get("FloorAreaMeasurements")
            and isinstance(building.get("FloorAreaMeasurements")[0], Mapping)
            else building.get("SizeInterior") or item.get("FloorArea")
        ),
        "description": item.get("PublicRemarks") or item.get("Remarks"),
        "source_url": canonical_url or requested_url,
        "feed_url": requested_url,
    }
    raw_lot = property_data.get("LandSize") or item.get("LotSize") or item.get("LotArea")
    raw_frontage = property_data.get("Frontage") or item.get("Frontage")
    result.update(_lot_measurements(raw_lot, raw_frontage))
    return {key: value for key, value in result.items() if value not in (None, "")}


def _json_payload(content: bytes) -> Any:
    return json.loads(content.decode("utf-8-sig"))


def _parse_realtor_records(
    content: bytes,
    requested_url: str,
    observed_at: datetime,
) -> tuple[list[dict[str, Any]], list[str]]:
    del observed_at
    payload = _json_payload(content)
    if isinstance(payload, Mapping):
        if "Results" in payload:
            items = payload["Results"]
        elif "results" in payload:
            items = payload["results"]
        elif "listings" in payload:
            items = payload["listings"]
        else:
            items = payload.get("data")
    else:
        items = payload
    if not isinstance(items, list):
        raise ValueError("REALTOR.ca JSON response has no Results/listings array")
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    for index, item in enumerate(items):
        if not isinstance(item, Mapping):
            errors.append(f"row {index}: expected listing object")
            continue
        try:
            rows.append(_realtor_row(item, requested_url))
        except (TypeError, ValueError, IndexError, KeyError) as exc:
            errors.append(f"row {index}: {type(exc).__name__}: {exc}")
    return rows, errors


def parse_realtor_ca_json(
    content: bytes,
    requested_url: str,
    observed_at: datetime,
) -> list[dict[str, Any]]:
    """Parse the public REALTOR.ca JSON search envelope."""

    rows, errors = _parse_realtor_records(content, requested_url, observed_at)
    if errors and not rows:
        raise ValueError("; ".join(errors))
    if errors:
        raise _PartialParseError(rows, errors)
    return rows


def _card_row(card: Any, requested_url: str) -> dict[str, Any] | None:
    attr = card.attrs if hasattr(card, "attrs") else {}
    classes = {str(item).lower() for item in (card.get("class") or [])}
    is_zolo = "card-listing" in classes or bool(
        card.select_one(".card-listing--location")
        and card.select_one(".card-listing--values")
    )
    if is_zolo:
        raw_address = _first(
            card,
            (".card-listing--location .street", ".card-listing--location"),
        )
        address, address_municipality = _validated_civic_address(raw_address)
        municipality = _first(
            card,
            (".card-listing--location .city", ".city"),
        ) or address_municipality
        neighbourhood = _first(
            card,
            (".card-listing--location .neighbourhood", ".neighbourhood"),
        )
        anchor = card.select_one("a.tile-overlay-link[href]")
        values_node = card.select_one(".card-listing--values")
        values = " ".join(values_node.get_text(" ").split()) if values_node else ""
        price_node = card.select_one(
            ".card-listing--values .price [itemprop='price'], "
            ".card-listing--values .price"
        )
        price_text = (
            (price_node.get("value") or price_node.get_text(" "))
            if price_node
            else None
        )
        bed_match = re.search(r"(\d+(?:\.\d+)?)\s*bed", values, re.IGNORECASE)
        bath_match = re.search(r"(\d+(?:\.\d+)?)\s*bath", values, re.IGNORECASE)
        sqft_match = re.search(r"([\d,]+)\s*sqft", values, re.IGNORECASE)
        year_match = re.search(r"built\s+in\s+(\d{4})", values, re.IGNORECASE)
        if not address:
            return None
        result = {
            "address": address,
            "municipality": municipality,
            "neighbourhood": neighbourhood,
            "asking": _price(price_text),
            "bedrooms": _number(bed_match.group(1)) if bed_match else None,
            "bathrooms": _number(bath_match.group(1)) if bath_match else None,
            "finished_sqft": _number(sqft_match.group(1)) if sqft_match else None,
            "year_built": _number(year_match.group(1)) if year_match else None,
            "source_url": (
                _absolute_url(anchor.get("href"), requested_url)
                if anchor
                else requested_url
            ),
            "feed_url": requested_url,
        }
        return {key: value for key, value in result.items() if value not in (None, "")}

    link = attr.get("data-url") or attr.get("data-href")
    if not link:
        anchor = card.select_one("a.listingDetailsLink[href]")
        if anchor is None:
            anchor = card.select_one("a[href*='/real-estate/']")
        if anchor is None:
            anchor = card.select_one("a[href]")
        link = anchor.get("href") if anchor else None
    full_text = " ".join(card.get_text(" ").split())
    raw_address = attr.get("data-address") or _first(
        card,
        (
            ".address",
            ".listingCardAddress",
            ".smallListingCardAddress",
            "[itemprop='streetAddress']",
        ),
    )
    address, address_municipality = _validated_civic_address(raw_address)
    price_node = card.select_one(".listingCardPrice")
    if price_node is None:
        price_node = card.select_one("[data-value-cad]")
    price_value = attr.get("data-price")
    if not price_value and price_node is not None:
        price_value = price_node.get("data-value-cad") or price_node.get_text(" ")
    bedrooms = attr.get("data-bedrooms") or _first(card, (".beds", ".bedrooms"))
    bedrooms = bedrooms or _icon_value(card, "bedroom")
    bathrooms = attr.get("data-bathrooms") or _first(card, (".baths", ".bathrooms"))
    bathrooms = bathrooms or _icon_value(card, "bathroom")
    finished_sqft = attr.get("data-house-size") or _first(
        card,
        (".house-size", ".floor-area"),
    )
    finished_sqft = finished_sqft or _icon_value(card, "square feet")
    finished_sqft = finished_sqft or _icon_value(card, "sqft")
    if not address:
        return None
    return {
        "listing_id": attr.get("data-listing-id") or attr.get("data-property-id"),
        "mls_number": _mls(
            attr.get("data-mls-number")
            or _first(card, (".mls-number", ".listingCardMLS"))
            or full_text
        ),
        "address": address,
        "municipality": (
            attr.get("data-city")
            or attr.get("data-municipality")
            or _first(card, (".city", ".municipality"))
            or address_municipality
        ),
        "neighbourhood": attr.get("data-neighbourhood") or _first(card, (".neighbourhood", ".community")),
        "property_type": attr.get("data-property-type") or _first(card, (".property-type",)),
        "ownership_type": attr.get("data-tenure") or _first(card, (".tenure", ".ownership")),
        "listing_status": attr.get("data-status") or _first(card, (".status",)),
        "asking": _price(price_value or _first(card, (".price", "[itemprop='price']"))),
        "original_asking": _price(attr.get("data-original-price") or _first(card, (".original-price",))),
        "listing_date": attr.get("data-listing-date") or _first(card, (".listing-date",)),
        "dom": _number(attr.get("data-dom") or _first(card, (".dom", "[data-dom]"))),
        "cumulative_dom": _number(
            attr.get("data-cumulative-dom") or _first(card, (".cumulative-dom", "[data-cumulative-dom]"))
        ),
        "relist_count": _number(attr.get("data-relist-count") or _first(card, (".relist-count",))),
        "bedrooms": _number(bedrooms),
        "bathrooms": _number(bathrooms),
        "finished_sqft": _number(finished_sqft),
        **_lot_measurements(
            attr.get("data-lot-size") or _first(card, (".lot-size",)),
            attr.get("data-frontage") or _first(card, (".frontage",)),
        ),
        "year_built": _number(attr.get("data-year-built") or _first(card, (".year-built",))),
        "description": attr.get("data-description") or _first(card, (".description",)),
        **_map_card_coordinates(attr.get("data-value")),
        "source_url": _absolute_url(link, requested_url) or requested_url,
        "feed_url": requested_url,
    }


def _icon_value(card: Any, label: str) -> str | None:
    wanted = label.lower()
    for block in card.select(".listingCardIconCon, .smallListingCardIconCon"):
        text = " ".join(block.get_text(" ").split()).lower()
        if wanted not in text:
            continue
        value = _first(
            block,
            (".listingCardIconNum", ".smallListingCardIconNum"),
        )
        if value:
            return value
    return None


def _html_address_parts(value: Any) -> tuple[str | None, str | None]:
    """Split explicit listing-card addresses without guessing arbitrary text."""

    address, municipality = _address_parts(value)
    if municipality or value in (None, ""):
        return address, municipality
    parts = [part.strip() for part in str(value).split(",") if part.strip()]
    provinces = {
        "ab",
        "alberta",
        "bc",
        "british columbia",
        "mb",
        "manitoba",
        "nb",
        "new brunswick",
        "nl",
        "newfoundland and labrador",
        "ns",
        "nova scotia",
        "nt",
        "northwest territories",
        "nu",
        "nunavut",
        "on",
        "ontario",
        "pe",
        "prince edward island",
        "qc",
        "quebec",
        "sk",
        "saskatchewan",
        "yt",
        "yukon",
    }
    if len(parts) >= 3 and parts[-1].lower() in provinces:
        return parts[0], parts[-2]
    if len(parts) == 2 and parts[-1].lower() not in provinces:
        return parts[0], parts[1]
    return address, municipality


def _looks_like_listing_card(card: Any) -> bool:
    classes = {str(item).lower() for item in (card.get("class") or [])}
    if "listing-card" in classes or "listingcard" in classes or "card-listing" in classes:
        return True
    if card.has_attr("data-listing-card"):
        return True
    if card.select_one(".card-listing--location") and card.select_one(
        ".card-listing--values"
    ):
        return True
    return bool(
        card.select_one(
            "a.listingDetailsLink, .listingCardPrice, .listingCardAddress, "
            ".listingCardMLS, [data-mls-number], [data-price]"
        )
    )


def _jsonld_candidates(soup: BeautifulSoup) -> list[Mapping[str, Any]]:
    candidates: list[Mapping[str, Any]] = []

    def walk(value: Any) -> None:
        if isinstance(value, Mapping):
            candidates.append(value)
            for child in value.values():
                if isinstance(child, (Mapping, list)):
                    walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    for script in soup.select("script[type='application/ld+json']"):
        text = script.string or script.get_text()
        if not text.strip():
            continue
        try:
            walk(json.loads(text))
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
    return candidates


def _is_non_listing_jsonld(item: Mapping[str, Any]) -> bool:
    raw_types = item.get("@type")
    types = raw_types if isinstance(raw_types, list) else [raw_types]
    return any(
        str(item_type).rsplit("/", 1)[-1].rsplit("#", 1)[-1].casefold()
        in _NON_LISTING_JSONLD_TYPES
        for item_type in types
        if item_type
    )


def _jsonld_row(item: Mapping[str, Any], requested_url: str) -> dict[str, Any] | None:
    if _is_non_listing_jsonld(item):
        return None
    address, municipality = _validated_civic_address(item.get("address"))
    offers = item.get("offers") if isinstance(item.get("offers"), Mapping) else {}
    description = item.get("description")
    mls_number = _mls(description or item.get("identifier") or item.get("name"))
    if not address and not mls_number:
        return None
    return {
        "mls_number": mls_number,
        "address": address,
        "municipality": municipality,
        "property_type": item.get("@type"),
        "asking": _price(offers.get("price") or item.get("price")),
        "bedrooms": _number(item.get("numberOfBedrooms")),
        "finished_sqft": _number(item.get("floorSize")),
        "description": description,
        "source_url": _absolute_url(item.get("url"), requested_url) or requested_url,
        "feed_url": requested_url,
    }


def _detail_label_value(blocks: list[str], label_pattern: str) -> str | None:
    label_re = re.compile(
        rf"^\s*(?:{label_pattern})\s*(?:(?:[:#]|[-–—])\s*)?(.*?)\s*$",
        re.IGNORECASE,
    )
    for index, block in enumerate(blocks):
        match = label_re.match(block)
        if not match:
            continue
        value = match.group(1).strip()
        if value:
            return value
        if index + 1 < len(blocks):
            return blocks[index + 1]
    return None


def _detail_metric(
    blocks: Iterable[str],
    unit_pattern: str,
    *,
    excluded_prefixes: tuple[str, ...] = (),
) -> int | float | None:
    metric_re = re.compile(
        rf"(?<!\w)(\d[\d,]*(?:\.\d+)?)\s*(?:{unit_pattern})(?!\w)",
        re.IGNORECASE,
    )
    for block in blocks:
        if any(re.match(prefix, block, re.IGNORECASE) for prefix in excluded_prefixes):
            continue
        match = metric_re.search(block)
        if match:
            return _number(match.group(1))
    return None


def _detail_finished_sqft(blocks: Iterable[str]) -> int | float | None:
    metric_re = re.compile(
        r"(?<!\w)(\d[\d,]*(?:\.\d+)?)\s*"
        r"(?:sq\.?\s*ft\.?|sqft|square\s+feet)(?!\w)",
        re.IGNORECASE,
    )
    block_list = list(blocks)
    fallback: list[int | float] = []
    for index, block in enumerate(block_list):
        previous = block_list[index - 1] if index else ""
        context = f"{previous} {block}"
        if re.search(r"\blot\s+(?:size|area)\b", context, re.IGNORECASE):
            continue
        match = metric_re.search(block)
        if not match:
            continue
        value = _number(match.group(1))
        if value is None:
            continue
        if re.search(
            r"\b(?:house|floor|finished|interior|living)\b",
            context,
            re.IGNORECASE,
        ):
            return value
        fallback.append(value)
    return fallback[0] if len(fallback) == 1 else None


def _detail_currency(value: Any) -> int | float | None:
    if value in (None, ""):
        return None
    match = re.search(r"\$\s*([\d,]+(?:\.\d+)?)", str(value))
    return _price(match.group(1)) if match else None


def _history_date(value: Any) -> str | None:
    """Normalize the date formats used by REALTOR.ca history tables."""

    text = " ".join(str(value or "").split())
    if not text:
        return None
    formats = (
        "%b %d, %Y",
        "%B %d, %Y",
        "%Y-%m-%d",
        "%m/%d/%Y",
        "%d/%m/%Y",
    )
    for date_format in formats:
        try:
            return datetime.strptime(text, date_format).replace(tzinfo=timezone.utc).isoformat()
        except ValueError:
            continue
    return None


def _realtor_price_sale_history(soup: BeautifulSoup) -> list[dict[str, Any]] | None:
    """Read explicit price/history rows when the detail page renders them.

    The result intentionally preserves all displayed event types (for example
    ``Listed`` and ``Sale Closed``).  It is not a replacement for BCA's
    registered-transfer history and it does not infer a sale when the table is
    absent or contains no explicit sale event.
    """

    table = soup.select_one("#tableHistoryDetail")
    if table is None:
        return None
    rows: list[dict[str, Any]] = []
    for row in table.select("tr"):
        cells = [" ".join(cell.stripped_strings) for cell in row.select("th, td")]
        if len(cells) < 2:
            continue
        event_date = _history_date(cells[0])
        # Header rows such as "Date / Status / Price" are not transaction
        # events and must not be persisted as if REALTOR.ca had recorded one.
        if event_date is None:
            continue
        event_status = cells[1].strip() or None
        event_price = _detail_currency(cells[2]) if len(cells) >= 3 else None
        if event_date is None and event_status is None and event_price is None:
            continue
        event: dict[str, Any] = {
            "date": event_date,
            "status": event_status,
            "price": event_price,
        }
        rows.append(event)
    return rows


def _visible_detail_blocks(soup: BeautifulSoup) -> list[str]:
    for element in soup(["script", "style", "template"]):
        element.decompose()
    root = soup.body or soup
    return [" ".join(value.split()) for value in root.stripped_strings if value.strip()]


def _jsonld_scalar(value: Any) -> Any:
    if not isinstance(value, Mapping):
        return value
    for key in ("value", "name", "text"):
        if value.get(key) not in (None, "", [], {}):
            return value[key]
    return None


def _jsonld_first(items: Iterable[Mapping[str, Any]], *keys: str) -> Any:
    for item in items:
        for key in keys:
            value = item.get(key)
            if value not in (None, "", [], {}):
                return value
    return None


def _jsonld_detail_items(items: list[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    fields = (
        "address",
        "offers",
        "numberOfBedrooms",
        "numberOfBathrooms",
        "numberOfBathroomsTotal",
        "floorSize",
        "lotSize",
        "yearBuilt",
        "propertyType",
        "description",
        "identifier",
        "neighborhood",
        "neighbourhood",
    )
    selected = [
        item
        for item in items
        if any(item.get(field) not in (None, "", [], {}) for field in fields)
    ]
    return selected or items


def _jsonld_offer_price(items: Iterable[Mapping[str, Any]]) -> int | float | None:
    for item in items:
        offers = item.get("offers")
        offer_items = offers if isinstance(offers, list) else [offers]
        for offer in offer_items:
            if not isinstance(offer, Mapping):
                continue
            price = _price(offer.get("price") or offer.get("lowPrice"))
            if price is not None:
                return price
        price = _price(item.get("price"))
        if price is not None:
            return price
    return None


def _validated_civic_address(value: Any) -> tuple[str | None, str | None]:
    address, municipality = _html_address_parts(value)
    if not address or not _DETAIL_CIVIC_ADDRESS_RE.match(address):
        return None, None
    return address, municipality


def parse_zolo_detail(
    content: bytes,
    requested_url: str,
    observed_at: datetime,
) -> dict[str, Any]:
    """Parse one Zolo detail page without inventing missing legal facts."""

    soup = BeautifulSoup(content, "html.parser")
    jsonld_items = [
        item
        for item in _jsonld_candidates(soup)
        if not _is_non_listing_jsonld(item)
    ]
    detail_items = _jsonld_detail_items(jsonld_items)
    blocks = _visible_detail_blocks(soup)

    h1_address = _text(soup, "h1")
    raw_address = (
        h1_address
        if h1_address and _EXPLICIT_H1_ADDRESS_RE.search(h1_address)
        else _first(soup, (".street",))
    )
    address, address_municipality = _validated_civic_address(raw_address)
    jsonld_address: str | None = None
    jsonld_municipality: str | None = None
    for item in jsonld_items:
        candidate_address, candidate_municipality = _validated_civic_address(
            item.get("address")
        )
        if candidate_address and not jsonld_address:
            jsonld_address = candidate_address
            jsonld_municipality = candidate_municipality
    address = address or jsonld_address
    municipality = (
        _first(soup, (".city",))
        or address_municipality
        or jsonld_municipality
    )

    asking = _price(_first(soup, (".price", "[itemprop='price']")))
    if asking is None:
        asking = next(
            (
                _detail_currency(block)
                for block in blocks
                if "$" in block
                and not re.match(r"(?:property\s+)?tax(?:es|ation)\b", block, re.IGNORECASE)
                and not re.match(r"assessment\b", block, re.IGNORECASE)
            ),
            None,
        )
    if asking is None:
        title_text = _text(soup, "title")
        asking = _detail_currency(title_text)
    if asking is None:
        asking = _jsonld_offer_price(detail_items)

    bedrooms = _number(_first(soup, (".bedrooms", ".beds", "[itemprop='numberOfBedrooms']")))
    bedrooms = bedrooms or _detail_metric(blocks, r"bed(?:room)?s?")
    if bedrooms is None:
        bedrooms = _number(_jsonld_first(detail_items, "numberOfBedrooms"))

    bathrooms = _number(_first(soup, (".bathrooms", ".baths", "[itemprop='numberOfBathrooms']")))
    bathrooms = bathrooms or _detail_metric(blocks, r"bath(?:room)?s?")
    if bathrooms is None:
        bathrooms = _number(
            _jsonld_first(detail_items, "numberOfBathroomsTotal", "numberOfBathrooms")
        )

    finished_sqft = _number(
        _first(
            soup,
            (
                ".finished-sqft",
                ".floor-area",
                ".house-size",
                "[itemprop='floorSize']",
            ),
        )
    )
    finished_sqft = finished_sqft or _detail_finished_sqft(blocks)
    if finished_sqft is None:
        floor_size = _jsonld_scalar(_jsonld_first(detail_items, "floorSize"))
        finished_sqft = _number(floor_size)

    lot_value = _first(
        soup,
        (".lot-size", ".lot-area", "[itemprop='lotSize']"),
    ) or _detail_label_value(blocks, r"lot\s+(?:size|area)")
    lot_measurements = _lot_measurements(lot_value, None)
    if not lot_measurements:
        jsonld_lot = _jsonld_scalar(_jsonld_first(detail_items, "lotSize"))
        lot_measurements = _lot_measurements(jsonld_lot, None)

    type_value = _first(
        soup,
        (".property-type", "[itemprop='propertyType']"),
    ) or _detail_label_value(blocks, r"(?:property\s+)?type")
    if not type_value:
        known_types = {
            "apartment",
            "condo",
            "condominium",
            "detached",
            "duplex",
            "manufactured home",
            "mobile home",
            "row house",
            "single family",
            "single family home",
            "single family residence",
            "townhouse",
            "triplex",
        }
        type_value = next(
            (block for block in blocks if block.casefold() in known_types),
            None,
        )
    if not type_value:
        jsonld_type = _jsonld_scalar(_jsonld_first(detail_items, "propertyType", "@type"))
        if isinstance(jsonld_type, list):
            jsonld_type = next((item for item in jsonld_type if item), None)
        type_value = str(jsonld_type).strip() if jsonld_type else None

    year_text = _first(soup, (".year-built", "[itemprop='yearBuilt']")) or _detail_label_value(
        blocks,
        r"year\s+built",
    )
    year_match = re.search(r"\b((?:18|19|20)\d{2})\b", year_text or "")
    year_built = _number(year_match.group(1)) if year_match else None
    if year_built is None:
        year_built = _number(_jsonld_first(detail_items, "yearBuilt"))

    taxes_text = _first(soup, (".taxes", "[itemprop='taxes']")) or _detail_label_value(
        blocks,
        r"tax(?:es|ation)",
    )
    taxes = _detail_currency(taxes_text) or _number(taxes_text)
    if taxes is None:
        taxes = _number(_jsonld_scalar(_jsonld_first(detail_items, "taxes")))

    mls_text = _detail_label_value(
        blocks,
        r"MLS(?:\s*®)?(?:\s*(?:#|number|no\.?))?",
    )
    mls_number = _mls(mls_text)
    if mls_number is None:
        mls_number = _mls(
            _jsonld_scalar(_jsonld_first(detail_items, "identifier", "sku"))
        )

    neighbourhood = _first(
        soup,
        (
            ".neighbourhood",
            ".neighborhood",
            ".property-area-minor",
            "[itemprop='neighborhood']",
            "[itemprop='neighbourhood']",
        ),
    ) or _detail_label_value(
        blocks,
        r"(?:property\s+area\s+minor|neighbou?rhood|community|area\s+minor)",
    )
    if not neighbourhood:
        neighbourhood = _jsonld_scalar(
            _jsonld_first(detail_items, "neighbourhood", "neighborhood")
        )

    description = _first(
        soup,
        (".description", ".property-description", "[itemprop='description']"),
    ) or _detail_label_value(blocks, r"description")
    if not description:
        description = _jsonld_scalar(_jsonld_first(detail_items, "description"))

    if not address and not mls_number:
        raise ValueError("Zolo detail page has no explicit property identity")

    result: dict[str, Any] = {
        "address": address,
        "municipality": municipality,
        "asking": asking,
        "bedrooms": bedrooms,
        "bathrooms": bathrooms,
        "finished_sqft": finished_sqft,
        "property_type": type_value,
        "year_built": year_built,
        "mls_number": mls_number,
        "neighbourhood": neighbourhood,
        "description": description,
        "taxes": taxes,
        "source_url": requested_url,
        "feed_url": requested_url,
        "observed_at": _aware(observed_at).isoformat(),
    }
    result.update(lot_measurements)
    return {key: value for key, value in result.items() if value not in (None, "")}


def parse_realtor_ca_detail(
    content: bytes,
    requested_url: str,
    observed_at: datetime,
) -> dict[str, Any]:
    """Parse one rendered REALTOR.ca detail page and retain lot provenance.

    REALTOR.ca exposes a stable property-summary DOM plus a small GTM data
    layer.  Read those explicit fields first; the generic public-detail parser
    remains a fallback for fields that this page variant does not render.
    """

    result = parse_zolo_detail(content, requested_url, observed_at)
    soup = BeautifulSoup(content, "html.parser")
    blocks = _visible_detail_blocks(soup)

    native = _realtor_data_layer_values(content)

    address_node = BeautifulSoup(content, "html.parser").select_one("#listingAddress")
    if address_node is not None:
        address_lines = [
            " ".join(line.split())
            for line in address_node.get_text("\n").splitlines()
            if line.strip()
        ]
        if address_lines and _EXPLICIT_H1_ADDRESS_RE.search(address_lines[0]):
            result["address"] = address_lines[0]
    city = native.get("city")
    if city:
        result["municipality"] = city

    summary = lambda suffix: _text(  # noqa: E731 - keeps the selector table local.
        soup,
        f"#propertyDetailsSectionContentSubCon_{suffix} "
        ".propertyDetailsSectionContentValue",
    )
    native_mls = _mls(_text(soup, "#MLNumberVal")) or _mls(native.get("listingID"))
    if native_mls:
        result["mls_number"] = native_mls
    native_price = _price(summary("Price") or native.get("price"))
    if native_price is not None:
        result["asking"] = native_price
    native_bedrooms = _number(summary("Bedrooms") or native.get("bedrooms"))
    if native_bedrooms is not None:
        result["bedrooms"] = native_bedrooms
    native_bathrooms = _number(summary("Bathrooms") or native.get("bathrooms"))
    if native_bathrooms is not None:
        result["bathrooms"] = native_bathrooms
    native_finished = _number(
        summary("SquareFootage") or native.get("interiorFloorSpace")
    )
    if native_finished is not None:
        result["finished_sqft"] = native_finished
    native_type = summary("PropertyType") or native.get("propertyType")
    if native_type:
        result["property_type"] = native_type
    native_neighbourhood = native.get("neighbourhood") or native.get("neighborhood")
    if native_neighbourhood:
        result["neighbourhood"] = native_neighbourhood
    native_status = native.get("status")
    if native_status:
        result["listing_status"] = native_status
    native_title = summary("Title")
    if native_title:
        result["ownership_type"] = native_title

    native_year = _number(summary("BuiltIn") or native.get("builtIn"))
    if native_year is not None:
        result["year_built"] = native_year
    native_taxes = _detail_currency(summary("AnnualPropertyTaxes"))
    if native_taxes is not None:
        result["taxes"] = native_taxes
    native_dom = summary("TimeOnRealtor")
    if native_dom:
        result["dom"] = native_dom

    area_value = _first(
        soup,
        (".lot-area", "[itemprop='lotSize']"),
    ) or _detail_label_value(blocks, r"(?:lot|land)\s+area")
    area_value = area_value or summary("LandSize") or native.get("landSize")
    explicit_area = _lot_area_sqft(area_value)

    finished_value = _first(
        soup,
        (".finished-sqft", ".floor-area", ".house-size", "[itemprop='floorSize']"),
    ) or _detail_label_value(
        blocks,
        r"(?:finished|living|interior|floor)\s+(?:area|size)",
    )
    finished_area = _number(finished_value)
    if finished_area is not None:
        result["finished_sqft"] = finished_area
    elif explicit_area is not None and result.get("finished_sqft") == explicit_area:
        result.pop("finished_sqft", None)

    dimensions_value = _first(
        soup,
        (".lot-dimensions", ".land-dimensions"),
    ) or _detail_label_value(
        blocks,
        r"(?:(?:lot|land)\s+dimensions|size\s+total(?:\s+text)?)",
    )
    dimensions_value = dimensions_value or summary("LandSize")
    dimensions = _lot_dimensions(dimensions_value)
    if not dimensions:
        description = _text(soup, "#propertyDescriptionCon") or result.get("description")
        description_dimensions = _realtor_description_dimensions(description)
        if description_dimensions:
            expected = float(description_dimensions[0]) * float(description_dimensions[1])
            explicit_is_placeholder = explicit_area is not None and float(explicit_area) < 500
            conflicts = (
                explicit_area is not None
                and expected > 0
                and not (0.5 <= float(explicit_area) / expected <= 2.0)
            )
            if conflicts and not explicit_is_placeholder:
                # Marketing copy often describes a different parcel (e.g. an
                # assembled development site), not this lot.  Trust the
                # structured Land Size and keep the mismatch auditable.
                result["lot_dimensions_description_conflict"] = True
            else:
                dimensions = description_dimensions
                dimensions_value = f"{dimensions[0]} x {dimensions[1]} ft"
    if dimensions:
        result["lot_dimensions"] = str(dimensions_value).strip()
        result["frontage_ft"] = dimensions[0]
        result["depth_ft"] = dimensions[1]
        result["geometry_source"] = "LISTING_OBSERVED"

    reconciled_area, area_source, area_audit = _reconcile_lot_area_with_dimensions(
        explicit_area,
        dimensions,
    )
    if reconciled_area is not None:
        result["lot_area_sqft"] = reconciled_area
        result["lot_area_source"] = area_source or "EXPLICIT_DETAIL"
    elif result.get("lot_area_sqft") is not None:
        result["lot_area_source"] = "EXPLICIT_DETAIL"
    for key, value in area_audit.items():
        result[key] = value

    price_sale_history = _realtor_price_sale_history(soup)
    if price_sale_history is not None:
        result["realtor_price_sale_history"] = price_sale_history
        has_sale = any(
            re.search(r"\b(?:sale|sold|closed)\b", str(event.get("status") or ""), re.IGNORECASE)
            and event.get("price") is not None
            for event in price_sale_history
        )
        result["realtor_history_status"] = "RECORDED" if has_sale else "NO_RECORDED_SALE_DISPLAYED"
    elif str(native.get("soldData") or "").strip().casefold() in {"yes", "true", "1"}:
        # The page says that sold data exists, but no explicit history rows
        # were rendered into the saved HTML. Keep this distinction visible.
        result["realtor_history_status"] = "SOLD_DATA_FLAG_WITHOUT_RENDERED_HISTORY"

    return result


def _realtor_data_layer_values(content: bytes) -> dict[str, str]:
    text = content.decode("utf-8", errors="ignore")
    values: dict[str, str] = {}
    for field in (
        "listingID",
        "propertyType",
        "price",
        "neighbourhood",
        "neighborhood",
        "bedrooms",
        "bathrooms",
        "landSize",
        "interiorFloorSpace",
        "builtIn",
        "city",
        "status",
        "soldData",
    ):
        match = re.search(
            rf"\b{re.escape(field)}\s*:\s*(['\"])(.*?)\1",
            text,
            re.IGNORECASE | re.DOTALL,
        )
        if match and match.group(2).strip():
            values[field] = " ".join(match.group(2).split())
    return values


def _realtor_description_dimensions(
    value: Any,
) -> tuple[int | float, int | float] | None:
    if value in (None, ""):
        return None
    text = str(value)
    dimension_pattern = re.compile(
        r"(?<!\w)(\d[\d,]*(?:\.\d+)?)\s*(?:'|ft\.?|feet)?\s*"
        r"(?:x|×)\s*"
        r"(\d[\d,]*(?:\.\d+)?)\s*(?:'|ft\.?|feet)?",
        re.IGNORECASE,
    )
    for match in dimension_pattern.finditer(text):
        context = text[max(0, match.start() - 60) : min(len(text), match.end() + 60)]
        if not re.search(r"\b(?:lot|land)\b", context, re.IGNORECASE):
            continue
        return _lot_dimensions(f"{match.group(1)} x {match.group(2)} ft")
    return None


def _row_key(row: Mapping[str, Any]) -> str:
    return str(row.get("mls_number") or row.get("source_url") or row.get("address") or "").strip().lower()


def _parse_html_records(
    content: bytes,
    requested_url: str,
    observed_at: datetime,
) -> tuple[list[dict[str, Any]], list[str]]:
    del observed_at
    soup = BeautifulSoup(content, "html.parser")
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    seen: set[str] = set()
    cards = soup.select(
        "[data-listing-card], .listing-card, .listingCard, .smallListingCard, .card-listing"
    )
    for index, card in enumerate(cards):
        row = _card_row(card, requested_url)
        if not row:
            if _looks_like_listing_card(card):
                errors.append(f"row {index}: listing card has no explicit address")
            continue
        row = {key: value for key, value in row.items() if value not in (None, "")}
        key = _row_key(row)
        if key and key not in seen:
            seen.add(key)
            rows.append(row)
    for item in _jsonld_candidates(soup):
        row = _jsonld_row(item, requested_url)
        if not row:
            continue
        row = {key: value for key, value in row.items() if value not in (None, "")}
        key = _row_key(row)
        if key and key not in seen:
            seen.add(key)
            rows.append(row)
    return rows, errors


def parse_public_html(
    content: bytes,
    requested_url: str,
    source_name: str,
    observed_at: datetime,
) -> list[dict[str, Any]]:
    """Parse explicit public listing cards/JSON-LD without guessing fields."""

    del source_name
    rows, errors = _parse_html_records(content, requested_url, observed_at)
    if errors and not rows:
        raise _PartialParseError(rows, errors)
    if errors:
        raise _PartialParseError(rows, errors)
    return rows


def _as_response(value: Any, *, request: Mapping[str, Any]) -> PublicFeedResponse:
    if isinstance(value, PublicFeedResponse):
        return value
    if isinstance(value, httpx.Response):
        return PublicFeedResponse(
            url=str(value.url),
            status_code=value.status_code,
            headers=dict(value.headers),
            content=value.content,
            method=str(request.get("method", "GET")).upper(),
        )
    if isinstance(value, Mapping):
        content = value.get("content", b"")
        if isinstance(content, str):
            content = content.encode("utf-8")
        return PublicFeedResponse(
            url=str(value.get("url") or request.get("url")),
            status_code=int(value.get("status_code", 200)),
            headers=dict(value.get("headers") or {}),
            content=bytes(content),
            method=str(value.get("method") or request.get("method", "GET")).upper(),
        )
    raise TypeError("public feed transport must return PublicFeedResponse, httpx.Response, or mapping")


class PublicFeedSource(_BaseSource):
    """Bounded read-only source for public HTML or public JSON listing feeds."""

    def __init__(
        self,
        *,
        name: str,
        source_type: SourceType | str,
        feed_requests: Iterable[Mapping[str, Any]],
        raw_snapshot_dir: str | Path,
        config: dict[str, Any] | None = None,
        transport: Callable[[Mapping[str, Any]], Any] | None = None,
        robots_transport: Callable[[str], Any] | None = None,
        sleep_fn: Callable[[float], None] = time.sleep,
        source_registry_id: str | None = None,
        source_url: str | None = None,
    ) -> None:
        super().__init__(
            name=name,
            source_type=source_type,
            source_registry_id=source_registry_id,
            source_url=source_url,
            config=config,
        )
        if self.coverage_complete is None:
            self.coverage_complete = False
        self.feed_requests = [dict(item) for item in feed_requests]
        self.raw_snapshot_dir = Path(raw_snapshot_dir)
        self.transport = transport
        self.robots_transport = robots_transport
        self.sleep_fn = sleep_fn
        self._robots_cache: dict[str, tuple[bool, str, PublicFeedResponse | None]] = {}

    def _allowed_domains(self) -> set[str]:
        configured = self.config.get("allowed_domains") or []
        if configured:
            return {str(item).lower().rstrip(".") for item in configured}
        parsed = urlparse(self.source_url)
        return {parsed.hostname.lower()} if parsed.hostname else set()

    def _allowed_url(self, url: str) -> bool:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return False
        host = parsed.hostname.lower().rstrip(".")
        return any(host == domain or host.endswith(f".{domain}") for domain in self._allowed_domains())

    def _robots_url(self, url: str) -> str:
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}/robots.txt"

    def _get_robots(self, target_url: str) -> tuple[bool, str, PublicFeedResponse | None]:
        if not bool(self.config.get("respect_robots", True)):
            return True, "robots check disabled by explicit source configuration", None
        robots_url = self._robots_url(target_url)
        if robots_url in self._robots_cache:
            allowed, message, _ = self._robots_cache[robots_url]
            return allowed, message, None
        try:
            request = {"url": robots_url, "method": "GET", "headers": self._headers()}
            if self.robots_transport is not None:
                response = _as_response(self.robots_transport(robots_url), request=request)
            else:
                with httpx.Client(timeout=float(self.config.get("timeout_seconds", 15)), follow_redirects=True) as client:
                    raw = client.get(robots_url, headers=self._headers())
                response = _as_response(raw, request=request)
            if response.status_code != 200 or _has_challenge(response.content):
                result = (False, f"robots unavailable or challenged: HTTP {response.status_code}", response)
            elif not self._allowed_url(response.url):
                result = (False, f"robots redirected to disallowed domain: {response.url}", response)
            else:
                parser = RobotFileParser()
                parser.parse(response.content.decode("utf-8", errors="replace").splitlines())
                allowed = parser.can_fetch(str(self.config.get("user_agent")), target_url)
                result = (bool(allowed), "robots.txt disallows target" if not allowed else "", response)
        except (httpx.HTTPError, OSError, TypeError, ValueError) as exc:
            result = (False, f"robots fetch failed: {type(exc).__name__}: {exc}", None)
        self._robots_cache[robots_url] = result
        return result

    def _headers(self) -> dict[str, str]:
        return {
            "User-Agent": str(self.config.get("user_agent", "northshore-property-watchdog/0.1")),
            "Accept": "application/json, text/html;q=0.9, */*;q=0.1",
        }

    def _fetch(self, request: Mapping[str, Any]) -> PublicFeedResponse:
        if self.transport is not None:
            return _as_response(self.transport(request), request=request)
        method = str(request.get("method", "GET")).upper()
        headers = dict(self._headers())
        headers.update({str(key): str(value) for key, value in (request.get("headers") or {}).items()})
        with httpx.Client(timeout=float(self.config.get("timeout_seconds", 15)), follow_redirects=True) as client:
            response = client.request(
                method,
                str(request["url"]),
                params=request.get("params"),
                data=request.get("data"),
                headers=headers,
            )
        return _as_response(response, request=request)

    def _write_snapshot(
        self,
        response: PublicFeedResponse,
        *,
        observed_at: datetime,
        request_index: int,
        request_kind: str = "target",
    ) -> SourceSnapshot:
        payload_hash = sha256(response.content).hexdigest()
        suffix = (
            ".json"
            if "json" in _content_type(response)
            else ".html"
            if "html" in _content_type(response)
            else ".txt"
            if "text" in _content_type(response)
            else ".bin"
        )
        stamp = _aware(observed_at).strftime("%Y%m%dT%H%M%S%fZ")
        path = self.raw_snapshot_dir / f"{stamp}_{request_index:03d}_{payload_hash[:12]}{suffix}"
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            path = self.raw_snapshot_dir / f"{stamp}_{request_index:03d}_{payload_hash[:12]}_{time.time_ns()}{suffix}"
        path.write_bytes(response.content)
        screenshot_path: Path | None = None
        screenshot = getattr(response, "screenshot", None)
        if screenshot:
            screenshot_path = path.with_suffix(".png")
            screenshot_path.write_bytes(bytes(screenshot))
        retrieved_at = datetime.now(timezone.utc)
        return SourceSnapshot(
            source_registry_id=self.source_registry_id,
            url=response.url,
            final_url=response.url,
            method=response.method,
            request_kind=request_kind,
            status_code=response.status_code,
            content_type=_content_type(response) or None,
            raw_path=str(path),
            screenshot_path=str(screenshot_path) if screenshot_path else None,
            sha256=payload_hash,
            size_bytes=len(response.content),
            observed_at=_aware(observed_at),
            retrieved_at=retrieved_at,
            source_version=self.config.get("source_version"),
        )

    def _follow_up_requests(
        self,
        request: Mapping[str, Any],
        response: PublicFeedResponse,
        parsed_rows: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Return bounded follow-up requests for adapters that support paging."""

        del request, response, parsed_rows
        return []

    def _parse(self, response: PublicFeedResponse, request: Mapping[str, Any], observed_at: datetime) -> tuple[list[dict[str, Any]], list[str]]:
        parser = str(request.get("parser") or "").lower()
        if not parser:
            parser = "realtor_ca_json" if "json" in _content_type(response) else "public_html"
        listing_base_url = str(request.get("base_url") or request["url"])
        feed_url = str(request["url"])
        if parser in {"realtor_ca_json", "realtor_json"}:
            try:
                rows, errors = _parse_realtor_records(response.content, listing_base_url, observed_at)
            except _PartialParseError as exc:
                rows, errors = exc.rows, exc.errors
            for row in rows:
                row["feed_url"] = feed_url
            return rows, errors
        if parser in {"public_html", "html", "zolo_html"}:
            try:
                rows, errors = _parse_html_records(response.content, listing_base_url, observed_at)
            except _PartialParseError as exc:
                rows, errors = exc.rows, exc.errors
            for row in rows:
                row["feed_url"] = feed_url
            return rows, errors
        if parser == "json":
            payload = _json_payload(response.content)
            items = payload if isinstance(payload, list) else payload.get("records", payload.get("listings", [])) if isinstance(payload, Mapping) else []
            if not isinstance(items, list):
                raise ValueError("generic public JSON feed has no records/listings array")
            rows = [dict(item) for item in items if isinstance(item, Mapping)]
            errors = [f"row {index}: expected listing object" for index, item in enumerate(items) if not isinstance(item, Mapping)]
            return rows, errors
        raise ValueError(f"unsupported public feed parser: {parser}")

    def collect(self, observed_at: datetime) -> SourceRunResult:
        started_at = datetime.now(timezone.utc)
        observed_at = _aware(observed_at)
        if self.config.get("enabled") is False:
            return self._result(started_at=started_at, status=SourceRunStatus.SKIPPED_DISABLED, finished_at=datetime.now(timezone.utc))
        if not self.feed_requests:
            return self._result(
                started_at=started_at,
                status=SourceRunStatus.FAILED,
                finished_at=datetime.now(timezone.utc),
                errors=["public feed has no requests"],
            )

        max_requests = max(1, int(self.config.get("max_requests_per_run", len(self.feed_requests))))
        rows: list[dict[str, Any]] = []
        snapshots: list[SourceSnapshot] = []
        errors: list[str] = []
        warnings: list[str] = []
        policy_snapshots: list[SourceSnapshot] = []
        attempts = 0
        blocked = False
        parse_failed = False
        cap_reached = False
        blocked_hosts: set[str] = set()

        pending_requests = [dict(item) for item in self.feed_requests]
        request_index = 0
        while pending_requests:
            if attempts >= max_requests:
                warnings.append(f"request cap reached: {max_requests}")
                cap_reached = True
                break
            request = pending_requests.pop(0)
            current_request_index = request_index
            request_index += 1
            target_url = str(request.get("url") or "")
            if not self._allowed_url(target_url):
                errors.append(f"target domain or scheme is not allowed: {target_url}")
                blocked = True
                break
            target_host = (urlparse(target_url).hostname or "").lower().rstrip(".")
            if target_host in blocked_hosts:
                warnings.append(f"skipped request on blocked host: {target_host}")
                continue
            robots_allowed, robots_error, robots_response = self._get_robots(target_url)
            if robots_response is not None:
                policy_snapshots.append(
                    self._write_snapshot(
                        robots_response,
                        observed_at=observed_at,
                        request_index=current_request_index,
                        request_kind="robots",
                    )
                )
            if not robots_allowed:
                errors.append(f"robots policy blocked {target_url}: {robots_error}")
                blocked = True
                blocked_hosts.add(target_host)
                continue
            if attempts:
                self.sleep_fn(float(self.config.get("delay_seconds", self.config.get("min_delay_seconds", 0))))
            try:
                response = self._fetch(request)
            except (httpx.HTTPError, OSError, TypeError, ValueError) as exc:
                attempts += 1
                errors.append(f"request {target_url}: {type(exc).__name__}: {exc}")
                blocked_hosts.add(target_host)
                continue
            attempts += 1
            snapshot = self._write_snapshot(
                response,
                observed_at=observed_at,
                request_index=current_request_index,
            )
            snapshots.append(snapshot)
            if not self._allowed_url(response.url):
                errors.append(f"redirected to disallowed domain: {response.url}")
                blocked = True
                break
            if response.status_code in {401, 403, 429}:
                errors.append(f"public feed blocked: HTTP {response.status_code} at {target_url}")
                blocked = True
                blocked_hosts.add(target_host)
                continue
            if _has_challenge(response.content):
                errors.append(f"public feed challenge detected at {target_url}")
                blocked = True
                blocked_hosts.add(target_host)
                continue
            try:
                parsed_rows, parse_errors = self._parse(response, request, observed_at)
            except (ValueError, TypeError, json.JSONDecodeError) as exc:
                parse_failed = True
                errors.append(f"parse {target_url}: {type(exc).__name__}: {exc}")
                continue
            rows.extend(parsed_rows)
            errors.extend(f"parse {target_url}: {error}" for error in parse_errors)
            try:
                pending_requests.extend(
                    dict(item)
                    for item in self._follow_up_requests(request, response, parsed_rows)
                )
            except (ValueError, TypeError) as exc:
                errors.append(f"pagination {target_url}: {type(exc).__name__}: {exc}")

        normalized_rows, normalize_errors = self._normalize_rows(rows, observed_at)
        errors.extend(normalize_errors)
        snapshots.extend(policy_snapshots)
        if blocked:
            status = SourceRunStatus.PARTIAL if rows else SourceRunStatus.BLOCKED_POLICY
        elif errors and normalized_rows:
            status = SourceRunStatus.PARTIAL
        elif cap_reached:
            status = SourceRunStatus.PARTIAL
        elif errors or parse_failed:
            status = SourceRunStatus.FAILED
        else:
            status = SourceRunStatus.SUCCESS
        return self._result(
            started_at=started_at,
            finished_at=datetime.now(timezone.utc),
            records=normalized_rows,
            snapshots=snapshots,
            errors=errors,
            warnings=warnings,
            status=status,
        )


def _has_challenge(content: bytes) -> bool:
    text = content.decode("utf-8", errors="ignore")
    soup = BeautifulSoup(text, "html.parser")
    for element in soup(["script", "style", "template"]):
        element.decompose()
    visible_text = " ".join(soup.get_text(" ").split()).lower()
    return any(marker in visible_text for marker in _CHALLENGE_MARKERS)


def has_visible_challenge(content: bytes) -> bool:
    """Expose the existing visible-text-only challenge check for local replays."""

    return _has_challenge(content)
