#!/usr/bin/env python3
"""Rebuild the 2026-10-05 discovery artifact from persisted evidence.

The weekly ``research-discover`` run completed successfully (135 candidates,
all detail snapshots fetched, all five official adapters enriched every
property), but the machine-readable stdout exceeded the capture buffer and
the middle of the JSON was lost.  Everything needed survives elsewhere:

- card list + listing fields: ``validation.candidate_cards`` blocks in the
  truncated stdout dump (135 cards, pre-enrichment)
- official enrichment rows: ``source_runs`` records in
  ``outputs/research_mls_first.sqlite`` (5 adapters x 135 properties)
- detail evidence: per-MLS HTML snapshots under
  ``data/research/raw/discovery/realtor-ca-native-details/``

This script re-applies the persisted adapter rows onto the recovered cards
(same merge semantics as ``enrich_discovery_cards``) and writes
``regional-official-enrichment-2026-10-05.json`` so the standard downstream
chain (shadow -> analysis -> auto-annotations -> packet) runs unchanged.
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

CARDS_DUMP = Path("/tmp/today_candidate_cards.json")
SQLITE = ROOT / "outputs/research_mls_first.sqlite"
SINCE = "2026-10-05T07:00"
OUT = ROOT / "data/research/normalized/regional-official-enrichment-2026-10-05.json"
DATE = "2026-10-05"
OBSERVED_AT = "2026-10-05T07:33:04.961424Z"

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

# Preset outcomes observed in the run (source_runs 1303-1311).  Only the two
# passing presets produced candidates; the three boundary-rejected presets'
# card lists were not persisted this run.
PRESET_RESULTS = [
    {"preset_name": "vancouver-west", "status": "PASS", "result_count": 8},
    {"preset_name": "vancouver-east", "status": "PASS", "result_count": 127},
    {"preset_name": "vancouver-central-core", "status": "FILTER_NOT_APPLIED", "result_count": None},
    {"preset_name": "vancouver-oakridge", "status": "FILTER_NOT_APPLIED", "result_count": None},
    {"preset_name": "vancouver-south", "status": "FILTER_NOT_APPLIED", "result_count": None},
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
    raw_cards = json.loads(CARDS_DUMP.read_text(encoding="utf-8"))
    cards = [normalize_discovery_card(DiscoveryCard.model_validate(c)) for c in raw_cards]
    by_id = {_property_id(c): c for c in cards}
    print(f"cards: {len(cards)}")

    con = sqlite3.connect(SQLITE)
    rows = con.execute(
        "SELECT source_name, payload FROM source_runs "
        "WHERE started_at > ? AND source_name != 'realtor-ca-native' ORDER BY id",
        (SINCE,),
    ).fetchall()

    # property_id -> adapter -> record
    per_prop: dict[str, dict[str, dict]] = {}
    status_counts: dict[str, int] = {}
    field_coverage: dict[str, int] = {}
    for source_name, payload in rows:
        data = json.loads(payload)
        for rec in data.get("records") or []:
            pid = rec.get("property_id")
            if not pid:
                continue
            per_prop.setdefault(pid, {})[source_name] = rec

    enriched_ids: set[str] = set()
    evidence: list[dict[str, Any]] = []
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
                {"note": "see outputs/research_mls_first.sqlite source_runs 1312-1381"}
            ],
        },
        "preset_results": PRESET_RESULTS,
        "status": "PARTIAL",
        "rescue_log": [],
        "rebuilt_from": {
            "cards": str(CARDS_DUMP),
            "enrichment": "outputs/research_mls_first.sqlite source_runs 1312+",
            "rebuilt_at": datetime.now(timezone.utc).isoformat(),
        },
    }
    OUT.write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUT} ({OUT.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
