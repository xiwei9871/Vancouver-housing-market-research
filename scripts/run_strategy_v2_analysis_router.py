#!/usr/bin/env python3
"""Allocate the Strategy V2 visual-analysis budget without changing scores.

The runner keeps every property in the structured research universe, applies
cheap track-aware routing rules, and uses the existing Phase 2A shortlist
selector only to choose the small visual-analysis budget.  It never calls a
network endpoint or a vision model.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from northshore_watchdog.research.strategy_v2_analysis_router import (  # noqa: E402
    AnalysisRoutingDecision,
    apply_visual_selection,
    classify_visual_eligibility,
)
from northshore_watchdog.research.strategy_v2_online_screening import (  # noqa: E402
    select_shortlist,
)


DEFAULT_INPUT_CSV = ROOT / "data/research/normalized/strategy-v2-online-screening-2026-09-11.csv"
DEFAULT_LISTING_INPUT = ROOT / "data/research/normalized/portfolio-official-enrichment-2026-09-07.json"
DEFAULT_PHOTO_ROOT = ROOT / "data/research/raw/photo-assets"
DEFAULT_CONFIG = ROOT / "config/strategy-v2-phase-2a.yaml"


def _resolve(path: str | Path, *, base: Path = ROOT) -> Path:
    value = Path(path)
    return value if value.is_absolute() else base / value


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        result = float(value)
        return result if math.isfinite(result) else None
    text = str(value).strip()
    if not text or text.upper() in {"UNKNOWN", "N/A", "NA", "NULL", "NONE", "UNAVAILABLE"}:
        return None
    match = re.search(r"[-+]?\d[\d,]*(?:\.\d+)?", text)
    if not match:
        return None
    try:
        result = float(match.group(0).replace(",", ""))
    except ValueError:
        return None
    return result if math.isfinite(result) else None


def _identity(value: Any) -> str:
    return " ".join(str(value or "").upper().split())


def _safe_filename(value: str) -> str:
    safe = "".join(char.lower() if char.isalnum() else "-" for char in str(value))
    return "-".join(part for part in safe.split("-") if part) or "unknown-property"


def _load_config(path: Path) -> Mapping[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise ValueError(f"config must contain a mapping: {path}")
    return value


def _load_listing_candidates(path: Path) -> tuple[dict[str, Mapping[str, Any]], dict[str, Mapping[str, Any]]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    candidates = payload.get("candidates") if isinstance(payload, Mapping) else None
    if not isinstance(candidates, list):
        raise ValueError(f"listing input must contain a candidates list: {path}")
    by_mls: dict[str, Mapping[str, Any]] = {}
    by_address: dict[str, Mapping[str, Any]] = {}
    for candidate in candidates:
        if not isinstance(candidate, Mapping):
            continue
        mls = _identity(candidate.get("mls_number") or candidate.get("mls"))
        address = _identity(candidate.get("address"))
        if mls:
            by_mls[mls] = candidate
        if address:
            by_address[address] = candidate
    return by_mls, by_address


def _read_photo_stats(photo_root: Path, property_key: str) -> dict[str, Any]:
    path = photo_root / _safe_filename(property_key) / "manifest.json"
    if not path.is_file():
        return {
            "manifest_path": str(path),
            "manifest_found": False,
            "manifest_source_status": "UNKNOWN",
            "unique_photo_count": None,
        }
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {
            "manifest_path": str(path),
            "manifest_found": False,
            "manifest_source_status": "INVALID",
            "unique_photo_count": None,
        }
    images = payload.get("images") if isinstance(payload, Mapping) else None
    if not isinstance(images, list):
        images = []
    unique_count = sum(
        1
        for image in images
        if isinstance(image, Mapping) and not bool(image.get("is_duplicate"))
    )
    return {
        "manifest_path": str(path),
        "manifest_found": True,
        "manifest_source_status": str(payload.get("source_status") or "UNKNOWN"),
        "unique_photo_count": unique_count,
    }


def _candidate_with_local_context(
    row: Mapping[str, Any],
    *,
    by_mls: Mapping[str, Mapping[str, Any]],
    by_address: Mapping[str, Mapping[str, Any]],
    photo_root: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    candidate = dict(row)
    mls = _identity(row.get("mls"))
    address = _identity(row.get("address"))
    source = by_mls.get(mls) or by_address.get(address) or {}
    if not candidate.get("description"):
        candidate["description"] = source.get("description") or ""
    if not candidate.get("property_key"):
        candidate["property_key"] = f"mls:{mls}" if mls else f"address:{address}"
    photo_stats = _read_photo_stats(photo_root, str(candidate["property_key"]))
    if photo_stats["unique_photo_count"] is None:
        fallback = _number(row.get("unique_photo_count"))
        photo_stats["unique_photo_count"] = int(fallback) if fallback is not None else None
    return candidate, photo_stats


def _int_or_none(value: Any) -> int | None:
    number = _number(value)
    return int(number) if number is not None else None


def _basic_failure(candidate: Mapping[str, Any], config: Mapping[str, Any]) -> bool:
    rules = config.get("analysis_routing") if isinstance(config.get("analysis_routing"), Mapping) else {}
    min_lot = _number(rules.get("min_home_lot_area_sqft")) or 3000
    min_beds = _number(rules.get("min_home_bedrooms")) or 3
    min_baths = _number(rules.get("min_home_bathrooms")) or 2
    lot = _number(candidate.get("lot_area_sqft"))
    beds = _number(candidate.get("bedrooms"))
    baths = _number(candidate.get("bathrooms"))
    return (
        lot is None
        or beds is None
        or baths is None
        or lot < min_lot
        or beds < min_beds
        or baths < min_baths
    )


def _route_counts(candidates: Sequence[Mapping[str, Any]], config: Mapping[str, Any]) -> dict[str, Any]:
    rules = config.get("analysis_routing") if isinstance(config.get("analysis_routing"), Mapping) else {}
    min_lot = _number(rules.get("min_home_lot_area_sqft")) or 3000
    min_beds = _number(rules.get("min_home_bedrooms")) or 3
    min_baths = _number(rules.get("min_home_bathrooms")) or 2
    min_photos = _number(rules.get("min_visual_photo_count")) or 3
    counters: Counter[str] = Counter()
    for candidate in candidates:
        lot = _number(candidate.get("lot_area_sqft"))
        beds = _number(candidate.get("bedrooms"))
        baths = _number(candidate.get("bathrooms"))
        photos = _number(candidate.get("photo_count"))
        counters["lot_lt_minimum"] += lot is not None and lot < min_lot
        counters["beds_lt_minimum"] += beds is not None and beds < min_beds
        counters["baths_lt_minimum"] += baths is not None and baths < min_baths
        counters["photos_lt_minimum"] += photos is not None and photos < min_photos
        counters["home_baseline_failure_or_unknown"] += _basic_failure(candidate, config)
    counters["any_cheap_rule_failure"] = sum(
        1
        for candidate in candidates
        if _basic_failure(candidate, config)
        or (_number(candidate.get("photo_count")) is not None and _number(candidate.get("photo_count")) < min_photos)
    )
    return dict(counters)


def _csv_value(value: Any) -> Any:
    if isinstance(value, (list, tuple, set)):
        return ";".join(str(item) for item in value)
    if isinstance(value, Mapping):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return value


def _route_row(
    candidate: Mapping[str, Any],
    decision: AnalysisRoutingDecision,
    *,
    photo_stats: Mapping[str, Any],
    selection_rank: int | None,
    shortlist_selected: bool,
    max_attempts: int,
) -> dict[str, Any]:
    image_count = decision.visual_budget_images
    estimated_retry_budget = image_count * max(0, max_attempts - 1)
    data = {
        "property_key": decision.property_key,
        "address": candidate.get("address"),
        "mls": candidate.get("mls"),
        "neighbourhood": candidate.get("neighbourhood"),
        "asking": candidate.get("asking"),
        "primary_track": decision.primary_track,
        "analysis_route": decision.analysis_route,
        "visual_eligibility": decision.visual_eligibility,
        "selected_for_visual": decision.selected_for_visual,
        "visual_budget_images": decision.visual_budget_images,
        "photo_count": decision.photo_count,
        "unique_photo_count": decision.unique_photo_count,
        "lot_area_sqft": decision.lot_area_sqft,
        "bedrooms": decision.bedrooms,
        "bathrooms": decision.bathrooms,
        "home_fit": decision.home_fit,
        "development_text_signal": decision.development_text_signal,
        "flags": decision.flags,
        "reasons": decision.reasons,
        "selection_reasons": decision.selection_reasons,
        "shortlist_selected": shortlist_selected,
        "selection_rank": selection_rank,
        "image_count": image_count,
        "estimated_http_requests": image_count,
        "estimated_retry_budget": estimated_retry_budget,
        "estimated_max_http_attempts": image_count * max_attempts,
        "visual_request_mode": "ONE_REQUEST_PER_IMAGE",
        "visual_max_attempts": max_attempts,
        "photo_manifest_path": photo_stats.get("manifest_path"),
        "photo_manifest_found": photo_stats.get("manifest_found"),
        "photo_manifest_source_status": photo_stats.get("manifest_source_status"),
        "structured_analysis_preserved": True,
    }
    return data


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    fields = [
        "property_key",
        "address",
        "mls",
        "neighbourhood",
        "asking",
        "primary_track",
        "analysis_route",
        "visual_eligibility",
        "selected_for_visual",
        "visual_budget_images",
        "photo_count",
        "unique_photo_count",
        "lot_area_sqft",
        "bedrooms",
        "bathrooms",
        "home_fit",
        "development_text_signal",
        "flags",
        "reasons",
        "selection_reasons",
        "shortlist_selected",
        "selection_rank",
        "image_count",
        "estimated_http_requests",
        "estimated_retry_budget",
        "estimated_max_http_attempts",
        "visual_request_mode",
        "visual_max_attempts",
        "photo_manifest_path",
        "photo_manifest_found",
        "photo_manifest_source_status",
        "structured_analysis_preserved",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: _csv_value(row.get(field)) for field in fields})


def _status_for_shortlist(selected: Sequence[Mapping[str, Any]], target_range: Sequence[int]) -> str:
    minimum, maximum = int(target_range[0]), int(target_range[1])
    count = len(selected)
    if minimum <= count <= maximum:
        return "WITHIN_TARGET_RANGE"
    protected = any(
        bool(item.get("pareto_frontier")) or bool(item.get("exceptional_signal"))
        for item in selected
    )
    if count > maximum and protected:
        return "OVER_TARGET_DUE_TO_PROTECTED_SIGNALS"
    return "OUTSIDE_TARGET_RANGE_REVIEW"


def _report_markdown(payload: Mapping[str, Any]) -> str:
    counts = payload.get("counts") if isinstance(payload.get("counts"), Mapping) else {}
    shortlist = payload.get("shortlist") if isinstance(payload.get("shortlist"), Mapping) else {}
    properties = payload.get("properties") if isinstance(payload.get("properties"), list) else []
    selected = [item for item in properties if isinstance(item, Mapping) and item.get("selected_for_visual")]
    lines = [
        "# Strategy V2 Analysis Routing Report",
        "",
        f"Generated: `{payload.get('generated_at')}`  ",
        f"Properties preserved: **{payload.get('property_count')}**  ",
        "No visual API was invoked in this run.",
        "",
        "## Purpose",
        "",
        "This is a resource-routing artifact, not a property rejection list or an investment ranking. "
        "Every property remains in structured research. `STRUCTURED_ONLY` means no visual call is allocated; "
        "in this run, STRUCTURED_ONLY means no visual call. It does not mean the property has no land, zoning, "
        "price, or other research value.",
        "",
        "## Cheap-rule counts",
        "",
        f"- Lot below 3,000 sqft: `{counts.get('cheap_rules', {}).get('lot_lt_minimum')}`",
        f"- Bedrooms below 3: `{counts.get('cheap_rules', {}).get('beds_lt_minimum')}`",
        f"- Bathrooms below 2: `{counts.get('cheap_rules', {}).get('baths_lt_minimum')}`",
        f"- Fewer than 3 photos: `{counts.get('cheap_rules', {}).get('photos_lt_minimum')}`",
        f"- Any Home/photo baseline failure: `{counts.get('cheap_rules', {}).get('any_cheap_rule_failure')}`",
        "",
        "## Routing counts",
        "",
        f"- Visual eligibility: `{json.dumps(counts.get('visual_eligibility', {}), ensure_ascii=False, sort_keys=True)}`",
        f"- Final analysis route: `{json.dumps(counts.get('analysis_routes', {}), ensure_ascii=False, sort_keys=True)}`",
        f"- Primary tracks: `{json.dumps(counts.get('primary_tracks', {}), ensure_ascii=False, sort_keys=True)}`",
        f"- Listing development-language signal: `{json.dumps(counts.get('development_text_signal', {}), ensure_ascii=False, sort_keys=True)}`",
        "",
        "## Visual budget",
        "",
        f"- Shortlist count: `{shortlist.get('count')}`",
        f"- Target range: `{shortlist.get('target_range')}`",
        f"- Shortlist status: `{shortlist.get('status')}`",
        f"- Full-vision properties: `{counts.get('full_visual_properties')}`; images: `{counts.get('full_visual_images')}`",
        f"- Representative-vision properties: `{counts.get('representative_visual_properties')}`; images: `{counts.get('representative_visual_images')}`",
        f"- Image count allocated: `{counts.get('visual_image_count')}`",
        f"- Estimated HTTP requests (one request per image): `{counts.get('estimated_http_requests')}`",
        f"- Estimated retry budget (additional attempts; max attempts `{counts.get('visual_max_attempts')}`): `{counts.get('estimated_retry_budget')}`",
        f"- Estimated maximum total HTTP attempts: `{counts.get('estimated_max_http_attempts')}`",
        "",
        "## Selected visual pool",
        "",
        "| Rank | Address | Track | Route | Photos | Budget | Eligibility | Reasons |",
        "|---:|---|---|---|---:|---:|---|---|",
    ]
    for index, item in enumerate(selected, start=1):
        reasons = ", ".join(str(reason) for reason in item.get("selection_reasons") or [])
        lines.append(
            f"| {index} | {item.get('address') or 'UNKNOWN'} | {item.get('primary_track')} | "
            f"{item.get('analysis_route')} | {item.get('unique_photo_count') or 0} | "
            f"{item.get('visual_budget_images')} | {item.get('visual_eligibility')} | {reasons} |"
        )
    lines.extend([
        "",
        "## Explicit development wording",
        "",
        "Development wording changes the route; it is not an automatic rejection. Strong land-dominant wording can skip interior visual work, while a qualifying 3-bedroom/2-bathroom home remains eligible for representative evidence.",
        "",
        "## Limitations",
        "",
        "- The 25–35 pool is allocated with the existing track-local/Pareto shortlist selector; its signal is a resource-allocation aid, not a replacement for V1 or V2 valuation.",
        "- Fewer than three photos suppresses visual calls but does not lower the property’s structured value.",
        "- A listing’s rebuild/lot language is seller marketing evidence and must not be treated as official zoning or an inspection.",
        "- `photo_evidence_coverage` remains zero until a separate visual-classification run; it was not used as a photo-availability filter here.",
    ])
    return "\n".join(lines) + "\n"


def run_analysis_router(
    *,
    input_csv: str | Path = DEFAULT_INPUT_CSV,
    listing_input: str | Path = DEFAULT_LISTING_INPUT,
    photo_root: str | Path = DEFAULT_PHOTO_ROOT,
    config_path: str | Path = DEFAULT_CONFIG,
    output_root: str | Path = ROOT,
    output_date: str | None = None,
) -> dict[str, Any]:
    input_path = _resolve(input_csv)
    listing_path = _resolve(listing_input)
    photo_path = _resolve(photo_root)
    config = _load_config(_resolve(config_path))
    rows = list(csv.DictReader(input_path.open(newline="", encoding="utf-8")))
    if len(rows) != 166:
        raise ValueError(f"frozen 166-listing input required; got {len(rows)} from {input_path}")
    by_mls, by_address = _load_listing_candidates(listing_path)
    generated_at = _now().isoformat()
    date_value = output_date or _now().date().isoformat()

    contexts: list[tuple[dict[str, Any], dict[str, Any]]] = []
    base_decisions: dict[str, AnalysisRoutingDecision] = {}
    for row in rows:
        candidate, photo_stats = _candidate_with_local_context(
            row,
            by_mls=by_mls,
            by_address=by_address,
            photo_root=photo_path,
        )
        decision = classify_visual_eligibility(candidate, config=config)
        key = decision.property_key
        if key in base_decisions:
            raise ValueError(f"duplicate routing property key: {key}")
        base_decisions[key] = decision
        contexts.append((candidate, photo_stats))

    eligible = [
        candidate
        for candidate, _ in contexts
        if base_decisions[str(candidate["property_key"])].visual_eligibility
        in {"FULL_POOL", "REPRESENTATIVE_POOL"}
    ]
    shortlist_config = config.get("shortlist") if isinstance(config.get("shortlist"), Mapping) else {}
    target_raw = shortlist_config.get("target_range") or [25, 35]
    target_range = (int(target_raw[0]), int(target_raw[1]))
    photo_config = config.get("photo") if isinstance(config.get("photo"), Mapping) else {}
    visual_config = photo_config.get("visual") if isinstance(photo_config.get("visual"), Mapping) else {}
    visual_max_attempts = int(_number(visual_config.get("max_attempts")) or 3)
    targets_raw = shortlist_config.get("track_targets") if isinstance(shortlist_config.get("track_targets"), Mapping) else {}
    selected = select_shortlist(
        eligible,
        track_targets={str(key): int(value) for key, value in targets_raw.items()},
        target_range=target_range,
        special_tracks=shortlist_config.get("special_tracks_excluded") or ["SPECIAL_SITUATION"],
    )
    selected_by_key = {str(item.get("property_key")): item for item in selected}
    selection_ranks = {key: index for index, key in enumerate(selected_by_key, start=1)}

    output_rows: list[dict[str, Any]] = []
    for candidate, photo_stats in contexts:
        key = str(candidate["property_key"])
        decision = base_decisions[key]
        selected_item = selected_by_key.get(key)
        selection_reasons = selected_item.get("selection_reasons") if selected_item else []
        final_decision = apply_visual_selection(
            decision,
            selected=selected_item is not None,
            unique_photo_count=_int_or_none(photo_stats.get("unique_photo_count")),
            selection_reasons=selection_reasons or [],
            representative_photo_budget=int(
                _number(
                    (
                        config.get("analysis_routing")
                        if isinstance(config.get("analysis_routing"), Mapping)
                        else {}
                    ).get("representative_photo_budget")
                )
                or 3
            ),
        )
        if selected_item is None and decision.visual_eligibility in {"FULL_POOL", "REPRESENTATIVE_POOL"}:
            final_decision = final_decision.model_copy(
                update={
                    "flags": [*final_decision.flags, "NOT_SELECTED_VISUAL_BUDGET"],
                    "reasons": [*final_decision.reasons, "eligible for visual review but not allocated a slot in the current budget"],
                }
            )
        output_rows.append(
            _route_row(
                candidate,
                final_decision,
                photo_stats=photo_stats,
                selection_rank=selection_ranks.get(key),
                shortlist_selected=selected_item is not None,
                max_attempts=visual_max_attempts,
            )
        )

    route_counts = Counter(str(row["analysis_route"]) for row in output_rows)
    eligibility_counts = Counter(str(row["visual_eligibility"]) for row in output_rows)
    track_counts = Counter(str(row["primary_track"]) for row in output_rows)
    text_counts = Counter(str(row["development_text_signal"]) for row in output_rows)
    full_rows = [row for row in output_rows if row["analysis_route"] == "FULL_VISION"]
    representative_rows = [row for row in output_rows if row["analysis_route"] == "REPRESENTATIVE_VISION"]
    full_images = sum(int(row["visual_budget_images"]) for row in full_rows)
    representative_images = sum(int(row["visual_budget_images"]) for row in representative_rows)
    visual_image_count = full_images + representative_images
    cheap_rules = _route_counts([candidate for candidate, _ in contexts], config)
    shortlist_status = _status_for_shortlist(selected, target_range)
    report_root = _resolve(output_root) / "reports/strategy-v2-analysis-routing" / date_value
    normalized_root = _resolve(output_root) / "data/research/normalized"
    report_root.mkdir(parents=True, exist_ok=True)
    normalized_root.mkdir(parents=True, exist_ok=True)
    json_path = normalized_root / f"strategy-v2-analysis-routing-{date_value}.json"
    csv_path = normalized_root / f"strategy-v2-analysis-routing-{date_value}.csv"
    report_path = report_root / "routing-report.md"
    payload: dict[str, Any] = {
        "schema_version": "strategy-v2-analysis-routing-v2",
        "generated_at": generated_at,
        "input_csv": str(input_path),
        "listing_input": str(listing_path),
        "photo_root": str(photo_path),
        "visual_api_calls": 0,
        "property_count": len(output_rows),
        "shortlist": {
            "count": len(selected),
            "target_range": list(target_range),
            "status": shortlist_status,
            "track_targets": dict(targets_raw),
            "property_keys": list(selected_by_key),
        },
        "counts": {
            "analysis_routes": dict(sorted(route_counts.items())),
            "visual_eligibility": dict(sorted(eligibility_counts.items())),
            "primary_tracks": dict(sorted(track_counts.items())),
            "development_text_signal": dict(sorted(text_counts.items())),
            "cheap_rules": cheap_rules,
            "full_visual_properties": len(full_rows),
            "full_visual_images": full_images,
            "representative_visual_properties": len(representative_rows),
            "representative_visual_images": representative_images,
            "visual_image_count": visual_image_count,
            "estimated_http_requests": visual_image_count,
            "estimated_retry_budget": visual_image_count * max(0, visual_max_attempts - 1),
            "estimated_max_http_attempts": visual_image_count * visual_max_attempts,
            "visual_request_mode": "ONE_REQUEST_PER_IMAGE",
            "visual_max_attempts": visual_max_attempts,
            "structured_only_count": route_counts.get("STRUCTURED_ONLY", 0),
            "special_review_count": route_counts.get("SPECIAL_REVIEW", 0),
        },
        "properties": output_rows,
        "artifacts": {
            "json": str(json_path),
            "csv": str(csv_path),
            "report": str(report_path),
        },
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _write_csv(csv_path, output_rows)
    report_path.write_text(_report_markdown(payload), encoding="utf-8")
    return payload


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-csv", type=Path, default=DEFAULT_INPUT_CSV)
    parser.add_argument("--listing-input", type=Path, default=DEFAULT_LISTING_INPUT)
    parser.add_argument("--photo-root", type=Path, default=DEFAULT_PHOTO_ROOT)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output-root", type=Path, default=ROOT)
    parser.add_argument("--output-date", default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    payload = run_analysis_router(
        input_csv=args.input_csv,
        listing_input=args.listing_input,
        photo_root=args.photo_root,
        config_path=args.config,
        output_root=args.output_root,
        output_date=args.output_date,
    )
    print(json.dumps({
        "status": "COMPLETED",
        "property_count": payload["property_count"],
        "visual_api_calls": payload["visual_api_calls"],
        "shortlist": payload["shortlist"],
        "counts": payload["counts"],
        "artifacts": payload["artifacts"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
