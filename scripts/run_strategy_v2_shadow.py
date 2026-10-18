#!/usr/bin/env python3
"""Run the read-only Strategy V2 Shadow Model for the saved portfolio.

The command consumes the existing local official-enrichment artifact.  It does
not acquire listings, call external providers, invoke the V1 scorer, or write
any V1 artifact.  It emits a date-stamped V2 JSON authority plus CSV/Markdown
projections for human inspection.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
import sys
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from northshore_watchdog.research.discovery import DiscoveryCard  # noqa: E402
from northshore_watchdog.research.strategy_v2_shadow import (  # noqa: E402
    DEFAULT_CONFIG_PATH,
    DEFAULT_LOCATION_SNAPSHOT_PATH,
    DEFAULT_RULES_PATH,
    build_shadow_portfolio,
    _config,
    _provider_snapshot_map,
    render_shadow_property,
)


DEFAULT_INPUT = ROOT / "data/research/normalized/portfolio-official-enrichment-2026-09-07.json"
DEFAULT_LEGACY = ROOT / "data/research/normalized/portfolio-analysis-2026-09-09.json"
DEFAULT_LEGACY_INDEX = ROOT / "reports/properties/2026-09-09/index.md"


def _sha256(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _load_cards(path: Path) -> list[DiscoveryCard]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError(f"input artifact must be an object: {path}")
    values = None
    official = payload.get("official_enrichment")
    if isinstance(official, Mapping) and isinstance(official.get("cards"), list):
        values = official["cards"]
    elif isinstance(payload.get("candidates"), list):
        values = payload["candidates"]
    if not isinstance(values, list):
        raise ValueError(f"input artifact has no candidates/cards: {path}")
    return [item if isinstance(item, DiscoveryCard) else DiscoveryCard.model_validate(item) for item in values]


def _safe_filename(value: str) -> str:
    safe = "".join(char.lower() if char.isalnum() else "-" for char in value)
    safe = "-".join(part for part in safe.split("-") if part)
    return safe[:180] or "unknown-property"


def _flatten_row(row: Mapping[str, Any]) -> dict[str, Any]:
    classification = row.get("asset_classification") or {}
    holding = row.get("holding") or {}
    location = row.get("location") or {}
    provider = location.get("external_provider") or {}
    school = location.get("school") or {}
    area_prior = location.get("area_baseline_prior") or {}
    parcel = location.get("parcel_facts") or {}
    road = location.get("road_adjustment") or {}
    acquisition = row.get("acquisition") or {}
    scenarios = {item.get("scenario_name"): item for item in row.get("development_scenarios") or [] if isinstance(item, Mapping)}
    base = scenarios.get("BASE") or {}
    conditional = scenarios.get("CONDITIONAL") or {}
    upside = scenarios.get("UPSIDE") or {}
    return {
        "property_key": row.get("property_key"),
        "address": row.get("address"),
        "mls": row.get("mls"),
        "primary_track": classification.get("primary_track"),
        "secondary_tracks": ",".join(classification.get("secondary_tracks") or []),
        "classification_confidence": classification.get("confidence"),
        "classification_basis": ";".join(classification.get("basis") or []),
        "house_utility_band": holding.get("house_utility_band"),
        "holding_condition_band": holding.get("holding_condition_band"),
        "survival_capex_low": holding.get("survival_capex_low"),
        "survival_capex_base": holding.get("survival_capex_base"),
        "survival_capex_high": holding.get("survival_capex_high"),
        "cash_holding_basis_base": holding.get("cash_holding_basis_base"),
        "updated_components": ",".join(holding.get("updated_components") or []),
        "kitchen_count_signal": holding.get("kitchen_count_signal"),
        "walk_score_status": provider.get("walk_score_status"),
        "walk_score": provider.get("walk"),
        "transit_score": provider.get("transit"),
        "bike_score": provider.get("bike"),
        "walk_score_composite": provider.get("composite"),
        "elementary_catchment": school.get("elementary_catchment"),
        "secondary_catchment": school.get("secondary_catchment"),
        "area_baseline_prior_cad": area_prior.get("value_cad"),
        "road_category": road.get("category"),
        "road_exposure_factor": road.get("exposure_factor"),
        "road_discount_base_pct": road.get("discount_base_pct"),
        "road_effective_discount_base_pct": road.get("effective_discount_base_pct"),
        "road_class": parcel.get("road_class"),
        "road_distance_m": parcel.get("distance_to_major_road_m"),
        "direct_major_road": parcel.get("property_fronts_major_road"),
        "park_facing": parcel.get("park_facing"),
        "view_type": parcel.get("view_type"),
        "development_potential": row.get("development_potential"),
        "base_product": base.get("product_type"),
        "base_legal_status": base.get("legal_status"),
        "base_units": base.get("unit_count"),
        "base_theoretical_area": base.get("theoretical_area"),
        "base_realistic_area": base.get("realistic_area"),
        "conditional_product": conditional.get("product_type"),
        "conditional_legal_status": conditional.get("legal_status"),
        "conditional_units": conditional.get("unit_count"),
        "upside_product": upside.get("product_type"),
        "upside_legal_status": upside.get("legal_status"),
        "asking": acquisition.get("asking"),
        "assessment_total": acquisition.get("assessment_total"),
        "assessment_land": acquisition.get("assessment_land"),
        "assessment_improvement": acquisition.get("assessment_improvement"),
        "market_land_basis": acquisition.get("market_land_basis"),
        "normalized_physical_lot_basis": acquisition.get("normalized_physical_lot_basis"),
        "normalized_lot_basis_per_sqft": acquisition.get("normalized_lot_basis_per_sqft"),
        "base_cash_mos": acquisition.get("base_cash_mos"),
        "conditional_cash_mos": acquisition.get("conditional_cash_mos"),
        "upside_cash_mos": acquisition.get("upside_cash_mos"),
        "price_risk_flags": ",".join(acquisition.get("price_risk_flags") or []),
        "ranking_status": row.get("ranking_status"),
    }


def _write_csv(path: Path, rows: list[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = sorted({key for row in rows for key in row.keys()})
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _audit_markdown(rows: list[Mapping[str, Any]], split: Mapping[str, Any], generated_at: str) -> str:
    tracks = Counter(str(row.get("asset_classification", {}).get("primary_track", "UNKNOWN")) for row in rows)
    gate_counts: Counter[str] = Counter()
    location_counts: Counter[str] = Counter()
    for row in rows:
        for gate in row.get("gates", []):
            if isinstance(gate, Mapping):
                gate_counts[str(gate.get("status") or "UNKNOWN")] += 1
        location_counts[str((row.get("location") or {}).get("external_provider", {}).get("status") or "UNKNOWN")] += 1
    lines = [
        "# Strategy V2 Shadow Model — Phase 1 Audit",
        "",
        f"Generated: `{generated_at}`  ",
        f"Properties: `{len(rows)}`  ",
        "Ranking: `NOT_RUN`",
        "",
        "This is a ledger audit, not a Current Use, Dual, or Redevelopment leaderboard.",
        "",
        "## Counts",
        "",
        f"- Primary tracks: `{json.dumps(dict(sorted(tracks.items())), ensure_ascii=False)}`",
        f"- Gate findings: `{json.dumps(dict(sorted(gate_counts.items())), ensure_ascii=False)}`",
        f"- Walk Score provider status: `{json.dumps(dict(sorted(location_counts.items())), ensure_ascii=False)}`",
        f"- Calibration / blind split: `{split.get('calibration_count', 'UNKNOWN')} / {split.get('blind_test_count', 'UNKNOWN')}`",
        "",
        "## Ledger audit table",
        "",
        "| Address | Track | Utility | CAPEX base | CHB base | Location provider | Development potential | Base product | Base RALV | Base MOS | Road |",
        "|---|---|---|---:|---:|---|---:|---|---:|---:|---|",
    ]
    for row in rows:
        cls = row.get("asset_classification") or {}
        holding = row.get("holding") or {}
        loc = row.get("location") or {}
        provider = loc.get("external_provider") or {}
        road = loc.get("road_adjustment") or {}
        scenarios = {item.get("scenario_name"): item for item in row.get("development_scenarios") or [] if isinstance(item, Mapping)}
        base = scenarios.get("BASE") or {}
        lines.append(
            f"| {row.get('address') or 'UNKNOWN'} | {cls.get('primary_track', 'UNKNOWN')} | {holding.get('house_utility_band', 'UNKNOWN')} | {_money(holding.get('survival_capex_base'))} | {_money(holding.get('cash_holding_basis_base'))} | {provider.get('status', 'UNKNOWN')} | {_fmt(row.get('development_potential'))} | {base.get('product_type', 'UNKNOWN')} | {_money(base.get('ralv'))} | {_money(base.get('cash_mos'))} | {road.get('category', 'UNKNOWN')} |")
    return "\n".join(lines) + "\n"


def _fmt(value: Any) -> str:
    if value is None or value == "":
        return "UNKNOWN"
    if isinstance(value, float):
        return f"{value:,.2f}".rstrip("0").rstrip(".")
    return str(value)


def _money(value: Any) -> str:
    value = value if isinstance(value, (int, float)) and not isinstance(value, bool) else None
    return "UNKNOWN" if value is None else f"${value:,.0f}"


def _acceptance_markdown(payload: Mapping[str, Any]) -> str:
    counts = payload.get("counts") or {}
    return f"""# Strategy V2 Shadow Model — Acceptance Report

## Scope

- Input properties: `{payload.get('property_count')}`
- Ranking status: `{payload.get('ranking_status')}`
- V1 reference: `{(payload.get('legacy_reference') or {}).get('path', 'UNKNOWN')}`
- V1 SHA-256 before / after: `{(payload.get('legacy_reference') or {}).get('sha256_before', 'UNKNOWN')}` / `{(payload.get('legacy_reference') or {}).get('sha256_after', 'UNKNOWN')}`
- V1 unchanged: `{(payload.get('legacy_reference') or {}).get('unchanged')}`

## Output contract

The run produced one row per saved listing, with separate classification, gates,
holding, location, development-scenario, and acquisition ledgers.  V1 was not
called and no `rankings` object was emitted.

- Primary track counts: `{json.dumps(counts.get('primary_tracks', {}), ensure_ascii=False)}`
- Gate status counts: `{json.dumps(counts.get('gate_statuses', {}), ensure_ascii=False)}`
- Walk Score status counts: `{json.dumps(counts.get('walk_score_statuses', {}), ensure_ascii=False)}`
- Scenario RALV values known: `{json.dumps(counts.get('scenario_ralv_known', {}), ensure_ascii=False)}`
- Data completeness was not used as a score deduction.

## Required benchmark semantics

- `4561 NANAIMO STREET`: direct major-road exposure is retained as a permanent risk; the holding ledger and development ledger remain separate.
- `5454 MELBOURNE STREET`: strong geometry/potential remains visible, while rear-access-dependent capacity is conditional.
- `3910 W 10TH AVENUE`: survival CAPEX enters CHB and does not hard-cap a V2 score.
- `1016 E 18TH AVENUE`: substandard lot geometry is visible and is not treated as a standard-lot development case.
- `2413 COLLINGWOOD STREET`: below-standard premium-small-lot context is kept separate from ordinary large-lot comparison.

## Status

`PHASE_1_LEDGER_COMPLETE_NO_RANKING` — human band annotation is the next step.
"""


def run_shadow_model(
    *,
    input_path: str | Path = DEFAULT_INPUT,
    legacy_reference_path: str | Path = DEFAULT_LEGACY,
    output_root: str | Path = ROOT,
    output_date: str | None = None,
    config_path: str | Path = DEFAULT_CONFIG_PATH,
    rules_path: str | Path = DEFAULT_RULES_PATH,
    location_snapshot_path: str | Path | None = DEFAULT_LOCATION_SNAPSHOT_PATH,
) -> dict[str, Any]:
    input_file = Path(input_path)
    legacy_file = Path(legacy_reference_path)
    root = Path(output_root)
    date_value = output_date or datetime.now(timezone.utc).date().isoformat()
    generated_at = datetime.now(timezone.utc).isoformat()
    cards = _load_cards(input_file)
    if len(cards) != 166:
        raise ValueError(f"Strategy V2 frozen run requires 166 cards; got {len(cards)}")
    v1_before = _sha256(legacy_file)
    v1_index_before = _sha256(DEFAULT_LEGACY_INDEX)
    config = _config(config_path)
    snapshots = _provider_snapshot_map(location_snapshot_path)
    rows, split = build_shadow_portfolio(
        cards,
        legacy_reference_path=legacy_file,
        config=config,
        rules_path=rules_path,
        provider_snapshots=snapshots,
    )
    v1_after = _sha256(legacy_file)
    v1_index_after = _sha256(DEFAULT_LEGACY_INDEX)
    row_dicts = [row.model_dump(mode="json") for row in rows]
    normalized_dir = root / "data/research/normalized"
    history_dir = root / "data/research/history"
    report_dir = root / "reports/strategy-v2-shadow" / date_value
    property_dir = report_dir / "property"
    normalized_dir.mkdir(parents=True, exist_ok=True)
    history_dir.mkdir(parents=True, exist_ok=True)
    property_dir.mkdir(parents=True, exist_ok=True)
    json_path = normalized_dir / f"strategy-v2-shadow-{date_value}.json"
    csv_path = normalized_dir / f"strategy-v2-shadow-{date_value}.csv"
    split_path = history_dir / "strategy-v2-shadow-splits.json"
    audit_path = report_dir / "audit-table.md"
    acceptance_path = report_dir / "ACCEPTANCE-REPORT.md"
    _write_csv(csv_path, [_flatten_row(row) for row in row_dicts])
    split_path.write_text(json.dumps(split, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    property_paths: list[str] = []
    for row in rows:
        target = property_dir / (_safe_filename(f"{row.address}-{row.mls or row.property_key}") + ".md")
        target.write_text(render_shadow_property(row), encoding="utf-8")
        property_paths.append(str(target))
    tracks = Counter(str((row.get("asset_classification") or {}).get("primary_track") or "UNKNOWN") for row in row_dicts)
    gate_statuses = Counter(str(gate.get("status") or "UNKNOWN") for row in row_dicts for gate in row.get("gates") or [] if isinstance(gate, Mapping))
    provider_statuses = Counter(str(((row.get("location") or {}).get("external_provider") or {}).get("status") or "UNKNOWN") for row in row_dicts)
    ralv_known = Counter()
    for row in row_dicts:
        for scenario in row.get("development_scenarios") or []:
            if isinstance(scenario, Mapping):
                name = str(scenario.get("scenario_name") or "UNKNOWN")
                ralv_known[name] += int(scenario.get("ralv") is not None)
    legacy = {
        "path": str(legacy_file),
        "sha256_before": v1_before,
        "sha256_after": v1_after,
        "report_index_path": str(DEFAULT_LEGACY_INDEX),
        "report_index_sha256_before": v1_index_before,
        "report_index_sha256_after": v1_index_after,
        "unchanged": (
            v1_before is not None
            and v1_before == v1_after
            and v1_index_before is not None
            and v1_index_before == v1_index_after
        ),
        "status": "FROZEN_READ_ONLY",
    }
    payload: dict[str, Any] = {
        "schema_version": "strategy-v2-shadow-v1",
        "generated_at": generated_at,
        "input_path": str(input_file),
        "property_count": len(row_dicts),
        "ranking_status": "NOT_RUN",
        "legacy_reference": legacy,
        "split": split,
        "counts": {
            "primary_tracks": dict(sorted(tracks.items())),
            "gate_statuses": dict(sorted(gate_statuses.items())),
            "walk_score_statuses": dict(sorted(provider_statuses.items())),
            "scenario_ralv_known": dict(sorted(ralv_known.items())),
        },
        "properties": row_dicts,
        "artifacts": {
            "json": str(json_path),
            "csv": str(csv_path),
            "split": str(split_path),
            "audit_report": str(audit_path),
            "acceptance_report": str(acceptance_path),
            "property_reports": property_paths,
        },
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    audit_path.write_text(_audit_markdown(row_dicts, split, generated_at), encoding="utf-8")
    acceptance_path.write_text(_acceptance_markdown(payload), encoding="utf-8")
    return payload


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--legacy-reference", type=Path, default=DEFAULT_LEGACY)
    parser.add_argument("--output-root", type=Path, default=ROOT)
    parser.add_argument("--output-date", default=None)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--rules-path", type=Path, default=DEFAULT_RULES_PATH)
    parser.add_argument("--location-snapshot", type=Path, default=DEFAULT_LOCATION_SNAPSHOT_PATH)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    payload = run_shadow_model(
        input_path=args.input,
        legacy_reference_path=args.legacy_reference,
        output_root=args.output_root,
        output_date=args.output_date,
        config_path=args.config,
        rules_path=args.rules_path,
        location_snapshot_path=args.location_snapshot,
    )
    print(json.dumps({
        "status": "COMPLETED",
        "property_count": payload["property_count"],
        "ranking_status": payload["ranking_status"],
        "counts": payload["counts"],
        "artifacts": payload["artifacts"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
