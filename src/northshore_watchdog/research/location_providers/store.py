from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .base import LocationSnapshot


class LocationSnapshotStore:
    """Small append-only JSONL store for provider and composite snapshots."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def append(self, snapshot: LocationSnapshot) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(snapshot.model_dump_json() + "\n")

    def all(self) -> list[LocationSnapshot]:
        if not self.path.is_file():
            return []
        snapshots: list[LocationSnapshot] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                snapshots.append(LocationSnapshot.model_validate(json.loads(line)))
        return snapshots

    def latest(self, property_id: str) -> LocationSnapshot | None:
        matches = [snapshot for snapshot in self.all() if snapshot.property_id == property_id]
        return max(matches, key=lambda item: item.queried_at) if matches else None

    @staticmethod
    def should_refresh(
        snapshot: LocationSnapshot | None,
        *,
        now: datetime | None = None,
        ttl_days: int = 180,
    ) -> bool:
        if snapshot is None:
            return True
        if snapshot.walk_score.status in {"NOT_CONFIGURED", "FAILED", "INVALID_REQUEST"}:
            return True
        current = now or datetime.now(timezone.utc)
        queried = snapshot.queried_at
        if queried.tzinfo is None or queried.utcoffset() is None:
            queried = queried.replace(tzinfo=timezone.utc)
        return current - queried >= timedelta(days=ttl_days)
