from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from northshore_watchdog.normalize import canonical_url, normalized_address
from northshore_watchdog.utils.text import slug_text

from .models import RentalListing, ScoredRentalListing


SCHEMA = """
CREATE TABLE IF NOT EXISTS rental_listings (
  dedupe_key TEXT PRIMARY KEY,
  source TEXT NOT NULL,
  source_id TEXT,
  normalized_address TEXT,
  city TEXT,
  monthly_rent INTEGER,
  first_seen TEXT NOT NULL,
  last_seen TEXT NOT NULL,
  payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS scored_rental_listings (
  dedupe_key TEXT PRIMARY KEY,
  score_total INTEGER NOT NULL,
  grade TEXT NOT NULL,
  confidence TEXT NOT NULL,
  alert_level TEXT NOT NULL,
  scored_at TEXT NOT NULL,
  payload TEXT NOT NULL
);
"""


class RentalStorage:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def upsert_listing(self, listing: RentalListing) -> str:
        key = rental_dedupe_key(listing)
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        payload = listing.model_dump_json()
        self.conn.execute(
            "INSERT INTO rental_listings(dedupe_key, source, source_id, normalized_address, city, monthly_rent, first_seen, last_seen, payload) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(dedupe_key) DO UPDATE SET monthly_rent=excluded.monthly_rent, last_seen=excluded.last_seen, payload=excluded.payload",
            (key, listing.source, listing.source_id, normalized_address(listing.address), listing.city, listing.monthly_rent, now, now, payload),
        )
        self.conn.commit()
        return key

    def list_listings(self) -> list[RentalListing]:
        rows = self.conn.execute("SELECT payload FROM rental_listings ORDER BY last_seen DESC").fetchall()
        return [RentalListing.model_validate_json(row["payload"]) for row in rows]

    def upsert_scored(self, scored: ScoredRentalListing) -> str:
        key = rental_dedupe_key(scored.listing)
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        self.conn.execute(
            "INSERT INTO scored_rental_listings(dedupe_key, score_total, grade, confidence, alert_level, scored_at, payload) VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(dedupe_key) DO UPDATE SET score_total=excluded.score_total, grade=excluded.grade, confidence=excluded.confidence, alert_level=excluded.alert_level, scored_at=excluded.scored_at, payload=excluded.payload",
            (key, scored.score_total, scored.grade, scored.confidence, scored.alert_level, now, scored.model_dump_json()),
        )
        self.conn.commit()
        return key

    def list_scored(self) -> list[ScoredRentalListing]:
        rows = self.conn.execute("SELECT payload FROM scored_rental_listings ORDER BY score_total DESC").fetchall()
        return [ScoredRentalListing.model_validate_json(row["payload"]) for row in rows]


def rental_dedupe_key(listing: RentalListing) -> str:
    if listing.url:
        return f"url:{canonical_url(listing.url)}"
    return f"address:{normalized_address(listing.address)}|{slug_text(listing.city)}"
