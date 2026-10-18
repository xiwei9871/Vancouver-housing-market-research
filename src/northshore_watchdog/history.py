from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


HISTORY_SCHEMA = """
CREATE TABLE IF NOT EXISTS radar_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  radar TEXT NOT NULL,
  started_at TEXT NOT NULL,
  finished_at TEXT NOT NULL,
  status TEXT NOT NULL,
  data_status TEXT NOT NULL,
  source_files TEXT NOT NULL,
  input_fingerprint TEXT NOT NULL,
  delta TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS radar_run_listings (
  run_id INTEGER NOT NULL,
  identity_key TEXT NOT NULL,
  address TEXT NOT NULL,
  city TEXT NOT NULL,
  price INTEGER,
  grade TEXT NOT NULL,
  score_total INTEGER NOT NULL,
  payload TEXT NOT NULL,
  PRIMARY KEY (run_id, identity_key),
  FOREIGN KEY (run_id) REFERENCES radar_runs(id)
);
"""


def _iso(value: datetime | str) -> str:
    return value.isoformat() if isinstance(value, datetime) else str(value)


@dataclass(frozen=True)
class HistoryListing:
    identity_key: str
    address: str
    city: str
    price: int | None
    grade: str
    score_total: int
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "identity_key": self.identity_key,
            "address": self.address,
            "city": self.city,
            "price": self.price,
            "grade": self.grade,
            "score_total": self.score_total,
            "payload": self.payload,
        }


@dataclass(frozen=True)
class PriceChange:
    identity_key: str
    address: str
    city: str
    previous_price: int
    current_price: int
    grade: str
    score_total: int

    @property
    def amount(self) -> int:
        return self.current_price - self.previous_price

    def to_dict(self) -> dict[str, Any]:
        return {
            "identity_key": self.identity_key,
            "address": self.address,
            "city": self.city,
            "previous_price": self.previous_price,
            "current_price": self.current_price,
            "change": self.amount,
            "grade": self.grade,
            "score_total": self.score_total,
        }


@dataclass
class RunDelta:
    baseline: bool
    new: list[HistoryListing] = field(default_factory=list)
    price_reduced: list[PriceChange] = field(default_factory=list)
    price_increased: list[PriceChange] = field(default_factory=list)
    not_seen: list[HistoryListing] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "baseline": self.baseline,
            "new_count": len(self.new),
            "price_reduced_count": len(self.price_reduced),
            "price_increased_count": len(self.price_increased),
            "not_seen_count": len(self.not_seen),
            "new": [item.to_dict() for item in self.new],
            "price_reduced": [item.to_dict() for item in self.price_reduced],
            "price_increased": [item.to_dict() for item in self.price_increased],
            "not_seen": [item.to_dict() for item in self.not_seen],
        }


class RadarHistory:
    def __init__(self, path: str | Path, radar: str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.radar = radar
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(HISTORY_SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def latest_successful_run_id(self) -> int | None:
        row = self.conn.execute(
            "SELECT id FROM radar_runs "
            "WHERE radar = ? AND status = 'success' AND data_status = 'fresh' "
            "ORDER BY id DESC LIMIT 1",
            (self.radar,),
        ).fetchone()
        return int(row["id"]) if row else None

    def _latest_entries(self) -> list[HistoryListing]:
        run_id = self.latest_successful_run_id()
        if run_id is None:
            return []
        rows = self.conn.execute(
            "SELECT identity_key, address, city, price, grade, score_total, payload "
            "FROM radar_run_listings WHERE run_id = ? ORDER BY identity_key",
            (run_id,),
        ).fetchall()
        return [
            HistoryListing(
                identity_key=row["identity_key"],
                address=row["address"],
                city=row["city"],
                price=row["price"],
                grade=row["grade"],
                score_total=row["score_total"],
                payload=json.loads(row["payload"]),
            )
            for row in rows
        ]

    def compare(self, entries: Iterable[HistoryListing]) -> RunDelta:
        current_by_key = {entry.identity_key: entry for entry in entries}
        previous_run_exists = self.latest_successful_run_id() is not None
        previous_by_key = {
            entry.identity_key: entry for entry in self._latest_entries()
        }
        if not previous_run_exists:
            return RunDelta(baseline=True)

        new = [
            current_by_key[key]
            for key in sorted(current_by_key)
            if key not in previous_by_key
        ]
        price_reduced: list[PriceChange] = []
        price_increased: list[PriceChange] = []
        for key in sorted(current_by_key.keys() & previous_by_key.keys()):
            previous = previous_by_key[key]
            current = current_by_key[key]
            if previous.price is None or current.price is None or previous.price == current.price:
                continue
            change = PriceChange(
                identity_key=key,
                address=current.address,
                city=current.city,
                previous_price=previous.price,
                current_price=current.price,
                grade=current.grade,
                score_total=current.score_total,
            )
            if current.price < previous.price:
                price_reduced.append(change)
            else:
                price_increased.append(change)

        not_seen = [
            previous_by_key[key]
            for key in sorted(previous_by_key)
            if key not in current_by_key
        ]
        return RunDelta(
            baseline=False,
            new=new,
            price_reduced=price_reduced,
            price_increased=price_increased,
            not_seen=not_seen,
        )

    def record_success(
        self,
        started_at: datetime | str,
        finished_at: datetime | str,
        source_files: list[dict[str, Any]],
        input_fingerprint: str,
        entries: Iterable[HistoryListing],
        delta: RunDelta,
    ) -> int:
        return self.record_run(
            started_at=started_at,
            finished_at=finished_at,
            status="success",
            data_status="fresh",
            source_files=source_files,
            input_fingerprint=input_fingerprint,
            entries=entries,
            delta=delta,
        )

    def record_run(
        self,
        started_at: datetime | str,
        finished_at: datetime | str,
        status: str,
        data_status: str,
        source_files: list[dict[str, Any]],
        input_fingerprint: str,
        entries: Iterable[HistoryListing],
        delta: RunDelta | None,
    ) -> int:
        materialized = list(entries)
        delta_payload = delta.to_dict() if delta is not None else {}
        cursor = self.conn.cursor()
        try:
            cursor.execute(
                "INSERT INTO radar_runs "
                "(radar, started_at, finished_at, status, data_status, source_files, input_fingerprint, delta) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    self.radar,
                    _iso(started_at),
                    _iso(finished_at),
                    status,
                    data_status,
                    json.dumps(source_files, ensure_ascii=False, sort_keys=True),
                    input_fingerprint,
                    json.dumps(delta_payload, ensure_ascii=False, sort_keys=True),
                ),
            )
            run_id = int(cursor.lastrowid)
            cursor.executemany(
                "INSERT INTO radar_run_listings "
                "(run_id, identity_key, address, city, price, grade, score_total, payload) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        run_id,
                        entry.identity_key,
                        entry.address,
                        entry.city,
                        entry.price,
                        entry.grade,
                        entry.score_total,
                        json.dumps(entry.payload, ensure_ascii=False, sort_keys=True),
                    )
                    for entry in materialized
                ],
            )
            self.conn.commit()
            return run_id
        except Exception:
            self.conn.rollback()
            raise
