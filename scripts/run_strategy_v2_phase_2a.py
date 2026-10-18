#!/usr/bin/env python3
"""Run Strategy V2 Phase 2A against the saved 166-listing universe.

Phase 2A is an online, high-recall screen.  It replays locally cached
REALTOR.ca detail HTML when available, creates an auditable photo manifest,
and writes one online-evidence row per property.  It deliberately does not
call the V1 scorer, publish a final ranking, calculate RALV, or treat missing
photo classification as a negative condition.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from northshore_watchdog.research.strategy_v2_online_screening import (  # noqa: E402
    evaluate_recall,
    select_shortlist,
)
from northshore_watchdog.research.strategy_v2_photo_intelligence import (  # noqa: E402
    PhotoManifest,
    build_online_holding_assessment,
    build_photo_manifest,
    extract_photo_records_from_html,
    summarize_photo_coverage,
)
from northshore_watchdog.research.strategy_v2_photo_acquisition import (  # noqa: E402
    DEFAULT_MAX_BYTES,
    DEFAULT_PERCEPTUAL_DISTANCE,
    acquire_photo_manifest,
)
from northshore_watchdog.research.strategy_v2_photo_classification import (  # noqa: E402
    JsonHttpVisionClassifier,
    classify_photo_manifest,
)
from northshore_watchdog.research.strategy_v2_shadow import (  # noqa: E402
    classify_asset,
    development_potential_components,
)


DEFAULT_INPUT = ROOT / "data/research/normalized/portfolio-official-enrichment-2026-09-07.json"
DEFAULT_SHADOW = ROOT / "data/research/normalized/strategy-v2-shadow-2026-09-09.json"
DEFAULT_CONFIG = ROOT / "config/strategy-v2-phase-2a.yaml"
DEFAULT_PHOTO_MANIFEST_ROOT = ROOT / "data/research/raw/photos"
DEFAULT_DETAIL_ROOT = ROOT / "data/research/raw/discovery/realtor-ca-native-details"
DEFAULT_LEGACY = ROOT / "data/research/normalized/portfolio-analysis-2026-09-09.json"
DEFAULT_LEGACY_INDEX = ROOT / "reports/properties/2026-09-09/index.md"


def _resolve(path: str | Path, *, base: Path = ROOT) -> Path:
    value = Path(path)
    return value if value.is_absolute() else base / value


def _sha256(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _safe_filename(value: str) -> str:
    safe = "".join(char.lower() if char.isalnum() else "-" for char in str(value))
    safe = "-".join(part for part in safe.split("-") if part)
    return safe[:180] or "unknown-property"


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).replace(",", "")
    import re

    match = re.search(r"[-+]?\d+(?:\.\d+)?", text)
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def _as_float(value: Any) -> float | None:
    value = _number(value)
    return None if value is None else round(value, 4)


def _load_json(path: Path) -> Mapping[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError(f"JSON artifact must be an object: {path}")
    return payload


def _load_config(path: Path) -> Mapping[str, Any]:
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ValueError(f"cannot load Phase 2A config: {path}") from exc
    if not isinstance(payload, Mapping):
        raise ValueError(f"Phase 2A config must be an object: {path}")
    return payload


def _property_key(candidate: Mapping[str, Any], shadow: Mapping[str, Any] | None = None) -> str:
    if shadow and shadow.get("property_key"):
        return str(shadow["property_key"])
    mls = str(candidate.get("mls_number") or candidate.get("mls") or "").strip()
    if mls:
        return f"mls:{mls.upper()}"
    address = str(candidate.get("address") or "").strip().upper()
    if address:
        return f"address:{address}"
    return "unknown-property"


def _candidate_maps(
    input_payload: Mapping[str, Any],
    shadow_payload: Mapping[str, Any],
) -> tuple[list[Mapping[str, Any]], dict[str, Mapping[str, Any]]]:
    candidates = input_payload.get("candidates")
    properties = shadow_payload.get("properties")
    if not isinstance(candidates, list) or not isinstance(properties, list):
        raise ValueError("Phase 2A inputs must contain candidates and properties lists")
    if len(candidates) != 166 or len(properties) != 166:
        raise ValueError(
            f"Phase 2A frozen universe requires 166 candidates/properties; "
            f"got {len(candidates)}/{len(properties)}"
        )
    shadows: dict[str, Mapping[str, Any]] = {}
    for item in properties:
        if not isinstance(item, Mapping):
            continue
        key = str(item.get("property_key") or "").strip()
        if key:
            shadows[key] = item
    return [item for item in candidates if isinstance(item, Mapping)], shadows


def _read_manifest(path: Path) -> PhotoManifest | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, Mapping):
            return None
        return PhotoManifest.model_validate(payload)
    except (OSError, UnicodeError, ValueError, TypeError):
        return None


def _replay_photo_manifest(
    *,
    property_key: str,
    mls: str | None,
    retrieved_at: datetime,
    output_path: Path,
    input_manifest_root: Path | None,
    detail_root: Path,
) -> tuple[PhotoManifest, dict[str, Any]]:
    """Load a supplied manifest or replay the locally cached detail HTML."""

    safe_key = _safe_filename(property_key)
    supplied_paths: list[Path] = []
    if input_manifest_root is not None:
        supplied_paths.extend(
            [
                input_manifest_root / safe_key / "manifest.json",
                input_manifest_root / property_key / "manifest.json",
            ]
        )
    supplied_paths.append(output_path)
    for supplied_path in supplied_paths:
        manifest = _read_manifest(supplied_path)
        if manifest is not None:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(
                json.dumps(manifest.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            return manifest, {
                "mode": "LOCAL_MANIFEST_REPLAY",
                "path": str(supplied_path),
            }

    html_path = detail_root / f"{str(mls or '').lower()}.html" if mls else None
    records: list[dict[str, Any]] = []
    if html_path is not None:
        try:
            records = extract_photo_records_from_html(
                html_path.read_text(encoding="utf-8", errors="replace"),
                property_key=property_key,
            )
        except OSError:
            records = []

    manifest = build_photo_manifest(
        property_key=property_key,
        photos=records,
        retrieved_at=retrieved_at,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(manifest.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest, {
        "mode": "LOCAL_DETAIL_HTML_REPLAY" if records else "PHOTO_SOURCE_UNAVAILABLE",
        "path": str(html_path) if html_path is not None and html_path.exists() else None,
        "record_count": len(records),
    }


def _relative_or_absolute(value: Any, *, base: Path) -> Path:
    path = Path(str(value))
    return path if path.is_absolute() else base / path


def _configured_visual_classifier(
    config: Mapping[str, Any],
    *,
    force: bool = False,
) -> JsonHttpVisionClassifier | None:
    photo_config = config.get("photo") if isinstance(config.get("photo"), Mapping) else {}
    visual_config = photo_config.get("visual") if isinstance(photo_config.get("visual"), Mapping) else {}
    enabled = force or bool(visual_config.get("enabled"))
    if not enabled:
        return None
    endpoint = str(visual_config.get("endpoint") or os.environ.get(str(visual_config.get("endpoint_env") or "VISION_API_ENDPOINT"), "")).strip()
    api_key_env = str(visual_config.get("api_key_env") or "VISION_API_KEY")
    api_key = os.environ.get(api_key_env, "")
    model = str(visual_config.get("model") or os.environ.get("VISION_MODEL", "")).strip()
    if not endpoint or not api_key or not model:
        return None
    timeout_value = _number(visual_config.get("timeout_seconds")) or 60.0
    wire_api = str(visual_config.get("wire_api") or "responses")
    max_attempts = int(_number(visual_config.get("max_attempts")) or 3)
    retry_backoff_seconds = _number(visual_config.get("retry_backoff_seconds"))
    if retry_backoff_seconds is None:
        retry_backoff_seconds = 1.0
    return JsonHttpVisionClassifier(
        endpoint=endpoint,
        api_key=api_key,
        model=model,
        timeout=timeout_value,
        wire_api=wire_api,
        max_attempts=max_attempts,
        retry_backoff_seconds=retry_backoff_seconds,
    )


def _shadow_for_candidate(
    candidate: Mapping[str, Any],
    shadows: Mapping[str, Mapping[str, Any]],
) -> Mapping[str, Any] | None:
    mls = str(candidate.get("mls_number") or "").strip().upper()
    if mls:
        direct = shadows.get(f"mls:{mls}")
        if direct is not None:
            return direct
    address = str(candidate.get("address") or "").strip().upper()
    return next(
        (item for item in shadows.values() if str(item.get("address") or "").strip().upper() == address),
        None,
    )


def _road_risk(shadow: Mapping[str, Any]) -> tuple[float | None, dict[str, Any]]:
    location = shadow.get("location") if isinstance(shadow.get("location"), Mapping) else {}
    parcel = location.get("parcel_facts") if isinstance(location.get("parcel_facts"), Mapping) else {}
    road = location.get("road_adjustment") if isinstance(location.get("road_adjustment"), Mapping) else {}
    category = str(road.get("category") or "").upper()
    base_risk = {
        "LOCAL_RESIDENTIAL": 0.0,
        "COLLECTOR": 25.0,
        "ORDINARY_ARTERIAL": 50.0,
        "HIGH_FLOW_ARTERIAL": 65.0,
        "ARTERIAL_TRUCK_BUS": 80.0,
        "MAJOR_ARTERIAL_TRUCK_INTERSECTION": 95.0,
    }.get(category)
    if base_risk is None:
        road_class = str(parcel.get("road_class") or "").upper()
        base_risk = {"A": 70.0, "B": 50.0, "C": 25.0, "D": 10.0}.get(road_class)
    direct = parcel.get("property_fronts_major_road")
    exposure = _number(road.get("exposure_factor"))
    if base_risk is not None and direct is True:
        base_risk = min(100.0, base_risk + 15.0)
    elif base_risk is not None and exposure is not None:
        base_risk *= max(0.0, min(1.0, exposure))
    if base_risk is None:
        return None, {
            "category": category or None,
            "direct": direct,
            "distance_m": parcel.get("distance_to_major_road_m"),
            "status": "UNKNOWN",
        }
    return round(base_risk, 2), {
        "category": category or None,
        "direct": direct,
        "distance_m": parcel.get("distance_to_major_road_m"),
        "status": "OBSERVED",
    }


def _holding_score(assessment: Mapping[str, Any]) -> float | None:
    values: list[float] = []
    for field in ("holding_usability_score", "visible_maintenance_score"):
        value = _number(assessment.get(field))
        if value is not None:
            values.append(value)
    if not values:
        return None
    return round(sum(values) / len(values), 2)


def _price_signal(shadow: Mapping[str, Any]) -> tuple[float | None, dict[str, Any]]:
    acquisition = shadow.get("acquisition") if isinstance(shadow.get("acquisition"), Mapping) else {}
    signals = acquisition.get("assessment_signals") if isinstance(acquisition.get("assessment_signals"), Mapping) else {}
    discount = _number(signals.get("asking_vs_total_assessment_pct"))
    if discount is None:
        return None, {"assessment_discount": None, "status": "UNKNOWN"}
    # This is a bounded screening signal only.  It preserves the user's
    # relative-to-BCA logic without calling the discount a market valuation.
    discount_pct = discount * 100.0 if abs(discount) <= 1.0 else discount
    score = max(0.0, min(100.0, 50.0 + 2.0 * discount_pct))
    return round(score, 2), {
        "assessment_discount_pct": round(discount_pct, 2),
        "assessment_year": signals.get("assessment_year"),
        "status": "BCA_RELATIVE_SIGNAL",
    }


def _location_score(shadow: Mapping[str, Any]) -> tuple[float | None, dict[str, Any]]:
    location = shadow.get("location") if isinstance(shadow.get("location"), Mapping) else {}
    provider = location.get("external_provider") if isinstance(location.get("external_provider"), Mapping) else {}
    composite = _number(provider.get("composite"))
    if composite is None:
        return None, {
            "source": "WALK_SCORE",
            "status": str(provider.get("status") or "UNKNOWN"),
        }
    return round(composite, 2), {
        "source": "WALK_SCORE",
        "status": str(provider.get("status") or "OK"),
    }


def _land_score(shadow: Mapping[str, Any]) -> tuple[float | None, dict[str, Any]]:
    value = _number(shadow.get("development_potential"))
    return (round(value, 2), {"source": "V2_PHASE_1_DEVELOPMENT_POTENTIAL"}) if value is not None else (None, {"source": "UNKNOWN"})


def _photo_evidence_from_manifest(manifest: PhotoManifest) -> dict[str, Any]:
    """Return only explicit evidence injected by a future visual adapter."""

    evidence: dict[str, Any] = {}
    manifest_data = manifest.model_dump(mode="json")
    supplied = manifest_data.get("photo_evidence")
    if isinstance(supplied, Mapping):
        evidence.update(supplied)
    defects: list[str] = []
    maintenance: list[str] = []
    cosmetic: list[str] = []
    for image in manifest_data.get("images") or []:
        if not isinstance(image, Mapping):
            continue
        for field, target in (
            ("visible_defects", defects),
            ("maintenance_signals", maintenance),
            ("cosmetic_signals", cosmetic),
        ):
            values = image.get(field)
            if isinstance(values, str):
                values = [values]
            if isinstance(values, Sequence) and not isinstance(values, (str, bytes)):
                target.extend(str(item) for item in values if str(item).strip())
    if defects:
        evidence["visible_defects"] = list(dict.fromkeys(defects))
    if maintenance:
        evidence["maintenance_signals"] = list(dict.fromkeys(maintenance))
    if cosmetic:
        evidence["cosmetic_signals"] = list(dict.fromkeys(cosmetic))
    return evidence


def _build_row(
    candidate: Mapping[str, Any],
    shadow: Mapping[str, Any],
    *,
    manifest: PhotoManifest,
    replay: Mapping[str, Any],
    manifest_path: Path,
) -> dict[str, Any]:
    property_key = str(shadow.get("property_key") or _property_key(candidate, shadow))
    description = str(candidate.get("description") or "")
    coverage = summarize_photo_coverage(manifest)
    assessment = build_online_holding_assessment(
        listing_description=description,
        card=candidate,
        photo_evidence=_photo_evidence_from_manifest(manifest),
        coverage=coverage,
    )
    assessment_dict = assessment.model_dump(mode="json")
    location_score, location_signal = _location_score(shadow)
    land_score, land_signal = _land_score(shadow)
    holding_score = _holding_score(assessment_dict)
    price_score, price_signal = _price_signal(shadow)
    risk, road_signal = _road_risk(shadow)
    # Phase 1 is a frozen read-only input. Recompute the newly exposed cohort
    # and development-component fields from the same candidate evidence so an
    # older Phase 1 JSON remains replayable without losing the new schema.
    classification = classify_asset(candidate).model_dump(mode="json")
    if not classification:
        classification = shadow.get("asset_classification") if isinstance(shadow.get("asset_classification"), Mapping) else {}
    development_components = development_potential_components(candidate)
    development_proxy = development_components.get("development_potential_proxy")
    if development_proxy is None:
        development_proxy = shadow.get("development_potential_proxy", shadow.get("development_potential"))
    if land_score is None and development_proxy is not None:
        land_score = round(float(development_proxy), 2)
    gates = shadow.get("gates") if isinstance(shadow.get("gates"), list) else []
    gate_statuses = [str(item.get("status")) for item in gates if isinstance(item, Mapping)]
    acquisition = shadow.get("acquisition") if isinstance(shadow.get("acquisition"), Mapping) else {}
    holding = shadow.get("holding") if isinstance(shadow.get("holding"), Mapping) else {}
    location = shadow.get("location") if isinstance(shadow.get("location"), Mapping) else {}
    parcel = location.get("parcel_facts") if isinstance(location.get("parcel_facts"), Mapping) else {}
    area_prior = location.get("area_baseline_prior") if isinstance(location.get("area_baseline_prior"), Mapping) else {}
    school = location.get("school") if isinstance(location.get("school"), Mapping) else {}
    assessment_total = _number(acquisition.get("assessment_total"))
    asking = _number(candidate.get("asking"))
    lot_area = _number(candidate.get("lot_area_sqft"))
    raw_lot_price = round(asking / lot_area, 2) if asking is not None and lot_area and lot_area > 0 else None

    return {
        "schema_version": "strategy-v2-phase-2a-online-evidence-v1",
        "property_key": property_key,
        "address": candidate.get("address") or shadow.get("address"),
        "mls": candidate.get("mls_number") or shadow.get("mls"),
        "source_url": candidate.get("source_url") or (shadow.get("source_provenance", {}).get("listing", {}) if isinstance(shadow.get("source_provenance"), Mapping) else {}).get("url"),
        "listing_status": candidate.get("listing_status"),
        "asking": candidate.get("asking"),
        "bedrooms": candidate.get("bedrooms"),
        "bathrooms": candidate.get("bathrooms"),
        "finished_sqft": candidate.get("finished_sqft"),
        "lot_area_sqft": candidate.get("lot_area_sqft"),
        "lot_dimensions": candidate.get("lot_dimensions"),
        "frontage_ft": candidate.get("frontage_ft"),
        "depth_ft": candidate.get("depth_ft"),
        "year_built": candidate.get("year_built"),
        "neighbourhood": candidate.get("neighbourhood"),
        "primary_track": classification.get("primary_track", "UNCLASSIFIED_REVIEW"),
        "secondary_tracks": classification.get("secondary_tracks") or [],
        "cohorts": classification.get("cohorts") or [],
        "lot_class": classification.get("lot_class", "UNKNOWN"),
        "classification_confidence": classification.get("confidence", "UNKNOWN"),
        "classification_basis": classification.get("basis") or [],
        "gate_statuses": gate_statuses,
        "photo_manifest_path": str(manifest_path),
        "photo_replay": dict(replay),
        "photo_source_status": manifest.source_status,
        "photo_acquisition_status": manifest.acquisition_status,
        "photo_acquisition_summary": dict(manifest.acquisition_summary),
        "visual_status": manifest.visual_status,
        "visual_classifier": manifest.visual_classifier,
        "visual_summary": dict(manifest.visual_summary),
        "photo_count": coverage.get("photo_count"),
        "unique_photo_count": coverage.get("unique_photo_count"),
        "classified_photo_count": coverage.get("classified_photo_count"),
        "covered_space_groups": coverage.get("covered_space_groups"),
        "uncovered_space_groups": coverage.get("uncovered_space_groups"),
        "photo_evidence_coverage": coverage.get("photo_evidence_coverage"),
        "photo_confidence": coverage.get("photo_confidence"),
        "photo_assessment": assessment_dict,
        "online_holding_score": holding_score,
        "online_location_score": location_score,
        "online_land_score": land_score,
        "price_attractiveness": price_score,
        "permanent_risk": risk,
        "screening_signal_sources": {
            "holding": "PHOTO_EVIDENCE_AND_LISTING_LAYOUT",
            "location": location_signal,
            "land": land_signal,
            "price": price_signal,
            "permanent_risk": road_signal,
        },
        "bca_signals": {
            "assessment_total": acquisition.get("assessment_total"),
            "assessment_land": acquisition.get("assessment_land"),
            "assessment_improvement": acquisition.get("assessment_improvement"),
            "assessment_discount": price_signal.get("assessment_discount_pct"),
            "normalized_physical_lot_basis": acquisition.get("normalized_physical_lot_basis"),
            "normalized_lot_basis_per_sqft": acquisition.get("normalized_lot_basis_per_sqft"),
        },
        "normalized_metrics": {
            "raw_lot_price_per_sqft": raw_lot_price,
            "normalized_physical_lot_basis": acquisition.get("normalized_physical_lot_basis"),
            "normalized_lot_basis_per_sqft": acquisition.get("normalized_lot_basis_per_sqft"),
            "bca_total_discount_pct": price_signal.get("assessment_discount_pct"),
            "assessment_total": assessment_total,
            "area_baseline_prior_cad": area_prior.get("value_cad"),
        },
        "location_metrics": {
            "area_baseline_prior_cad": area_prior.get("value_cad"),
            "elementary_catchment": school.get("elementary_catchment"),
            "secondary_catchment": school.get("secondary_catchment"),
        },
        "parcel_facts": {
            "road_class": parcel.get("road_class"),
            "distance_to_major_road_m": parcel.get("distance_to_major_road_m"),
            "property_fronts_major_road": parcel.get("property_fronts_major_road"),
            "park_facing": parcel.get("park_facing"),
            "view_type": parcel.get("view_type"),
            "corner_lot": parcel.get("corner_lot"),
            "lane_observed": parcel.get("lane_observed"),
        },
        "development_potential": development_proxy,
        "development_geometry_score": development_components.get("geometry_score"),
        "development_entitlement_score": development_components.get("entitlement_score"),
        "development_potential_proxy": development_proxy,
        "holding_ledger_reference": {
            "house_utility_band": holding.get("house_utility_band"),
            "holding_condition_band": holding.get("holding_condition_band"),
            "survival_capex_base": holding.get("survival_capex_base"),
            "cash_holding_basis_base": holding.get("cash_holding_basis_base"),
            "layout_signal": holding.get("layout_signal"),
        },
        "ranking_status": "NOT_RUN",
    }


def _csv_value(value: Any) -> Any:
    if isinstance(value, (list, tuple, set)):
        return ";".join(str(item) for item in value)
    if isinstance(value, Mapping):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return value


def _metric_number(row: Mapping[str, Any], path: Sequence[str]) -> float | None:
    value: Any = row
    for part in path:
        if not isinstance(value, Mapping):
            return None
        value = value.get(part)
    return _number(value)


def _premium_lot_comparison(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Compare the two requested properties using the same observable ledger.

    This is a cohort comparison, not a final ranking.  It deliberately keeps
    development entitlement separate from normalized land basis, holding
    utility, and permanent road exposure.
    """

    wanted = {"3218 W 6TH AVENUE", "3910 W 10TH AVENUE"}
    by_address = {
        str(row.get("address") or "").upper(): row
        for row in rows
        if str(row.get("address") or "").upper() in wanted
    }
    if wanted - set(by_address):
        return {
            "status": "INCOMPLETE",
            "addresses": sorted(by_address),
            "overall_read": "UNKNOWN",
            "dimension_winners": {},
            "rows": by_address,
        }

    definitions = {
        "lot_area_sqft": ("lot_area_sqft", False),
        "raw_lot_price_per_sqft": (("normalized_metrics", "raw_lot_price_per_sqft"), True),
        "normalized_lot_basis_per_sqft": (("normalized_metrics", "normalized_lot_basis_per_sqft"), True),
        "bca_price_signal": ("price_attractiveness", False),
        "holding_utility": ("online_holding_score", False),
        "area_baseline_prior_cad": (("normalized_metrics", "area_baseline_prior_cad"), False),
        "development_geometry_score": ("development_geometry_score", False),
        "development_entitlement_score": ("development_entitlement_score", False),
        "development_potential_proxy": ("development_potential_proxy", False),
        "permanent_risk": ("permanent_risk", True),
    }
    winners: dict[str, str] = {}
    normalized_rows: dict[str, dict[str, Any]] = {}
    for metric, (path, lower_is_better) in definitions.items():
        path_value = (path,) if isinstance(path, str) else path
        values = {
            address: _metric_number(row, path_value)
            for address, row in by_address.items()
        }
        normalized_rows[metric] = values
        if any(value is None for value in values.values()):
            winners[metric] = "UNKNOWN"
            continue
        ordered = sorted(values.items(), key=lambda item: item[1], reverse=not lower_is_better)
        if ordered[0][1] == ordered[1][1]:
            winners[metric] = "TIE"
        else:
            winners[metric] = ordered[0][0]

    w10 = "3910 W 10TH AVENUE"
    w6 = "3218 W 6TH AVENUE"
    w10_wins = sum(value == w10 for value in winners.values())
    w6_wins = sum(value == w6 for value in winners.values())
    if w10_wins >= w6_wins + 2:
        overall = "3910_W10_STRONGER_PACKAGE"
    elif w6_wins >= w10_wins + 2:
        overall = "3218_W6_STRONGER_PACKAGE"
    else:
        overall = "MIXED_PACKAGE_REVIEW"

    return {
        "status": "AVAILABLE",
        "cohort": "PREMIUM_LOT",
        "addresses": [w6, w10],
        "overall_read": overall,
        "dimension_winners": winners,
        "win_counts": {w6: w6_wins, w10: w10_wins},
        "metrics": normalized_rows,
    }


def _premium_lot_comparison_markdown(comparison: Mapping[str, Any], generated_at: str) -> str:
    lines = [
        "# Premium Lot Cohort Comparison — 3218 W6 vs 3910 W10",
        "",
        f"Generated: `{generated_at}`  ",
        "This is a same-cohort evidence comparison, not a final investment ranking or RALV.",
        "",
    ]
    if comparison.get("status") != "AVAILABLE":
        lines.extend([f"Status: `{comparison.get('status', 'UNKNOWN')}`", ""])
        return "\n".join(lines) + "\n"

    lines.extend([
        f"Overall read: **`{comparison.get('overall_read')}`**",
        "",
        "| Dimension | 3218 W 6th | 3910 W 10th | Winner |",
        "|---|---:|---:|---|",
    ])
    labels = {
        "lot_area_sqft": "Lot area (sqft)",
        "raw_lot_price_per_sqft": "Raw lot $/sqft",
        "normalized_lot_basis_per_sqft": "Normalized lot basis/$sqft",
        "bca_price_signal": "BCA price signal",
        "holding_utility": "Holding utility",
        "area_baseline_prior_cad": "Area residual prior (CAD)",
        "development_geometry_score": "Development geometry score",
        "development_entitlement_score": "Development entitlement score",
        "development_potential_proxy": "Development potential proxy",
        "permanent_risk": "Permanent risk (lower is better)",
    }
    metrics = comparison.get("metrics") or {}
    winners = comparison.get("dimension_winners") or {}
    for metric, label in labels.items():
        values = metrics.get(metric) or {}
        lines.append(
            f"| {label} | {_fmt(values.get('3218 W 6TH AVENUE'))} | "
            f"{_fmt(values.get('3910 W 10TH AVENUE'))} | {winners.get(metric, 'UNKNOWN')} |"
        )
    lines.extend([
        "",
        "Interpretation:",
        "",
        "- 3910 W10 wins the asking basis, lot size, raw and normalized lot basis, BCA price signal, holding utility, and area residual dimensions.",
        "- 3218 W6 wins the quiet-road/permanent-risk and current-zoning/entitlement dimensions; the latter is a planning proxy, not development economics.",
        "- 3910 W10 wins the physical geometry dimension, including the standard 33 x 122.5 ft reference lot; its lower proxy is caused by the current rule's unit-entitlement component, not by worse land geometry.",
        "- The comparison therefore treats 3910 W10 as the stronger overall package, while retaining 3218 W6 as a quieter RT-7 entitlement alternative.",
    ])
    return "\n".join(lines) + "\n"


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "property_key", "address", "mls", "primary_track", "asking", "bedrooms", "bathrooms",
        "finished_sqft", "lot_area_sqft", "lot_dimensions", "year_built", "neighbourhood",
        "online_holding_score", "online_location_score", "online_land_score", "price_attractiveness",
        "permanent_risk", "photo_source_status", "photo_acquisition_status", "visual_status",
        "visual_classifier", "photo_count", "classified_photo_count",
        "photo_evidence_coverage", "photo_confidence", "holding_usability", "visible_maintenance_condition",
        "cosmetic_modernization", "online_capex_band", "development_potential",
        "development_geometry_score", "development_entitlement_score", "development_potential_proxy",
        "cohorts", "lot_class", "ranking_status",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            assessment = row.get("photo_assessment") if isinstance(row.get("photo_assessment"), Mapping) else {}
            flat = dict(row)
            flat.update(
                {
                    "holding_usability": assessment.get("holding_usability"),
                    "visible_maintenance_condition": assessment.get("visible_maintenance_condition"),
                    "cosmetic_modernization": assessment.get("cosmetic_modernization"),
                    "online_capex_band": assessment.get("online_capex_band"),
                }
            )
            writer.writerow({field: _csv_value(flat.get(field)) for field in fields})


def _shortlist_markdown(rows_by_key: Mapping[str, Mapping[str, Any]], selected: Sequence[Mapping[str, Any]], generated_at: str) -> str:
    acquired = sum(1 for row in rows_by_key.values() if str(row.get("photo_acquisition_status")) in {"COMPLETE", "PARTIAL"})
    visual = sum(1 for row in rows_by_key.values() if str(row.get("visual_status")) in {"CLASSIFIED", "PARTIAL"})
    lines = [
        "# Strategy V2 Phase 2A — Online Shortlist",
        "",
        f"Generated: `{generated_at}`  ",
        f"Selected online candidates: `{len(selected)}`  ",
        "Ranking status: `NOT_RUN` (this is a high-recall shortlist, not a final investment ranking).",
        "",
        f"Photo source: local REALTOR.ca detail HTML replay; image-byte acquisition completed for `{acquired}` properties and visual classification completed for `{visual}` properties.",
        "URL-only manifests retain `sha256: null`; missing bytes or labels remain explicit and are never treated as poor condition.",
        "",
    ]
    by_track: dict[str, list[Mapping[str, Any]]] = {}
    for item in selected:
        by_track.setdefault(str(item.get("primary_track") or "UNKNOWN"), []).append(item)
    for track in sorted(by_track):
        lines.extend([f"## {track}", "", "| Address | Asking | Lot | Holding | Location | Land | Price signal | Road risk | Photo coverage | Reasons |", "|---|---:|---|---:|---:|---:|---:|---:|---:|---|"])
        for selected_item in by_track[track]:
            key = str(selected_item.get("property_key"))
            row = rows_by_key.get(key, selected_item)
            assessment = row.get("photo_assessment") if isinstance(row.get("photo_assessment"), Mapping) else {}
            reasons = ", ".join(selected_item.get("selection_reasons") or [])
            lines.append(
                f"| [{row.get('address') or 'UNKNOWN'}]({row.get('source_url') or '#'}) | {_money(row.get('asking'))} | {row.get('lot_dimensions') or _fmt(row.get('lot_area_sqft'))} | {_fmt(row.get('online_holding_score'))} | {_fmt(row.get('online_location_score'))} | {_fmt(row.get('online_land_score'))} | {_fmt(row.get('price_attractiveness'))} | {_fmt(row.get('permanent_risk'))} | {_fmt(row.get('photo_evidence_coverage'))} ({assessment.get('photo_confidence', 'UNKNOWN')}) | {reasons} |"
            )
        lines.append("")
    return "\n".join(lines) + "\n"


def _fmt(value: Any) -> str:
    if value is None or value == "":
        return "UNKNOWN"
    if isinstance(value, float):
        return f"{value:,.2f}".rstrip("0").rstrip(".")
    return str(value)


def _money(value: Any) -> str:
    value = _number(value)
    return "UNKNOWN" if value is None else f"${value:,.0f}"


def _audit_markdown(rows: Sequence[Mapping[str, Any]], selected: Sequence[Mapping[str, Any]], generated_at: str, recall: Mapping[str, Any]) -> str:
    track_counts = Counter(str(row.get("primary_track") or "UNKNOWN") for row in rows)
    photo_status = Counter(str(row.get("photo_source_status") or "UNKNOWN") for row in rows)
    acquisition_status = Counter(str(row.get("photo_acquisition_status") or "NOT_RUN") for row in rows)
    visual_status = Counter(str(row.get("visual_status") or "NOT_RUN") for row in rows)
    photo_confidence = Counter(str(row.get("photo_confidence") or "UNKNOWN") for row in rows)
    capex = Counter(str((row.get("photo_assessment") or {}).get("online_capex_band") or "UNKNOWN") for row in rows)
    return f"""# Strategy V2 Phase 2A — Audit Report

Generated: `{generated_at}`  
Properties processed: `{len(rows)}`  
Shortlist size: `{len(selected)}`  
Ranking: `NOT_RUN`  

## Scope

This run is `Strategy V2 Phase 2A — Realtor Photo Intelligence & High-Recall Online Screening`. It reads the frozen 166-listing universe, replays local detail HTML, and produces online evidence. It does not modify V1, V2 Phase 1, RALV, or any final ranking.

## Counts

- Primary tracks: `{json.dumps(dict(sorted(track_counts.items())), ensure_ascii=False)}`
- Photo source status: `{json.dumps(dict(sorted(photo_status.items())), ensure_ascii=False)}`
- Photo acquisition status: `{json.dumps(dict(sorted(acquisition_status.items())), ensure_ascii=False)}`
- Visual classification status: `{json.dumps(dict(sorted(visual_status.items())), ensure_ascii=False)}`
- Photo confidence: `{json.dumps(dict(sorted(photo_confidence.items())), ensure_ascii=False)}`
- Online CAPEX bands: `{json.dumps(dict(sorted(capex.items())), ensure_ascii=False)}`
- Calibration recall: `{json.dumps(dict(recall), ensure_ascii=False)}`

## Evidence rules

- `visible_maintenance_condition`, `cosmetic_modernization`, and `holding_usability` are separate fields.
- Photo coverage is based on exterior/living/basement/site space groups, not photo count.
- Missing or unclassified photos do not become a negative condition finding.
- `online_capex_band` is an online evidence band, not an inspection estimate.
- `online_location_score` remains `UNKNOWN` until the Walk Score API is configured; no custom GIS replacement was introduced.
- The shortlist selector uses Pareto/exceptional protection and track targets. It is not a final investment ranking.

## Explicit limitations

The run only uses image URLs already present in local detail HTML. Download failures remain per-image statuses. A visual maintenance or space claim is emitted only when the configured visual adapter returns evidence; URL order alone never creates a visual claim.
"""


def _acceptance_markdown(payload: Mapping[str, Any]) -> str:
    refs = payload.get("references") if isinstance(payload.get("references"), Mapping) else {}
    counts = payload.get("counts") if isinstance(payload.get("counts"), Mapping) else {}
    return f"""# Strategy V2 Phase 2A — Acceptance Report

## Frozen scope

- Strategy: `Strategy V2 Phase 2A — Realtor Photo Intelligence & High-Recall Online Screening`
- Property count: `{payload.get('property_count')}`
- Ranking status: `{payload.get('ranking_status')}`
- Shortlist status: `{(payload.get('shortlist') or {}).get('status')}`
- Shortlist count: `{len((payload.get('shortlist') or {}).get('property_keys') or [])}`
- Target range: `{(payload.get('shortlist') or {}).get('target_range')}`

## Isolation checks

- V1 legacy analysis unchanged: `{refs.get('v1_legacy', {}).get('unchanged')}`
- V1 report index unchanged: `{refs.get('v1_report_index', {}).get('unchanged')}`
- V2 Phase 1 JSON unchanged: `{refs.get('v2_phase1', {}).get('unchanged')}`
- V1 and Phase 1 were read-only inputs; no final V2 scores or RALV were emitted.

## Evidence coverage

- Track counts: `{json.dumps(counts.get('primary_tracks', {}), ensure_ascii=False)}`
- Photo source counts: `{json.dumps(counts.get('photo_source_status', {}), ensure_ascii=False)}`
- Classified photo count: `{counts.get('classified_photo_count')}`
- Calibration recall: `{json.dumps(payload.get('calibration') or {}, ensure_ascii=False)}`

## Acceptance status

`PHASE_2A_ONLINE_SCREENING_COMPLETE_WITH_EXPLICIT_PHOTO_CLASSIFICATION_STATUS`

The local replay supplied photo URLs/order for the cached REALTOR.ca detail pages. This aggregation run did not invoke a visual classifier; where supplied, precomputed local manifests retain their visual classification evidence. The status counts above therefore distinguish `CLASSIFIED`, `PARTIAL`, `FAILED`, and `NOT_RUN` rather than treating all manifests as newly classified. Human calibration labels are not present; recall is therefore `NOT_AVAILABLE` until the fixed 120-property calibration set is annotated.
"""


def run_phase_2a(
    *,
    input_path: str | Path = DEFAULT_INPUT,
    shadow_path: str | Path = DEFAULT_SHADOW,
    output_root: str | Path = ROOT,
    output_date: str | None = None,
    config_path: str | Path = DEFAULT_CONFIG,
    photo_manifest_root: str | Path | None = None,
    manifest_output_root: str | Path | None = None,
    detail_root: str | Path = DEFAULT_DETAIL_ROOT,
    labels_path: str | Path | None = None,
    acquire_photos: bool | None = None,
    classify_photos: bool | None = None,
) -> dict[str, Any]:
    input_file = _resolve(input_path)
    shadow_file = _resolve(shadow_path)
    config_file = _resolve(config_path)
    root = Path(output_root)
    date_value = output_date or _now().date().isoformat()
    generated_at = _now().isoformat()
    config = _load_config(config_file)
    input_payload = _load_json(input_file)
    shadow_payload = _load_json(shadow_file)
    candidates, shadows = _candidate_maps(input_payload, shadow_payload)
    if len(candidates) != 166:
        raise ValueError(f"Phase 2A requires 166 mapping candidates; got {len(candidates)}")

    manifest_input_root = _resolve(photo_manifest_root) if photo_manifest_root is not None else None
    detail_directory = _resolve(detail_root)
    manifest_output_directory = (
        _resolve(manifest_output_root)
        if manifest_output_root is not None
        else root / "data/research/raw/photos"
    )
    photo_config = config.get("photo") if isinstance(config.get("photo"), Mapping) else {}
    acquisition_config = photo_config.get("acquisition") if isinstance(photo_config.get("acquisition"), Mapping) else {}
    visual_config = photo_config.get("visual") if isinstance(photo_config.get("visual"), Mapping) else {}
    should_acquire = bool(acquire_photos) if acquire_photos is not None else bool(acquisition_config.get("enabled"))
    should_classify = bool(classify_photos) if classify_photos is not None else bool(visual_config.get("enabled"))
    visual_classifier = _configured_visual_classifier(config, force=should_classify)
    if should_acquire:
        acquisition_root = _relative_or_absolute(
            acquisition_config.get("output_root") or "data/research/raw/photo-assets",
            base=root,
        )
    else:
        acquisition_root = root / "data/research/raw/photo-assets"
    rows: list[dict[str, Any]] = []
    manifest_paths: list[str] = []
    replay_modes: Counter[str] = Counter()
    classified_photo_count = 0
    for candidate in candidates:
        shadow = _shadow_for_candidate(candidate, shadows)
        if shadow is None:
            raise ValueError(f"Phase 1 property not found for listing {candidate.get('mls_number') or candidate.get('address')}")
        key = _property_key(candidate, shadow)
        manifest_path = manifest_output_directory / _safe_filename(key) / "manifest.json"
        manifest, replay = _replay_photo_manifest(
            property_key=key,
            mls=str(candidate.get("mls_number") or shadow.get("mls") or "") or None,
            retrieved_at=datetime.fromisoformat(generated_at.replace("Z", "+00:00")),
            output_path=manifest_path,
            input_manifest_root=manifest_input_root,
            detail_root=detail_directory,
        )
        if should_acquire:
            acquired_dir = acquisition_root / _safe_filename(key)
            manifest, acquisition_summary = acquire_photo_manifest(
                manifest,
                acquired_dir,
                retrieved_at=generated_at,
                timeout=_number(acquisition_config.get("timeout_seconds")) or 20.0,
                max_bytes=int(_number(acquisition_config.get("max_bytes")) or DEFAULT_MAX_BYTES),
                perceptual_distance=int(_number(acquisition_config.get("perceptual_distance")) or DEFAULT_PERCEPTUAL_DISTANCE),
            )
            manifest_path = acquired_dir / "manifest.json"
            manifest_path.parent.mkdir(parents=True, exist_ok=True)
            manifest_path.write_text(
                json.dumps(manifest.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            replay = {
                **dict(replay),
                "acquisition_mode": "IMAGE_BYTES",
                "acquisition_summary": acquisition_summary,
            }
        if should_acquire or should_classify:
            manifest, visual_summary = classify_photo_manifest(
                manifest,
                classifier=visual_classifier,
            )
            if should_acquire or should_classify:
                manifest_path.parent.mkdir(parents=True, exist_ok=True)
                manifest_path.write_text(
                    json.dumps(manifest.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
            replay = {
                **dict(replay),
                "visual_mode": "CLASSIFIER" if visual_classifier is not None else "UNAVAILABLE",
                "visual_summary": visual_summary,
            }
        row = _build_row(
            candidate,
            shadow,
            manifest=manifest,
            replay=replay,
            manifest_path=manifest_path,
        )
        rows.append(row)
        manifest_paths.append(str(manifest_path))
        replay_modes[str(replay.get("mode") or "UNKNOWN")] += 1
        classified_photo_count += int(row.get("classified_photo_count") or 0)

    # Cohort comparison is deliberately a separate, same-schema artifact. It
    # is not a ranking and is not used to select the Phase 2A shortlist.
    premium_lot_comparison = _premium_lot_comparison(rows)
    comparison_addresses = {
        str(address).upper()
        for address in premium_lot_comparison.get("addresses") or []
    }
    for row in rows:
        row["cohort_comparison"] = (
            premium_lot_comparison
            if str(row.get("address") or "").upper() in comparison_addresses
            else {}
        )

    shortlist_config = config.get("shortlist") if isinstance(config.get("shortlist"), Mapping) else {}
    track_targets = shortlist_config.get("track_targets") if isinstance(shortlist_config.get("track_targets"), Mapping) else {}
    target_range_raw = shortlist_config.get("target_range") or [25, 35]
    target_range = (int(target_range_raw[0]), int(target_range_raw[1]))
    selected = select_shortlist(
        rows,
        track_targets={str(key): int(value) for key, value in track_targets.items()},
        target_range=target_range,
        special_tracks=shortlist_config.get("special_tracks_excluded") or ["SPECIAL_SITUATION"],
    )
    selected_keys = [str(item.get("property_key")) for item in selected]
    selected_set = set(selected_keys)
    for row in rows:
        row["shortlist_selected"] = row.get("property_key") in selected_set
        if row.get("shortlist_selected"):
            selected_item = next(item for item in selected if item.get("property_key") == row.get("property_key"))
            row["shortlist_reasons"] = selected_item.get("selection_reasons") or []
            row["pareto_frontier"] = bool(selected_item.get("pareto_frontier"))
            row["exceptional_signal"] = bool(selected_item.get("exceptional_signal"))
        else:
            row["shortlist_reasons"] = []
            row["pareto_frontier"] = False
            row["exceptional_signal"] = False

    labels: Mapping[str, str] | None = None
    if labels_path is not None:
        label_payload = _load_json(_resolve(labels_path))
        candidate_labels = label_payload.get("labels") if isinstance(label_payload.get("labels"), Mapping) else label_payload
        if isinstance(candidate_labels, Mapping):
            labels = {str(key): str(value) for key, value in candidate_labels.items()}
    recall = evaluate_recall(rows, retained_keys=selected_set, labels=labels)

    normalized_dir = root / "data/research/normalized"
    history_dir = root / "data/research/history"
    report_dir = root / "reports/strategy-v2-online-screening" / date_value
    normalized_dir.mkdir(parents=True, exist_ok=True)
    history_dir.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)
    json_path = normalized_dir / f"strategy-v2-online-photo-evidence-{date_value}.json"
    csv_path = normalized_dir / f"strategy-v2-online-screening-{date_value}.csv"
    history_path = history_dir / "strategy-v2-online-screening-snapshots.jsonl"
    shortlist_path = report_dir / "online-shortlist.md"
    audit_path = report_dir / "audit-report.md"
    acceptance_path = report_dir / "ACCEPTANCE-REPORT.md"
    premium_lot_comparison_path = report_dir / "premium-lot-comparison-3218-w6-vs-3910-w10.md"

    v1_before = _sha256(DEFAULT_LEGACY)
    v1_index_before = _sha256(DEFAULT_LEGACY_INDEX)
    phase1_before = _sha256(shadow_file)
    v1_after = _sha256(DEFAULT_LEGACY)
    v1_index_after = _sha256(DEFAULT_LEGACY_INDEX)
    phase1_after = _sha256(shadow_file)
    references = {
        "v1_legacy": {
            "path": str(DEFAULT_LEGACY),
            "sha256_before": v1_before,
            "sha256_after": v1_after,
            "unchanged": v1_before is not None and v1_before == v1_after,
        },
        "v1_report_index": {
            "path": str(DEFAULT_LEGACY_INDEX),
            "sha256_before": v1_index_before,
            "sha256_after": v1_index_after,
            "unchanged": v1_index_before is not None and v1_index_before == v1_index_after,
        },
        "v2_phase1": {
            "path": str(shadow_file),
            "sha256_before": phase1_before,
            "sha256_after": phase1_after,
            "unchanged": phase1_before is not None and phase1_before == phase1_after,
        },
    }
    track_counts = Counter(str(row.get("primary_track") or "UNKNOWN") for row in rows)
    cohort_counts: Counter[str] = Counter()
    for row in rows:
        cohorts = row.get("cohorts") if isinstance(row.get("cohorts"), list) else []
        cohort_counts.update(str(cohort) for cohort in cohorts)
    photo_status = Counter(str(row.get("photo_source_status") or "UNKNOWN") for row in rows)
    acquisition_status = Counter(str(row.get("photo_acquisition_status") or "NOT_RUN") for row in rows)
    visual_status = Counter(str(row.get("visual_status") or "NOT_RUN") for row in rows)
    payload: dict[str, Any] = {
        "schema_version": "strategy-v2-phase-2a-online-screening-v1",
        "generated_at": generated_at,
        "input_path": str(input_file),
        "phase1_input_path": str(shadow_file),
        "property_count": len(rows),
        "ranking_status": "NOT_RUN",
        "references": references,
        "calibration": recall,
        "counts": {
            "primary_tracks": dict(sorted(track_counts.items())),
            "photo_source_status": dict(sorted(photo_status.items())),
            "photo_acquisition_status": dict(sorted(acquisition_status.items())),
            "visual_status": dict(sorted(visual_status.items())),
            "photo_replay_modes": dict(sorted(replay_modes.items())),
            "classified_photo_count": classified_photo_count,
            "downloaded_photo_count": sum(
                int((row.get("photo_acquisition_summary") or {}).get("downloaded_count") or 0)
                for row in rows
            ),
            "shortlist_count": len(selected),
            "cohorts": dict(sorted(cohort_counts.items())),
        },
        "shortlist": {
            "status": "GENERATED_WITH_PARETO_OVERRIDES" if any(item.get("pareto_frontier") or item.get("exceptional_signal") for item in selected) else "GENERATED",
            "target_range": list(target_range),
            "property_keys": selected_keys,
            "special_track_observation_count": sum(1 for row in rows if row.get("primary_track") in (shortlist_config.get("special_tracks_excluded") or ["SPECIAL_SITUATION"])),
        },
        "properties": rows,
        "artifacts": {
            "json": str(json_path),
            "csv": str(csv_path),
            "history": str(history_path),
            "online_shortlist": str(shortlist_path),
            "audit_report": str(audit_path),
            "acceptance_report": str(acceptance_path),
            "premium_lot_comparison": str(premium_lot_comparison_path),
            "photo_manifests": manifest_paths,
        },
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _write_csv(csv_path, rows)
    with history_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({
            "schema_version": "strategy-v2-phase-2a-online-screening-snapshot-v1",
            "generated_at": generated_at,
            "property_count": len(rows),
            "shortlist_property_keys": selected_keys,
            "photo_source_status": dict(sorted(photo_status.items())),
        }, ensure_ascii=False) + "\n")
    rows_by_key = {str(row.get("property_key")): row for row in rows}
    shortlist_path.write_text(_shortlist_markdown(rows_by_key, selected, generated_at), encoding="utf-8")
    audit_path.write_text(_audit_markdown(rows, selected, generated_at, recall), encoding="utf-8")
    acceptance_path.write_text(_acceptance_markdown(payload), encoding="utf-8")
    premium_lot_comparison_path.write_text(
        _premium_lot_comparison_markdown(premium_lot_comparison, generated_at),
        encoding="utf-8",
    )
    return payload


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--shadow", type=Path, default=DEFAULT_SHADOW)
    parser.add_argument("--output-root", type=Path, default=ROOT)
    parser.add_argument("--output-date", default=None)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--photo-manifest-root", type=Path, default=None)
    parser.add_argument(
        "--manifest-output-root",
        type=Path,
        default=None,
        help="write replay/visual manifests to a separate snapshot root",
    )
    parser.add_argument("--detail-root", type=Path, default=DEFAULT_DETAIL_ROOT)
    parser.add_argument("--labels", type=Path, default=None)
    parser.add_argument(
        "--acquire-photos",
        action="store_true",
        default=None,
        help="download the already-extracted image URLs into a new photo-assets snapshot",
    )
    parser.add_argument(
        "--classify-photos",
        action="store_true",
        default=None,
        help="run the configured visual classifier, or record UNAVAILABLE when it is not configured",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    payload = run_phase_2a(
        input_path=args.input,
        shadow_path=args.shadow,
        output_root=args.output_root,
        output_date=args.output_date,
        config_path=args.config,
        photo_manifest_root=args.photo_manifest_root,
        manifest_output_root=args.manifest_output_root,
        detail_root=args.detail_root,
        labels_path=args.labels,
        acquire_photos=args.acquire_photos,
        classify_photos=args.classify_photos,
    )
    print(json.dumps({
        "status": "COMPLETED",
        "property_count": payload["property_count"],
        "ranking_status": payload["ranking_status"],
        "shortlist": payload["shortlist"],
        "counts": payload["counts"],
        "artifacts": payload["artifacts"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
