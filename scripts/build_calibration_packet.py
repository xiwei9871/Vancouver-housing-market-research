#!/usr/bin/env python3
"""Build the Strategy V2 blind-reading calibration packet.

Generates one interactive HTML card per property (all 166), a dashboard
index.html, and an annotations-template.csv.  Cards contain facts and
extracted evidence only — no machine scores, tracks, or ledgers.

Reads frozen artifacts only; writes under reports/calibration/<date>/.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from northshore_watchdog.research.strategy_v2_calibration import (  # noqa: E402
    build_card_context,
    card_slug,
    load_split_manifest,
    split_for_key,
    write_packet,
)

DEFAULT_SHADOW = ROOT / "data/research/normalized/strategy-v2-shadow-2026-09-09.json"
DEFAULT_ENRICHMENT = (
    ROOT / "data/research/normalized/portfolio-official-enrichment-2026-09-07.json"
)
DEFAULT_PHOTO_EVIDENCE = (
    ROOT / "data/research/normalized/strategy-v2-online-photo-evidence-2026-09-12.json"
)
DEFAULT_SPLITS = ROOT / "data/research/history/strategy-v2-shadow-splits.json"
PHOTO_ASSETS_DIR = ROOT / "data/research/raw/photo-assets"


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _rows_by_key(path: Path, list_field: str = "properties") -> dict[str, dict[str, Any]]:
    payload = _load_json(path)
    rows = payload.get(list_field) or []
    return {
        str(row["property_key"]): row
        for row in rows
        if isinstance(row, Mapping) and row.get("property_key")
    }


def _candidates_by_key(path: Path) -> dict[str, dict[str, Any]]:
    payload = _load_json(path)
    official = payload.get("official_enrichment")
    if isinstance(official, Mapping) and isinstance(official.get("cards"), list):
        values = official["cards"]
    else:
        values = payload.get("candidates") or []
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


def _photo_manifest(
    property_key: str, photo_row: Mapping[str, Any] | None
) -> dict[str, Any] | None:
    """Resolve the richest available photo manifest for a property.

    Prefers the path recorded in the Phase 2A photo-evidence artifact
    (per-image visual classification); falls back to the raw
    ``photo-assets/<slug>/manifest.json`` cache which carries local bytes.
    """

    candidates: list[Path] = []
    if photo_row and photo_row.get("photo_manifest_path"):
        candidates.append(Path(str(photo_row["photo_manifest_path"])))
    replay = (photo_row or {}).get("photo_replay") or {}
    if replay.get("path"):
        candidates.append(Path(str(replay["path"])))
    candidates.append(PHOTO_ASSETS_DIR / card_slug(property_key).lower() / "manifest.json")

    best: dict[str, Any] | None = None
    for path in candidates:
        try:
            manifest = _load_json(path)
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(manifest, Mapping):
            continue
        if best is None:
            best = dict(manifest)
            continue
        # Prefer the manifest with more classified images.
        def _classified(m: Mapping[str, Any]) -> int:
            return sum(
                1
                for img in m.get("images") or []
                if isinstance(img, Mapping) and img.get("visual_status") == "CLASSIFIED"
            )

        if _classified(manifest) > _classified(best):
            best = dict(manifest)
    return best


def _auto_by_key(path: Path | None) -> dict[str, dict[str, Any]]:
    if not path or not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    records = payload.get("annotations") or payload
    if not isinstance(records, Mapping):
        return {}
    return {str(k): v for k, v in records.items() if isinstance(v, Mapping)}


def _latest_auto_annotations() -> Path | None:
    files = sorted(
        (ROOT / "data/research/normalized").glob("strategy-v2-auto-annotations-*.json")
    )
    return files[-1] if files else None


def build_contexts(
    *,
    shadow_path: Path = DEFAULT_SHADOW,
    enrichment_path: Path = DEFAULT_ENRICHMENT,
    photo_evidence_path: Path = DEFAULT_PHOTO_EVIDENCE,
    splits_path: Path = DEFAULT_SPLITS,
    cards_dir: Path | None = None,
    auto_path: Path | None = None,
) -> list[dict[str, Any]]:
    splits = load_split_manifest(splits_path)
    shadow_rows = _rows_by_key(shadow_path)
    candidates = _candidates_by_key(enrichment_path)
    photo_rows = (
        _rows_by_key(photo_evidence_path) if photo_evidence_path.exists() else {}
    )
    auto_rows = _auto_by_key(auto_path if auto_path is not None else _latest_auto_annotations())

    groups: dict[str, list[str]] = {"calibration": [], "blind": [], "unassigned": []}
    for key in shadow_rows:
        groups[split_for_key(splits, key)].append(key)

    def _sort(keys: list[str]) -> list[str]:
        return sorted(
            keys, key=lambda k: str(shadow_rows[k].get("address") or k)
        )

    contexts: list[dict[str, Any]] = []
    for split in ("calibration", "blind", "unassigned"):
        ordered = _sort(groups.get(split) or [])
        for i, key in enumerate(ordered):
            prev_key = ordered[i - 1] if i > 0 else None
            next_key = ordered[i + 1] if i + 1 < len(ordered) else None
            contexts.append(
                build_card_context(
                    shadow_row=shadow_rows[key],
                    candidate=candidates.get(key),
                    photo_manifest=_photo_manifest(key, photo_rows.get(key)),
                    split=split,
                    prev_key=prev_key,
                    next_key=next_key,
                    assets_from_dir=cards_dir,
                    auto=auto_rows.get(key),
                )
            )
    return contexts


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shadow", type=Path, default=DEFAULT_SHADOW)
    parser.add_argument("--enrichment", type=Path, default=DEFAULT_ENRICHMENT)
    parser.add_argument("--photo-evidence", type=Path, default=DEFAULT_PHOTO_EVIDENCE)
    parser.add_argument("--splits", type=Path, default=DEFAULT_SPLITS)
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--output-date", default=None)
    parser.add_argument(
        "--auto",
        type=Path,
        default=None,
        help="annotations.auto.json to bake in; defaults to latest "
        "strategy-v2-auto-annotations-*.json",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    date_value = args.output_date or datetime.now(timezone.utc).date().isoformat()
    output_dir = args.output_dir or (ROOT / "reports/calibration" / date_value)

    contexts = build_contexts(
        shadow_path=args.shadow,
        enrichment_path=args.enrichment,
        photo_evidence_path=args.photo_evidence,
        splits_path=args.splits,
        cards_dir=(output_dir / "cards").resolve(),
        auto_path=args.auto,
    )
    generated_at = datetime.now(timezone.utc).isoformat()
    summary = write_packet(output_dir, contexts, generated_at=generated_at)
    counts = {
        "calibration": sum(1 for c in contexts if c.get("split") == "calibration"),
        "blind": sum(1 for c in contexts if c.get("split") == "blind"),
        "unassigned": sum(1 for c in contexts if c.get("split") == "unassigned"),
        "with_photos": sum(1 for c in contexts if c.get("photo_count")),
        "auto_prefilled": sum(1 for c in contexts if c.get("auto")),
    }
    print(
        json.dumps(
            {"status": "COMPLETED", "counts": counts, "artifacts": summary},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
