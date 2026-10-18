from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from northshore_watchdog.config import resolve_path
from northshore_watchdog.monitoring import SnapshotMetadata, snapshot_metadata
from northshore_watchdog.models import Listing
from northshore_watchdog.utils.money import parse_money
from northshore_watchdog.utils.text import clean_text

from .base import Collector


class SearchPageSnapshotCollector(Collector):
    source_name = "search_page_snapshot"

    def collect(self) -> list[Listing]:
        listings: list[Listing] = []
        for path in self._paths():
            listings.extend(self._parse_file(path, snapshot_metadata(path, self.source_name)))
        return listings

    def _paths(self) -> list[Path]:
        selected = self.config.get("selected_files")
        if selected is not None:
            return [resolve_path(path) for path in selected if resolve_path(path).is_file()]
        paths: list[Path] = []
        for input_dir in self._input_dirs():
            if input_dir.exists():
                paths.extend(sorted(input_dir.glob("*.html")))
                paths.extend(sorted(input_dir.glob("*.htm")))
        return paths

    def _input_dirs(self) -> list[Path]:
        input_dirs = self.config.get("input_dirs")
        if input_dirs:
            return [resolve_path(path) for path in input_dirs]
        return [resolve_path(self.config.get("input_dir", "data/search_snapshots"))]

    def _parse_file(self, path: Path, metadata: SnapshotMetadata | None = None) -> list[Listing]:
        metadata = metadata or snapshot_metadata(path, self.source_name)
        soup = BeautifulSoup(path.read_text(encoding="utf-8", errors="ignore"), "html.parser")
        cards = soup.select("[data-listing-card], .listing-card, article")
        listings: list[Listing] = []
        for idx, card in enumerate(cards):
            address = card.get("data-address") or _text(card, ".address")
            city = card.get("data-city") or _text(card, ".city")
            href = card.get("data-url") or (card.find("a") or {}).get("href")
            if not address or not city:
                continue
            if not _allowed_url(href, self.config.get("allowed_domains", [])):
                continue
            listings.append(Listing(
                source=self.source_name,
                source_id=f"{path.name}:{idx}",
                mls_number=_mls_number(card.get("data-mls-number") or _text(card, ".mls-number") or href or card.get_text(" ")),
                url=href,
                address=clean_text(address),
                city=clean_text(city),
                neighbourhood=card.get("data-neighbourhood") or _text(card, ".neighbourhood"),
                property_type=card.get("data-property-type") or _text(card, ".property-type"),
                tenure=card.get("data-tenure") or _text(card, ".tenure"),
                listing_status=card.get("data-status") or _text(card, ".status"),
                list_price=parse_money(card.get("data-price") or _text(card, ".price")),
                bedrooms=_float(card.get("data-bedrooms") or _text(card, ".beds")),
                bathrooms=_float(card.get("data-bathrooms") or _text(card, ".baths")),
                house_size_sqft=_int(card.get("data-house-size") or _text(card, ".house-size")),
                lot_size_sqft=_int(card.get("data-lot-size") or _text(card, ".lot-size")),
                frontage_ft=_float(card.get("data-frontage") or _text(card, ".frontage")),
                year_built=_int(card.get("data-year-built") or _text(card, ".year-built")),
                zoning=card.get("data-zoning") or _text(card, ".zoning"),
                description=card.get("data-description") or _text(card, ".description"),
                last_seen_date=metadata.observed_at,
                raw_data={
                    "file": str(path),
                    "text": clean_text(card.get_text(" ")),
                    "snapshot_path": metadata.path,
                    "snapshot_observed_at": metadata.observed_at.isoformat(),
                    "snapshot_sha256": metadata.sha256,
                },
            ))
        return listings


def _text(node, selector: str) -> str | None:
    found = node.select_one(selector)
    return clean_text(found.get_text(" ")) if found else None


def _float(value: object | None) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(str(value).split()[0])
    except ValueError:
        return None


def _int(value: object | None) -> int | None:
    if value in (None, ""):
        return None
    text = str(value).replace(",", "")
    digits = "".join(ch for ch in text if ch.isdigit())
    return int(digits) if digits else None


def _mls_number(value: object | None) -> str | None:
    if value in (None, ""):
        return None
    match = re.search(r"\bR\d{7}\b", str(value), re.I)
    return match.group(0).upper() if match else None


def _allowed_url(url: str | None, allowed_domains: list[str]) -> bool:
    if not allowed_domains:
        return True
    if not url:
        return False
    host = urlparse(url).netloc.lower()
    return any(host == domain.lower() or host.endswith(f".{domain.lower()}") for domain in allowed_domains)
