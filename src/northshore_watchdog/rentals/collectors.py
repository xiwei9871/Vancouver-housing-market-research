from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from northshore_watchdog.config import resolve_path
from northshore_watchdog.monitoring import SnapshotMetadata, snapshot_metadata
from northshore_watchdog.utils.money import parse_money
from northshore_watchdog.utils.text import clean_text

from .models import RentalListing


class RentalSnapshotCollector:
    source_name = "rental_snapshot"

    def __init__(self, config: dict):
        self.config = config

    def collect(self) -> list[RentalListing]:
        listings: list[RentalListing] = []
        for path in self._paths():
            metadata = snapshot_metadata(path, self.source_name)
            if path.suffix.lower() in {".html", ".htm"}:
                listings.extend(self._parse_html(path, metadata))
            elif path.suffix.lower() in {".txt", ".eml"}:
                listings.extend(self._parse_links(path, metadata))
        return listings

    def _paths(self) -> list[Path]:
        selected = self.config.get("selected_files")
        if selected is not None:
            return [resolve_path(path) for path in selected if resolve_path(path).is_file()]
        paths: list[Path] = []
        for input_dir in self._input_dirs():
            if input_dir.exists():
                paths.extend(sorted(input_dir.glob("*")))
        return [path for path in paths if path.suffix.lower() in {".html", ".htm", ".txt", ".eml"}]

    def _input_dirs(self) -> list[Path]:
        input_dirs = self.config.get("input_dirs")
        if input_dirs:
            return [resolve_path(path) for path in input_dirs]
        return [resolve_path(self.config.get("input_dir", "data/rentals/snapshots"))]

    def _parse_html(self, path: Path, metadata: SnapshotMetadata | None = None) -> list[RentalListing]:
        metadata = metadata or snapshot_metadata(path, self.source_name)
        soup = BeautifulSoup(path.read_text(encoding="utf-8", errors="ignore"), "html.parser")
        cards = soup.select("[data-rental-card], .rental-card, article")
        listings: list[RentalListing] = []
        for idx, card in enumerate(cards):
            href = card.get("data-url") or (card.find("a") or {}).get("href")
            if not _allowed_url(href, self.config.get("allowed_domains", [])):
                continue
            address = card.get("data-address") or _text(card, ".address")
            city = card.get("data-city") or _text(card, ".city")
            if not address or not city:
                continue
            listings.append(RentalListing(
                source=self.source_name,
                source_id=f"{path.name}:{idx}",
                url=href,
                address=clean_text(address),
                city=clean_text(city),
                neighbourhood=card.get("data-neighbourhood") or _text(card, ".neighbourhood"),
                property_type=card.get("data-property-type") or _text(card, ".property-type"),
                monthly_rent=parse_money(card.get("data-rent") or card.get("data-price") or _text(card, ".rent") or _text(card, ".price")),
                bedrooms=_float(card.get("data-bedrooms") or _text(card, ".beds")),
                bathrooms=_float(card.get("data-bathrooms") or _text(card, ".baths")),
                size_sqft=_int(card.get("data-size") or _text(card, ".size")),
                furnished=_bool(card.get("data-furnished") or _text(card, ".furnished")),
                parking=_bool(card.get("data-parking") or _text(card, ".parking")),
                transit_minutes=_int(card.get("data-transit-minutes") or _text(card, ".transit-minutes")),
                amenities=_amenities(card.get("data-amenities") or _text(card, ".amenities")),
                description=card.get("data-description") or clean_text(card.get_text(" ")),
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

    def _parse_links(self, path: Path, metadata: SnapshotMetadata | None = None) -> list[RentalListing]:
        metadata = metadata or snapshot_metadata(path, self.source_name)
        text = path.read_text(encoding="utf-8", errors="ignore")
        listings: list[RentalListing] = []
        for idx, url in enumerate(_urls(text)):
            if not _allowed_url(url, self.config.get("allowed_domains", [])):
                continue
            listings.append(RentalListing(
                source=self.source_name,
                source_id=f"{path.name}:link:{idx}",
                url=url,
                address=_address_from_url(url),
                city=_city_from_url(url),
                last_seen_date=metadata.observed_at,
                raw_data={
                    "file": str(path),
                    "text": url,
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
    match = re.search(r"\d+(?:\.\d+)?", str(value))
    return float(match.group(0)) if match else None


def _int(value: object | None) -> int | None:
    if value in (None, ""):
        return None
    match = re.search(r"\d+", str(value).replace(",", ""))
    return int(match.group(0)) if match else None


def _bool(value: object | None) -> bool | None:
    if value in (None, ""):
        return None
    text = str(value).strip().lower()
    if text in {"true", "yes", "y", "1", "included"}:
        return True
    if text in {"false", "no", "n", "0", "none"}:
        return False
    return None


def _amenities(value: object | None) -> list[str]:
    if value in (None, ""):
        return []
    return [clean_text(item).lower() for item in re.split(r"[,;/|]", str(value)) if clean_text(item)]


def _urls(text: str) -> list[str]:
    return re.findall(r"https?://[^\s<>\"']+", text)


def _allowed_url(url: str | None, allowed_domains: list[str]) -> bool:
    if not allowed_domains:
        return True
    if not url:
        return False
    host = urlparse(url).netloc.lower()
    return any(host == domain.lower() or host.endswith(f".{domain.lower()}") for domain in allowed_domains)


def _address_from_url(url: str) -> str:
    slug = urlparse(url).path.rstrip("/").split("/")[-1]
    return slug.replace("-", " ") or "unknown address"


def _city_from_url(url: str) -> str:
    path = urlparse(url).path.lower()
    if "richmond" in path:
        return "Richmond"
    if "burnaby" in path:
        return "Burnaby"
    return "Vancouver"
