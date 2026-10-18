from __future__ import annotations

import json
from pathlib import Path

from northshore_watchdog.config import resolve_path
from northshore_watchdog.models import Listing

from .base import Collector


class OfficialFeedStubCollector(Collector):
    source_name = "official_feed_stub"

    def collect(self) -> list[Listing]:
        input_path = resolve_path(self.config.get("input_path", "data/samples/official_feed_sample.json"))
        if not input_path.exists():
            return []
        payload = json.loads(input_path.read_text(encoding="utf-8"))
        rows = payload.get("listings", payload) if isinstance(payload, dict) else payload
        listings: list[Listing] = []
        for row in rows:
            data = dict(row)
            data["source"] = self.source_name
            data.setdefault("raw_data", row)
            listings.append(Listing(**data))
        return listings
