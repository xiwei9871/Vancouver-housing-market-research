from __future__ import annotations

import json
import io
import math
import re
import struct
import zipfile
import csv
from dataclasses import dataclass
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from hashlib import sha256
from pathlib import Path
from typing import Any
from typing import Callable
from typing import Mapping
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict, Field
from bs4 import BeautifulSoup

from .models import OfficialQueryStatus, PropertyRecord, SourceType
from .normalization import normalize_address
from .official_transport import OfficialFetchResult, OfficialHttpResponse, OfficialHttpTransport
from .sources import SourceRunResult, SourceRunStatus


class OfficialLookupRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    property_id: str
    status: OfficialQueryStatus
    fields: dict[str, Any] = Field(default_factory=dict)
    source_url: str
    retrieved_at: datetime | None = None
    source_version: str | None = None
    match_method: str | None = None
    query_scope: str | None = None
    raw_snapshot_path: str | None = None
    warnings: list[str] = Field(default_factory=list)


class OfficialIdentityMatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: OfficialQueryStatus
    candidate: dict[str, Any] | None = None
    match_method: str | None = None
    warnings: list[str] = Field(default_factory=list)


class OfficialEnrichmentBatch(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    source_run: SourceRunResult
    rows: list[OfficialLookupRow] = Field(default_factory=list)


_NUMBER_RE = re.compile(r"[-+]?\d[\d,]*(?:\.\d+)?")
_DATE_RE = re.compile(r"\b(?:\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d{1,2}[-/]\d{1,2}[-/]\d{4})\b")
_LOT_DIMENSIONS_RE = re.compile(
    r"^\s*(?P<frontage>\d+(?:\.\d+)?)\s*[x×]\s*(?P<depth>\d+(?:\.\d+)?)\s*(?:ft|feet|foot|')?\s*$",
    re.IGNORECASE,
)
_POSTAL_CODE_RE = re.compile(r"^[A-Z]\d[A-Z]\d[A-Z]\d$", re.IGNORECASE)
_ORDINAL_ADDRESS_RE = re.compile(r"^(?P<number>\d+)(?:st|nd|rd|th)$", re.IGNORECASE)
_ADDRESS_DIRECTIONS = frozenset({"e", "w", "n", "s", "ne", "nw", "se", "sw"})
_ADDRESS_STREET_TYPES = frozenset({
    "st", "ave", "rd", "dr", "blvd", "pl", "cres", "ct", "ln", "hwy", "ter",
})
_ADDRESS_STREET_TYPE_VARIANTS = {
    "st": ("ST", "STREET"),
    "ave": ("AVE", "AV", "AVENUE"),
    "rd": ("RD", "ROAD"),
    "dr": ("DR", "DRIVE"),
    "blvd": ("BLVD", "BOULEVARD"),
    "pl": ("PL", "PLACE"),
    "cres": ("CRES", "CRESCENT"),
    "ct": ("CT", "COURT"),
    "ln": ("LN", "LANE"),
    "hwy": ("HWY", "HIGHWAY"),
    "ter": ("TER", "TERRACE"),
}
_ADDRESS_LOCALITIES = (
    ("west vancouver", "District of West Vancouver"),
    ("north vancouver", "North Vancouver"),
    ("port alberni", "City of Port Alberni"),
    ("vancouver", "City of Vancouver"),
    ("kitimat", "District of Kitimat"),
    ("burnaby", "City of Burnaby"),
    ("nanaimo", "City of Nanaimo"),
    ("victoria", "City of Victoria"),
    ("abbotsford", "City of Abbotsford"),
    ("surrey", "City of Surrey"),
    ("richmond", "City of Richmond"),
    ("coquitlam", "City of Coquitlam"),
    ("port coquitlam", "City of Port Coquitlam"),
    ("port moody", "City of Port Moody"),
    ("new westminster", "City of New Westminster"),
    ("white rock", "City of White Rock"),
    ("delta", "City of Delta"),
    ("langley", "City of Langley"),
    ("maple ridge", "City of Maple Ridge"),
    ("pitt meadows", "City of Pitt Meadows"),
    ("kelowna", "City of Kelowna"),
    ("kamloops", "City of Kamloops"),
    ("chilliwack", "District of Chilliwack"),
    ("mission", "District of Mission"),
    ("squamish", "District of Squamish"),
    ("whistler", "Resort Municipality of Whistler"),
    ("vernon", "City of Vernon"),
    ("penticton", "City of Penticton"),
    ("prince george", "City of Prince George"),
    ("courtenay", "City of Courtenay"),
    ("campbell river", "City of Campbell River"),
    ("duncan", "City of Duncan"),
    ("ladysmith", "Town of Ladysmith"),
    ("parksville", "City of Parksville"),
    ("saanich", "District of Saanich"),
    ("langford", "City of Langford"),
    ("colwood", "City of Colwood"),
    ("sidney", "Town of Sidney"),
    ("sooke", "District of Sooke"),
    ("trail", "City of Trail"),
    ("castlegar", "City of Castlegar"),
    ("nelson", "City of Nelson"),
    ("revelstoke", "City of Revelstoke"),
    ("salmon arm", "City of Salmon Arm"),
    ("cranbrook", "City of Cranbrook"),
    ("terrace", "City of Terrace"),
    ("dawson creek", "City of Dawson Creek"),
    ("fort st john", "City of Fort St John"),
    ("prince rupert", "City of Prince Rupert"),
    ("tofino", "District of Tofino"),
    ("ucluelet", "District of Ucluelet"),
)


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value


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
        result = float(raw)
    except ValueError:
        return None
    return int(result) if result.is_integer() else result


def _date(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return _aware(value)
    if value in (None, ""):
        return None
    text = str(value).strip()
    try:
        return _aware(datetime.fromisoformat(text.replace("Z", "+00:00")))
    except ValueError:
        pass
    for fmt in ("%b %d, %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    match = _DATE_RE.search(text)
    if not match:
        return None
    raw = match.group(0).replace("/", "-")
    for fmt in (
        "%Y-%m-%d",
        "%d-%m-%Y",
        "%m-%d-%Y",
        "%b %d, %Y",
        "%B %d, %Y",
    ):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _text(value: Any) -> str:
    return " ".join(str(value or "").split())


def _candidate_pids(candidate: Mapping[str, Any]) -> set[str]:
    values: list[Any] = [candidate.get("pid"), candidate.get("PID")]
    values.extend(candidate.get("pids") or [])
    return {str(value).strip() for value in values if str(value or "").strip()}


def _candidate_addresses(candidate: Mapping[str, Any]) -> set[str]:
    values: list[Any] = [candidate.get("address"), candidate.get("civic_address")]
    values.extend(candidate.get("secondary_civic_addresses") or [])
    values.extend(candidate.get("addresses") or [])
    addresses: set[str] = set()
    for value in values:
        if not str(value or "").strip():
            continue
        text = str(value)
        addresses.add(normalize_address(text, str(candidate.get("municipality") or "")))
        addresses.add(normalize_address(text, None))
    return addresses


def _address_tokens(value: Any, *, include_direction: bool = True) -> list[str]:
    """Return a conservative civic-address token sequence.

    BCA commonly moves a directional from ``E 45TH AVE`` to ``45TH AVE E``
    and uses ``45TH`` where a listing uses ``45``.  This normalizer handles
    those presentational differences while keeping the street name and civic
    number intact.  A postal code is recognized by the Canadian alternating
    letter/number pattern; arbitrary six-letter street names are not postal
    codes.
    """

    tokens = normalize_address(str(value or ""), None).split()
    if not tokens:
        return []
    for index, token in enumerate(tokens):
        if _POSTAL_CODE_RE.fullmatch(token):
            tokens = tokens[:index]
            break

    street_type_index = next(
        (index for index, token in enumerate(tokens[1:], start=1) if token in _ADDRESS_STREET_TYPES),
        None,
    )
    if street_type_index is not None:
        retained = tokens[: street_type_index + 1]
        for token in tokens[street_type_index + 1 :]:
            if token in {"vancouver", "burnaby", "nanaimo", "victoria", "abbotsford", "british", "columbia", "canada", "bc"}:
                break
            retained.append(token)
        tokens = retained

    normalized: list[str] = []
    for token in tokens:
        ordinal = _ORDINAL_ADDRESS_RE.fullmatch(token)
        normalized.append(ordinal.group("number") if ordinal else token)

    if len(normalized) > 1:
        civic = normalized[0]
        rest = normalized[1:]
        directions = [token for token in rest if token in _ADDRESS_DIRECTIONS]
        rest = [token for token in rest if token not in _ADDRESS_DIRECTIONS]
        if include_direction:
            rest.extend(directions)
        normalized = [civic, *rest]
    return normalized


def _address_core(value: Any) -> str:
    """Keep the civic portion while ignoring municipality/postal suffixes."""

    return " ".join(_address_tokens(value))


def _address_core_without_direction(value: Any) -> str:
    return " ".join(_address_tokens(value, include_direction=False))


def _addresses_match(left: Any, right: Any) -> bool:
    left_core = _address_core(left)
    right_core = _address_core(right)
    if not left_core or not right_core:
        return False
    return left_core == right_core or left_core.startswith(f"{right_core} ") or right_core.startswith(f"{left_core} ")


def _addresses_match_direction_tolerant(left: Any, right: Any) -> bool:
    left_core = _address_core_without_direction(left)
    right_core = _address_core_without_direction(right)
    if not left_core or not right_core:
        return False
    return left_core == right_core or left_core.startswith(f"{right_core} ") or right_core.startswith(f"{left_core} ")


def _municipality_from_address(value: Any) -> str | None:
    text = " ".join(normalize_address(str(value or ""), None).split()).casefold()
    for locality, municipality in _ADDRESS_LOCALITIES:
        normalized_locality = " ".join(normalize_address(locality, None).split()).casefold()
        if text.endswith(f" {normalized_locality}") or text == normalized_locality:
            return municipality
    return None


def _ordinalize_address_number(token: str) -> str:
    if not token.isdigit():
        return token
    number = int(token)
    suffix = "th"
    if number % 100 not in {11, 12, 13}:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(number % 10, "th")
    return f"{number}{suffix}"


def _address_query_variants(address: Any) -> list[tuple[str, str]]:
    """Generate deterministic civic-address variants used by official portals."""

    original = " ".join(str(address or "").split())
    if not original:
        return []
    normalized = normalize_address(original, None).split()
    variants: list[tuple[str, str]] = [(original, "original")]
    street_type_index = next(
        (index for index, token in enumerate(normalized[1:], start=1) if token in _ADDRESS_STREET_TYPES),
        None,
    )
    if street_type_index is None:
        return variants

    def add(tokens: list[str], name: str) -> None:
        # Keep fallback queries in the same uppercase style used by the
        # public portal's own address search requests.  This is cosmetic for
        # matching, but makes the raw request provenance easy to audit.
        value = " ".join(tokens).strip().upper()
        if value and value.casefold() not in {item[0].casefold() for item in variants}:
            variants.append((value, name))

    # Preserve the source ordering while normalizing punctuation/spacing
    # before generating the municipality's shorter suffix spelling.
    add(list(normalized), "normalized_source")

    leading_direction = (
        len(normalized) > 1 and normalized[1] in _ADDRESS_DIRECTIONS
    )
    has_numeric_street_name = any(
        token.isdigit() for token in normalized[1:street_type_index]
    )
    query_forms = (
        ((True, "normalized_ordinal"), (False, "normalized"))
        if has_numeric_street_name
        else ((False, "normalized"), (True, "normalized_ordinal"))
    )
    for ordinalize, label in query_forms:
        tokens = list(normalized)
        type_index = street_type_index
        if ordinalize:
            for index in range(1, type_index):
                tokens[index] = _ordinalize_address_number(tokens[index])
        if leading_direction:
            direction = tokens.pop(1)
            type_index -= 1
            tokens.insert(type_index + 1, direction)
        add(tokens, label)

        if leading_direction:
            dropped = list(tokens)
            dropped.pop(type_index + 1)
            add(dropped, f"{label}_without_leading_direction")

    # Municipal GIS address layers commonly use a shorter suffix than the
    # listing/BCA portal (for example ``AVENUE`` -> ``AV`` rather than
    # ``AVE``).  Add suffix-only variants while retaining the civic number,
    # street name and direction already produced above.
    current_variants = list(variants)
    for value, label in current_variants:
        tokens = value.split()
        type_index = next(
            (
                index
                for index, token in enumerate(tokens[1:], start=1)
                if token.casefold() in _ADDRESS_STREET_TYPE_VARIANTS
            ),
            None,
        )
        if type_index is None:
            continue
        for suffix in _ADDRESS_STREET_TYPE_VARIANTS[tokens[type_index].casefold()]:
            updated = list(tokens)
            updated[type_index] = suffix
            add(updated, f"{label}_{suffix.casefold()}")
    return variants


# Empirical east/west split for Vancouver numbered avenues: across the
# observed property universe no "E"-numbered address sits west of
# -123.1054 and no "W"-numbered address sits east of -123.1076.  The
# midpoint with a small dead zone keeps near-boundary properties honest.
_VANCOUVER_EW_SPLIT_LNG = -123.1066
_VANCOUVER_EW_SPLIT_DEAD_ZONE = 0.001


def _candidate_direction(address: Any) -> str | None:
    """Standalone E/W direction token in an address, if any."""

    tokens = normalize_address(str(address or ""), None).upper().split()
    for token in tokens:
        if token in {"E", "W"}:
            return token
    return None


def _record_ew_side(record: PropertyRecord) -> str | None:
    """Expected E/W side of a Vancouver numbered-avenue address by longitude."""

    longitude = getattr(record, "longitude", None)
    if longitude in (None, "", [], {}):
        return None
    try:
        lng = float(longitude)
    except (TypeError, ValueError):
        return None
    if lng > _VANCOUVER_EW_SPLIT_LNG + _VANCOUVER_EW_SPLIT_DEAD_ZONE:
        return "E"
    if lng < _VANCOUVER_EW_SPLIT_LNG - _VANCOUVER_EW_SPLIT_DEAD_ZONE:
        return "W"
    return None


def _disambiguate_address_matches(
    record: PropertyRecord,
    matches: list[Mapping[str, Any]],
) -> tuple[Mapping[str, Any] | None, str | None]:
    """Narrow multiple address matches using objective evidence only.

    Two auditable disambiguators, applied in order:

    1. Municipality-confirmed: when a province-wide BCA search returns a
       duplicate address in another city that is not in the locality table
       (``municipality_inferred`` false), prefer the single candidate whose
       municipality was parsed from its own label.
    2. Direction/geo: a numbered-avenue E/W twin in the same municipality is
       resolved by the listing longitude against the empirical split.  Only
       standalone W/E direction tokens are compared, and only when every
       match but one disagrees with the observed side.
    """

    inferred = [item for item in matches if item.get("municipality_inferred")]
    if len(inferred) == 1:
        return inferred[0], "ADDRESS_MUNICIPALITY_CONFIRMED"

    expected = _record_ew_side(record)
    if expected is None:
        return None, None
    directions = [_candidate_direction(item.get("address") or item.get("civic_address")) for item in matches]
    on_side = [item for item, direction in zip(matches, directions) if direction in (None, expected)]
    if len(on_side) == 1:
        return on_side[0], "ADDRESS_DIRECTION_GEO"
    return None, None


def match_official_identity(
    record: PropertyRecord,
    candidates: list[Mapping[str, Any]],
) -> OfficialIdentityMatch:
    """Match an official result without making a fuzzy identity choice."""

    if not candidates:
        return OfficialIdentityMatch(status=OfficialQueryStatus.NO_MATCH)
    record_pid = str(record.pid or "").strip()
    if record_pid:
        pid_matches = [item for item in candidates if record_pid in _candidate_pids(item)]
        if len(pid_matches) == 1:
            return OfficialIdentityMatch(
                status=OfficialQueryStatus.SUCCESS,
                candidate=dict(pid_matches[0]),
                match_method="PID",
            )
        if len(pid_matches) > 1:
            return OfficialIdentityMatch(status=OfficialQueryStatus.AMBIGUOUS, warnings=["MULTIPLE_PID_MATCHES"])
    address_matches = []
    for item in candidates:
        candidate_addresses = [
            item.get("address"),
            item.get("civic_address"),
            *(item.get("secondary_civic_addresses") or []),
            *(item.get("addresses") or []),
        ]
        municipality = str(item.get("municipality") or record.municipality)
        if (
            normalize_address(municipality, record.municipality)
            == normalize_address(record.municipality, record.municipality)
            and any(_addresses_match(record.address, value) for value in candidate_addresses)
        ):
            address_matches.append(item)
    if len(address_matches) == 1:
        candidate = address_matches[0]
        if record_pid and _candidate_pids(candidate) and record_pid not in _candidate_pids(candidate):
            return OfficialIdentityMatch(status=OfficialQueryStatus.AMBIGUOUS, warnings=["PID_MISMATCH"])
        return OfficialIdentityMatch(
            status=OfficialQueryStatus.SUCCESS,
            candidate=dict(candidate),
            match_method="ADDRESS",
        )
    if len(address_matches) > 1:
        resolved, method = _disambiguate_address_matches(record, address_matches)
        if resolved is not None:
            if record_pid and _candidate_pids(resolved) and record_pid not in _candidate_pids(resolved):
                return OfficialIdentityMatch(status=OfficialQueryStatus.AMBIGUOUS, warnings=["PID_MISMATCH"])
            return OfficialIdentityMatch(
                status=OfficialQueryStatus.SUCCESS,
                candidate=dict(resolved),
                match_method=method,
            )
        return OfficialIdentityMatch(status=OfficialQueryStatus.AMBIGUOUS, warnings=["MULTIPLE_ADDRESS_MATCHES"])

    # Some BCA labels omit a directional that is present in the listing
    # (for example ``2530 7TH AVE`` vs ``2530 7TH AVE E``).  Use this tolerant
    # comparison only after exact matching found no candidate, and still
    # require one unique result.
    tolerant_matches = []
    for item in candidates:
        candidate_addresses = [
            item.get("address"),
            item.get("civic_address"),
            *(item.get("secondary_civic_addresses") or []),
            *(item.get("addresses") or []),
        ]
        municipality = str(item.get("municipality") or record.municipality)
        if (
            normalize_address(municipality, record.municipality)
            == normalize_address(record.municipality, record.municipality)
            and any(_addresses_match_direction_tolerant(record.address, value) for value in candidate_addresses)
        ):
            tolerant_matches.append(item)
    if len(tolerant_matches) == 1:
        candidate = tolerant_matches[0]
        if record_pid and _candidate_pids(candidate) and record_pid not in _candidate_pids(candidate):
            return OfficialIdentityMatch(status=OfficialQueryStatus.AMBIGUOUS, warnings=["PID_MISMATCH"])
        return OfficialIdentityMatch(
            status=OfficialQueryStatus.SUCCESS,
            candidate=dict(candidate),
            match_method="ADDRESS_DIRECTION_TOLERANT",
        )
    if len(tolerant_matches) > 1:
        resolved, method = _disambiguate_address_matches(record, tolerant_matches)
        if resolved is not None:
            if record_pid and _candidate_pids(resolved) and record_pid not in _candidate_pids(resolved):
                return OfficialIdentityMatch(status=OfficialQueryStatus.AMBIGUOUS, warnings=["PID_MISMATCH"])
            return OfficialIdentityMatch(
                status=OfficialQueryStatus.SUCCESS,
                candidate=dict(resolved),
                match_method=f"{method}_TOLERANT",
            )
        return OfficialIdentityMatch(status=OfficialQueryStatus.AMBIGUOUS, warnings=["MULTIPLE_ADDRESS_MATCHES"])
    if record_pid and any(_candidate_pids(item) for item in candidates):
        return OfficialIdentityMatch(status=OfficialQueryStatus.AMBIGUOUS, warnings=["PID_MISMATCH"])
    return OfficialIdentityMatch(status=OfficialQueryStatus.NO_MATCH)


def _identity_warnings(
    record: PropertyRecord,
    candidates: list[Mapping[str, Any]],
    identity: OfficialIdentityMatch,
) -> list[str]:
    """Make an identity mismatch auditable without exposing it as a fact update."""

    warnings = list(identity.warnings)
    if "PID_MISMATCH" in warnings:
        official_pids = sorted({pid for item in candidates for pid in _candidate_pids(item)})
        warnings.append(f"EXPECTED_PID={record.pid or 'UNKNOWN'}")
        warnings.append(f"OFFICIAL_PIDS={','.join(official_pids) or 'UNKNOWN'}")
    return warnings


def _snapshot_for_request(
    fetched: OfficialFetchResult,
    request: Mapping[str, Any],
    response: OfficialHttpResponse | None = None,
) -> Any:
    """Select the snapshot for a response even when many requests share one URL."""

    candidates = [
        item
        for item in fetched.snapshots
        if item.url == request.get("url") or item.final_url == request.get("url")
    ]
    if response is not None:
        matching_responses = [item for item in fetched.responses if item is response]
        if matching_responses:
            response_index = fetched.responses.index(response)
            successful_snapshots = [
                item
                for item in fetched.snapshots
                if 200 <= item.status_code < 300
            ]
            if response_index < len(successful_snapshots):
                selected = successful_snapshots[response_index]
                if selected.url == request.get("url") or selected.final_url == request.get("url"):
                    return selected
        digest = sha256(response.content).hexdigest()
        digest_matches = [item for item in candidates if item.sha256 == digest]
        if digest_matches:
            return digest_matches[-1]
    return candidates[-1] if candidates else None


def _rows_from_payload(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [dict(item) for item in payload if isinstance(item, Mapping)]
    if not isinstance(payload, Mapping):
        return []
    for key in ("results", "Results", "items", "properties", "records", "data", "features"):
        value = payload.get(key)
        if isinstance(value, list):
            rows: list[dict[str, Any]] = []
            for item in value:
                if not isinstance(item, Mapping):
                    continue
                # ArcGIS REST query responses wrap attributes in features.
                # Preserve geometry as an optional field for adapters that
                # need it, while exposing attributes to the normal parsers.
                if isinstance(item.get("attributes"), Mapping):
                    row = dict(item["attributes"])
                    if isinstance(item.get("geometry"), Mapping):
                        row["geometry"] = dict(item["geometry"])
                    rows.append(row)
                else:
                    rows.append(dict(item))
            return rows
        if isinstance(value, Mapping):
            return [dict(value)]
    return [dict(payload)]


def _sales(raw: Mapping[str, Any]) -> tuple[list[datetime], list[int]]:
    values = raw.get("sales_history") or raw.get("salesHistory") or raw.get("sales") or []
    if isinstance(values, Mapping):
        values = [values]
    dates: list[datetime] = []
    prices: list[int] = []
    for item in values if isinstance(values, list) else []:
        if not isinstance(item, Mapping):
            continue
        sale_date = _date(item.get("date") or item.get("sale_date") or item.get("conveyance_date"))
        sale_price = _number(item.get("price") or item.get("sale_price") or item.get("conveyance_price"))
        if sale_date is not None and sale_price is not None:
            dates.append(sale_date)
            prices.append(int(sale_price))
    return dates, prices


def _normalize_bca_row(raw: Mapping[str, Any], source_url: str) -> dict[str, Any]:
    row = dict(raw)
    dates, prices = _sales(row)
    fields: dict[str, Any] = {}
    aliases = {
        "total": "assessment_total",
        "land": "assessment_land",
        "improvement": "assessment_improvement",
        "building": "assessment_improvement",
        "previous_total": "previous_year_total",
        "previous_land": "previous_year_land",
        "previous_improvement": "previous_year_improvement",
        "previous_building": "previous_year_improvement",
    }
    for raw_name, value in row.items():
        field = aliases.get(str(raw_name), str(raw_name))
        if field in {
            "assessment_total",
            "assessment_land",
            "assessment_improvement",
            "previous_year_total",
            "previous_year_land",
            "previous_year_improvement",
        }:
            numeric = _number(value)
            if numeric is not None:
                fields[field] = int(numeric)
    for field in (
        "assessment_year",
        "pid",
        "PID",
        "legal_description",
        "address",
        "civic_address",
        "municipality",
        "year_built",
        "finished_sqft",
        "lot_area_sqft",
        "bedrooms",
        "bathrooms",
        "previous_year_improvement",
        "first_floor_area",
        "second_floor_area",
        "basement_finish_area",
        "ownership_type",
    ):
        if row.get(field) not in (None, ""):
            normalized_field = "pid" if field == "PID" else field
            value = row[field]
            if normalized_field in {
                "assessment_year",
                "year_built",
                "finished_sqft",
                "lot_area_sqft",
                "bedrooms",
                "bathrooms",
                "previous_year_improvement",
            }:
                numeric = _number(value)
                value = int(numeric) if isinstance(numeric, float) and numeric.is_integer() else numeric
            fields[normalized_field] = value
    dimension_value = row.get("lot_dimensions")
    if dimension_value in (None, ""):
        # JSON/API responses may already call this field ``lot_area_sqft``;
        # only reinterpret it when the value is visibly a dimension pair.
        candidate_dimension = row.get("lot_area_sqft")
    else:
        candidate_dimension = dimension_value
    dimension_match = _LOT_DIMENSIONS_RE.match(_text(candidate_dimension))
    if dimension_match:
        fields.pop("lot_area_sqft", None)
        fields["frontage_ft"] = _number(dimension_match.group("frontage"))
        fields["depth_ft"] = _number(dimension_match.group("depth"))
    if row.get("secondary_civic_addresses") or row.get("addresses"):
        addresses = row.get("secondary_civic_addresses") or row.get("addresses")
        fields["secondary_civic_addresses"] = list(addresses) if isinstance(addresses, list) else [str(addresses)]
    pids = _candidate_pids(row)
    if pids:
        fields["pids"] = sorted(pids)
        fields["number_of_pids"] = len(pids)
    if len(pids) == 1 and fields.get("secondary_civic_addresses"):
        fields["single_title_multi_address"] = True
    if dates:
        fields["previous_sale_dates"] = dates
        fields["previous_sale_prices"] = prices
        fields["sold_history_scope"] = "last_three_full_calendar_years"
    if row.get("assessment_history"):
        fields["assessment_history"] = row["assessment_history"]
    else:
        history: list[dict[str, Any]] = []
        current_year = fields.get("assessment_year")
        if current_year and fields.get("assessment_total") is not None:
            history.append({
                "assessment_year": int(current_year),
                "total": fields.get("assessment_total"),
                "land": fields.get("assessment_land"),
                "improvement": fields.get("assessment_improvement"),
            })
        if current_year and fields.get("previous_year_total") is not None:
            history.append({
                "assessment_year": int(current_year) - 1,
                "total": fields.get("previous_year_total"),
                "land": fields.get("previous_year_land"),
                "improvement": fields.get("previous_year_improvement"),
            })
        if history:
            fields["assessment_history"] = history
    fields = {key: value for key, value in fields.items() if value not in (None, "", [], {})}
    fields["source_url"] = source_url
    return fields


def _labelled_html_values(soup: BeautifulSoup) -> dict[str, Any]:
    values: dict[str, Any] = {}
    label_map = {
        "pid": "pid",
        "parcel identifier": "pid",
        "address": "address",
        "civic address": "address",
        "municipality": "municipality",
        "legal description": "legal_description",
        "assessment year": "assessment_year",
        "total assessed value": "assessment_total",
        "total assessment": "assessment_total",
        "land value": "assessment_land",
        "land assessment": "assessment_land",
        "improvement value": "assessment_improvement",
        "improvement assessment": "assessment_improvement",
        "previous total": "previous_year_total",
        "previous land": "previous_year_land",
        "year built": "year_built",
        "lot size": "lot_dimensions",
    }
    for row in soup.select("tr"):
        cells = [" ".join(cell.get_text(" ").split()) for cell in row.select("th,td")]
        if len(cells) >= 2:
            key = label_map.get(cells[0].lower().rstrip(":"))
            if key:
                values[key] = cells[1]
    for definition in soup.select("dt"):
        label = definition.get_text(" ").strip().lower().rstrip(":")
        key = label_map.get(label)
        value = definition.find_next_sibling("dd")
        if key and value:
            values[key] = value.get_text(" ")
    text = " ".join(soup.get_text(" ").split())
    for label, key in label_map.items():
        if key in values:
            continue
        match = re.search(rf"{re.escape(label)}\s*:\s*([^|;]+)", text, re.IGNORECASE)
        if match:
            values[key] = match.group(1).strip()

    id_map = {
        "lblTotalAssessedValue": "assessment_total",
        "lblTotalAssessedLand": "assessment_land",
        "lblTotalAssessedBuilding": "assessment_improvement",
        "lblPreviousAssessedValue": "previous_year_total",
        "lblPreviousAssessedLand": "previous_year_land",
        "lblPreviousAssessedBuilding": "previous_year_improvement",
        "lblYearBuilt": "year_built",
        "lblBedrooms": "bedrooms",
        "lblBathRooms": "bathrooms",
        "lblLandSize": "lot_dimensions",
        "lblFirstFloorArea": "first_floor_area",
        "lblSecondFloorArea": "second_floor_area",
        "lblBasementFinishArea": "basement_finish_area",
    }
    for element_id, field in id_map.items():
        node = soup.select_one(f"#{element_id}")
        if node:
            values[field] = node.get_text(" ")

    title = soup.select_one("#mainaddresstitle")
    if title:
        values["address"] = title.get_text(" ")

    assessment_label = soup.select_one("#lblLastAssessmentDate")
    if assessment_label:
        year_match = re.search(r"\b(20\d{2})\b", assessment_label.get_text(" "))
        if year_match:
            values["assessment_year"] = year_match.group(1)

    legal_node = soup.select_one("#lblLegalDescription")
    if legal_node:
        legal_parts = [" ".join(item.get_text(" ").split()) for item in legal_node.select("p")]
        legal_text = " ".join(part for part in legal_parts if part)
        if legal_text:
            values["legal_description"] = legal_parts[0] if legal_parts else legal_text
            pid_match = re.search(r"\bPID\s*:\s*([0-9-]+)", legal_text, re.IGNORECASE)
            if pid_match:
                values["pid"] = pid_match.group(1)

    sales: list[dict[str, Any]] = []
    for row in soup.select("tr.sale, tr[data-sale-date], .sale-history tr, #lblSalesHistory tr"):
        date_value = row.get("data-sale-date") or next(
            (_text_node.get_text(" ") for _text_node in row.select(".date, [data-sale-date], td")),
            None,
        )
        cells = [" ".join(cell.get_text(" ").split()) for cell in row.select("td")]
        price_value = row.get("data-sale-price") or (cells[1] if len(cells) > 1 else None)
        if _date(date_value) and _number(price_value) is not None:
            sales.append({"date": date_value, "price": price_value})
    if sales:
        values["sales_history"] = sales
    return values


def parse_bc_assessment_payload(
    content: bytes,
    source_url: str,
    observed_at: datetime,
) -> list[dict[str, Any]]:
    """Parse a saved BC Assessment response into conservative property rows."""

    del observed_at
    try:
        payload = json.loads(content.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        soup = BeautifulSoup(content, "html.parser")
        raw_rows = [_labelled_html_values(soup)]
        pid_node = soup.select_one("[data-pid], [data-PID]")
        if pid_node and pid_node.get("data-pid"):
            raw_rows[0]["pid"] = pid_node.get("data-pid")
    else:
        raw_rows = _rows_from_payload(payload)
    return [_normalize_bca_row(row, source_url) for row in raw_rows if row]


def _parse_bca_search_candidates(content: bytes) -> list[dict[str, Any]]:
    """Parse the public portal's address/PID search response.

    The address autocomplete endpoint returns ``label``/``value`` objects,
    while the PID endpoint returns a DataTables ``aaData`` row whose final
    column is the opaque OID used by ``PropertyInformation``.
    """

    try:
        payload = json.loads(content.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        text = content.decode("utf-8", errors="ignore").strip()
        if text.lower().startswith("ok-"):
            return [{"oid": text[3:].strip()}]
        return []

    if isinstance(payload, Mapping):
        values = payload.get("aaData")
        if isinstance(values, list):
            candidates: list[dict[str, Any]] = []
            for item in values:
                if isinstance(item, list):
                    candidate: dict[str, Any] = {}
                    if len(item) > 1 and item[1] not in (None, ""):
                        candidate["pid"] = item[1]
                    if len(item) > 2 and item[2] not in (None, ""):
                        candidate["address"] = item[2]
                    if len(item) > 3 and item[3] not in (None, ""):
                        candidate["roll_number"] = item[3]
                    if len(item) > 4 and item[4] not in (None, ""):
                        candidate["oid"] = item[4]
                    if candidate:
                        candidates.append(candidate)
                elif isinstance(item, Mapping):
                    candidates.append(dict(item))
            return candidates
        payload = payload.get("results") or payload.get("items") or payload

    if isinstance(payload, list):
        candidates = []
        for item in payload:
            if not isinstance(item, Mapping):
                continue
            candidate = dict(item)
            if candidate.get("value") not in (None, ""):
                candidate["oid"] = candidate["value"]
            if candidate.get("label") not in (None, ""):
                candidate["address"] = candidate["label"]
            candidates.append(candidate)
        return candidates
    return []


@dataclass(frozen=True)
class ZoningSourceRoute:
    name: str
    municipality: str
    source_url: str


_DEFAULT_ZONING_ROUTES = {
    "city of vancouver": ZoningSourceRoute(
        name="municipal-zoning-vancouver",
        municipality="City of Vancouver",
        source_url="https://maps.vancouver.ca/portal/home/",
    ),
    "city of burnaby": ZoningSourceRoute(
        name="municipal-zoning-burnaby",
        municipality="City of Burnaby",
        source_url="https://www.burnaby.ca/services-and-payments/permits-and-construction/development-planning",
    ),
    "city of north vancouver": ZoningSourceRoute(
        name="municipal-zoning-cnv",
        municipality="City of North Vancouver",
        source_url="https://www.cnv.org/",
    ),
    "district of north vancouver": ZoningSourceRoute(
        name="municipal-zoning-dnv",
        municipality="District of North Vancouver",
        source_url="https://geoweb.dnv.org/properties/",
    ),
    "district of west vancouver": ZoningSourceRoute(
        name="municipal-zoning-wv",
        municipality="District of West Vancouver",
        source_url="https://westvancouver.ca/business-development/building-development/zoning-map",
    ),
}


def route_zoning_source(
    municipality: str,
    routes: Mapping[str, Mapping[str, Any]] | None = None,
) -> ZoningSourceRoute:
    key = " ".join(str(municipality or "").strip().lower().split())
    if routes and key in {" ".join(str(item).lower().split()) for item in routes}:
        raw_key = next(item for item in routes if " ".join(str(item).lower().split()) == key)
        raw = dict(routes[raw_key])
        return ZoningSourceRoute(
            name=str(raw.get("name") or f"municipal-zoning-{key.replace(' ', '-') }"),
            municipality=str(raw.get("municipality") or municipality),
            source_url=str(raw.get("source_url") or raw.get("url") or ""),
        )
    route = _DEFAULT_ZONING_ROUTES.get(key)
    if route is None:
        raise ValueError(f"no official zoning route configured for municipality: {municipality}")
    return route


_ZONING_RULE_FIELDS = {
    "permitted_uses",
    "conditional_uses",
    "minimum_site_area",
    "minimum_frontage",
    "maximum_fsr",
    "maximum_units",
    "maximum_height",
    "setbacks",
    "lot_coverage",
    "parking",
    "tree_constraints",
    "character_rules",
    "servicing_constraints",
}


def _same_zoning(left: Any, right: Any) -> bool:
    return _text(left).casefold() == _text(right).casefold() and bool(_text(left))


def _normalize_zoning_row(raw: Mapping[str, Any], source_url: str) -> dict[str, Any]:
    raw = dict(raw)
    if raw.get("zoning") in (None, "") and raw.get("zonename") not in (None, ""):
        raw["zoning"] = raw["zonename"]
    if raw.get("current_zoning") in (None, "") and raw.get("zoning") not in (None, ""):
        raw["current_zoning"] = raw["zoning"]
    fields: dict[str, Any] = {}
    for field in (
        "pid",
        "PID",
        "address",
        "civic_address",
        "municipality",
        "legal_description",
        "zoning",
        "bylaw_version",
        "toa",
        "toa_status",
        "toa_tier",
        "current_zoning",
        "rezoning_requirement",
        "assembly_requirement",
        "heritage_status",
        "character_status",
    ):
        if raw.get(field) not in (None, ""):
            fields["pid" if field == "PID" else field] = raw[field]
    rules = raw.get("rules")
    if isinstance(rules, Mapping) and _same_zoning(rules.get("zoning"), raw.get("zoning")):
        for field in _ZONING_RULE_FIELDS:
            value = rules.get(field)
            if value in (None, "", [], {}):
                continue
            if field in {"minimum_site_area", "minimum_frontage", "maximum_fsr", "maximum_height", "lot_coverage"}:
                value = _number(value)
            elif field == "maximum_units":
                numeric = _number(value)
                value = int(numeric) if numeric is not None else value
            fields[field] = value
        fields["bylaw_version"] = raw.get("bylaw_version") or rules.get("bylaw_version")
    pids = _candidate_pids(raw)
    if pids:
        fields["pids"] = sorted(pids)
        fields["number_of_pids"] = len(pids)
    fields["source_url"] = source_url
    return {key: value for key, value in fields.items() if value not in (None, "", [], {})}


def parse_zoning_payload(
    content: bytes,
    source_url: str,
    observed_at: datetime,
) -> list[dict[str, Any]]:
    del observed_at
    try:
        payload = json.loads(content.decode("utf-8-sig"))
        raw_rows = _rows_from_payload(payload)
    except (UnicodeDecodeError, json.JSONDecodeError):
        soup = BeautifulSoup(content, "html.parser")
        raw_rows = [_labelled_html_values(soup)]
    return [_normalize_zoning_row(row, source_url) for row in raw_rows if row]


class BCAssessmentAdapter:
    """Read-only, bounded adapter for BC Assessment property responses."""

    def __init__(
        self,
        *,
        name: str,
        source_registry_id: str,
        source_url: str,
        raw_snapshot_dir: str | Path,
        config: Mapping[str, Any] | None = None,
        transport: Callable[[Mapping[str, Any]], OfficialHttpResponse] | None = None,
    ) -> None:
        self.name = name
        self.source_registry_id = source_registry_id
        self.source_url = source_url
        self.config = dict(config or {})
        self.source_type = SourceType.BC_ASSESSMENT
        self.http = OfficialHttpTransport(
            name=name,
            source_registry_id=source_registry_id,
            source_type=SourceType.BC_ASSESSMENT,
            source_url=source_url,
            raw_snapshot_dir=raw_snapshot_dir,
            config=self.config,
            transport=transport,
        )

    def _request_for(
        self,
        record: PropertyRecord,
        *,
        address: str | None = None,
        query_variant: str = "original",
        phase: str = "search",
    ) -> dict[str, Any]:
        lookup_address = address or record.address
        if str(self.config.get("lookup_mode") or "").lower() == "public_portal":
            if record.pid:
                template = str(
                    self.config.get("pid_search_url")
                    or f"{self.source_url.rstrip('/')}/Property/Search/GetByPid/{{pid}}"
                )
                identifier = re.sub(r"[^0-9]", "", str(record.pid))
            else:
                template = str(
                    self.config.get("address_search_url")
                    or f"{self.source_url.rstrip('/')}/Property/Search/GetByAddress?addr={{address}}"
                )
                identifier = lookup_address
            request = {
                "url": template.format(
                    pid=quote(identifier, safe=""),
                    address=quote(lookup_address, safe=""),
                ),
                "method": "GET",
                "property_id": record.property_id,
                "phase": phase,
                "query_variant": query_variant,
            }
            return request
        template = str(self.config.get("search_url") or self.source_url)
        url = template.format(
            pid=quote(str(record.pid or ""), safe=""),
            address=quote(lookup_address, safe=""),
        )
        request: dict[str, Any] = {
            "url": url,
            "method": "GET",
            "property_id": record.property_id,
            "phase": phase,
            "query_variant": query_variant,
        }
        if not record.pid and self.config.get("address_param"):
            request["params"] = {str(self.config["address_param"]): lookup_address}
        return request

    @staticmethod
    def _response_for(
        fetched: OfficialFetchResult,
        request: Mapping[str, Any],
    ) -> OfficialHttpResponse | None:
        return next(
            (
                item
                for item in fetched.responses
                if item.request
                and item.request.get("property_id") == request.get("property_id")
                and item.request.get("phase") == request.get("phase")
            ),
            None,
        )

    def _public_portal_batch(
        self,
        records: list[PropertyRecord],
        observed_at: datetime,
    ) -> OfficialEnrichmentBatch:
        rows_by_property: dict[str, OfficialLookupRow] = {}
        detail_requests: list[dict[str, Any]] = []
        search_matches: dict[
            str,
            tuple[OfficialIdentityMatch, dict[str, Any], dict[str, Any]],
        ] = {}
        search_fetches: list[OfficialFetchResult] = []

        pending: list[tuple[PropertyRecord, dict[str, Any]]] = [
            (record, self._request_for(record)) for record in records
        ]
        variant_positions: dict[str, int] = {record.property_id: 1 for record in records}
        while pending:
            requests = [request for _, request in pending]
            fetched = self.http.fetch_all(requests, observed_at=observed_at)
            search_fetches.append(fetched)
            next_pending: list[tuple[PropertyRecord, dict[str, Any]]] = []
            for record, request in pending:
                response = self._response_for(fetched, request)
                snapshot = _snapshot_for_request(fetched, request, response)
                source_url = response.url if response else str(request["url"])
                if response is None:
                    variants = _address_query_variants(record.address)
                    position = variant_positions.get(record.property_id, 1)
                    if not record.pid and position < len(variants):
                        query, variant_name = variants[position]
                        variant_positions[record.property_id] = position + 1
                        next_pending.append((
                            record,
                            self._request_for(
                                record,
                                address=query,
                                query_variant=variant_name,
                            ),
                        ))
                        continue
                    rows_by_property[record.property_id] = OfficialLookupRow(
                        property_id=record.property_id,
                        status=(
                            OfficialQueryStatus.BLOCKED
                            if fetched.status is SourceRunStatus.BLOCKED_POLICY
                            else OfficialQueryStatus.FAILED
                        ),
                        source_url=source_url,
                        source_version=self.config.get("source_version"),
                        raw_snapshot_path=snapshot.raw_path if snapshot else None,
                        warnings=list(fetched.errors),
                    )
                    continue

                candidates = _parse_bca_search_candidates(response.content)
                for candidate in candidates:
                    inferred_municipality = _municipality_from_address(
                        candidate.get("address") or candidate.get("civic_address")
                    )
                    if inferred_municipality:
                        candidate["municipality"] = inferred_municipality
                        candidate["municipality_inferred"] = True
                    else:
                        candidate.setdefault("municipality", record.municipality)
                        candidate.setdefault("municipality_inferred", False)
                identity = match_official_identity(record, candidates)

                if identity.status is OfficialQueryStatus.NO_MATCH:
                    variants = _address_query_variants(record.address)
                    position = variant_positions.get(record.property_id, 1)
                    if position < len(variants):
                        query, variant_name = variants[position]
                        variant_positions[record.property_id] = position + 1
                        next_pending.append((
                            record,
                            self._request_for(
                                record,
                                address=query,
                                query_variant=variant_name,
                            ),
                        ))
                        continue

                if identity.status is not OfficialQueryStatus.SUCCESS or identity.candidate is None:
                    warnings = _identity_warnings(record, candidates, identity)
                    if len(_address_query_variants(record.address)) > 1:
                        warnings.append("BCA_QUERY_VARIANTS_EXHAUSTED")
                    rows_by_property[record.property_id] = OfficialLookupRow(
                        property_id=record.property_id,
                        status=identity.status,
                        source_url=source_url,
                        retrieved_at=_aware(datetime.now(timezone.utc)),
                        source_version=self.config.get("source_version"),
                        raw_snapshot_path=snapshot.raw_path if snapshot else None,
                        warnings=warnings,
                    )
                    continue

                oid = identity.candidate.get("oid") or identity.candidate.get("value")
                if oid in (None, ""):
                    rows_by_property[record.property_id] = OfficialLookupRow(
                        property_id=record.property_id,
                        status=OfficialQueryStatus.FAILED,
                        source_url=source_url,
                        retrieved_at=_aware(datetime.now(timezone.utc)),
                        source_version=self.config.get("source_version"),
                        raw_snapshot_path=snapshot.raw_path if snapshot else None,
                        warnings=["BCA_SEARCH_RESULT_MISSING_OID"],
                    )
                    continue

                detail_template = str(
                    self.config.get("property_information_url")
                    or f"{self.source_url.rstrip('/')}/Property/PropertyInformation"
                )
                detail_url = detail_template.format(
                    oid=quote(str(oid), safe=""),
                    pid=quote(str(record.pid or ""), safe=""),
                    address=quote(record.address, safe=""),
                )
                detail_method = str(self.config.get("property_information_method") or "POST").upper()
                detail_request: dict[str, Any] = {
                    "url": detail_url,
                    "method": detail_method,
                    "property_id": record.property_id,
                    "phase": "detail",
                    "lookup_token": str(oid),
                }
                if detail_method == "GET":
                    detail_request["params"] = {"oid": str(oid)}
                else:
                    detail_request["data"] = {
                        str(self.config.get("property_information_oid_param") or "oid"): str(oid),
                    }
                detail_requests.append(detail_request)
                search_matches[record.property_id] = (
                    identity,
                    dict(identity.candidate),
                    request,
                )
            pending = next_pending

        search_fetched = search_fetches[0] if search_fetches else None

        detail_fetched: OfficialFetchResult | None = None
        if detail_requests:
            detail_fetched = self.http.fetch_all(detail_requests, observed_at=observed_at)
            for record in records:
                match = search_matches.get(record.property_id)
                if match is None:
                    continue
                identity, search_candidate, request = match
                detail_request = next(
                    item for item in detail_requests if item["property_id"] == record.property_id
                )
                response = self._response_for(detail_fetched, detail_request)
                snapshot = _snapshot_for_request(detail_fetched, detail_request, response)
                source_url = response.url if response else str(detail_request["url"])
                query_variant_warning = (
                    [f"BCA_QUERY_VARIANT={request.get('query_variant')}"]
                    if request.get("query_variant") not in (None, "original")
                    else []
                )
                if response is None:
                    rows_by_property[record.property_id] = OfficialLookupRow(
                        property_id=record.property_id,
                        status=(
                            OfficialQueryStatus.BLOCKED
                            if detail_fetched.status is SourceRunStatus.BLOCKED_POLICY
                            else OfficialQueryStatus.FAILED
                        ),
                        source_url=source_url,
                        source_version=self.config.get("source_version"),
                        raw_snapshot_path=snapshot.raw_path if snapshot else None,
                        warnings=list(detail_fetched.errors) + query_variant_warning,
                    )
                    continue
                candidates = parse_bc_assessment_payload(response.content, response.url, observed_at)
                identity = match_official_identity(record, candidates)
                if identity.status is OfficialQueryStatus.SUCCESS and identity.candidate is not None:
                    fields = dict(identity.candidate)
                    fields["source_url"] = source_url
                    rows_by_property[record.property_id] = OfficialLookupRow(
                        property_id=record.property_id,
                        status=OfficialQueryStatus.SUCCESS,
                        fields=fields,
                        source_url=source_url,
                        retrieved_at=_aware(datetime.now(timezone.utc)),
                        source_version=self.config.get("source_version"),
                        match_method=identity.match_method,
                        query_scope=fields.get("sold_history_scope") or "last_three_full_calendar_years",
                        raw_snapshot_path=snapshot.raw_path if snapshot else None,
                        warnings=query_variant_warning,
                    )
                else:
                    rows_by_property[record.property_id] = OfficialLookupRow(
                        property_id=record.property_id,
                        status=identity.status,
                        source_url=source_url,
                        retrieved_at=_aware(datetime.now(timezone.utc)),
                        source_version=self.config.get("source_version"),
                        raw_snapshot_path=snapshot.raw_path if snapshot else None,
                        warnings=_identity_warnings(record, candidates, identity) + query_variant_warning,
                    )

        rows = [
            rows_by_property.get(
                record.property_id,
                OfficialLookupRow(
                    property_id=record.property_id,
                    status=OfficialQueryStatus.FAILED,
                    source_url=str(self.source_url),
                    source_version=self.config.get("source_version"),
                    warnings=["BCA_PUBLIC_PORTAL_NO_RESULT"],
                ),
            )
            for record in records
        ]
        all_snapshots = [snapshot for fetched in search_fetches for snapshot in fetched.snapshots]
        errors = [error for fetched in search_fetches for error in fetched.errors]
        warnings = [warning for fetched in search_fetches for warning in fetched.warnings]
        search_responses = sum(len(fetched.responses) for fetched in search_fetches)
        if search_responses and errors:
            final_status = SourceRunStatus.PARTIAL
        elif search_responses:
            final_status = SourceRunStatus.SUCCESS
        elif any(fetched.status is SourceRunStatus.BLOCKED_POLICY for fetched in search_fetches):
            final_status = SourceRunStatus.BLOCKED_POLICY
        else:
            final_status = SourceRunStatus.FAILED
        if detail_fetched is not None:
            all_snapshots.extend(detail_fetched.snapshots)
            errors.extend(detail_fetched.errors)
            warnings.extend(detail_fetched.warnings)
            if detail_fetched.status in {SourceRunStatus.BLOCKED_POLICY, SourceRunStatus.FAILED}:
                final_status = (
                    SourceRunStatus.PARTIAL
                    if search_responses
                    else detail_fetched.status
                )
            elif final_status is not SourceRunStatus.SUCCESS:
                final_status = SourceRunStatus.PARTIAL
            else:
                final_status = detail_fetched.status
        source_run = (search_fetched or detail_fetched).source_run.model_copy(update={
            "status": final_status,
            "finished_at": datetime.now(timezone.utc),
            "records": [
                {
                    "property_id": row.property_id,
                    "status": row.status.value,
                    "fields": row.fields,
                    "source_url": row.source_url,
                    "match_method": row.match_method,
                    "query_scope": row.query_scope,
                    "raw_snapshot_path": row.raw_snapshot_path,
                    "warnings": row.warnings,
                }
                for row in rows
            ],
            "snapshots": all_snapshots,
            "errors": errors,
            "warnings": warnings,
        })
        return OfficialEnrichmentBatch(source_run=source_run, rows=rows)

    def collect_for_records(
        self,
        records: list[PropertyRecord],
        observed_at: datetime,
    ) -> OfficialEnrichmentBatch:
        if str(self.config.get("lookup_mode") or "").lower() == "public_portal":
            return self._public_portal_batch(records, observed_at)
        requests = [self._request_for(record) for record in records]
        fetched = self.http.fetch_all(requests, observed_at=observed_at)
        rows: list[OfficialLookupRow] = []
        for record, request in zip(records, requests):
            response = next(
                (item for item in fetched.responses if item.request and item.request.get("property_id") == record.property_id),
                None,
            )
            snapshot = _snapshot_for_request(fetched, request, response)
            source_url = response.url if response else request["url"]
            if response is None:
                status = OfficialQueryStatus.BLOCKED if fetched.status is SourceRunStatus.BLOCKED_POLICY else OfficialQueryStatus.FAILED
                warnings = list(fetched.errors)
                rows.append(
                    OfficialLookupRow(
                        property_id=record.property_id,
                        status=status,
                        source_url=source_url,
                        source_version=self.config.get("source_version"),
                        raw_snapshot_path=snapshot.raw_path if snapshot else None,
                        warnings=warnings,
                    )
                )
                continue
            try:
                candidates = parse_bc_assessment_payload(response.content, response.url, observed_at)
            except (ValueError, TypeError, UnicodeError) as exc:
                rows.append(
                    OfficialLookupRow(
                        property_id=record.property_id,
                        status=OfficialQueryStatus.FAILED,
                        source_url=source_url,
                        raw_snapshot_path=snapshot.raw_path if snapshot else None,
                        source_version=self.config.get("source_version"),
                        warnings=[f"{type(exc).__name__}: {exc}"],
                    )
                )
                continue
            identity = match_official_identity(record, candidates)
            if identity.status is OfficialQueryStatus.SUCCESS and identity.candidate is not None:
                fields = dict(identity.candidate)
                fields["source_url"] = source_url
                rows.append(
                    OfficialLookupRow(
                        property_id=record.property_id,
                        status=OfficialQueryStatus.SUCCESS,
                        fields=fields,
                        source_url=source_url,
                        retrieved_at=_aware(datetime.now(timezone.utc)),
                        source_version=self.config.get("source_version"),
                        match_method=identity.match_method,
                        query_scope=fields.get("sold_history_scope"),
                        raw_snapshot_path=snapshot.raw_path if snapshot else None,
                    )
                )
            else:
                rows.append(
                    OfficialLookupRow(
                        property_id=record.property_id,
                        status=identity.status,
                        source_url=source_url,
                        retrieved_at=_aware(datetime.now(timezone.utc)),
                        source_version=self.config.get("source_version"),
                        match_method=identity.match_method,
                        raw_snapshot_path=snapshot.raw_path if snapshot else None,
                        warnings=_identity_warnings(record, candidates, identity),
                    )
                )
        source_run = fetched.source_run.model_copy(update={
            "records": [
                {
                    "property_id": row.property_id,
                    "status": row.status.value,
                    "fields": row.fields,
                    "source_url": row.source_url,
                    "match_method": row.match_method,
                    "query_scope": row.query_scope,
                    "raw_snapshot_path": row.raw_snapshot_path,
                    "warnings": row.warnings,
                }
                for row in rows
            ]
        })
        return OfficialEnrichmentBatch(source_run=source_run, rows=rows)


class MunicipalZoningAdapter:
    """Read-only adapter for official municipal parcel and bylaw responses."""

    def __init__(
        self,
        *,
        name: str,
        source_registry_id: str,
        source_url: str,
        raw_snapshot_dir: str | Path,
        config: Mapping[str, Any] | None = None,
        transport: Callable[[Mapping[str, Any]], OfficialHttpResponse] | None = None,
    ) -> None:
        self.name = name
        self.source_registry_id = source_registry_id
        self.source_url = source_url
        self.config = dict(config or {})
        self.source_type = SourceType.OFFICIAL_GOV
        self.http = OfficialHttpTransport(
            name=name,
            source_registry_id=source_registry_id,
            source_type=SourceType.OFFICIAL_GOV,
            source_url=source_url,
            raw_snapshot_dir=raw_snapshot_dir,
            config=self.config,
            transport=transport,
        )

    def _request_for(self, record: PropertyRecord) -> dict[str, Any]:
        route = route_zoning_source(
            str(self.config.get("municipality") or record.municipality),
            self.config.get("routes"),
        )
        template = str(self.config.get("search_url") or route.source_url or self.source_url)
        return {
            "url": template.format(
                pid=quote(str(record.pid or ""), safe=""),
                address=quote(record.address, safe=""),
            ),
            "method": "GET",
            "property_id": record.property_id,
        }

    def _uses_arcgis_parcel_lookup(self) -> bool:
        return str(self.config.get("lookup_mode") or "").lower() in {
            "arcgis_address_spatial",
            "arcgis_parcel_spatial",
        }

    def _arcgis_parcel_request(self, record: PropertyRecord) -> dict[str, Any]:
        address_url = str(self.config.get("parcel_search_url") or "")
        if not address_url:
            raise ValueError("arcgis zoning lookup requires parcel_search_url")
        variants = _address_query_variants(record.address)
        values = list(dict.fromkeys(value.upper() for value, _ in variants if value.strip()))
        if not values:
            values = [normalize_address(record.address, None).upper()]
        escaped_values = [value.replace("'", "''") for value in values]
        # The exact variants handle normal portal spelling differences.  The
        # constrained prefix catches municipal abbreviations not present in
        # the source spelling (e.g. AVE -> AV) without searching by a loose
        # house number or street fragment.  The adapter still requires one
        # returned parcel with geometry below.
        prefix_tokens = normalize_address(record.address, None).upper().split()
        prefix_index = next(
            (
                index
                for index, token in enumerate(prefix_tokens[1:], start=1)
                if token.casefold() in _ADDRESS_STREET_TYPE_VARIANTS
            ),
            None,
        )
        if prefix_index is not None:
            prefix_tokens[prefix_index] = _ADDRESS_STREET_TYPE_VARIANTS[
                prefix_tokens[prefix_index].casefold()
            ][1 if prefix_tokens[prefix_index].casefold() == "ave" else 0]
        prefix = " ".join(prefix_tokens).replace("'", "''")
        where = (
            f"address IN ({', '.join(repr(value) for value in escaped_values)})"
            f" OR address LIKE {repr(prefix + '%')}"
        )
        return {
            "url": address_url,
            "method": "GET",
            "params": {
                "where": where,
                "outFields": "*",
                "returnGeometry": "true",
                "f": "json",
            },
            "property_id": record.property_id,
            "query_step": "address_parcel",
            "address_query_mode": "EXACT_VARIANTS_AND_CONSTRAINED_PREFIX",
            "address_variants": values,
        }

    def _arcgis_coordinate_parcel_request(self, record: PropertyRecord) -> dict[str, Any] | None:
        latitude = _number(getattr(record, "latitude", None))
        longitude = _number(getattr(record, "longitude", None))
        if latitude is None or longitude is None:
            return None
        address_url = str(self.config.get("parcel_search_url") or "")
        if not address_url:
            return None
        return {
            "url": address_url,
            "method": "GET",
            "params": {
                "geometry": f"{longitude},{latitude}",
                "geometryType": "esriGeometryPoint",
                "inSR": "4326",
                "outSR": str(self.config.get("parcel_spatial_reference") or "3857"),
                "spatialRel": "esriSpatialRelIntersects",
                "outFields": "*",
                "returnGeometry": "true",
                "f": "json",
            },
            "property_id": record.property_id,
            "query_step": "address_parcel_coordinate_fallback",
            "address_query_mode": "LISTING_COORDINATE_POINT",
        }

    @staticmethod
    def _parcel_probe_point(geometry: Mapping[str, Any]) -> tuple[float, float] | None:
        rings = geometry.get("rings") if isinstance(geometry, Mapping) else None
        if not isinstance(rings, list):
            return None
        points = [
            point
            for ring in rings
            if isinstance(ring, list)
            for point in ring
            if isinstance(point, (list, tuple)) and len(point) >= 2
        ]
        if not points:
            return None
        # Vancouver address parcels are small, regular polygons.  The centre
        # of their bounding box is a stable interior probe and avoids the
        # neighbouring zoning polygon that a full-parcel INTERSECTS query can
        # touch at a boundary.
        xs = [float(point[0]) for point in points]
        ys = [float(point[1]) for point in points]
        return ((min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0)

    def _arcgis_zoning_request(
        self,
        record: PropertyRecord,
        geometry: Mapping[str, Any],
    ) -> dict[str, Any]:
        zoning_url = str(self.config.get("zoning_query_url") or "")
        if not zoning_url:
            raise ValueError("arcgis zoning lookup requires zoning_query_url")
        probe = self._parcel_probe_point(geometry)
        if probe is None:
            raise ValueError("arcgis zoning lookup requires a parcel polygon probe point")
        x, y = probe
        return {
            "url": zoning_url,
            "method": "GET",
            "params": {
                "geometry": json.dumps(
                    {
                        "x": x,
                        "y": y,
                        "spatialReference": {
                            "wkid": int(self.config.get("parcel_spatial_reference") or 3857),
                        },
                    },
                    separators=(",", ":"),
                ),
                "geometryType": "esriGeometryPoint",
                "inSR": str(self.config.get("parcel_spatial_reference") or "3857"),
                "spatialRel": "esriSpatialRelIntersects",
                "outFields": "*",
                "returnGeometry": "false",
                "f": "json",
            },
            "property_id": record.property_id,
            "query_step": "zoning_polygon",
        }

    @staticmethod
    def _features(response: OfficialHttpResponse) -> list[dict[str, Any]]:
        try:
            payload = json.loads(response.content.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return []
        return _rows_from_payload(payload)

    def _collect_arcgis_for_records(
        self,
        records: list[PropertyRecord],
        observed_at: datetime,
    ) -> OfficialEnrichmentBatch:
        parcel_requests = [self._arcgis_parcel_request(record) for record in records]
        parcel_fetch = self.http.fetch_all(parcel_requests, observed_at=observed_at)
        coordinate_requests: list[dict[str, Any]] = []
        zoning_requests: list[dict[str, Any]] = []
        parcel_fetch_coordinate = None
        rows: list[OfficialLookupRow] = []
        for record in records:
            request = next(item for item in parcel_requests if item["property_id"] == record.property_id)
            response = next(
                (item for item in parcel_fetch.responses if item.request and item.request.get("property_id") == record.property_id),
                None,
            )
            snapshot = _snapshot_for_request(parcel_fetch, request, response)
            candidates = self._features(response) if response is not None else []
            if len(candidates) != 1 or not candidates[0].get("geometry"):
                coordinate_request = self._arcgis_coordinate_parcel_request(record)
                if coordinate_request is not None:
                    coordinate_requests.append(coordinate_request)
                    continue
            if response is None:
                rows.append(OfficialLookupRow(
                    property_id=record.property_id,
                    status=(
                        OfficialQueryStatus.BLOCKED
                        if parcel_fetch.status is SourceRunStatus.BLOCKED_POLICY
                        else OfficialQueryStatus.FAILED
                    ),
                    source_url=request["url"],
                    source_version=self.config.get("source_version"),
                    raw_snapshot_path=snapshot.raw_path if snapshot else None,
                    warnings=list(parcel_fetch.errors),
                ))
                continue
            if len(candidates) != 1 or not candidates[0].get("geometry"):
                rows.append(OfficialLookupRow(
                    property_id=record.property_id,
                    status=(OfficialQueryStatus.NO_MATCH if not candidates else OfficialQueryStatus.AMBIGUOUS),
                    source_url=response.url,
                    retrieved_at=_aware(datetime.now(timezone.utc)),
                    source_version=self.config.get("source_version"),
                    raw_snapshot_path=snapshot.raw_path if snapshot else None,
                    warnings=["address parcel geometry was not uniquely resolved"],
                ))
                continue
            zoning_requests.append(self._arcgis_zoning_request(record, candidates[0]["geometry"]))

        if coordinate_requests:
            parcel_fetch_coordinate = self.http.fetch_all(coordinate_requests, observed_at=observed_at)
            for record in records:
                request = next((item for item in coordinate_requests if item["property_id"] == record.property_id), None)
                if request is None:
                    continue
                response = next(
                    (item for item in parcel_fetch_coordinate.responses if item.request and item.request.get("property_id") == record.property_id),
                    None,
                )
                candidates = self._features(response) if response is not None else []
                if len(candidates) != 1 or not candidates[0].get("geometry"):
                    rows.append(OfficialLookupRow(
                        property_id=record.property_id,
                        status=(
                            OfficialQueryStatus.NO_MATCH
                            if not candidates
                            else OfficialQueryStatus.AMBIGUOUS
                        ),
                        source_url=response.url if response else request["url"],
                        retrieved_at=_aware(datetime.now(timezone.utc)),
                        source_version=self.config.get("source_version"),
                        raw_snapshot_path=_snapshot_for_request(parcel_fetch_coordinate, request, response).raw_path
                        if _snapshot_for_request(parcel_fetch_coordinate, request, response)
                        else None,
                        warnings=["address and listing-coordinate parcel lookup did not uniquely resolve"],
                    ))
                    continue
                zoning_requests.append(self._arcgis_zoning_request(record, candidates[0]["geometry"]))

        zoning_fetch = self.http.fetch_all(zoning_requests, observed_at=observed_at) if zoning_requests else None
        for record in records:
            if any(row.property_id == record.property_id for row in rows):
                continue
            request = next(item for item in zoning_requests if item["property_id"] == record.property_id)
            response = next(
                (item for item in (zoning_fetch.responses if zoning_fetch else []) if item.request and item.request.get("property_id") == record.property_id),
                None,
            )
            snapshot = _snapshot_for_request(zoning_fetch, request, response) if zoning_fetch else None
            if response is None:
                rows.append(OfficialLookupRow(
                    property_id=record.property_id,
                    status=(
                        OfficialQueryStatus.BLOCKED
                        if zoning_fetch and zoning_fetch.status is SourceRunStatus.BLOCKED_POLICY
                        else OfficialQueryStatus.FAILED
                    ),
                    source_url=request["url"],
                    source_version=self.config.get("source_version"),
                    raw_snapshot_path=snapshot.raw_path if snapshot else None,
                    warnings=list(zoning_fetch.errors if zoning_fetch else []),
                ))
                continue
            raw_candidates = self._features(response)
            candidates = []
            for candidate in raw_candidates:
                candidate.setdefault("address", record.address)
                candidate.setdefault("municipality", record.municipality)
                candidates.extend([_normalize_zoning_row(candidate, response.url)])
            identity = match_official_identity(record, candidates)
            if identity.status is OfficialQueryStatus.SUCCESS and identity.candidate is not None:
                fields = dict(identity.candidate)
                rows.append(OfficialLookupRow(
                    property_id=record.property_id,
                    status=OfficialQueryStatus.SUCCESS,
                    fields=fields,
                    source_url=response.url,
                    retrieved_at=_aware(datetime.now(timezone.utc)),
                    source_version=self.config.get("source_version"),
                    match_method="ADDRESS_VARIANT+PARCEL_CENTROID",
                    query_scope="current_bylaw",
                    raw_snapshot_path=snapshot.raw_path if snapshot else None,
                ))
            else:
                rows.append(OfficialLookupRow(
                    property_id=record.property_id,
                    status=identity.status,
                    source_url=response.url,
                    retrieved_at=_aware(datetime.now(timezone.utc)),
                    source_version=self.config.get("source_version"),
                    raw_snapshot_path=snapshot.raw_path if snapshot else None,
                    warnings=_identity_warnings(record, candidates, identity),
                ))

        fetches = [parcel_fetch]
        if parcel_fetch_coordinate is not None:
            fetches.append(parcel_fetch_coordinate)
        if zoning_fetch:
            fetches.append(zoning_fetch)
        source_run = parcel_fetch.source_run.model_copy(update={
            "status": (
                SourceRunStatus.PARTIAL
                if any(fetch.status is not SourceRunStatus.SUCCESS for fetch in fetches)
                and any(fetch.responses for fetch in fetches)
                else next(
                    (fetch.status for fetch in fetches if fetch.status is not SourceRunStatus.SUCCESS),
                    SourceRunStatus.SUCCESS,
                )
            ),
            "finished_at": max(fetch.source_run.finished_at for fetch in fetches),
            "records": [
                {
                    "property_id": row.property_id,
                    "status": row.status.value,
                    "fields": row.fields,
                    "source_url": row.source_url,
                    "match_method": row.match_method,
                    "query_scope": row.query_scope,
                    "raw_snapshot_path": row.raw_snapshot_path,
                    "warnings": row.warnings,
                }
                for row in rows
            ],
            "snapshots": [snapshot for fetch in fetches for snapshot in fetch.snapshots],
            "errors": [error for fetch in fetches for error in fetch.errors],
            "warnings": [warning for fetch in fetches for warning in fetch.warnings],
        })
        return OfficialEnrichmentBatch(source_run=source_run, rows=rows)

    def collect_for_records(
        self,
        records: list[PropertyRecord],
        observed_at: datetime,
    ) -> OfficialEnrichmentBatch:
        if self._uses_arcgis_parcel_lookup():
            return self._collect_arcgis_for_records(records, observed_at)
        requests = [self._request_for(record) for record in records]
        fetched = self.http.fetch_all(requests, observed_at=observed_at)
        rows: list[OfficialLookupRow] = []
        for record, request in zip(records, requests):
            response = next(
                (item for item in fetched.responses if item.request and item.request.get("property_id") == record.property_id),
                None,
            )
            snapshot = _snapshot_for_request(fetched, request, response)
            source_url = response.url if response else request["url"]
            if response is None:
                rows.append(
                    OfficialLookupRow(
                        property_id=record.property_id,
                        status=OfficialQueryStatus.BLOCKED if fetched.status is SourceRunStatus.BLOCKED_POLICY else OfficialQueryStatus.FAILED,
                        source_url=source_url,
                        source_version=self.config.get("source_version"),
                        raw_snapshot_path=snapshot.raw_path if snapshot else None,
                        warnings=list(fetched.errors),
                    )
                )
                continue
            try:
                candidates = parse_zoning_payload(response.content, response.url, observed_at)
            except (ValueError, TypeError, UnicodeError) as exc:
                rows.append(
                    OfficialLookupRow(
                        property_id=record.property_id,
                        status=OfficialQueryStatus.FAILED,
                        source_url=source_url,
                        source_version=self.config.get("source_version"),
                        raw_snapshot_path=snapshot.raw_path if snapshot else None,
                        warnings=[f"{type(exc).__name__}: {exc}"],
                    )
                )
                continue
            identity = match_official_identity(record, candidates)
            if identity.status is OfficialQueryStatus.SUCCESS and identity.candidate is not None:
                fields = dict(identity.candidate)
                fields["source_url"] = source_url
                rows.append(
                    OfficialLookupRow(
                        property_id=record.property_id,
                        status=OfficialQueryStatus.SUCCESS,
                        fields=fields,
                        source_url=source_url,
                        retrieved_at=_aware(datetime.now(timezone.utc)),
                        source_version=self.config.get("source_version"),
                        match_method=identity.match_method,
                        query_scope="current_bylaw",
                        raw_snapshot_path=snapshot.raw_path if snapshot else None,
                    )
                )
            else:
                rows.append(
                    OfficialLookupRow(
                        property_id=record.property_id,
                        status=identity.status,
                        source_url=source_url,
                        retrieved_at=_aware(datetime.now(timezone.utc)),
                        source_version=self.config.get("source_version"),
                        raw_snapshot_path=snapshot.raw_path if snapshot else None,
                        warnings=_identity_warnings(record, candidates, identity),
                    )
                )
        source_run = fetched.source_run.model_copy(update={
            "records": [
                {
                    "property_id": row.property_id,
                    "status": row.status.value,
                    "fields": row.fields,
                    "source_url": row.source_url,
                    "match_method": row.match_method,
                    "query_scope": row.query_scope,
                    "raw_snapshot_path": row.raw_snapshot_path,
                    "warnings": row.warnings,
                }
                for row in rows
            ]
        })
        return OfficialEnrichmentBatch(source_run=source_run, rows=rows)


def _web_mercator_to_wgs84(x: float, y: float) -> tuple[float, float]:
    longitude = x / 20037508.34 * 180.0
    latitude = math.degrees(2.0 * math.atan(math.exp(math.radians(y / 20037508.34 * 180.0))) - math.pi / 2.0)
    return latitude, longitude


def _wgs84_to_web_mercator(latitude: float, longitude: float) -> tuple[float, float]:
    """Convert listing-card WGS84 coordinates to the VSB polygon CRS."""

    origin = 20037508.34
    x = longitude * origin / 180.0
    latitude_radians = math.radians(max(min(latitude, 89.5), -89.5))
    y = origin * math.log(math.tan(math.pi / 4.0 + latitude_radians / 2.0)) / math.pi
    return x, y


def _geometry_center(geometry: Mapping[str, Any]) -> tuple[float, float] | None:
    rings = geometry.get("rings")
    if isinstance(rings, list) and rings and isinstance(rings[0], list):
        points = [point for point in rings[0] if isinstance(point, (list, tuple)) and len(point) >= 2]
        if points:
            x = sum(float(point[0]) for point in points) / len(points)
            y = sum(float(point[1]) for point in points) / len(points)
            return _web_mercator_to_wgs84(x, y)
    point = geometry.get("x"), geometry.get("y")
    if point[0] is not None and point[1] is not None:
        return _web_mercator_to_wgs84(float(point[0]), float(point[1]))
    return None


def _geometry_center_xy(geometry: Mapping[str, Any]) -> tuple[float, float] | None:
    rings = geometry.get("rings")
    if isinstance(rings, list) and rings and isinstance(rings[0], list):
        points = [point for point in rings[0] if isinstance(point, (list, tuple)) and len(point) >= 2]
        if points:
            return (
                sum(float(point[0]) for point in points) / len(points),
                sum(float(point[1]) for point in points) / len(points),
            )
    if geometry.get("x") is not None and geometry.get("y") is not None:
        return float(geometry["x"]), float(geometry["y"])
    return None


def _dbf_rows(content: bytes) -> list[dict[str, str]]:
    if len(content) < 32:
        return []
    record_count = struct.unpack_from("<I", content, 4)[0]
    header_length = struct.unpack_from("<H", content, 8)[0]
    record_length = struct.unpack_from("<H", content, 10)[0]
    fields: list[tuple[str, int]] = []
    offset = 32
    while offset + 32 <= len(content) and content[offset] != 0x0D:
        descriptor = content[offset:offset + 32]
        name = descriptor[:11].split(b"\x00", 1)[0].decode("latin1", errors="ignore").strip()
        length = descriptor[16]
        if name:
            fields.append((name, length))
        offset += 32
    rows: list[dict[str, str]] = []
    for index in range(record_count):
        start = header_length + index * record_length
        end = start + record_length
        if end > len(content) or content[start:start + 1] == b"*":
            continue
        cursor = start + 1
        row: dict[str, str] = {}
        for name, length in fields:
            value = content[cursor:cursor + length].decode("utf-8", errors="replace").strip()
            row[name] = value
            cursor += length
        rows.append(row)
    return rows


def _shp_polygons(content: bytes) -> list[list[list[tuple[float, float]]]]:
    if len(content) < 100:
        return []
    polygons: list[list[list[tuple[float, float]]]] = []
    offset = 100
    while offset + 8 <= len(content):
        content_length_words = struct.unpack_from(">i", content, offset + 4)[0]
        body_start = offset + 8
        body_end = body_start + content_length_words * 2
        if body_end > len(content) or body_start + 44 > body_end:
            break
        shape_type = struct.unpack_from("<i", content, body_start)[0]
        if shape_type == 5:
            part_count = struct.unpack_from("<i", content, body_start + 36)[0]
            point_count = struct.unpack_from("<i", content, body_start + 40)[0]
            parts_start = body_start + 44
            points_start = parts_start + part_count * 4
            part_offsets = [
                struct.unpack_from("<i", content, parts_start + part * 4)[0]
                for part in range(max(part_count, 0))
            ]
            all_points = [
                struct.unpack_from("<dd", content, points_start + point * 16)
                for point in range(max(point_count, 0))
            ]
            rings: list[list[tuple[float, float]]] = []
            for index, start in enumerate(part_offsets):
                end = part_offsets[index + 1] if index + 1 < len(part_offsets) else len(all_points)
                rings.append(list(all_points[start:end]))
            polygons.append(rings)
        offset = body_end
    return polygons


def _point_in_ring(point: tuple[float, float], ring: list[tuple[float, float]]) -> bool:
    if len(ring) < 3:
        return False
    px, py = point
    inside = False
    for left, right in zip(ring, [*ring[1:], ring[0]]):
        x1, y1 = left
        x2, y2 = right
        if (y1 > py) == (y2 > py):
            continue
        intersection_x = (x2 - x1) * (py - y1) / (y2 - y1) + x1
        if px < intersection_x:
            inside = not inside
    return inside


def _point_in_polygon(point: tuple[float, float], rings: list[list[tuple[float, float]]]) -> bool:
    # Even-odd handling keeps interior holes excluded without relying on ring
    # winding order, which differs between GIS exporters.
    return sum(_point_in_ring(point, ring) for ring in rings) % 2 == 1


def parse_vsb_boundary_zip(
    content: bytes,
    source_url: str,
    observed_at: datetime | None = None,
) -> list[dict[str, Any]]:
    """Parse a VSB polygon shapefile zip into auditable boundary rows."""

    del observed_at
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        names = {name.lower(): name for name in archive.namelist()}
        shp_name = next((name for name in names.values() if name.lower().endswith(".shp")), None)
        dbf_name = next((name for name in names.values() if name.lower().endswith(".dbf")), None)
        if not shp_name or not dbf_name:
            return []
        polygons = _shp_polygons(archive.read(shp_name))
        attributes = _dbf_rows(archive.read(dbf_name))
    rows: list[dict[str, Any]] = []
    for index, shape in enumerate(polygons):
        attribute = attributes[index] if index < len(attributes) else {}
        school_name = next(
            (value for key, value in attribute.items() if key.lower() in {"schoolname", "school_name"} and value),
            None,
        )
        if not school_name:
            continue
        rows.append({"school_name": school_name, "rings": shape, "source_url": source_url})
    return rows


def _street_component(value: Any) -> str:
    tokens = normalize_address(str(value or ""), None).split()
    if tokens and (tokens[0][0].isdigit() or "-" in tokens[0]):
        tokens = tokens[1:]
    # City street records mix abbreviated and full street types, e.g. ``AV``
    # and ``AVENUE``. Normalize the suffix before comparing frontage.
    aliases = {
        "street": "st", "st": "st",
        "avenue": "ave", "av": "ave", "ave": "ave",
        "road": "rd", "rd": "rd",
        "drive": "dr", "dr": "dr",
        "boulevard": "blvd", "blvd": "blvd",
        "place": "pl", "pl": "pl",
        "crescent": "cres", "cres": "cres",
        "court": "ct", "ct": "ct",
        "lane": "ln", "ln": "ln",
        "highway": "hwy", "hwy": "hwy",
        "terrace": "ter", "ter": "ter",
    }
    if tokens:
        tokens[-1] = aliases.get(tokens[-1], tokens[-1])
    return " ".join(tokens)


def _line_distance_m(point: tuple[float, float], geometry: Mapping[str, Any]) -> float | None:
    raw_geometry = geometry.get("geometry") if isinstance(geometry.get("geometry"), Mapping) else geometry
    coordinates = raw_geometry.get("coordinates") if isinstance(raw_geometry, Mapping) else None
    if not isinstance(coordinates, list):
        return None
    if raw_geometry.get("type") == "MultiLineString":
        lines = coordinates
    else:
        lines = [coordinates]
    lat0, lon0 = point
    x_scale = 111320.0 * math.cos(math.radians(lat0))
    y_scale = 110540.0
    px, py = lon0 * x_scale, lat0 * y_scale
    distances: list[float] = []
    for line in lines:
        if not isinstance(line, list):
            continue
        for left, right in zip(line, line[1:]):
            if not all(isinstance(item, (list, tuple)) and len(item) >= 2 for item in (left, right)):
                continue
            ax, ay = float(left[0]) * x_scale, float(left[1]) * y_scale
            bx, by = float(right[0]) * x_scale, float(right[1]) * y_scale
            dx, dy = bx - ax, by - ay
            denominator = dx * dx + dy * dy
            ratio = ((px - ax) * dx + (py - ay) * dy) / denominator if denominator else 0.0
            ratio = max(0.0, min(1.0, ratio))
            distances.append(math.hypot(px - (ax + ratio * dx), py - (ay + ratio * dy)))
    return min(distances) if distances else None


def _road_class(streetuse: Any) -> str:
    value = " ".join(str(streetuse or "").lower().split())
    if "highway" in value or "freeway" in value or "rail" in value:
        return "A"
    if "arterial" in value:
        return "B"
    if "collector" in value:
        return "D"
    return "E"


class MunicipalRoadAdapter:
    """Compute bounded road exposure from official City street geometry."""

    def __init__(
        self,
        *,
        name: str,
        source_registry_id: str,
        source_url: str,
        raw_snapshot_dir: str | Path,
        config: Mapping[str, Any] | None = None,
        transport: Callable[[Mapping[str, Any]], OfficialHttpResponse] | None = None,
    ) -> None:
        self.name = name
        self.source_registry_id = source_registry_id
        self.source_url = source_url
        self.config = dict(config or {})
        self.source_type = SourceType.OFFICIAL_GOV
        self.http = OfficialHttpTransport(
            name=name,
            source_registry_id=source_registry_id,
            source_type=SourceType.OFFICIAL_GOV,
            source_url=source_url,
            raw_snapshot_dir=raw_snapshot_dir,
            config=self.config,
            transport=transport,
        )

    def _address_request(self, record: PropertyRecord) -> dict[str, Any]:
        address_url = str(self.config.get("address_search_url") or "")
        if not address_url:
            raise ValueError("municipal road lookup requires address_search_url")
        address = normalize_address(record.address, None).upper()
        return {
            "url": address_url,
            "method": "GET",
            "params": {
                "where": f"address = '{address.replace(chr(39), chr(39) + chr(39))}'",
                "outFields": "*",
                "returnGeometry": "true",
                "f": "json",
            },
            "property_id": record.property_id,
            "query_step": "address_parcel",
        }

    def _street_request(
        self,
        record: PropertyRecord,
        point: tuple[float, float],
        *,
        query_step: str = "street_proximity",
        point_source: str = "ADDRESS_PARCEL",
        fallback_warnings: list[str] | None = None,
    ) -> dict[str, Any]:
        street_url = str(self.config.get("street_records_url") or "")
        if not street_url:
            raise ValueError("municipal road lookup requires street_records_url")
        latitude, longitude = point
        radius = float(self.config.get("search_radius_m") or 500)
        return {
            "url": street_url,
            "method": "GET",
            "params": {
                "select": "hblock,streetuse,geo_point_2d,geom",
                "where": f"within_distance(geom, geom'POINT({longitude:.7f} {latitude:.7f})', {radius:g} m)",
                "limit": int(self.config.get("record_limit") or 100),
            },
            "property_id": record.property_id,
            "query_step": query_step,
            "point_source": point_source,
            "_query_point": {"latitude": latitude, "longitude": longitude},
            "_fallback_warnings": list(fallback_warnings or []),
        }

    @staticmethod
    def _features(response: OfficialHttpResponse) -> list[dict[str, Any]]:
        try:
            payload = json.loads(response.content.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return []
        return _rows_from_payload(payload)

    def collect_for_records(
        self,
        records: list[PropertyRecord],
        observed_at: datetime,
    ) -> OfficialEnrichmentBatch:
        address_requests = [self._address_request(record) for record in records]
        address_fetch = self.http.fetch_all(address_requests, observed_at=observed_at)
        street_requests: list[dict[str, Any]] = []
        rows: list[OfficialLookupRow] = []
        for record in records:
            request = next(item for item in address_requests if item["property_id"] == record.property_id)
            response = next(
                (item for item in address_fetch.responses if item.request and item.request.get("property_id") == record.property_id),
                None,
            )
            snapshot = _snapshot_for_request(address_fetch, request, response)
            if response is None:
                latitude = _number(getattr(record, "latitude", None))
                longitude = _number(getattr(record, "longitude", None))
                if (
                    bool(self.config.get("coordinate_fallback_enabled", True))
                    and latitude is not None
                    and longitude is not None
                ):
                    street_requests.append(self._street_request(
                        record,
                        (float(latitude), float(longitude)),
                        query_step="coordinate_street_proximity",
                        point_source="LISTING_COORDINATE",
                        fallback_warnings=["address parcel lookup returned no response; used listing coordinates"],
                    ))
                    continue
                rows.append(OfficialLookupRow(
                    property_id=record.property_id,
                    status=(
                        OfficialQueryStatus.BLOCKED
                        if address_fetch.status is SourceRunStatus.BLOCKED_POLICY
                        else OfficialQueryStatus.FAILED
                    ),
                    source_url=request["url"],
                    source_version=self.config.get("source_version"),
                    raw_snapshot_path=snapshot.raw_path if snapshot else None,
                    warnings=list(address_fetch.errors),
                ))
                continue
            candidates = self._features(response)
            if len(candidates) != 1 or not candidates[0].get("geometry"):
                latitude = _number(record.latitude)
                longitude = _number(record.longitude)
                if (
                    bool(self.config.get("coordinate_fallback_enabled", True))
                    and latitude is not None
                    and longitude is not None
                ):
                    street_requests.append(self._street_request(
                        record,
                        (float(latitude), float(longitude)),
                        query_step="coordinate_street_proximity",
                        point_source="LISTING_COORDINATE",
                        fallback_warnings=["address parcel was not uniquely resolved; used listing coordinates"],
                    ))
                    continue
                rows.append(OfficialLookupRow(
                    property_id=record.property_id,
                    status=(OfficialQueryStatus.NO_MATCH if not candidates else OfficialQueryStatus.AMBIGUOUS),
                    source_url=response.url,
                    retrieved_at=_aware(datetime.now(timezone.utc)),
                    source_version=self.config.get("source_version"),
                    raw_snapshot_path=snapshot.raw_path if snapshot else None,
                    warnings=["address parcel geometry was not uniquely resolved"],
                ))
                continue
            point = _geometry_center(candidates[0]["geometry"])
            if point is None:
                latitude = _number(record.latitude)
                longitude = _number(record.longitude)
                if (
                    bool(self.config.get("coordinate_fallback_enabled", True))
                    and latitude is not None
                    and longitude is not None
                ):
                    street_requests.append(self._street_request(
                        record,
                        (float(latitude), float(longitude)),
                        query_step="coordinate_street_proximity",
                        point_source="LISTING_COORDINATE",
                        fallback_warnings=["address parcel had no usable geometry center; used listing coordinates"],
                    ))
                    continue
                rows.append(OfficialLookupRow(
                    property_id=record.property_id,
                    status=OfficialQueryStatus.NO_MATCH,
                    source_url=response.url,
                    retrieved_at=_aware(datetime.now(timezone.utc)),
                    source_version=self.config.get("source_version"),
                    raw_snapshot_path=snapshot.raw_path if snapshot else None,
                    warnings=["address parcel did not contain a usable geometry center"],
                ))
                continue
            street_requests.append(self._street_request(record, point))

        street_fetch = self.http.fetch_all(street_requests, observed_at=observed_at) if street_requests else None
        for record in records:
            if any(row.property_id == record.property_id for row in rows):
                continue
            request = next(item for item in street_requests if item["property_id"] == record.property_id)
            response = next(
                (item for item in (street_fetch.responses if street_fetch else []) if item.request and item.request.get("property_id") == record.property_id),
                None,
            )
            snapshot = _snapshot_for_request(street_fetch, request, response) if street_fetch else None
            if response is None:
                rows.append(OfficialLookupRow(
                    property_id=record.property_id,
                    status=(
                        OfficialQueryStatus.BLOCKED
                        if street_fetch and street_fetch.status is SourceRunStatus.BLOCKED_POLICY
                        else OfficialQueryStatus.FAILED
                    ),
                    source_url=request["url"],
                    source_version=self.config.get("source_version"),
                    raw_snapshot_path=snapshot.raw_path if snapshot else None,
                    warnings=list(street_fetch.errors if street_fetch else []),
                ))
                continue
            candidates = self._features(response)
            # The center point is encoded in the query geometry.  Reconstruct
            # it from the request so the response remains self-contained and
            # no geocoder result is treated as a property fact.
            query_point = request.get("_query_point")
            if isinstance(query_point, Mapping):
                point = (
                    _number(query_point.get("latitude")),
                    _number(query_point.get("longitude")),
                )
                point = (
                    (float(point[0]), float(point[1]))
                    if point[0] is not None and point[1] is not None
                    else None
                )
            else:
                point_match = re.search(r"POINT\(([-+\d.]+)\s+([-+\d.]+)\)", str(request.get("params", {}).get("where")))
                point = (
                    (float(point_match.group(2)), float(point_match.group(1)))
                    if point_match
                    else None
                )
            if point is None:
                rows.append(OfficialLookupRow(
                    property_id=record.property_id,
                    status=OfficialQueryStatus.FAILED,
                    source_url=response.url,
                    retrieved_at=_aware(datetime.now(timezone.utc)),
                    source_version=self.config.get("source_version"),
                    raw_snapshot_path=snapshot.raw_path if snapshot else None,
                    warnings=["road query center could not be reconstructed"],
                ))
                continue
            scored = []
            local_scored = []
            for candidate in candidates:
                classification = _road_class(candidate.get("streetuse"))
                distance = _line_distance_m(point, candidate.get("geom") or {})
                if distance is not None:
                    if classification == "E":
                        local_scored.append((distance, candidate))
                    else:
                        scored.append((distance, candidate))
            used_local_street = (
                bool(self.config.get("local_street_fallback_enabled", True))
                and not scored
                and bool(local_scored)
            )
            if used_local_street:
                scored = local_scored
            if not scored:
                rows.append(OfficialLookupRow(
                    property_id=record.property_id,
                    status=OfficialQueryStatus.NO_MATCH,
                    source_url=response.url,
                    retrieved_at=_aware(datetime.now(timezone.utc)),
                    source_version=self.config.get("source_version"),
                    raw_snapshot_path=snapshot.raw_path if snapshot else None,
                    warnings=["no official street geometry found within configured radius"],
                ))
                continue
            distance, candidate = min(scored, key=lambda item: item[0])
            hblock = str(candidate.get("hblock") or "").strip()
            road_class = _road_class(candidate.get("streetuse"))
            point_source = str(request.get("point_source") or "ADDRESS_PARCEL")
            match_method = f"{point_source}+STREET_GEOMETRY"
            warnings = list(request.get("_fallback_warnings") or [])
            if used_local_street:
                warnings.append("no arterial or collector street found; classified nearest official local street as E/quiet")
            fields = {
                "address": record.address,
                "municipality": record.municipality,
                "nearest_road": hblock,
                "nearest_major_road": None if used_local_street else hblock,
                "distance_to_major_road_m": None if used_local_street else round(distance, 1),
                "property_fronts_major_road": False if used_local_street else _street_component(record.address) == _street_component(hblock),
                "road_class": road_class,
                "source_url": response.url,
            }
            rows.append(OfficialLookupRow(
                property_id=record.property_id,
                status=OfficialQueryStatus.SUCCESS,
                fields=fields,
                source_url=response.url,
                retrieved_at=_aware(datetime.now(timezone.utc)),
                source_version=self.config.get("source_version"),
                match_method=match_method,
                query_scope="nearest_official_street_within_radius" if used_local_street else "nearest_arterial_or_collector_within_radius",
                raw_snapshot_path=snapshot.raw_path if snapshot else None,
                warnings=warnings,
            ))

        fetches = [address_fetch, street_fetch] if street_fetch else [address_fetch]
        source_run = address_fetch.source_run.model_copy(update={
            "status": (
                SourceRunStatus.PARTIAL
                if any(fetch.status is not SourceRunStatus.SUCCESS for fetch in fetches)
                and any(fetch.responses for fetch in fetches)
                else next(
                    (fetch.status for fetch in fetches if fetch.status is not SourceRunStatus.SUCCESS),
                    SourceRunStatus.SUCCESS,
                )
            ),
            "finished_at": max(fetch.source_run.finished_at for fetch in fetches),
            "records": [
                {
                    "property_id": row.property_id,
                    "status": row.status.value,
                    "fields": row.fields,
                    "source_url": row.source_url,
                    "match_method": row.match_method,
                    "query_scope": row.query_scope,
                    "raw_snapshot_path": row.raw_snapshot_path,
                    "warnings": row.warnings,
                }
                for row in rows
            ],
            "snapshots": [snapshot for fetch in fetches for snapshot in fetch.snapshots],
            "errors": [error for fetch in fetches for error in fetch.errors],
            "warnings": [warning for fetch in fetches for warning in fetch.warnings],
        })
        return OfficialEnrichmentBatch(source_run=source_run, rows=rows)


@dataclass(frozen=True)
class SchoolSourceRoute:
    name: str
    district: str
    municipality: str
    source_url: str


_DEFAULT_SCHOOL_ROUTES = {
    "city of vancouver": SchoolSourceRoute(
        name="school-catchment-sd39",
        district="SD39",
        municipality="City of Vancouver",
        source_url="https://vsb.bc.ca/school-directory-and-map",
    ),
    "city of burnaby": SchoolSourceRoute(
        name="school-catchment-sd41",
        district="SD41",
        municipality="City of Burnaby",
        source_url="https://www.sd41.bc.ca/",
    ),
    "city of north vancouver": SchoolSourceRoute(
        name="school-catchment-sd44",
        district="SD44",
        municipality="City of North Vancouver",
        source_url="https://www.sd44.ca/Schools/SchoolLocator/Pages/default.aspx",
    ),
    "district of north vancouver": SchoolSourceRoute(
        name="school-catchment-sd44",
        district="SD44",
        municipality="District of North Vancouver",
        source_url="https://www.sd44.ca/Schools/SchoolLocator/Pages/default.aspx",
    ),
    "district of west vancouver": SchoolSourceRoute(
        name="school-catchment-sd45",
        district="SD45",
        municipality="District of West Vancouver",
        source_url="https://westvancouverschools.ca/",
    ),
}


def route_school_source(
    municipality: str,
    routes: Mapping[str, Mapping[str, Any]] | None = None,
) -> SchoolSourceRoute:
    key = " ".join(str(municipality or "").strip().lower().split())
    if routes:
        for raw_key, raw_route in routes.items():
            if " ".join(str(raw_key).lower().split()) != key:
                continue
            item = dict(raw_route)
            return SchoolSourceRoute(
                name=str(item.get("name") or f"school-catchment-{item.get('district', 'unknown').lower()}"),
                district=str(item.get("district") or item.get("school_district") or "UNKNOWN"),
                municipality=str(item.get("municipality") or municipality),
                source_url=str(item.get("source_url") or item.get("url") or ""),
            )
    route = _DEFAULT_SCHOOL_ROUTES.get(key)
    if route is None:
        raise ValueError(f"no official school route configured for municipality: {municipality}")
    return route


def _normalize_school_row(raw: Mapping[str, Any], source_url: str) -> dict[str, Any]:
    aliases = {
        "elementary": "official_elementary_catchment",
        "elementary_school": "official_elementary_catchment",
        "secondary": "official_secondary_catchment",
        "secondary_school": "official_secondary_catchment",
        "district": "school_district",
        "current_score": "current_school_score",
        "future_score": "school_future_score",
    }
    fields: dict[str, Any] = {}
    for raw_name, value in raw.items():
        field = aliases.get(str(raw_name), str(raw_name))
        if field in {
            "pid",
            "PID",
            "address",
            "civic_address",
            "municipality",
            "official_elementary_catchment",
            "official_secondary_catchment",
            "school_district",
            "elementary_distance",
            "secondary_distance",
            "kindergarten_capacity",
            "overflow",
            "school_plan",
            "enrolment",
            "literacy",
            "numeracy",
            "special_programs",
            "capital_projects",
            "seismic_upgrade",
            "nearby_private_schools",
            "current_school_score",
            "school_future_score",
            "family_location_score",
        } and value not in (None, "", [], {}):
            field = "pid" if field == "PID" else field
            if field in {"elementary_distance", "secondary_distance", "current_school_score", "school_future_score", "family_location_score"}:
                numeric = _number(value)
                value = numeric if numeric is not None else value
            fields[field] = value
    pids = _candidate_pids(raw)
    if pids:
        fields["pids"] = sorted(pids)
        fields["number_of_pids"] = len(pids)
    fields["source_url"] = source_url
    return fields


def parse_school_payload(
    content: bytes,
    source_url: str,
    observed_at: datetime,
) -> list[dict[str, Any]]:
    del observed_at
    try:
        payload = json.loads(content.decode("utf-8-sig"))
        raw_rows = _rows_from_payload(payload)
    except (UnicodeDecodeError, json.JSONDecodeError):
        soup = BeautifulSoup(content, "html.parser")
        raw_rows = [_labelled_html_values(soup)]
    return [_normalize_school_row(row, source_url) for row in raw_rows if row]


def parse_sold_history_payload(
    content: bytes,
    source_url: str,
    observed_at: datetime,
) -> list[dict[str, Any]]:
    allowed = {
        "pid",
        "PID",
        "pids",
        "number_of_pids",
        "address",
        "civic_address",
        "municipality",
        "secondary_civic_addresses",
        "legal_description",
        "previous_sale_dates",
        "previous_sale_prices",
        "sold_history_scope",
        "source_url",
    }
    rows = parse_bc_assessment_payload(content, source_url, observed_at)
    return [{key: value for key, value in row.items() if key in allowed} for row in rows]


class SchoolCatchmentAdapter:
    """Read-only adapter for official school district catchment responses."""

    def __init__(
        self,
        *,
        name: str,
        source_registry_id: str,
        source_url: str,
        raw_snapshot_dir: str | Path,
        config: Mapping[str, Any] | None = None,
        transport: Callable[[Mapping[str, Any]], OfficialHttpResponse] | None = None,
    ) -> None:
        self.name = name
        self.source_registry_id = source_registry_id
        self.source_url = source_url
        self.config = dict(config or {})
        self.source_type = SourceType.OFFICIAL_GOV
        self.http = OfficialHttpTransport(
            name=name,
            source_registry_id=source_registry_id,
            source_type=SourceType.OFFICIAL_GOV,
            source_url=source_url,
            raw_snapshot_dir=raw_snapshot_dir,
            config=self.config,
            transport=transport,
        )

    def _request_for(self, record: PropertyRecord) -> dict[str, Any]:
        route = route_school_source(
            str(self.config.get("municipality") or record.municipality),
            self.config.get("routes"),
        )
        template = str(self.config.get("search_url") or route.source_url or self.source_url)
        return {
            "url": template.format(
                pid=quote(str(record.pid or ""), safe=""),
                address=quote(record.address, safe=""),
            ),
            "method": "GET",
            "property_id": record.property_id,
        }

    def _uses_boundary_zip(self) -> bool:
        return str(self.config.get("lookup_mode") or "").lower() in {
            "vsb_boundary_zip",
            "boundary_zip",
        }

    def _address_request(self, record: PropertyRecord) -> dict[str, Any]:
        return self._address_request_for_value(record, normalize_address(record.address, None).upper())

    def _address_requests(self, record: PropertyRecord) -> list[dict[str, Any]]:
        """Build bounded address variants for the City's parcel layer.

        Vancouver's public AddressSearchPolygons layer uses ``AV`` for
        avenues, while listing feeds commonly normalize the same suffix to
        ``AVE``.  Keep the listing-normalized form first, then try the
        official abbreviation and full-name form as deterministic fallbacks.
        """
        address = normalize_address(record.address, None).upper()
        variants = [address]
        suffix_aliases = {
            "AVE": ("AV", "AVENUE"),
            "ST": ("STREET",),
            "RD": ("ROAD",),
            "DR": ("DRIVE",),
            "BLVD": ("BOULEVARD",),
            "PL": ("PLACE",),
            "CRES": ("CRESCENT",),
            "CT": ("COURT",),
            "LN": ("LANE",),
            "HWY": ("HIGHWAY",),
            "TER": ("TERRACE",),
        }
        for canonical, aliases in suffix_aliases.items():
            if not re.search(rf"\b{canonical}$", address):
                continue
            for alias in aliases:
                variant = re.sub(rf"\b{canonical}$", alias, address)
                if variant not in variants:
                    variants.append(variant)
        # Vancouver's address layer uses ordinal avenue names (for example
        # ``45TH AV``) while some listing cards omit the ordinal suffix.
        ordinal_match = re.search(r"\b(\d+)\s+(AVE|AV|AVENUE)$", address)
        if ordinal_match:
            number = int(ordinal_match.group(1))
            if 10 < number % 100 < 14:
                ordinal = f"{number}TH"
            else:
                ordinal = f"{number}{ {1: 'ST', 2: 'ND', 3: 'RD'}.get(number % 10, 'TH') }"
            variant = re.sub(r"\b\d+\s+(?:AVE|AV|AVENUE)$", f"{ordinal} AV", address)
            if variant not in variants:
                variants.append(variant)
        # Some named Vancouver streets are stored without an E/W/N/S token
        # even when a listing feed includes one (for example E Main Street).
        undirected = re.sub(r"^(\d+)\s+[NSEW]\s+", r"\1 ", address)
        if undirected != address and undirected not in variants:
            variants.append(undirected)
        # Preserve punctuation in proper names such as St. Margarets, which
        # the generic normalizer intentionally removes for identity matching.
        display_address = " ".join(str(record.address or "").upper().split())
        if display_address and display_address not in variants:
            variants.append(display_address)
            display_suffix_aliases = {
                "STREET": "ST",
                "AVENUE": "AVE",
                "ROAD": "RD",
                "DRIVE": "DR",
                "BOULEVARD": "BLVD",
                "PLACE": "PL",
                "CRESCENT": "CRES",
                "COURT": "CT",
                "LANE": "LN",
                "HIGHWAY": "HWY",
                "TERRACE": "TER",
            }
            for full, abbreviated in display_suffix_aliases.items():
                variant = re.sub(rf"\b{full}$", abbreviated, display_address)
                if variant != display_address and variant not in variants:
                    variants.append(variant)
            for canonical, aliases in suffix_aliases.items():
                if not re.search(rf"\b{canonical}$", normalize_address(record.address, None).upper()):
                    continue
                for alias in aliases:
                    variant = re.sub(rf"\b(?:STREET|AVENUE|ROAD|DRIVE|BOULEVARD|PLACE|CRESCENT|COURT|LANE|HIGHWAY|TERRACE|ST|AVE|RD|DR|BLVD|PL|CRES|CT|LN|HWY|TER)$", alias, display_address)
                    if variant not in variants:
                        variants.append(variant)
        return [self._address_request_for_value(record, value) for value in variants]

    def _address_batch_request(self, records: list[PropertyRecord]) -> dict[str, Any]:
        """Query all address variants in one bounded ArcGIS request."""
        variants: list[str] = []
        for record in records:
            for request in self._address_requests(record):
                value = str(request["address_variant"])
                if value not in variants:
                    variants.append(value)
        values = ", ".join(
            f"'{value.replace(chr(39), chr(39) + chr(39))}'"
            for value in variants
        )
        address_url = str(self.config.get("address_search_url") or "")
        if not address_url:
            raise ValueError("boundary zip school lookup requires address_search_url")
        return {
            "url": address_url,
            "method": "GET",
            "params": {
                "where": f"address IN ({values})",
                "outFields": "*",
                "returnGeometry": "true",
                "f": "json",
            },
            "property_id": "__address_parcels__",
            "query_step": "address_parcels",
            "address_variants": variants,
        }

    def _address_request_for_value(self, record: PropertyRecord, address: str) -> dict[str, Any]:
        address_url = str(self.config.get("address_search_url") or "")
        if not address_url:
            raise ValueError("boundary zip school lookup requires address_search_url")
        return {
            "url": address_url,
            "method": "GET",
            "params": {
                "where": f"address = '{address.replace(chr(39), chr(39) + chr(39))}'",
                "outFields": "*",
                "returnGeometry": "true",
                "f": "json",
            },
            "property_id": record.property_id,
            "query_step": "address_parcel",
            "address_variant": address,
        }

    def _boundary_requests(self) -> list[dict[str, Any]]:
        requests: list[dict[str, Any]] = []
        for level, key in (("elementary", "elementary_boundary_url"), ("secondary", "secondary_boundary_url")):
            url = str(self.config.get(key) or "")
            if not url:
                raise ValueError(f"boundary zip school lookup requires {key}")
            requests.append({
                "url": url,
                "method": "GET",
                "query_step": f"{level}_boundary",
                "property_id": f"boundary:{level}",
            })
        return requests

    def _boundary_cache_path(self) -> Path:
        configured_path = self.config.get("boundary_cache_path")
        if configured_path not in (None, ""):
            return Path(str(configured_path))
        configured_dir = self.config.get("boundary_cache_dir")
        directory = (
            Path(str(configured_dir))
            if configured_dir not in (None, "")
            else self.http.raw_snapshot_dir / self.name / "boundary-cache"
        )
        identity = "|".join(
            [
                self.source_registry_id,
                str(self.config.get("source_version") or ""),
                str(self.config.get("elementary_boundary_url") or ""),
                str(self.config.get("secondary_boundary_url") or ""),
            ]
        )
        digest = sha256(identity.encode("utf-8")).hexdigest()[:16]
        safe_name = re.sub(r"[^a-z0-9]+", "-", self.name.casefold()).strip("-") or "school"
        return directory / f"{safe_name}-{digest}.json"

    def _read_boundary_cache(self) -> dict[str, Any] | None:
        path = self._boundary_cache_path()
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return None
        if not isinstance(payload, Mapping):
            return None
        if payload.get("schema_version") != "school-boundary-cache/v1":
            return None
        if str(payload.get("source_registry_id") or "") != self.source_registry_id:
            return None
        if str(payload.get("source_version") or "") != str(self.config.get("source_version") or ""):
            return None
        expected_urls = {
            "elementary": str(self.config.get("elementary_boundary_url") or ""),
            "secondary": str(self.config.get("secondary_boundary_url") or ""),
        }
        if payload.get("boundary_urls") != expected_urls:
            return None
        configured_ttl = self.config.get("boundary_cache_ttl_days")
        if configured_ttl not in (None, ""):
            try:
                ttl_days = float(configured_ttl)
            except (TypeError, ValueError):
                return None
            if ttl_days < 0:
                return None
            try:
                modified_at = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
            except OSError:
                return None
            if datetime.now(timezone.utc) - modified_at > timedelta(days=ttl_days):
                return None
        boundaries = payload.get("boundaries")
        if not isinstance(boundaries, Mapping):
            return None
        for level in ("elementary", "secondary"):
            rows = boundaries.get(level)
            if not isinstance(rows, list) or not rows:
                return None
            if not all(isinstance(row, Mapping) and row.get("rings") for row in rows):
                return None
        return dict(payload)

    def _write_boundary_cache(
        self,
        boundaries: Mapping[str, list[dict[str, Any]]],
        *,
        observed_at: datetime,
        raw_snapshot_paths: list[str],
    ) -> Path:
        path = self._boundary_cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": "school-boundary-cache/v1",
            "source_registry_id": self.source_registry_id,
            "source_url": self.source_url,
            "source_version": self.config.get("source_version"),
            "boundary_urls": {
                "elementary": str(self.config.get("elementary_boundary_url") or ""),
                "secondary": str(self.config.get("secondary_boundary_url") or ""),
            },
            "observed_at": _aware(observed_at).isoformat(),
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "raw_snapshot_paths": raw_snapshot_paths,
            "boundaries": boundaries,
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return path

    def prefetch_boundary_cache(self, observed_at: datetime) -> dict[str, Any]:
        """Fetch and persist official catchment polygons once per source version."""

        cached = self._read_boundary_cache()
        cache_path = self._boundary_cache_path()
        if cached is not None:
            boundaries = cached.get("boundaries", {})
            return {
                "status": "CACHE_HIT",
                "cache_hit": True,
                "cache_path": str(cache_path),
                "boundary_counts": {
                    level: len(boundaries.get(level, []))
                    for level in ("elementary", "secondary")
                },
                "raw_snapshot_paths": list(cached.get("raw_snapshot_paths") or []),
            }

        requests = self._boundary_requests()
        fetched = self.http.fetch_all(requests, observed_at=observed_at)
        boundaries: dict[str, list[dict[str, Any]]] = {}
        errors = list(fetched.errors)
        raw_snapshot_paths: list[str] = []
        for request in requests:
            response = next(
                (
                    item
                    for item in fetched.responses
                    if item.request and item.request.get("query_step") == request["query_step"]
                ),
                None,
            )
            snapshot = _snapshot_for_request(fetched, request, response)
            if snapshot is not None:
                raw_snapshot_paths.append(snapshot.raw_path)
            if response is None:
                errors.append(f"missing official boundary response: {request['query_step']}")
                continue
            try:
                rows = parse_vsb_boundary_zip(response.content, response.url, observed_at)
            except (OSError, TypeError, ValueError, zipfile.BadZipFile) as exc:
                rows = []
                errors.append(f"{request['query_step']}: {type(exc).__name__}: {exc}")
            level = str(request["query_step"]).removesuffix("_boundary")
            boundaries[level] = rows
            if not rows:
                errors.append(f"{request['query_step']}: no school boundary polygons parsed")

        if errors or any(not boundaries.get(level) for level in ("elementary", "secondary")):
            return {
                "status": fetched.status.value,
                "cache_hit": False,
                "cache_path": str(cache_path),
                "boundary_counts": {
                    level: len(boundaries.get(level, []))
                    for level in ("elementary", "secondary")
                },
                "raw_snapshot_paths": raw_snapshot_paths,
                "errors": errors,
            }
        written = self._write_boundary_cache(
            boundaries,
            observed_at=observed_at,
            raw_snapshot_paths=raw_snapshot_paths,
        )
        return {
            "status": "REFRESHED",
            "cache_hit": False,
            "cache_path": str(written),
            "boundary_counts": {
                level: len(boundaries.get(level, []))
                for level in ("elementary", "secondary")
            },
            "raw_snapshot_paths": raw_snapshot_paths,
        }

    @staticmethod
    def _features(response: OfficialHttpResponse) -> list[dict[str, Any]]:
        try:
            payload = json.loads(response.content.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return []
        return _rows_from_payload(payload)

    def _collect_boundary_zip_for_records(
        self,
        records: list[PropertyRecord],
        observed_at: datetime,
    ) -> OfficialEnrichmentBatch:
        address_request = self._address_batch_request(records)
        cached_boundaries = self._read_boundary_cache()
        boundary_requests = [] if cached_boundaries is not None else self._boundary_requests()
        fetched = self.http.fetch_all([address_request, *boundary_requests], observed_at=observed_at)
        boundaries: dict[str, list[dict[str, Any]]] = (
            dict(cached_boundaries.get("boundaries", {}))
            if cached_boundaries is not None
            else {}
        )
        boundary_parse_errors: dict[str, str] = {}
        boundary_responses: dict[str, OfficialHttpResponse] = {}
        boundary_snapshot_paths: list[str] = (
            [str(self._boundary_cache_path())]
            if cached_boundaries is not None
            else []
        )
        for request in boundary_requests:
            response = next(
                (item for item in fetched.responses if item.request and item.request.get("query_step") == request["query_step"]),
                None,
            )
            if response is None:
                continue
            boundary_responses[request["query_step"]] = response
            snapshot = _snapshot_for_request(fetched, request, response)
            if snapshot is not None:
                boundary_snapshot_paths.append(snapshot.raw_path)
            try:
                boundaries[request["query_step"]] = parse_vsb_boundary_zip(
                    response.content,
                    response.url,
                    observed_at,
                )
            except (OSError, TypeError, ValueError, zipfile.BadZipFile) as exc:
                boundaries[request["query_step"]] = []
                boundary_parse_errors[request["query_step"]] = f"{type(exc).__name__}: {exc}"
        if cached_boundaries is None and not boundary_parse_errors and all(
            boundaries.get(level) for level in ("elementary_boundary", "secondary_boundary")
        ):
            cache_path = self._write_boundary_cache(
                {
                    "elementary": boundaries["elementary_boundary"],
                    "secondary": boundaries["secondary_boundary"],
                },
                observed_at=observed_at,
                raw_snapshot_paths=boundary_snapshot_paths,
            )
            boundary_snapshot_paths.append(str(cache_path))
        route = route_school_source(
            str((self.config.get("municipality") or records[0].municipality) if records else ""),
            self.config.get("routes"),
        )
        rows: list[OfficialLookupRow] = []
        address_response = next(
            (
                item
                for item in fetched.responses
                if item.request and item.request.get("query_step") == "address_parcels"
            ),
            None,
        )
        address_snapshot = _snapshot_for_request(fetched, address_request, address_response)
        address_features = self._features(address_response) if address_response else []
        for record in records:
            if address_response is None:
                rows.append(OfficialLookupRow(
                    property_id=record.property_id,
                    status=(
                        OfficialQueryStatus.BLOCKED
                        if fetched.status is SourceRunStatus.BLOCKED_POLICY
                        else OfficialQueryStatus.FAILED
                    ),
                    source_url=address_request["url"],
                    source_version=self.config.get("source_version"),
                    raw_snapshot_path=address_snapshot.raw_path if address_snapshot else None,
                    warnings=list(fetched.errors),
                ))
                continue
            missing_boundary_steps = [
                request["query_step"]
                for request in boundary_requests
                if request["query_step"] not in boundary_responses
            ]
            if missing_boundary_steps:
                rows.append(OfficialLookupRow(
                    property_id=record.property_id,
                    status=(
                        OfficialQueryStatus.BLOCKED
                        if fetched.errors
                        else OfficialQueryStatus.FAILED
                    ),
                    source_url=self.source_url,
                    retrieved_at=_aware(datetime.now(timezone.utc)),
                    source_version=self.config.get("source_version"),
                    raw_snapshot_path=address_snapshot.raw_path if address_snapshot else None,
                    warnings=[
                        "official school boundary download unavailable",
                        f"missing_steps={','.join(missing_boundary_steps)}",
                        *fetched.errors,
                    ],
                ))
                continue
            if boundary_parse_errors:
                rows.append(OfficialLookupRow(
                    property_id=record.property_id,
                    status=OfficialQueryStatus.FAILED,
                    source_url=self.source_url,
                    retrieved_at=_aware(datetime.now(timezone.utc)),
                    source_version=self.config.get("source_version"),
                    raw_snapshot_path=address_snapshot.raw_path if address_snapshot else None,
                    warnings=[
                        "official school boundary file could not be parsed",
                        *[
                            f"{step}: {message}"
                            for step, message in sorted(boundary_parse_errors.items())
                        ],
                    ],
                ))
                continue
            expected_variants = {
                str(request["address_variant"])
                for request in self._address_requests(record)
            }
            address_candidates = [
                feature
                for feature in address_features
                if normalize_address(
                    str(feature.get("address") or feature.get("ADDRESS") or ""),
                    None,
                ).upper()
                in expected_variants
            ]
            point = None
            point_source = "ADDRESS+VSB_BOUNDARY_POLYGON"
            warnings: list[str] = []
            if len(address_candidates) == 1 and address_candidates[0].get("geometry"):
                point = _geometry_center_xy(address_candidates[0]["geometry"])
            if point is None:
                latitude = _number(getattr(record, "latitude", None))
                longitude = _number(getattr(record, "longitude", None))
                if (
                    bool(self.config.get("coordinate_fallback_enabled", True))
                    and latitude is not None
                    and longitude is not None
                ):
                    # Cached VSB polygons are Web Mercator; listing-card
                    # coordinates are WGS84. This is a deterministic fallback
                    # for address presentation variants such as a missing E/W
                    # direction, not a neighbourhood-based school guess.
                    point = _wgs84_to_web_mercator(float(latitude), float(longitude))
                    point_source = "LISTING_COORDINATE+VSB_BOUNDARY_POLYGON"
                    warnings.append(
                        "address parcel was not uniquely resolved; used listing coordinates"
                    )
                else:
                    rows.append(OfficialLookupRow(
                        property_id=record.property_id,
                        status=(
                            OfficialQueryStatus.NO_MATCH
                            if not address_candidates
                            else OfficialQueryStatus.AMBIGUOUS
                        ),
                        source_url=address_response.url,
                        retrieved_at=_aware(datetime.now(timezone.utc)),
                        source_version=self.config.get("source_version"),
                        raw_snapshot_path=address_snapshot.raw_path if address_snapshot else None,
                        warnings=["address parcel geometry was not uniquely resolved"],
                    ))
                    continue
            if point is None:
                rows.append(OfficialLookupRow(
                    property_id=record.property_id,
                    status=OfficialQueryStatus.NO_MATCH,
                    source_url=address_response.url,
                    retrieved_at=_aware(datetime.now(timezone.utc)),
                    source_version=self.config.get("source_version"),
                    raw_snapshot_path=address_snapshot.raw_path if address_snapshot else None,
                    warnings=["address parcel did not contain a usable geometry center"],
                ))
                continue
            matches: dict[str, str] = {}
            for level, boundary_rows in (
                (
                    "elementary",
                    boundaries.get(
                        "elementary" if cached_boundaries is not None else "elementary_boundary",
                        [],
                    ),
                ),
                (
                    "secondary",
                    boundaries.get(
                        "secondary" if cached_boundaries is not None else "secondary_boundary",
                        [],
                    ),
                ),
            ):
                match = next(
                    (
                        item["school_name"]
                        for item in boundary_rows
                        if _point_in_polygon(point, item["rings"])
                    ),
                    None,
                )
                if match:
                    matches[level] = match
            official_page_url = route.source_url or self.source_url
            if not matches:
                rows.append(OfficialLookupRow(
                    property_id=record.property_id,
                    status=OfficialQueryStatus.NO_MATCH,
                    source_url=official_page_url,
                    retrieved_at=_aware(datetime.now(timezone.utc)),
                    source_version=self.config.get("source_version"),
                    raw_snapshot_path=";".join(boundary_snapshot_paths) or (address_snapshot.raw_path if address_snapshot else None),
                    warnings=["address point did not fall inside a published school boundary polygon"],
                ))
                continue
            fields = {
                "address": record.address,
                "municipality": record.municipality,
                "school_district": self.config.get("school_district") or route.district,
                "source_url": official_page_url,
            }
            if "elementary" in matches:
                fields["official_elementary_catchment"] = matches["elementary"]
            if "secondary" in matches:
                fields["official_secondary_catchment"] = matches["secondary"]
            rows.append(OfficialLookupRow(
                property_id=record.property_id,
                status=OfficialQueryStatus.SUCCESS,
                fields=fields,
                source_url=official_page_url,
                retrieved_at=_aware(datetime.now(timezone.utc)),
                source_version=self.config.get("source_version"),
                match_method=(
                    f"{point_source}_CACHED"
                    if cached_boundaries is not None
                    else point_source
                ),
                query_scope=(
                    "official_catchment_boundary_cached_2026"
                    if cached_boundaries is not None
                    else "official_catchment_boundary_2026"
                ),
                raw_snapshot_path=";".join(boundary_snapshot_paths) or (address_snapshot.raw_path if address_snapshot else None),
                warnings=warnings,
            ))
        source_run = fetched.source_run.model_copy(update={
            "records": [
                {
                    "property_id": row.property_id,
                    "status": row.status.value,
                    "fields": row.fields,
                    "source_url": row.source_url,
                    "match_method": row.match_method,
                    "query_scope": row.query_scope,
                    "raw_snapshot_path": row.raw_snapshot_path,
                    "warnings": row.warnings,
                }
                for row in rows
            ]
        })
        return OfficialEnrichmentBatch(source_run=source_run, rows=rows)


    def collect_for_records(
        self,
        records: list[PropertyRecord],
        observed_at: datetime,
    ) -> OfficialEnrichmentBatch:
        if self._uses_boundary_zip():
            return self._collect_boundary_zip_for_records(records, observed_at)
        requests = [self._request_for(record) for record in records]
        fetched = self.http.fetch_all(requests, observed_at=observed_at)
        rows: list[OfficialLookupRow] = []
        for record, request in zip(records, requests):
            response = next(
                (item for item in fetched.responses if item.request and item.request.get("property_id") == record.property_id),
                None,
            )
            snapshot = _snapshot_for_request(fetched, request, response)
            source_url = response.url if response else request["url"]
            if response is None:
                rows.append(
                    OfficialLookupRow(
                        property_id=record.property_id,
                        status=OfficialQueryStatus.BLOCKED if fetched.status is SourceRunStatus.BLOCKED_POLICY else OfficialQueryStatus.FAILED,
                        source_url=source_url,
                        source_version=self.config.get("source_version"),
                        raw_snapshot_path=snapshot.raw_path if snapshot else None,
                        warnings=list(fetched.errors),
                    )
                )
                continue
            try:
                candidates = parse_school_payload(response.content, response.url, observed_at)
            except (ValueError, TypeError, UnicodeError) as exc:
                rows.append(
                    OfficialLookupRow(
                        property_id=record.property_id,
                        status=OfficialQueryStatus.FAILED,
                        source_url=source_url,
                        source_version=self.config.get("source_version"),
                        raw_snapshot_path=snapshot.raw_path if snapshot else None,
                        warnings=[f"{type(exc).__name__}: {exc}"],
                    )
                )
                continue
            identity = match_official_identity(record, candidates)
            if identity.status is OfficialQueryStatus.SUCCESS and identity.candidate is not None:
                fields = dict(identity.candidate)
                fields["source_url"] = source_url
                route = route_school_source(
                    str(self.config.get("municipality") or record.municipality),
                    self.config.get("routes"),
                )
                fields.setdefault("school_district", self.config.get("school_district") or route.district)
                rows.append(
                    OfficialLookupRow(
                        property_id=record.property_id,
                        status=OfficialQueryStatus.SUCCESS,
                        fields=fields,
                        source_url=source_url,
                        retrieved_at=_aware(datetime.now(timezone.utc)),
                        source_version=self.config.get("source_version"),
                        match_method=identity.match_method,
                        query_scope="official_catchment_locator",
                        raw_snapshot_path=snapshot.raw_path if snapshot else None,
                    )
                )
            else:
                rows.append(
                    OfficialLookupRow(
                        property_id=record.property_id,
                        status=identity.status,
                        source_url=source_url,
                        retrieved_at=_aware(datetime.now(timezone.utc)),
                        source_version=self.config.get("source_version"),
                        raw_snapshot_path=snapshot.raw_path if snapshot else None,
                        warnings=_identity_warnings(record, candidates, identity),
                    )
                )
        source_run = fetched.source_run.model_copy(update={
            "records": [
                {
                    "property_id": row.property_id,
                    "status": row.status.value,
                    "fields": row.fields,
                    "source_url": row.source_url,
                    "match_method": row.match_method,
                    "query_scope": row.query_scope,
                    "raw_snapshot_path": row.raw_snapshot_path,
                    "warnings": row.warnings,
                }
                for row in rows
            ]
        })
        return OfficialEnrichmentBatch(source_run=source_run, rows=rows)


class SchoolPerformanceAdapter:
    """Read official school-level FSA results for identified catchments.

    The catchment adapter identifies the school; this adapter joins that
    official name to the current DataBC FSA extract.  It intentionally emits a
    proxy score, not a school ranking or a guarantee of educational outcome.
    """

    def __init__(
        self,
        *,
        name: str,
        source_registry_id: str,
        source_url: str,
        raw_snapshot_dir: str | Path,
        config: Mapping[str, Any] | None = None,
        transport: Callable[[Mapping[str, Any]], OfficialHttpResponse] | None = None,
    ) -> None:
        self.name = name
        self.source_registry_id = source_registry_id
        self.source_url = source_url
        self.config = dict(config or {})
        self.source_type = SourceType.OFFICIAL_GOV
        self.http = OfficialHttpTransport(
            name=name,
            source_registry_id=source_registry_id,
            source_type=SourceType.OFFICIAL_GOV,
            source_url=source_url,
            raw_snapshot_dir=raw_snapshot_dir,
            config=self.config,
            transport=transport,
        )

    @staticmethod
    def _key(value: Any) -> str:
        text = re.sub(r"[^a-z0-9]+", " ", str(value or "").lower())
        text = re.sub(r"\b(?:community|school|elementary|secondary|the)\b", " ", text)
        return " ".join(text.split())

    @classmethod
    def _matches(cls, catchment: Any, school_name: Any) -> bool:
        left = cls._key(catchment)
        right = cls._key(school_name)
        return bool(left and right and (left == right or left in right or right in left))

    @staticmethod
    def _rate(row: Mapping[str, Any]) -> float | None:
        def number(key: str) -> float | None:
            value = row.get(key)
            if value in (None, "", "Msk"):
                return None
            try:
                return float(str(value).replace(",", ""))
            except ValueError:
                return None

        expected = number("NUMBER_EXPECTED_WRITERS")
        ontrack = number("NUMBER_ONTRACK")
        extending = number("NUMBER_EXTENDING")
        if expected is None or expected <= 0 or ontrack is None:
            return None
        # DataBC masks small cohorts and sometimes suppresses the extending
        # count even when the on-track count is public.  In that case retain
        # an auditable lower-bound proxy instead of turning the whole school
        # dataset into UNKNOWN.
        numerator = ontrack if extending is None else ontrack + extending
        return max(0.0, min(1.0, numerator / expected))

    @staticmethod
    def _rate_basis(row: Mapping[str, Any]) -> str:
        value = row.get("NUMBER_EXTENDING")
        if value in (None, "", "Msk"):
            return "ONTRACK_ONLY_EXTENDING_SUPPRESSED"
        return "ONTRACK_PLUS_EXTENDING"

    @classmethod
    def _school_rows(cls, content: bytes) -> list[dict[str, Any]]:
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError:
            # The current DataBC extract is published as Windows-1252 even
            # though most school names are ASCII.  Accented names must not
            # make the entire province-wide dataset fail to parse.
            text = content.decode("cp1252")
        return list(csv.DictReader(io.StringIO(text)))

    def _request(self, records: list[PropertyRecord]) -> dict[str, Any]:
        return {
            "url": str(self.config.get("dataset_url") or self.source_url),
            "method": "GET",
            "property_id": "__school_performance_dataset__",
            "query_step": "school_performance_dataset",
        }

    def _collect_school_performance(
        self,
        records: list[PropertyRecord],
        observed_at: datetime,
    ) -> OfficialEnrichmentBatch:
        request = self._request(records)
        fetched = self.http.fetch_all([request], observed_at=observed_at)
        response = next((item for item in fetched.responses if item.request and item.request.get("property_id") == request["property_id"]), None)
        snapshot = _snapshot_for_request(fetched, request, response)
        if response is None:
            rows = [OfficialLookupRow(
                property_id=record.property_id,
                status=OfficialQueryStatus.BLOCKED if fetched.status is SourceRunStatus.BLOCKED_POLICY else OfficialQueryStatus.FAILED,
                source_url=request["url"],
                source_version=self.config.get("source_version"),
                raw_snapshot_path=snapshot.raw_path if snapshot else None,
                warnings=list(fetched.errors) or ["official school performance dataset was not returned"],
            ) for record in records]
        else:
            try:
                all_rows = self._school_rows(response.content)
            except (UnicodeDecodeError, csv.Error) as exc:
                all_rows = []
                parse_warning = f"{type(exc).__name__}: {exc}"
            else:
                parse_warning = None
            latest_year = str(self.config.get("latest_school_year") or "2025/2026")
            rows = []
            for record in records:
                elementary = record.official_elementary_catchment or (
                    record.model_extra.get("official_elementary_catchment") if record.model_extra else None
                )
                secondary = record.official_secondary_catchment or (
                    record.model_extra.get("official_secondary_catchment") if record.model_extra else None
                )
                fields: dict[str, Any] = {"source_url": response.url}
                warnings: list[str] = []
                matches = []
                for level, catchment in (("elementary", elementary), ("secondary", secondary)):
                    candidates = [
                        item for item in all_rows
                        if str(item.get("SCHOOL_YEAR") or "") == latest_year
                        and str(item.get("DATA_LEVEL") or "").lower() == "school level"
                        and str(item.get("PUBLIC_OR_INDEPENDENT") or "").lower() in {
                            "public school",
                            "bc public school",
                        }
                        and str(item.get("SUB_POPULATION") or "").lower() == "all students"
                        and str(item.get("FSA_SKILL_CODE") or "").lower() in {"literacy", "numeracy"}
                        and str(item.get("GRADE") or "") in ({"04", "4"} if level == "elementary" else {"07", "7"})
                        and self._matches(catchment, item.get("SCHOOL_NAME"))
                    ]
                    rates = [rate for rate in (self._rate(item) for item in candidates) if rate is not None]
                    if rates:
                        rate = sum(rates) / len(rates)
                        field = f"official_{level}_score"
                        maximum = 7.0 if level == "elementary" else 3.0
                        fields[field] = round(rate * maximum, 2)
                        fields[f"official_{level}_performance"] = {
                            "school_name": catchment,
                            "school_year": latest_year,
                            "ontrack_or_extending_rate": round(rate, 4),
                            "rate_basis": self._rate_basis(candidates[0]),
                            "subjects": len(rates),
                        }
                        if any(
                            self._rate_basis(item) == "ONTRACK_ONLY_EXTENDING_SUPPRESSED"
                            for item in candidates
                        ):
                            warnings.append(
                                f"{level} FSA extending count was masked; proxy uses on-track count only"
                            )
                        matches.append(level)
                if parse_warning:
                    warnings.append(parse_warning)
                if matches:
                    rows.append(OfficialLookupRow(
                        property_id=record.property_id,
                        status=OfficialQueryStatus.SUCCESS,
                        fields=fields,
                        source_url=response.url,
                        retrieved_at=_aware(datetime.now(timezone.utc)),
                        source_version=self.config.get("source_version"),
                        match_method="OFFICIAL_SCHOOL_NAME+FSA_DATASET",
                        query_scope=f"FSA_{latest_year}",
                        raw_snapshot_path=snapshot.raw_path if snapshot else None,
                        warnings=warnings,
                    ))
                else:
                    rows.append(OfficialLookupRow(
                        property_id=record.property_id,
                        status=OfficialQueryStatus.NO_MATCH,
                        source_url=response.url,
                        source_version=self.config.get("source_version"),
                        raw_snapshot_path=snapshot.raw_path if snapshot else None,
                        warnings=warnings or ["identified catchment had no matching official school-level FSA row"],
                    ))
        source_run = fetched.source_run.model_copy(update={
            "records": [
                {
                    "property_id": row.property_id,
                    "status": row.status.value,
                    "fields": row.fields,
                    "source_url": row.source_url,
                    "match_method": row.match_method,
                    "query_scope": row.query_scope,
                    "raw_snapshot_path": row.raw_snapshot_path,
                    "warnings": row.warnings,
                }
                for row in rows
            ]
        })
        return OfficialEnrichmentBatch(source_run=source_run, rows=rows)

    def collect_for_records(
        self,
        records: list[PropertyRecord],
        observed_at: datetime,
    ) -> OfficialEnrichmentBatch:
        return self._collect_school_performance(records, observed_at)


class SoldHistoryAdapter(BCAssessmentAdapter):
    """Project only the registered-sales portion of BC Assessment results."""

    def collect_for_records(
        self,
        records: list[PropertyRecord],
        observed_at: datetime,
    ) -> OfficialEnrichmentBatch:
        batch = super().collect_for_records(records, observed_at)
        rows: list[OfficialLookupRow] = []
        for row in batch.rows:
            fields = {
                key: value
                for key, value in row.fields.items()
                if key in {
                    "pid",
                    "pids",
                    "number_of_pids",
                    "address",
                    "municipality",
                    "secondary_civic_addresses",
                    "legal_description",
                    "previous_sale_dates",
                    "previous_sale_prices",
                    "sold_history_scope",
                    "source_url",
                }
            }
            if row.status is OfficialQueryStatus.SUCCESS:
                # An empty result is still a successful query.  Preserve the
                # portal's time scope so UNKNOWN does not hide the fact that
                # the official window was checked and contained no displayed
                # registered sale.
                fields.setdefault("sold_history_scope", "last_three_full_calendar_years")
            rows.append(row.model_copy(update={"fields": fields, "query_scope": row.query_scope or "last_three_full_calendar_years"}))
        source_run = batch.source_run.model_copy(update={
            "records": [
                {
                    "property_id": row.property_id,
                    "status": row.status.value,
                    "fields": row.fields,
                    "source_url": row.source_url,
                    "match_method": row.match_method,
                    "query_scope": row.query_scope,
                    "raw_snapshot_path": row.raw_snapshot_path,
                    "warnings": row.warnings,
                }
                for row in rows
            ]
        })
        return OfficialEnrichmentBatch(source_run=source_run, rows=rows)
