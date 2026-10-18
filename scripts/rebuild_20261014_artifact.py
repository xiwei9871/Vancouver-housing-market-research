#!/usr/bin/env python3
"""Rebuild the 2026-10-14 enrichment artifact from persisted source_runs.

The 2026-10-14 scan ran per-preset (each preset invoked separately after the
pagination fix).  Successful runs persisted their captures to
``outputs/research_mls_first.sqlite``:

- source_run 1432: vancouver-east   PASS 124 cards (120/124 details)
- source_run 1498: vancouver-west   PASS  10 cards (  9/ 10 details)
- source_run 1509: vancouver-central-core PASS 17 cards (17/17 details)
- source_run 1398: vancouver-south  PASS  33 cards ( 31/ 32 details)
- source_run 1522: vancouver-oakridge PASS 0 results (legitimately empty)

Card payloads live under ``crawl_metrics.discovery.cards``; official
enrichment was persisted as the five adapter source_runs following each
preset row.  This script merges them (same semantics as
``enrich_discovery_cards``) and writes
``regional-official-enrichment-2026-10-14.json``.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from northshore_watchdog.research.discovery import DiscoveryCard  # noqa: E402
from northshore_watchdog.research.discovery_official import (  # noqa: E402
    _merge_row,
    _property_id,
    normalize_discovery_card,
)
from northshore_watchdog.research.official_enrichment import (  # noqa: E402
    OfficialLookupRow,
    OfficialQueryStatus,
)

SQLITE = ROOT / "outputs/research_mls_first.sqlite"
SINCE = "2026-10-14T06:00"
OUT = ROOT / "data/research/normalized/regional-official-enrichment-2026-10-14.json"
DATE = "2026-10-14"
OBSERVED_AT = "2026-10-14T08:00:00.000000Z"

# Persisted PASS runs for 2026-10-14 (id, preset, declared result_count).
PASS_RUNS = [
    (1432, "vancouver-east", 124),
    (1498, "vancouver-west", 10),
    (1509, "vancouver-central-core", 17),
    (1398, "vancouver-south", 33),
]

ADAPTER_ORDER = [
    "bca-assessment",
    "municipal-zoning",
    "municipal-road",
    "school-catchment",
    "school-performance",
]
SOURCE_TYPE = {
    "bca-assessment": "BC_ASSESSMENT",
    "municipal-zoning": "OFFICIAL_GOV",
    "municipal-road": "OFFICIAL_GOV",
    "school-catchment": "OFFICIAL_GOV",
    "school-performance": "OFFICIAL_GOV",
}

PRESET_RESULTS = [
    {"preset_name": "vancouver-east", "status": "PASS", "result_count": 124, "source_run_id": 1432},
    {"preset_name": "vancouver-west", "status": "PASS", "result_count": 10, "source_run_id": 1498},
    {"preset_name": "vancouver-central-core", "status": "PASS", "result_count": 17, "source_run_id": 1509},
    {"preset_name": "vancouver-oakridge", "status": "PASS", "result_count": 0, "source_run_id": 1522},
    {"preset_name": "vancouver-south", "status": "PASS", "result_count": 33, "source_run_id": 1398},
    {"preset_name": "north-vancouver-city", "status": "PARSER_ERROR",
     "error": "apply_custom_boundary requires map_params.boundary_id"},
    {"preset_name": "north-vancouver-district", "status": "PARSER_ERROR",
     "error": "apply_custom_boundary requires map_params.boundary_id"},
    {"preset_name": "west-vancouver", "status": "PARSER_ERROR",
     "error": "apply_custom_boundary requires map_params.boundary_id"},
    {"preset_name": "vancouver-broad-scan", "status": "PARSER_ERROR",
     "error": "apply_custom_boundary requires map_params.boundary_id"},
]


def _fake_adapter(name: str) -> SimpleNamespace:
    return SimpleNamespace(
        name=name,
        source_registry_id=f"source:{name}",
        source_type=SOURCE_TYPE[name],
    )


def main() -> int:
    con = sqlite3.connect(SQLITE)

    # --- cards from persisted PASS captures, deduped by property_id ---
    raw_cards: list[dict] = []
    for run_id, preset, declared in PASS_RUNS:
        payload = json.loads(
            con.execute("SELECT payload FROM source_runs WHERE id=?", (run_id,)).fetchone()[0]
        )
        cards = payload["crawl_metrics"]["discovery"].get("cards") or []
        print(f"run {run_id} {preset}: {len(cards)} cards (declared {declared})")
        raw_cards.extend(cards)

    cards: list[Any] = []
    seen: set[str] = set()
    for raw in raw_cards:
        card = normalize_discovery_card(DiscoveryCard.model_validate(raw))
        pid = _property_id(card)
        if pid in seen:
            continue
        seen.add(pid)
        cards.append(card)
    by_id = {_property_id(c): c for c in cards}
    print(f"merged unique cards: {len(cards)}")

    # --- persisted enrichment rows for the whole day ---
    rows = con.execute(
        "SELECT source_name, payload FROM source_runs "
        "WHERE started_at > ? AND source_name != 'realtor-ca-native' ORDER BY id",
        (SINCE,),
    ).fetchall()

    per_prop: dict[str, dict[str, dict]] = {}
    for source_name, payload in rows:
        data = json.loads(payload)
        for rec in data.get("records") or []:
            pid = rec.get("property_id")
            if not pid:
                continue
            per_prop.setdefault(pid, {})[source_name] = rec

    enriched_ids: set[str] = set()
    evidence: list[dict[str, Any]] = []
    status_counts: dict[str, int] = {}
    field_coverage: dict[str, int] = {}
    missing = [pid for pid in by_id if pid not in per_prop]
    print(f"properties with persisted enrichment: {len(per_prop)}; missing for cards: {len(missing)}")

    for pid, adapter_rows in per_prop.items():
        card = by_id.get(pid)
        if card is None:
            continue
        for name in ADAPTER_ORDER:
            rec = adapter_rows.get(name)
            if rec is None:
                continue
            status = str(rec.get("status") or "FAILED")
            try:
                status_enum = OfficialQueryStatus(status)
            except ValueError:
                status_enum = OfficialQueryStatus.FAILED
            row = OfficialLookupRow(
                property_id=pid,
                status=status_enum,
                fields=rec.get("fields") or {},
                source_url=str(rec.get("source_url") or ""),
                retrieved_at=datetime.now(timezone.utc),
                match_method=rec.get("match_method"),
                query_scope=rec.get("query_scope"),
                raw_snapshot_path=rec.get("raw_snapshot_path"),
                warnings=list(rec.get("warnings") or []),
            )
            status_counts[status] = status_counts.get(status, 0) + 1
            evidence.append({
                "property_id": pid,
                "address": card.address,
                "adapter": name,
                "status": status,
                "source_url": row.source_url,
                "warnings": list(row.warnings),
            })
            card = _merge_row(card, row, _fake_adapter(name))
            by_id[pid] = card
            if status_enum is OfficialQueryStatus.SUCCESS:
                enriched_ids.add(pid)
                for field in row.fields:
                    field_coverage[field] = field_coverage.get(field, 0) + 1

    final_cards = [by_id.get(_property_id(c), c) for c in cards]
    geo = sum(1 for c in final_cards if c.geometry_source == "OFFICIAL_BCA")
    print(f"enriched properties: {len(enriched_ids)}; OFFICIAL_BCA geometry: {geo}")
    print("status_counts:", status_counts)

    artifact = {
        "candidate_count": len(final_cards),
        "candidates": [c.model_dump(mode="json") for c in final_cards],
        "dry_run": False,
        "observed_at": OBSERVED_AT,
        "official_enrichment": {
            "status": "COMPLETED",
            "attempted_card_count": len(final_cards),
            "enriched_card_count": len(enriched_ids),
            "status_counts": status_counts,
            "field_coverage": field_coverage,
            "cards": [c.model_dump(mode="json") for c in final_cards],
            "evidence": evidence,
            "warnings": [],
            "source_runs": [
                {"note": "see outputs/research_mls_first.sqlite source_runs 1398-1522"}
            ],
        },
        "preset_results": PRESET_RESULTS,
        "status": "PARTIAL",
        "rescue_log": [],
        "rebuilt_from": {
            "cards": "source_runs 1432/1498/1509/1398 crawl_metrics.discovery.cards",
            "enrichment": "source_runs >2026-10-14T06:00 non-realtor adapters",
            "rebuilt_at": datetime.now(timezone.utc).isoformat(),
        },
    }
    OUT.write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUT} ({OUT.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
