#!/usr/bin/env python3
"""Evaluate Strategy V2 human calibration annotations against model outputs.

Reads annotations (per-card JSON exports, a packet-level annotations.json, a
filled annotations-template.csv, or a directory of JSON files), validates
them, and writes evaluation.md + disagreements.md under
reports/calibration/eval-<date>/.  Appends one JSONL record per run to
data/research/history/calibration-evals.jsonl.

Default scope is the 120-property calibration split.  ``--split blind`` is
reserved for the final acceptance run after calibration converges; blind
labels never drive iteration.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from northshore_watchdog.research.strategy_v2_calibration import (  # noqa: E402
    BAND_LABELS,
    DIMENSION_MODEL_FIELD,
    DISAGREEMENT_THRESHOLD,
    TOP_N_DEPTHS,
    HumanAnnotation,
    append_eval_history,
    evaluate_calibration,
    load_annotations,
    load_split_manifest,
    render_disagreements_markdown,
    render_evaluation_markdown,
    split_for_key,
)

DEFAULT_MODEL = ROOT / "data/research/normalized/portfolio-analysis-2026-09-18.json"
DEFAULT_SHADOW = ROOT / "data/research/normalized/strategy-v2-shadow-2026-09-09.json"
DEFAULT_PHOTO_EVIDENCE = (
    ROOT / "data/research/normalized/strategy-v2-online-photo-evidence-2026-09-12.json"
)
DEFAULT_SPLITS = ROOT / "data/research/history/strategy-v2-shadow-splits.json"
DEFAULT_HISTORY = ROOT / "data/research/history/calibration-evals.jsonl"


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _rows_by_key(path: Path) -> dict[str, dict[str, Any]]:
    payload = _load_json(path)
    rows = payload.get("properties") or []
    return {
        str(row["property_key"]): row
        for row in rows
        if isinstance(row, Mapping) and row.get("property_key")
    }


def _retained_keys(path: Path) -> set[str]:
    if not path.exists():
        return set()
    payload = _load_json(path)
    keys = (payload.get("shortlist") or {}).get("property_keys") or []
    return {str(key) for key in keys}


def _fingerprint(paths: Mapping[str, Path]) -> str:
    """Digest the evaluation inputs + frozen criteria for history comparison."""

    basis: dict[str, Any] = {
        "artifacts": {},
        "band_labels": list(BAND_LABELS),
        "dimension_model_field": dict(sorted(DIMENSION_MODEL_FIELD.items())),
        "disagreement_threshold": DISAGREEMENT_THRESHOLD,
        "top_n_depths": list(TOP_N_DEPTHS),
    }
    for name, path in sorted(paths.items()):
        try:
            basis["artifacts"][name] = {
                "path": str(path),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        except OSError:
            basis["artifacts"][name] = {"path": str(path), "sha256": None}
    return hashlib.sha256(
        json.dumps(basis, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:16]


def _dedupe(
    annotations: list[HumanAnnotation],
) -> tuple[list[HumanAnnotation], list[str]]:
    """Last-annotated_at-wins for duplicate property keys; warnings recorded."""

    by_key: dict[str, HumanAnnotation] = {}
    order: list[str] = []
    warnings: list[str] = []
    for ann in annotations:
        if ann.property_key not in by_key:
            order.append(ann.property_key)
            by_key[ann.property_key] = ann
            continue
        previous = by_key[ann.property_key]
        winner = ann
        if previous.annotated_at and ann.annotated_at:
            winner = ann if ann.annotated_at >= previous.annotated_at else previous
        by_key[ann.property_key] = winner
        warnings.append(
            f"{ann.property_key}: duplicate annotations; kept "
            f"annotated_at={winner.annotated_at or 'UNKNOWN'}"
        )
    return [by_key[key] for key in order], warnings


def _load_pairwise(path: Path) -> list[dict[str, Any]]:
    with path.open(newline="", encoding="utf-8-sig") as fh:
        return [dict(row) for row in csv.DictReader(fh)]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--annotations",
        type=Path,
        required=True,
        help="JSON file, CSV file, or directory of per-card JSON exports",
    )
    parser.add_argument("--split", choices=["calibration", "blind"], default="calibration")
    parser.add_argument("--pairwise", type=Path, default=None,
                        help="optional CSV: property_key_a,property_key_b,winner(A|B)")
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--shadow", type=Path, default=DEFAULT_SHADOW)
    parser.add_argument("--photo-evidence", type=Path, default=DEFAULT_PHOTO_EVIDENCE)
    parser.add_argument("--splits", type=Path, default=DEFAULT_SPLITS)
    parser.add_argument("--history", type=Path, default=DEFAULT_HISTORY)
    parser.add_argument("--out-dir", type=Path, default=None)
    parser.add_argument("--output-date", default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    date_value = args.output_date or datetime.now(timezone.utc).date().isoformat()
    out_dir = args.out_dir or (ROOT / "reports/calibration" / f"eval-{date_value}")

    model_rows = _rows_by_key(args.model)
    shadow_rows = _rows_by_key(args.shadow)
    universe = set(model_rows) | set(shadow_rows)
    splits = load_split_manifest(args.splits)

    annotations, errors = load_annotations(args.annotations)

    # Assign splits from the frozen manifest when the label source didn't
    # carry one (e.g. a hand-edited CSV with the split column cleared).
    for ann in annotations:
        if ann.split == "unassigned":
            ann.split = split_for_key(splits, ann.property_key)

    # Reject unknown property keys outright.
    unknown = [ann.property_key for ann in annotations if ann.property_key not in universe]
    for key in unknown:
        errors.append(f"{key}: not a known property_key in the 166-property universe")
    annotations = [a for a in annotations if a.property_key in universe]

    annotations, duplicate_warnings = _dedupe(annotations)

    if errors:
        for err in errors:
            print(f"ERROR: {err}", file=sys.stderr)
        print(
            f"{len(errors)} annotation error(s); fix the input and re-run. "
            "No reports written.",
            file=sys.stderr,
        )
        return 2

    pairwise = _load_pairwise(args.pairwise) if args.pairwise else None
    fingerprint = _fingerprint(
        {
            "model": args.model,
            "shadow": args.shadow,
            "photo_evidence": args.photo_evidence,
            "splits": args.splits,
        }
    )

    result = evaluate_calibration(
        annotations,
        model_rows=model_rows,
        shadow_rows=shadow_rows,
        retained_keys=_retained_keys(args.photo_evidence),
        split=args.split,
        config_fingerprint=fingerprint,
        pairwise=pairwise,
    )
    result["duplicate_warnings"] = duplicate_warnings
    result["annotations_source"] = str(args.annotations)
    result["evaluated_at"] = datetime.now(timezone.utc).isoformat()

    out_dir.mkdir(parents=True, exist_ok=True)
    eval_path = out_dir / "evaluation.md"
    disag_path = out_dir / "disagreements.md"
    eval_path.write_text(render_evaluation_markdown(result), encoding="utf-8")
    disag_path.write_text(render_disagreements_markdown(result), encoding="utf-8")
    entry = append_eval_history(args.history, result)

    if args.split == "blind":
        print(
            "NOTE: blind-split evaluation recorded. Blind labels must not be "
            "used to tune criteria.",
            file=sys.stderr,
        )
    for warning in duplicate_warnings:
        print(f"WARNING: {warning}", file=sys.stderr)

    recall = result.get("recall") or {}
    print(
        json.dumps(
            {
                "status": result.get("status"),
                "split": result.get("split"),
                "labeled_count": result.get("labeled_count"),
                "must_keep_recall": recall.get("must_keep_recall"),
                "keep_recall": recall.get("keep_recall"),
                "pass_leakage": len(result.get("pass_leakage") or []),
                "named_case_failures": [
                    c["id"]
                    for c in result.get("named_case_checks") or []
                    if c.get("status") == "FAIL"
                ],
                "eval_id": entry.get("eval_id"),
                "artifacts": {
                    "evaluation": str(eval_path),
                    "disagreements": str(disag_path),
                    "history": str(args.history),
                },
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
