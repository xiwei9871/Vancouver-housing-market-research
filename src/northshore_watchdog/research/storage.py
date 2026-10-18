from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .models import (
    CandidateStatus,
    PropertyObservation,
    PropertyRecord,
    RedevelopmentScenario,
    ScoreSnapshot,
    WatchlistEvent,
    VerificationItem,
)
from .provenance import DataConflict, SourceRecord, SourceRegistryEntry
from .sources import SourceRunResult
from .enrichment import EnrichedPropertyResult
from .delta import diff_property_records
from .normalization import normalize_address


SCHEMA = """
CREATE TABLE IF NOT EXISTS properties (
  property_id TEXT PRIMARY KEY,
  address TEXT NOT NULL,
  municipality TEXT NOT NULL,
  neighbourhood TEXT,
  current_observation_id TEXT,
  first_seen TEXT NOT NULL,
  last_seen TEXT NOT NULL,
  current_payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS property_observations (
  observation_id TEXT PRIMARY KEY,
  property_id TEXT NOT NULL,
  observed_at TEXT NOT NULL,
  source_registry_id TEXT NOT NULL,
  weekly_run_id INTEGER,
  parser_version TEXT NOT NULL,
  raw_payload TEXT NOT NULL,
  normalized_record TEXT NOT NULL,
  normalization_warnings TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS field_provenance (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  property_id TEXT NOT NULL,
  field_name TEXT NOT NULL,
  source_url TEXT NOT NULL,
  source_type TEXT NOT NULL,
  source_registry_id TEXT,
  source_run_id INTEGER,
  weekly_run_id INTEGER,
  retrieved_at TEXT NOT NULL,
  raw_value TEXT,
  normalized_value TEXT,
  source_version TEXT,
  evidence_status TEXT NOT NULL,
  notes TEXT
);
CREATE TABLE IF NOT EXISTS data_conflicts (
  conflict_id TEXT PRIMARY KEY,
  property_id TEXT NOT NULL,
  field_name TEXT NOT NULL,
  conflict_type TEXT NOT NULL,
  source_values TEXT NOT NULL,
  conflicting_sources TEXT NOT NULL,
  detected_at TEXT NOT NULL,
  resolution_status TEXT NOT NULL,
  resolution_note TEXT
);
CREATE TABLE IF NOT EXISTS data_conflict_history (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  conflict_id TEXT NOT NULL,
  property_id TEXT NOT NULL,
  field_name TEXT NOT NULL,
  conflict_type TEXT NOT NULL,
  source_values TEXT NOT NULL,
  conflicting_sources TEXT NOT NULL,
  detected_at TEXT NOT NULL,
  recorded_at TEXT NOT NULL,
  resolution_status TEXT NOT NULL,
  resolution_note TEXT,
  weekly_run_id INTEGER
);
CREATE TABLE IF NOT EXISTS comparable_records (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  property_id TEXT NOT NULL,
  comp_id TEXT,
  observed_at TEXT NOT NULL,
  weekly_run_id INTEGER,
  source_registry_id TEXT,
  source_version TEXT,
  payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS redevelopment_scenarios (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  property_id TEXT NOT NULL,
  scenario_type TEXT NOT NULL,
  recorded_at TEXT NOT NULL,
  weekly_run_id INTEGER,
  source_registry_id TEXT,
  source_version TEXT,
  payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS score_snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  property_id TEXT NOT NULL,
  generated_at TEXT NOT NULL,
  scoring_version TEXT NOT NULL,
  config_hash TEXT NOT NULL,
  weekly_run_id INTEGER,
  source_run_id INTEGER,
  source_registry_id TEXT,
  total_score REAL NOT NULL,
  live_score REAL NOT NULL,
  land_score REAL NOT NULL,
  investment_score REAL NOT NULL,
  data_confidence_score REAL NOT NULL,
  payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS watchlist_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  property_id TEXT NOT NULL,
  event_type TEXT NOT NULL,
  created_at TEXT NOT NULL,
  weekly_run_id INTEGER,
  source_run_id INTEGER,
  source_registry_id TEXT,
  payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS source_registry (
  source_registry_id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  source_type TEXT NOT NULL,
  domain TEXT,
  access_method TEXT,
  authorization_status TEXT NOT NULL,
  evidence_status TEXT NOT NULL DEFAULT 'UNCONFIRMED',
  reliability_notes TEXT,
  reviewed_at TEXT,
  enabled INTEGER NOT NULL DEFAULT 1,
  last_run_status TEXT,
  last_run_at TEXT,
  payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS weekly_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_type TEXT NOT NULL,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  status TEXT NOT NULL,
  summary TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS source_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  weekly_run_id INTEGER,
  source_registry_id TEXT NOT NULL,
  source_name TEXT NOT NULL,
  started_at TEXT NOT NULL,
  finished_at TEXT NOT NULL,
  status TEXT NOT NULL,
  payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS source_snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_run_id INTEGER NOT NULL,
  source_registry_id TEXT NOT NULL,
  url TEXT NOT NULL,
  final_url TEXT,
  method TEXT NOT NULL,
  request_kind TEXT NOT NULL DEFAULT 'target',
  status_code INTEGER NOT NULL,
  content_type TEXT,
  raw_path TEXT NOT NULL,
  screenshot_path TEXT,
  sha256 TEXT NOT NULL,
  size_bytes INTEGER NOT NULL,
  observed_at TEXT NOT NULL,
  retrieved_at TEXT NOT NULL,
  source_version TEXT,
  payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS enrichment_observations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  property_id TEXT NOT NULL,
  provider_name TEXT NOT NULL,
  observed_at TEXT NOT NULL,
  status TEXT NOT NULL,
  weekly_run_id INTEGER,
  source_registry_id TEXT,
  source_version TEXT,
  payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS identity_aliases (
  alias_key TEXT PRIMARY KEY,
  alias_type TEXT NOT NULL,
  alias_value TEXT NOT NULL,
  property_id TEXT NOT NULL,
  first_seen TEXT NOT NULL,
  last_seen TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS identity_alias_observations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  alias_key TEXT NOT NULL,
  alias_type TEXT NOT NULL,
  alias_value TEXT NOT NULL,
  property_id TEXT NOT NULL,
  source_registry_id TEXT,
  source_run_id INTEGER,
  weekly_run_id INTEGER,
  observed_at TEXT NOT NULL,
  source_url TEXT
);
CREATE INDEX IF NOT EXISTS idx_identity_alias_observations_alias
  ON identity_alias_observations(alias_key);
CREATE TABLE IF NOT EXISTS verification_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  verification_id TEXT NOT NULL,
  property_id TEXT NOT NULL,
  source_registry_id TEXT NOT NULL,
  source_url TEXT NOT NULL,
  status TEXT NOT NULL,
  reason TEXT NOT NULL,
  match_method TEXT,
  raw_snapshot_path TEXT,
  observed_at TEXT NOT NULL,
  weekly_run_id INTEGER,
  source_run_id INTEGER,
  details TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_verification_items_property
  ON verification_items(property_id, observed_at);
"""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None = None) -> str:
    return (value or _now()).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _projection_value_present(value: Any) -> bool:
    if value in (None, "", [], {}):
        return False
    return getattr(value, "value", None) != "UNKNOWN"


def _merge_current_projection(
    existing: PropertyRecord,
    incoming: PropertyRecord,
) -> PropertyRecord:
    """Merge sparse stage observations without erasing trusted current facts."""

    updates: dict[str, Any] = {}
    for field_name in PropertyRecord.model_fields:
        if field_name not in incoming.model_fields_set:
            continue
        value = getattr(incoming, field_name)
        if not _projection_value_present(value):
            continue
        if field_name in {"data_conflict", "major_data_conflict"}:
            if value is True:
                updates[field_name] = True
            elif field_name in incoming.model_fields_set:
                # A fresh normalized observation may explicitly resolve a
                # previously persisted conflict. Do not keep stale veto flags
                # in the current projection after that resolution.
                updates[field_name] = False
            continue
        if field_name == "manual_verification_required":
            if value is False and existing.manual_verification_required:
                continue
        if field_name == "manual_verification_reasons":
            if not value and existing.manual_verification_reasons:
                continue
            if value:
                updates[field_name] = list(dict.fromkeys([
                    *existing.manual_verification_reasons,
                    *value,
                ]))
                continue
        if field_name in {"acquisition_channel", "feed_class", "data_verification_status"}:
            existing_value = getattr(existing, field_name)
            if str(value).upper() == "UNKNOWN" and str(existing_value).upper() != "UNKNOWN":
                continue
        if field_name == "candidate_status" and (
            value is CandidateStatus.NEED_VERIFICATION
            and existing.candidate_status is not CandidateStatus.NEED_VERIFICATION
            and not (
                # Scoring writes a fresh status together with the score
                # projection.  Let that status replace a stale hard-veto
                # state, even when the resolved conflict flags were already
                # cleared in an earlier stage.
                {"last_updated", "total_score", "live_score", "land_score", "investment_score", "data_confidence_score"}
                & incoming.model_fields_set
            )
        ):
            continue
        updates[field_name] = value
    return existing.model_copy(update=updates)


def _parse_iso(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo and parsed.utcoffset() is not None else parsed.replace(tzinfo=timezone.utc)


class ResearchStorage:
    """Append-only evidence store with a small current-property projection."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection_target = ":memory:" if str(path) == ":memory:" else self.path
        self.conn = sqlite3.connect(connection_target)
        self.conn.row_factory = sqlite3.Row
        self.init()

    def init(self) -> None:
        self.conn.executescript(SCHEMA)
        self._ensure_column("field_provenance", "source_registry_id", "TEXT")
        self._ensure_column("field_provenance", "source_run_id", "INTEGER")
        self._ensure_column("field_provenance", "weekly_run_id", "INTEGER")
        self._ensure_column("source_registry", "enabled", "INTEGER NOT NULL DEFAULT 1")
        self._ensure_column("source_registry", "evidence_status", "TEXT NOT NULL DEFAULT 'UNCONFIRMED'")
        self._ensure_column("source_registry", "last_run_status", "TEXT")
        self._ensure_column("source_registry", "last_run_at", "TEXT")
        self._ensure_column("source_runs", "weekly_run_id", "INTEGER")
        self._ensure_column("source_snapshots", "request_kind", "TEXT NOT NULL DEFAULT 'target'")
        self._ensure_column("source_snapshots", "screenshot_path", "TEXT")
        self._ensure_column("property_observations", "weekly_run_id", "INTEGER")
        self._ensure_column("comparable_records", "weekly_run_id", "INTEGER")
        self._ensure_column("comparable_records", "source_registry_id", "TEXT")
        self._ensure_column("comparable_records", "source_version", "TEXT")
        self._ensure_column("redevelopment_scenarios", "weekly_run_id", "INTEGER")
        self._ensure_column("redevelopment_scenarios", "source_registry_id", "TEXT")
        self._ensure_column("redevelopment_scenarios", "source_version", "TEXT")
        self._ensure_column("watchlist_events", "weekly_run_id", "INTEGER")
        self._ensure_column("watchlist_events", "source_run_id", "INTEGER")
        self._ensure_column("watchlist_events", "source_registry_id", "TEXT")
        self._ensure_column("enrichment_observations", "weekly_run_id", "INTEGER")
        self._ensure_column("enrichment_observations", "source_registry_id", "TEXT")
        self._ensure_column("enrichment_observations", "source_version", "TEXT")
        self._ensure_column("score_snapshots", "weekly_run_id", "INTEGER")
        self._ensure_column("score_snapshots", "source_run_id", "INTEGER")
        self._ensure_column("score_snapshots", "source_registry_id", "TEXT")
        self.conn.commit()

    def _ensure_column(self, table: str, column: str, definition: str) -> None:
        columns = {
            row["name"]
            for row in self.conn.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column not in columns:
            self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "ResearchStorage":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def register_source(self, source: SourceRegistryEntry) -> str:
        payload = source.model_dump(mode="json")
        self.conn.execute(
            """INSERT INTO source_registry(
                source_registry_id, name, source_type, domain, access_method,
                authorization_status, evidence_status, reliability_notes, reviewed_at, enabled,
                last_run_status, last_run_at, payload
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_registry_id) DO UPDATE SET
                name=excluded.name, source_type=excluded.source_type,
                domain=excluded.domain, access_method=excluded.access_method,
                authorization_status=excluded.authorization_status,
                evidence_status=excluded.evidence_status,
                reliability_notes=excluded.reliability_notes,
                reviewed_at=excluded.reviewed_at, enabled=excluded.enabled,
                payload=excluded.payload""",
            (
                source.source_registry_id,
                source.name,
                source.source_type.value,
                source.domain,
                source.access_method,
                source.authorization_status,
                source.evidence_status.value,
                source.reliability_notes,
                _iso(source.reviewed_at) if source.reviewed_at else None,
                int(source.enabled),
                source.last_run_status,
                _iso(source.last_run_at) if source.last_run_at else None,
                _json(payload),
            ),
        )
        self.conn.commit()
        return source.source_registry_id

    def save_observation(
        self,
        observation: PropertyObservation,
        *,
        weekly_run_id: int | None = None,
    ) -> str:
        record = observation.normalized_record
        property_id = observation.property_id or record.property_id
        observed_at = _iso(observation.observed_at)
        raw_payload = _json(observation.raw_payload)
        normalized_payload = _json(record.model_dump(mode="json", by_alias=True))
        warnings = _json(observation.normalization_warnings)

        with self.conn:
            self.conn.execute(
                """INSERT INTO property_observations(
                    observation_id, property_id, observed_at, source_registry_id, weekly_run_id,
                    parser_version, raw_payload, normalized_record, normalization_warnings
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    observation.observation_id,
                    property_id,
                    observed_at,
                    observation.source_registry_id,
                    weekly_run_id,
                    observation.parser_version,
                    raw_payload,
                    normalized_payload,
                    warnings,
                ),
            )
            existing = self.conn.execute(
                "SELECT current_payload, current_observation_id, last_seen FROM properties WHERE property_id = ?",
                (property_id,),
            ).fetchone()
            if existing is None:
                self.conn.execute(
                    """INSERT INTO properties(
                        property_id, address, municipality, neighbourhood,
                        current_observation_id, first_seen, last_seen, current_payload
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        property_id,
                        record.address,
                        record.municipality,
                        record.neighbourhood,
                        observation.observation_id,
                        observed_at,
                        observed_at,
                        normalized_payload,
                    ),
                )
                self._record_event(
                    property_id,
                    "first_seen",
                    {"observation_id": observation.observation_id, "asking": record.asking},
                    created_at=observation.observed_at,
                    weekly_run_id=weekly_run_id,
                    source_registry_id=observation.source_registry_id,
                )
            else:
                old = PropertyRecord.model_validate_json(existing["current_payload"])
                if _parse_iso(observed_at) < _parse_iso(existing["last_seen"]):
                    return observation.observation_id
                projected = _merge_current_projection(old, record)
                for event in diff_property_records(old, projected, observed_at=observation.observed_at):
                    self._record_event(
                        event.property_id,
                        event.event_type,
                        event.payload,
                        created_at=event.created_at,
                        weekly_run_id=weekly_run_id,
                        source_registry_id=observation.source_registry_id,
                    )
                self.conn.execute(
                    """UPDATE properties SET address=?, municipality=?, neighbourhood=?,
                        current_observation_id=?, last_seen=?, current_payload=?
                        WHERE property_id=?""",
                    (
                        projected.address,
                        projected.municipality,
                        projected.neighbourhood,
                        observation.observation_id,
                        observed_at,
                        _json(projected.model_dump(mode="json", by_alias=True)),
                        property_id,
                    ),
                )
        return observation.observation_id

    def register_identity_aliases(
        self,
        record: PropertyRecord,
        *,
        observed_at: datetime | None = None,
        source_registry_id: str | None = None,
        source_run_id: int | None = None,
        weekly_run_id: int | None = None,
        source_url: str | None = None,
    ) -> int:
        """Persist address/PID aliases without changing existing ownership."""

        timestamp = _iso(observed_at)
        keys = [
            ("address", normalize_address(record.address, record.municipality), record.property_id),
        ]
        pids = record.pids or ([record.pid] if record.pid else [])
        keys.extend(("pid", str(pid).strip(), record.property_id) for pid in pids if str(pid).strip())
        if record.mls_number and str(record.mls_number).strip():
            keys.append(("mls", str(record.mls_number).strip().upper(), record.property_id))
        with self.conn:
            for alias_type, alias_value, property_id in keys:
                alias_key = f"{alias_type}:{alias_value}"
                self.conn.execute(
                    """INSERT INTO identity_aliases(
                        alias_key, alias_type, alias_value, property_id, first_seen, last_seen
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(alias_key) DO UPDATE SET last_seen=excluded.last_seen""",
                    (alias_key, alias_type, alias_value, property_id, timestamp, timestamp),
                )
                self.conn.execute(
                    """INSERT INTO identity_alias_observations(
                        alias_key, alias_type, alias_value, property_id,
                        source_registry_id, source_run_id, weekly_run_id,
                        observed_at, source_url
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        alias_key,
                        alias_type,
                        alias_value,
                        property_id,
                        source_registry_id,
                        source_run_id,
                        weekly_run_id,
                        timestamp,
                        source_url,
                    ),
                )
        return len(keys)

    def record_identity_alias_observation(
        self,
        *,
        alias_type: str,
        alias_value: str,
        property_id: str,
        observed_at: datetime | None = None,
        source_registry_id: str | None = None,
        source_run_id: int | None = None,
        weekly_run_id: int | None = None,
        source_url: str | None = None,
    ) -> None:
        """Append one observed alias while retaining a conservative projection."""

        normalized_value = str(alias_value).strip().upper() if alias_type == "mls" else str(alias_value).strip()
        if not normalized_value:
            return
        timestamp = _iso(observed_at)
        alias_key = f"{alias_type}:{normalized_value}"
        with self.conn:
            self.conn.execute(
                """INSERT INTO identity_aliases(
                    alias_key, alias_type, alias_value, property_id, first_seen, last_seen
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(alias_key) DO UPDATE SET last_seen=excluded.last_seen""",
                (alias_key, alias_type, normalized_value, property_id, timestamp, timestamp),
            )
            self.conn.execute(
                """INSERT INTO identity_alias_observations(
                    alias_key, alias_type, alias_value, property_id,
                    source_registry_id, source_run_id, weekly_run_id,
                    observed_at, source_url
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    alias_key,
                    alias_type,
                    normalized_value,
                    property_id,
                    source_registry_id,
                    source_run_id,
                    weekly_run_id,
                    timestamp,
                    source_url,
                ),
            )

    def list_identity_alias_observations(
        self,
        *,
        alias_key: str | None = None,
    ) -> list[sqlite3.Row]:
        """Return append-only alias observations, including conflicts."""

        if alias_key:
            return self.conn.execute(
                """SELECT * FROM identity_alias_observations
                   WHERE alias_key = ? ORDER BY id""",
                (alias_key,),
            ).fetchall()
        return self.conn.execute(
            "SELECT * FROM identity_alias_observations ORDER BY id"
        ).fetchall()

    def list_identity_aliases(self) -> dict[str, str]:
        aliases = {
            row["alias_key"]: row["property_id"]
            for row in self.conn.execute(
                "SELECT alias_key, property_id FROM identity_aliases ORDER BY alias_key"
            ).fetchall()
        }
        # Backfill aliases for databases created before the alias table was
        # introduced.  Existing address/PID projections remain canonical.
        for row in self.conn.execute("SELECT current_payload FROM properties ORDER BY property_id").fetchall():
            record = PropertyRecord.model_validate_json(row["current_payload"])
            address_value = normalize_address(record.address, record.municipality)
            aliases.setdefault(f"address:{address_value}", record.property_id)
            pids = record.pids or ([record.pid] if record.pid else [])
            for pid in pids:
                if str(pid).strip():
                    aliases.setdefault(f"pid:{str(pid).strip()}", record.property_id)
            if record.mls_number and str(record.mls_number).strip():
                aliases.setdefault(
                    f"mls:{str(record.mls_number).strip().upper()}",
                    record.property_id,
                )
        return aliases

    def list_identity_alias_pids(self) -> dict[str, set[str]]:
        """Return PIDs currently associated with each persisted alias."""

        result: dict[str, set[str]] = {}
        rows = self.conn.execute(
            "SELECT alias_key, property_id FROM identity_aliases ORDER BY alias_key"
        ).fetchall()
        for row in rows:
            current = self.get_current_property(row["property_id"])
            if current is None:
                continue
            pids = current.pids or ([current.pid] if current.pid else [])
            result[row["alias_key"]] = {str(pid).strip() for pid in pids if str(pid).strip()}
        for record in self.list_current_properties():
            address_key = f"address:{normalize_address(record.address, record.municipality)}"
            pids = record.pids or ([record.pid] if record.pid else [])
            result.setdefault(address_key, {str(pid).strip() for pid in pids if str(pid).strip()})
        return result

    def save_provenance(self, records: SourceRecord | Iterable[SourceRecord]) -> int:
        items = [records] if isinstance(records, SourceRecord) else list(records)
        with self.conn:
            for record in items:
                self.conn.execute(
                    """INSERT INTO field_provenance(
                        property_id, field_name, source_url, source_type,
                        source_registry_id, source_run_id, weekly_run_id, retrieved_at, raw_value, normalized_value, source_version,
                        evidence_status, notes
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        record.property_id,
                        record.field_name,
                        record.source_url,
                        record.source_type.value,
                        record.source_registry_id,
                        record.source_run_id,
                        record.weekly_run_id,
                        _iso(record.retrieved_at),
                        _json(record.raw_value),
                        _json(record.normalized_value),
                        record.source_version,
                        record.evidence_status.value,
                        record.notes,
                    ),
                )
        return len(items)

    def save_verification_item(self, item: VerificationItem) -> int:
        cursor = self.conn.execute(
            """INSERT INTO verification_items(
                verification_id, property_id, source_registry_id, source_url,
                status, reason, match_method, raw_snapshot_path, observed_at,
                weekly_run_id, source_run_id, details
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                item.verification_id,
                item.property_id,
                item.source_registry_id,
                item.source_url,
                item.status,
                item.reason,
                item.match_method,
                item.raw_snapshot_path,
                _iso(item.observed_at),
                item.weekly_run_id,
                item.source_run_id,
                _json(item.details),
            ),
        )
        self.conn.commit()
        return int(cursor.lastrowid)

    def list_verification_items(
        self,
        property_id: str | None = None,
        *,
        open_only: bool = False,
    ) -> list[VerificationItem]:
        query = "SELECT * FROM verification_items"
        params: list[Any] = []
        clauses: list[str] = []
        if property_id:
            clauses.append("property_id = ?")
            params.append(property_id)
        if open_only:
            clauses.append("status NOT IN ('RESOLVED', 'CLOSED')")
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY id"
        rows = self.conn.execute(query, params).fetchall()
        return [
            VerificationItem(
                verification_id=row["verification_id"],
                property_id=row["property_id"],
                source_registry_id=row["source_registry_id"],
                source_url=row["source_url"],
                status=row["status"],
                reason=row["reason"],
                match_method=row["match_method"],
                raw_snapshot_path=row["raw_snapshot_path"],
                observed_at=_parse_iso(row["observed_at"]),
                weekly_run_id=row["weekly_run_id"],
                source_run_id=row["source_run_id"],
                details=json.loads(row["details"] or "{}"),
            )
            for row in rows
        ]

    def list_open_verification_items(self, property_id: str | None = None) -> list[VerificationItem]:
        return self.list_verification_items(property_id, open_only=True)

    def save_conflict(
        self,
        conflict: DataConflict,
        *,
        weekly_run_id: int | None = None,
    ) -> str:
        existing = self.conn.execute(
            "SELECT resolution_status FROM data_conflicts WHERE conflict_id = ?",
            (conflict.conflict_id,),
        ).fetchone()
        with self.conn:
            self.conn.execute(
                """INSERT INTO data_conflict_history(
                    conflict_id, property_id, field_name, conflict_type,
                    source_values, conflicting_sources, detected_at, recorded_at,
                    resolution_status, resolution_note, weekly_run_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    conflict.conflict_id,
                    conflict.property_id,
                    conflict.field_name,
                    conflict.conflict_type,
                    _json(conflict.source_values),
                    _json(conflict.conflicting_sources),
                    _iso(conflict.detected_at),
                    _iso(),
                    conflict.resolution_status,
                    conflict.resolution_note,
                    weekly_run_id,
                ),
            )
            self.conn.execute(
                """INSERT INTO data_conflicts(
                    conflict_id, property_id, field_name, conflict_type,
                    source_values, conflicting_sources, detected_at,
                    resolution_status, resolution_note
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(conflict_id) DO UPDATE SET
                    property_id=excluded.property_id, field_name=excluded.field_name,
                    conflict_type=excluded.conflict_type, source_values=excluded.source_values,
                    conflicting_sources=excluded.conflicting_sources, detected_at=excluded.detected_at,
                    resolution_status=excluded.resolution_status,
                    resolution_note=excluded.resolution_note""",
                (
                    conflict.conflict_id,
                    conflict.property_id,
                    conflict.field_name,
                    conflict.conflict_type,
                    _json(conflict.source_values),
                    _json(conflict.conflicting_sources),
                    _iso(conflict.detected_at),
                    conflict.resolution_status,
                    conflict.resolution_note,
                ),
            )
            old_status = existing["resolution_status"] if existing else None
            if old_status != conflict.resolution_status:
                event_type = (
                    "conflict_resolved"
                    if conflict.resolution_status.upper() in {"RESOLVED", "CLOSED"}
                    else "conflict_opened"
                )
                self._record_event(
                    conflict.property_id,
                    event_type,
                    {
                        "conflict_id": conflict.conflict_id,
                        "field_name": conflict.field_name,
                        "resolution_status": conflict.resolution_status,
                    },
                    created_at=conflict.detected_at,
                    weekly_run_id=weekly_run_id,
                )
        return conflict.conflict_id

    def save_comparable(
        self,
        property_id: str,
        comparable: dict[str, Any],
        *,
        weekly_run_id: int | None = None,
        source_registry_id: str | None = None,
        source_version: str | None = None,
    ) -> int:
        observed_at = comparable.get("observed_at")
        if isinstance(observed_at, datetime):
            observed_at = _iso(observed_at)
        elif observed_at:
            try:
                observed_at = _iso(_parse_iso(str(observed_at)))
            except ValueError:
                observed_at = str(observed_at)
        observed_at = str(observed_at or _iso())
        comparable_id = comparable.get("comp_id") or comparable.get("comparable_id")
        cursor = self.conn.execute(
            """INSERT INTO comparable_records(
                property_id, comp_id, observed_at, weekly_run_id,
                source_registry_id, source_version, payload
            ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                property_id,
                comparable_id,
                observed_at,
                weekly_run_id,
                source_registry_id,
                source_version,
                _json(comparable),
            ),
        )
        self.conn.commit()
        return int(cursor.lastrowid)

    def save_source_run(self, result: SourceRunResult) -> int:
        payload = result.model_dump(mode="json")
        cursor = self.conn.execute(
            """INSERT INTO source_runs(
                weekly_run_id, source_registry_id, source_name, started_at, finished_at, status, payload
            ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                result.weekly_run_id,
                result.source_registry_id,
                result.source_name,
                _iso(result.started_at),
                _iso(result.finished_at),
                result.status.value,
                _json(payload),
            ),
        )
        source_run_id = int(cursor.lastrowid)
        payload["source_run_id"] = source_run_id
        self.conn.execute(
            "UPDATE source_runs SET payload=? WHERE id=?",
            (_json(payload), source_run_id),
        )
        for snapshot in result.snapshots:
            snapshot_payload = snapshot.model_dump(mode="json")
            self.conn.execute(
                """INSERT INTO source_snapshots(
                    source_run_id, source_registry_id, url, final_url, method,
                    request_kind, status_code, content_type, raw_path, screenshot_path, sha256, size_bytes,
                    observed_at, retrieved_at, source_version, payload
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    source_run_id,
                    snapshot.source_registry_id,
                    snapshot.url,
                    snapshot.final_url,
                    snapshot.method,
                    snapshot.request_kind,
                    snapshot.status_code,
                    snapshot.content_type,
                    snapshot.raw_path,
                    snapshot.screenshot_path,
                    snapshot.sha256,
                    snapshot.size_bytes,
                    _iso(snapshot.observed_at),
                    _iso(snapshot.retrieved_at),
                    snapshot.source_version,
                    _json(snapshot_payload),
                ),
            )
        registry = self.conn.execute(
            "SELECT payload FROM source_registry WHERE source_registry_id = ?",
            (result.source_registry_id,),
        ).fetchone()
        if registry is not None:
            registry_payload = json.loads(registry["payload"])
            registry_payload.update(
                last_run_status=result.status.value,
                last_run_at=_iso(result.finished_at),
            )
            self.conn.execute(
                """UPDATE source_registry
                      SET last_run_status=?, last_run_at=?, payload=?
                    WHERE source_registry_id=?""",
                (
                    result.status.value,
                    _iso(result.finished_at),
                    _json(registry_payload),
                    result.source_registry_id,
                ),
            )
        self.conn.commit()
        return source_run_id

    def list_source_runs(self, source_registry_id: str | None = None) -> list[SourceRunResult]:
        if source_registry_id:
            rows = self.conn.execute(
                "SELECT payload FROM source_runs WHERE source_registry_id = ? ORDER BY id",
                (source_registry_id,),
            ).fetchall()
        else:
            rows = self.conn.execute("SELECT payload FROM source_runs ORDER BY id").fetchall()
        return [SourceRunResult.model_validate_json(row["payload"]) for row in rows]

    def save_enrichment_observation(
        self,
        result: EnrichedPropertyResult,
        *,
        observed_at: datetime | None = None,
        weekly_run_id: int | None = None,
        source_registry_id: str | None = None,
        source_version: str | None = None,
    ) -> int:
        payload = result.model_dump(mode="json")
        provider_name = ",".join(item.provider_name for item in result.provider_results) or "unknown"
        status = "SUCCESS" if not any(item.status == "FAILED" for item in result.provider_results) else "PARTIAL"
        cursor = self.conn.execute(
            """INSERT INTO enrichment_observations(
                property_id, provider_name, observed_at, status,
                weekly_run_id, source_registry_id, source_version, payload
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                result.property.property_id,
                provider_name,
                _iso(observed_at),
                status,
                weekly_run_id,
                source_registry_id,
                source_version,
                _json(payload),
            ),
        )
        self.conn.commit()
        return int(cursor.lastrowid)

    def list_enrichment_observations(self, property_id: str | None = None) -> list[EnrichedPropertyResult]:
        if property_id:
            rows = self.conn.execute(
                "SELECT payload FROM enrichment_observations WHERE property_id = ? ORDER BY id",
                (property_id,),
            ).fetchall()
        else:
            rows = self.conn.execute("SELECT payload FROM enrichment_observations ORDER BY id").fetchall()
        return [EnrichedPropertyResult.model_validate_json(row["payload"]) for row in rows]

    def save_scenario(
        self,
        scenario: RedevelopmentScenario,
        *,
        recorded_at: datetime | None = None,
        weekly_run_id: int | None = None,
        source_registry_id: str | None = None,
        source_version: str | None = None,
    ) -> int:
        cursor = self.conn.execute(
            """INSERT INTO redevelopment_scenarios(
                property_id, scenario_type, recorded_at, weekly_run_id,
                source_registry_id, source_version, payload
            ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                scenario.property_id,
                scenario.scenario_type.value,
                _iso(recorded_at),
                weekly_run_id,
                source_registry_id,
                source_version,
                _json(scenario.model_dump(mode="json")),
            ),
        )
        self.conn.commit()
        return int(cursor.lastrowid)

    def save_score_snapshot(
        self,
        snapshot: ScoreSnapshot,
        *,
        weekly_run_id: int | None = None,
        source_run_id: int | None = None,
        source_registry_id: str | None = None,
    ) -> int:
        cursor = self.conn.execute(
            """INSERT INTO score_snapshots(
                property_id, generated_at, scoring_version, config_hash,
                weekly_run_id, source_run_id, source_registry_id,
                total_score, live_score, land_score, investment_score,
                data_confidence_score, payload
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                snapshot.property_id,
                _iso(snapshot.generated_at),
                snapshot.scoring_version,
                snapshot.config_hash,
                weekly_run_id if weekly_run_id is not None else snapshot.weekly_run_id,
                source_run_id if source_run_id is not None else snapshot.source_run_id,
                source_registry_id if source_registry_id is not None else snapshot.source_registry_id,
                snapshot.total_score,
                snapshot.live_score,
                snapshot.land_score,
                snapshot.investment_score,
                snapshot.data_confidence_score,
                _json(snapshot.model_dump(mode="json")),
            ),
        )
        self.conn.commit()
        return int(cursor.lastrowid)

    def record_watchlist_event(
        self,
        event_or_property_id: WatchlistEvent | str,
        event_type: str | None = None,
        payload: dict[str, Any] | None = None,
        created_at: datetime | None = None,
    ) -> int:
        if isinstance(event_or_property_id, WatchlistEvent):
            event = event_or_property_id
            property_id = event.property_id
            event_type = event.event_type
            payload = event.payload
            created_at = event.created_at
            weekly_run_id = event.weekly_run_id
            source_run_id = event.source_run_id
            source_registry_id = event.source_registry_id
        else:
            property_id = event_or_property_id
            weekly_run_id = None
            source_run_id = None
            source_registry_id = None
        if not event_type:
            raise ValueError("event_type is required")
        with self.conn:
            return self._record_event(
                property_id,
                event_type,
                payload or {},
                created_at=created_at,
                weekly_run_id=weekly_run_id,
                source_run_id=source_run_id,
                source_registry_id=source_registry_id,
            )

    def record_watchlist_events(self, events: Iterable[WatchlistEvent]) -> int:
        materialized = list(events)
        with self.conn:
            for event in materialized:
                self._record_event(
                    event.property_id,
                    event.event_type,
                    event.payload,
                    created_at=event.created_at,
                    weekly_run_id=event.weekly_run_id,
                    source_run_id=event.source_run_id,
                    source_registry_id=event.source_registry_id,
                )
        return len(materialized)

    def _record_event(
        self,
        property_id: str,
        event_type: str,
        payload: dict[str, Any],
        *,
        created_at: datetime | None = None,
        weekly_run_id: int | None = None,
        source_run_id: int | None = None,
        source_registry_id: str | None = None,
    ) -> int:
        cursor = self.conn.execute(
            """INSERT INTO watchlist_events(
                property_id, event_type, created_at, weekly_run_id,
                source_run_id, source_registry_id, payload
            ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                property_id,
                event_type,
                _iso(created_at),
                weekly_run_id,
                source_run_id,
                source_registry_id,
                _json(payload),
            ),
        )
        return int(cursor.lastrowid)

    def get_current_property(self, property_id: str) -> PropertyRecord | None:
        row = self.conn.execute(
            "SELECT current_payload FROM properties WHERE property_id = ?",
            (property_id,),
        ).fetchone()
        return PropertyRecord.model_validate_json(row["current_payload"]) if row else None

    def list_current_properties(self) -> list[PropertyRecord]:
        rows = self.conn.execute(
            "SELECT current_payload FROM properties ORDER BY property_id"
        ).fetchall()
        return [PropertyRecord.model_validate_json(row["current_payload"]) for row in rows]

    def list_current_properties_for_source(self, source_registry_id: str) -> list[PropertyRecord]:
        """List properties previously observed through one source.

        The raw payload fallback keeps this query useful for older pipeline
        observations whose observation envelope used the pipeline registry ID.
        """

        rows = self.conn.execute(
            """SELECT p.current_payload
                 FROM properties AS p
                WHERE EXISTS (
                    SELECT 1
                      FROM property_observations AS o
                     WHERE o.property_id = p.property_id
                       AND (
                           o.source_registry_id = ?
                           OR o.raw_payload LIKE ?
                       )
                )
                ORDER BY p.property_id""",
            (source_registry_id, f"%{source_registry_id}%"),
        ).fetchall()
        return [PropertyRecord.model_validate_json(row["current_payload"]) for row in rows]

    def list_open_conflicts(self) -> list[DataConflict]:
        rows = self.conn.execute(
            """SELECT conflict_id, property_id, field_name, conflict_type,
                      source_values, conflicting_sources, detected_at,
                      resolution_status, resolution_note
                 FROM data_conflicts
                WHERE UPPER(resolution_status) NOT IN ('RESOLVED', 'CLOSED')
                ORDER BY detected_at"""
        ).fetchall()
        return [
            DataConflict(
                conflict_id=row["conflict_id"],
                property_id=row["property_id"],
                field_name=row["field_name"],
                conflict_type=row["conflict_type"],
                source_values=json.loads(row["source_values"]),
                conflicting_sources=json.loads(row["conflicting_sources"]),
                detected_at=datetime.fromisoformat(row["detected_at"]),
                resolution_status=row["resolution_status"],
                resolution_note=row["resolution_note"],
            )
            for row in rows
        ]

    def list_actionable_verification_items(self) -> list[VerificationItem]:
        """Return open verification work without repeating manual refreshes."""

        items = self.list_open_verification_items()
        latest_manual: dict[tuple[str, str, str], VerificationItem] = {}
        actionable: list[VerificationItem] = []
        for item in items:
            verification_type = str(item.details.get("verification_type", ""))
            if not verification_type.startswith("MANUAL_PROVISIONAL_"):
                actionable.append(item)
                continue
            key = (item.property_id, item.source_registry_id, verification_type)
            latest_manual[key] = item
        actionable.extend(latest_manual.values())
        return sorted(actionable, key=lambda item: (item.observed_at, item.property_id, item.source_registry_id))

    def list_score_snapshots(self, property_id: str | None = None) -> list[ScoreSnapshot]:
        if property_id:
            rows = self.conn.execute(
                "SELECT payload FROM score_snapshots WHERE property_id = ? ORDER BY id",
                (property_id,),
            ).fetchall()
        else:
            rows = self.conn.execute("SELECT payload FROM score_snapshots ORDER BY id").fetchall()
        return [ScoreSnapshot.model_validate_json(row["payload"]) for row in rows]

    def list_watchlist_events(self, property_id: str | None = None) -> list[WatchlistEvent]:
        if property_id:
            rows = self.conn.execute(
                "SELECT property_id, event_type, created_at, weekly_run_id, source_run_id, source_registry_id, payload FROM watchlist_events WHERE property_id = ? ORDER BY id",
                (property_id,),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT property_id, event_type, created_at, weekly_run_id, source_run_id, source_registry_id, payload FROM watchlist_events ORDER BY id"
            ).fetchall()
        return [
            WatchlistEvent(
                property_id=row["property_id"],
                event_type=row["event_type"],
                created_at=datetime.fromisoformat(row["created_at"]),
                weekly_run_id=row["weekly_run_id"],
                source_run_id=row["source_run_id"],
                source_registry_id=row["source_registry_id"],
                payload=json.loads(row["payload"]),
            )
            for row in rows
        ]

    def start_weekly_run(
        self,
        run_type: str = "weekly",
        *,
        started_at: datetime | None = None,
        summary: dict[str, Any] | None = None,
    ) -> int:
        cursor = self.conn.execute(
            "INSERT INTO weekly_runs(run_type, started_at, status, summary) VALUES (?, ?, ?, ?)",
            (run_type, _iso(started_at), "RUNNING", _json(summary or {})),
        )
        self.conn.commit()
        return int(cursor.lastrowid)

    def finish_weekly_run(
        self,
        run_id: int,
        *,
        status: str = "SUCCESS",
        summary: dict[str, Any] | None = None,
        finished_at: datetime | None = None,
    ) -> None:
        self.conn.execute(
            "UPDATE weekly_runs SET finished_at=?, status=?, summary=? WHERE id=?",
            (_iso(finished_at), status, _json(summary or {}), run_id),
        )
        self.conn.commit()
