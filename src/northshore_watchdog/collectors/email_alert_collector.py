from __future__ import annotations

import re
from email import policy
from email.parser import BytesParser
from pathlib import Path
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from northshore_watchdog.config import resolve_path
from northshore_watchdog.monitoring import SnapshotMetadata, snapshot_metadata
from northshore_watchdog.models import Listing
from northshore_watchdog.utils.money import parse_money
from northshore_watchdog.utils.text import clean_text

from .base import Collector


class EmailAlertCollector(Collector):
    source_name = "email_alert"

    def collect(self) -> list[Listing]:
        input_dir = resolve_path(self.config.get("input_dir", "data/email_alerts"))
        if not input_dir.exists():
            return []
        listings: list[Listing] = []
        for path in self._paths(input_dir):
            text = self._read_text(path)
            listings.extend(self._parse_text(text, path, snapshot_metadata(path, self.source_name)))
        return listings

    def _paths(self, input_dir: Path) -> list[Path]:
        selected = self.config.get("selected_files")
        if selected is not None:
            return [resolve_path(path) for path in selected if resolve_path(path).is_file()]
        if not input_dir.exists():
            return []
        return [
            path for path in sorted(input_dir.iterdir())
            if path.suffix.lower() in {".eml", ".html", ".htm", ".txt"}
        ]

    def _read_text(self, path: Path) -> str:
        if path.suffix.lower() == ".eml":
            message = BytesParser(policy=policy.default).parsebytes(path.read_bytes())
            parts = []
            for part in message.walk():
                if part.get_content_type() in {"text/plain", "text/html"}:
                    parts.append(part.get_content())
            return "\n".join(str(part) for part in parts)
        return path.read_text(encoding="utf-8", errors="ignore")

    def _parse_text(self, text: str, path: Path, metadata: SnapshotMetadata | None = None) -> list[Listing]:
        metadata = metadata or snapshot_metadata(path, self.source_name)
        soup = BeautifulSoup(text, "html.parser")
        visible_text = clean_text(soup.get_text(" ") if soup.find() else text)
        urls = [
            url for url in re.findall(r"https?://[^\s<>\"]+", text)
            if _allowed_url(url, self.config.get("allowed_domains", []))
        ]
        blocks = re.split(r"(?:\n\s*\n|Listing:)", text)
        listings: list[Listing] = []
        for idx, block in enumerate(blocks):
            if not block.strip():
                continue
            listing = self._parse_block(block, urls[idx] if idx < len(urls) else (urls[0] if urls else None), path, idx, metadata)
            if listing:
                listings.append(listing)
        if not listings and urls:
            listing = self._parse_block(visible_text, urls[0], path, 0, metadata)
            if listing:
                listings.append(listing)
        return listings

    def _parse_block(self, block: str, url: str | None, path: Path, idx: int, metadata: SnapshotMetadata) -> Listing | None:
        text = clean_text(BeautifulSoup(block, "html.parser").get_text(" "))
        address_match = re.search(r"([0-9]{1,6}\s+[A-Za-z0-9 .'-]+?\s(?:Avenue|Ave|Street|St|Road|Rd|Drive|Dr|Lane|Place|Way))", text, re.I)
        city_match = re.search(r"\b(West Vancouver|North Vancouver)\b", text, re.I)
        price_match = re.search(r"\$[0-9,.]+(?:\s*[mk])?", text, re.I)
        if not address_match or not city_match:
            return None
        return Listing(
            source=self.source_name,
            source_id=f"{path.name}:{idx}",
            url=url,
            address=clean_text(address_match.group(1)),
            city=clean_text(city_match.group(1)),
            list_price=parse_money(price_match.group(0)) if price_match else None,
            last_seen_date=metadata.observed_at,
            description=text[:1000],
            raw_data={
                "file": str(path),
                "raw_text": text,
                "snapshot_path": metadata.path,
                "snapshot_observed_at": metadata.observed_at.isoformat(),
                "snapshot_sha256": metadata.sha256,
            },
        )


def _allowed_url(url: str | None, allowed_domains: list[str]) -> bool:
    if not allowed_domains:
        return True
    if not url:
        return False
    host = urlparse(url).netloc.lower()
    return any(host == domain.lower() or host.endswith(f".{domain.lower()}") for domain in allowed_domains)
