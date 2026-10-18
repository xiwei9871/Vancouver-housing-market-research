#!/usr/bin/env python3
"""Build data-only auto-prefill annotations for the calibration packet.

Reads the frozen shadow/enrichment/split artifacts and writes
``data/research/normalized/strategy-v2-auto-annotations-<date>.json`` —
the file ``build_calibration_packet.py --auto`` bakes into the cards.

Data fields only: lot/location/redevelopment/price bands, tracks, factual
tags, special-situation detection, data_only_signal.  Current Use, Reno and
the final KEEP/WATCH/PASS decision are left for the human.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from northshore_watchdog.research.strategy_v2_auto_prefill import (  # noqa: E402
    RULES_VERSION,
    build_auto_annotations,
)
from northshore_watchdog.research.strategy_v2_calibration import (  # noqa: E402
    load_split_manifest,
)

DEFAULT_SHADOW = ROOT / "data/research/normalized/strategy-v2-shadow-2026-09-09.json"
DEFAULT_ENRICHMENT = (
    ROOT / "data/research/normalized/portfolio-official-enrichment-2026-09-07.json"
)
DEFAULT_PHOTO_EVIDENCE = (
    ROOT / "data/research/normalized/strategy-v2-online-photo-evidence-2026-09-12.json"
)
DEFAULT_SPLITS = ROOT / "data/research/history/strategy-v2-shadow-splits.json"


def _rows_by_key(path: Path) -> dict[str, dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {
        str(row["property_key"]): row
        for row in payload.get("properties") or []
        if isinstance(row, Mapping) and row.get("property_key")
    }


def _candidates_by_key(path: Path) -> dict[str, dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    official = payload.get("official_enrichment")
    values = (
        official["cards"]
        if isinstance(official, Mapping) and isinstance(official.get("cards"), list)
        else payload.get("candidates") or []
    )
    out: dict[str, dict[str, Any]] = {}
    for row in values:
        if not isinstance(row, Mapping):
            continue
        key = row.get("property_key") or (
            f"mls:{row['mls_number']}" if row.get("mls_number") else None
        )
        if key:
            out[str(key)] = dict(row)
    return out


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shadow", type=Path, default=DEFAULT_SHADOW)
    parser.add_argument("--enrichment", type=Path, default=DEFAULT_ENRICHMENT)
    parser.add_argument("--photo-evidence", type=Path, default=DEFAULT_PHOTO_EVIDENCE)
    parser.add_argument("--splits", type=Path, default=DEFAULT_SPLITS)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--output-date", default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    date_value = args.output_date or datetime.now(timezone.utc).date().isoformat()
    output = args.output or (
        ROOT
        / "data/research/normalized"
        / f"strategy-v2-auto-annotations-{date_value}.json"
    )

    shadow_rows = _rows_by_key(args.shadow)
    candidates = _candidates_by_key(args.enrichment)
    splits = load_split_manifest(args.splits)
    photo_rows = (
        _rows_by_key(args.photo_evidence) if args.photo_evidence.exists() else {}
    )
    photo_counts = {
        key: row.get("photo_count") for key, row in photo_rows.items()
    }

    records = build_auto_annotations(
        shadow_rows, candidates, splits=splits, photo_counts=photo_counts
    )
    payload = {
        "schema_version": "strategy-v2-auto-annotations-v1",
        "rules_version": RULES_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "inputs": {
            "shadow": str(args.shadow),
            "enrichment": str(args.enrichment),
            "splits": str(args.splits),
        },
        "property_count": len(records),
        "annotations": records,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    signals = Counter(rec["data_only_signal"] for rec in records.values())
    confidence = Counter(rec["confidence"] for rec in records.values())
    flagged = sum(1 for rec in records.values() if rec["needs_human_review"])
    print(
        json.dumps(
            {
                "status": "COMPLETED",
                "property_count": len(records),
                "data_only_signal": dict(sorted(signals.items())),
                "confidence": dict(sorted(confidence.items())),
                "needs_human_review_count": flagged,
                "output": str(output),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
