from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from .models import Listing, ScoredListing
from .normalize import listing_dedupe_key


SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
  dedupe_key TEXT PRIMARY KEY,
  source TEXT NOT NULL,
  source_id TEXT,
  normalized_address TEXT,
  city TEXT,
  list_price INTEGER,
  first_seen TEXT NOT NULL,
  last_seen TEXT NOT NULL,
  payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS scored_listings (
  dedupe_key TEXT PRIMARY KEY,
  score_total INTEGER NOT NULL,
  grade TEXT NOT NULL,
  confidence TEXT NOT NULL,
  alert_level TEXT NOT NULL,
  scored_at TEXT NOT NULL,
  payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS listing_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  dedupe_key TEXT NOT NULL,
  event_type TEXT NOT NULL,
  created_at TEXT NOT NULL,
  payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS notification_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  event_type TEXT NOT NULL,
  listing_ids TEXT NOT NULL,
  title TEXT NOT NULL,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL,
  sent_at TEXT,
  payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS source_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source TEXT NOT NULL,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  status TEXT NOT NULL,
  records_found INTEGER NOT NULL DEFAULT 0,
  error TEXT
);
"""


class Storage:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.init()

    def init(self) -> None:
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def upsert_listing(self, listing: Listing) -> str:
        key = listing_dedupe_key(listing)
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        existing = self.conn.execute("SELECT list_price FROM listings WHERE dedupe_key = ?", (key,)).fetchone()
        payload = listing.model_dump_json()
        if existing:
            if existing["list_price"] and listing.list_price and existing["list_price"] != listing.list_price:
                self.record_listing_event(key, "price_change", {"old_price": existing["list_price"], "new_price": listing.list_price})
            self.conn.execute(
                "UPDATE listings SET list_price=?, last_seen=?, payload=? WHERE dedupe_key=?",
                (listing.list_price, now, payload, key),
            )
        else:
            self.conn.execute(
                "INSERT INTO listings(dedupe_key, source, source_id, normalized_address, city, list_price, first_seen, last_seen, payload) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (key, listing.source, listing.source_id, listing.address.lower(), listing.city, listing.list_price, now, now, payload),
            )
            self.record_listing_event(key, "first_seen", {"listing": listing.model_dump(mode="json")})
        self.conn.commit()
        return key

    def list_listings(self) -> list[Listing]:
        rows = self.conn.execute("SELECT payload FROM listings ORDER BY last_seen DESC").fetchall()
        return [Listing.model_validate_json(row["payload"]) for row in rows]

    def upsert_scored(self, scored: ScoredListing) -> str:
        key = listing_dedupe_key(scored.listing)
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        existing = self.conn.execute("SELECT grade, score_total FROM scored_listings WHERE dedupe_key = ?", (key,)).fetchone()
        if existing and existing["grade"] != "A" and scored.grade == "A":
            self.record_listing_event(key, "score_upgrade", {"old_grade": existing["grade"], "new_grade": "A"})
        self.conn.execute(
            "INSERT INTO scored_listings(dedupe_key, score_total, grade, confidence, alert_level, scored_at, payload) VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(dedupe_key) DO UPDATE SET score_total=excluded.score_total, grade=excluded.grade, confidence=excluded.confidence, alert_level=excluded.alert_level, scored_at=excluded.scored_at, payload=excluded.payload",
            (key, scored.score_total, scored.grade, scored.confidence, scored.alert_level, now, scored.model_dump_json()),
        )
        self.conn.commit()
        return key

    def list_scored(self) -> list[ScoredListing]:
        rows = self.conn.execute("SELECT payload FROM scored_listings ORDER BY score_total DESC").fetchall()
        return [ScoredListing.model_validate_json(row["payload"]) for row in rows]

    def record_listing_event(self, key: str, event_type: str, payload: dict) -> None:
        self.conn.execute(
            "INSERT INTO listing_events(dedupe_key, event_type, created_at, payload) VALUES (?, ?, ?, ?)",
            (key, event_type, datetime.now().astimezone().isoformat(timespec="seconds"), json.dumps(payload, ensure_ascii=False)),
        )

    def record_notification(self, event_type: str, listing_ids: list[str], title: str, status: str, payload: dict, sent_at: str | None = None) -> None:
        self.conn.execute(
            "INSERT INTO notification_events(event_type, listing_ids, title, status, created_at, sent_at, payload) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                event_type,
                json.dumps(listing_ids),
                title,
                status,
                datetime.now().astimezone().isoformat(timespec="seconds"),
                sent_at,
                json.dumps(payload, ensure_ascii=False),
            ),
        )
        self.conn.commit()
