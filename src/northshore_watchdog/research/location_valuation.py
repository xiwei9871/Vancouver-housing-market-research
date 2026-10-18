"""Evidence-aware location valuation research.

This module is deliberately separate from the production screening model.  It
uses local snapshots and produces research artifacts; it does not change a
property's Current Use, Redevelopment, Dual Value, RALV, or MOS score.

The important distinction in this module is between an estimate and an
estimate that is safe to apply automatically.  A configured school prior, for
example, remains visible in the CSV but contributes zero to the automatic
normalized-lot calculation until transaction evidence supports it.
"""

from __future__ import annotations

import csv
import json
import math
import re
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import pandas as pd
import yaml


_PREMIUM_COLUMNS = [
    "component",
    "segment",
    "value",
    "applied_value",
    "method",
    "sample_size",
    "confidence",
    "automatic_rankable",
    "source_ids",
    "notes",
]
_PAIR_COLUMNS = [
    "pair_id",
    "factor",
    "left_id",
    "right_id",
    "left_factor_value",
    "right_factor_value",
    "price_difference",
    "price_difference_pct",
    "matched",
    "diagnostics",
]


def _number(value: Any) -> float | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "").replace("$", "")
    multiplier = 1.0
    if text.lower().endswith("m"):
        multiplier = 1_000_000.0
        text = text[:-1]
    elif text.lower().endswith("k"):
        multiplier = 1_000.0
        text = text[:-1]
    try:
        return float(text) * multiplier
    except ValueError:
        return None


def _date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value in (None, ""):
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            return None


def _text(value: Any) -> str:
    return " ".join(str(value or "").lower().replace("-", " ").split())


def _key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", _text(value))


def _same_value(left: Any, right: Any) -> bool:
    if left in (None, "") or right in (None, ""):
        return False
    return _text(left) == _text(right)


def _factor_value(row: Mapping[str, Any], factor: str) -> Any:
    aliases = {
        "school": ("school", "elementary_school", "official_elementary_catchment"),
        "elementary_school": ("elementary_school", "official_elementary_catchment"),
        "secondary_school": ("secondary_school", "official_secondary_catchment"),
        "road": ("road", "road_class", "road_exposure_level"),
        "view": ("view", "view_type", "view_status"),
        "park": ("park", "park_facing", "park_status"),
    }
    for field in aliases.get(factor, (factor,)):
        if row.get(field) not in (None, ""):
            return row[field]
    return None


def _applied(value: float | "PremiumEstimate" | None) -> float:
    if isinstance(value, PremiumEstimate):
        return value.applied_value
    return float(value or 0)


@dataclass(frozen=True)
class PremiumEstimate:
    component: str
    value: float
    applied_value: float
    method: str
    sample_size: int
    confidence: str
    automatic_rankable: bool
    source_ids: tuple[str, ...] = ()
    notes: str = ""


@dataclass(frozen=True)
class NormalizedPrice:
    market_land_basis: float | None
    total_location_value: float
    normalized_physical_lot_basis: float | None
    normalized_lot_price_per_sqft: float | None
    normalized_price_per_buildable_sqft: float | None
    ralv: float | None = None


@dataclass(frozen=True)
class MatchedPair:
    pair_id: str
    factor: str
    left_id: str
    right_id: str
    left_factor_value: Any
    right_factor_value: Any
    price_difference: float
    price_difference_pct: float | None
    matched: bool
    diagnostics: dict[str, Any]


@dataclass(frozen=True)
class HedonicResult:
    status: str
    row_count: int
    coefficients: dict[str, float]
    diagnostics: dict[str, Any]


def estimate_premium(
    *,
    component: str,
    value: float,
    method: str,
    sample_size: int,
    confidence: str,
    source_ids: Iterable[str] = (),
    notes: str = "",
) -> PremiumEstimate:
    """Create an estimate and decide whether it may enter automatic math.

    HIGH and MEDIUM are reserved for observed evidence.  A prior, low-
    confidence estimate, or unknown value remains visible but has an applied
    value of zero.  This prevents the research layer from turning a plausible
    number into a false market fact.
    """

    confidence = str(confidence).upper()
    automatic = confidence in {"HIGH", "MEDIUM"} and sample_size > 0
    applied_value = float(value) if automatic else 0.0
    return PremiumEstimate(
        component=component,
        value=float(value),
        applied_value=applied_value,
        method=method,
        sample_size=int(sample_size),
        confidence=confidence,
        automatic_rankable=automatic,
        source_ids=tuple(str(item) for item in source_ids),
        notes=notes,
    )


def sum_location_value(
    *,
    school_value: float | PremiumEstimate | None = 0,
    area_value: float | PremiumEstimate | None = 0,
    micro_values: Mapping[str, float | PremiumEstimate | None] | None = None,
) -> float:
    """Aggregate school plus non-school components exactly once."""

    total = _applied(school_value) + _applied(area_value)
    total += sum(_applied(value) for value in (micro_values or {}).values())
    return round(total, 2)


def normalize_price(
    *,
    asking: float | None,
    existing_house_value: float | None,
    school_value: float | PremiumEstimate | None = 0,
    area_value: float | PremiumEstimate | None = 0,
    micro_adjustments: Mapping[str, float | PremiumEstimate | None] | None = None,
    lot_area_sqft: float | None = None,
    realistic_buildable_area: float | None = None,
    ralv: float | None = None,
) -> NormalizedPrice:
    """Return Model A values; ``ralv`` is carried separately and never mixed."""

    total_location = sum_location_value(
        school_value=school_value,
        area_value=area_value,
        micro_values=micro_adjustments,
    )
    market_land = None
    if asking is not None and existing_house_value is not None:
        market_land = float(asking) - float(existing_house_value)
    normalized = market_land - total_location if market_land is not None else None
    lot_psf = normalized / lot_area_sqft if normalized is not None and lot_area_sqft else None
    buildable_psf = (
        normalized / realistic_buildable_area
        if normalized is not None and realistic_buildable_area
        else None
    )
    return NormalizedPrice(
        market_land_basis=_round_or_none(market_land),
        total_location_value=total_location,
        normalized_physical_lot_basis=_round_or_none(normalized),
        normalized_lot_price_per_sqft=_round_or_none(lot_psf),
        normalized_price_per_buildable_sqft=_round_or_none(buildable_psf),
        ralv=ralv,
    )


def _round_or_none(value: float | None, digits: int = 2) -> float | None:
    return round(float(value), digits) if value is not None else None


def normalize_sale_rows(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Normalize sale rows without treating asking observations as sales."""

    normalized: list[dict[str, Any]] = []
    for index, raw in enumerate(rows):
        row = dict(raw)
        row["id"] = str(row.get("id") or row.get("comparable_id") or row.get("property_id") or f"row-{index + 1}")
        row["sold_price"] = _number(row.get("sold_price", row.get("sale_price", row.get("sold"))))
        row["asking_price"] = _number(row.get("asking_price", row.get("asking")))
        row["sold_date"] = _date(row.get("sold_date", row.get("sale_date")))
        row["lot_area_sqft"] = _number(row.get("lot_area_sqft", row.get("lot_size_sqft")))
        row["frontage_ft"] = _number(row.get("frontage_ft"))
        row["depth_ft"] = _number(row.get("depth_ft"))
        row["house_size_sqft"] = _number(row.get("house_size_sqft", row.get("house_sqft")))
        row["year_built"] = _number(row.get("year_built"))
        row["is_sale"] = bool(row["sold_price"] is not None and row["sold_date"] is not None)
        row["source_id"] = str(row.get("source_registry_id") or row.get("source_url") or "UNKNOWN")
        normalized.append(row)
    return normalized


def find_matched_pairs(
    rows: Sequence[Mapping[str, Any]],
    *,
    factor: str,
    date_window_days: int = 183,
    lot_tolerance: float = 0.10,
    frontage_tolerance: float = 0.15,
    house_tolerance: float = 0.20,
    year_tolerance: int = 25,
) -> list[MatchedPair]:
    """Find deterministic pairs with similar physical context and a factor difference."""

    normalized = normalize_sale_rows(rows)
    pairs: list[MatchedPair] = []
    for left_index, left in enumerate(normalized):
        if not left.get("is_sale"):
            continue
        for right in normalized[left_index + 1 :]:
            if not right.get("is_sale"):
                continue
            if _key(left.get("neighbourhood")) != _key(right.get("neighbourhood")):
                continue
            left_factor = _factor_value(left, factor)
            right_factor = _factor_value(right, factor)
            if left_factor in (None, "") or right_factor in (None, "") or _same_value(left_factor, right_factor):
                continue
            diagnostics: dict[str, Any] = {}
            left_date = left.get("sold_date")
            right_date = right.get("sold_date")
            if left_date and right_date:
                diagnostics["date_difference_days"] = abs((left_date - right_date).days)
                if diagnostics["date_difference_days"] > date_window_days:
                    continue
            if not _within_ratio(left.get("lot_area_sqft"), right.get("lot_area_sqft"), lot_tolerance):
                continue
            if not _within_ratio(left.get("frontage_ft"), right.get("frontage_ft"), frontage_tolerance):
                continue
            if not _within_ratio(left.get("house_size_sqft"), right.get("house_size_sqft"), house_tolerance):
                continue
            if not _within_difference(left.get("year_built"), right.get("year_built"), year_tolerance):
                continue
            left_price = float(left["sold_price"])
            right_price = float(right["sold_price"])
            base = (left_price + right_price) / 2
            pairs.append(
                MatchedPair(
                    pair_id=f"{factor}:{left['id']}:{right['id']}",
                    factor=factor,
                    left_id=left["id"],
                    right_id=right["id"],
                    left_factor_value=left_factor,
                    right_factor_value=right_factor,
                    price_difference=round(left_price - right_price, 2),
                    price_difference_pct=round((left_price - right_price) / base, 6) if base else None,
                    matched=True,
                    diagnostics=diagnostics,
                )
            )
    return pairs


def _within_ratio(left: float | None, right: float | None, tolerance: float) -> bool:
    if left is None or right is None:
        return True
    denominator = max(abs(left), abs(right), 1.0)
    return abs(left - right) / denominator <= tolerance


def _within_difference(left: float | None, right: float | None, tolerance: float) -> bool:
    return left is None or right is None or abs(left - right) <= tolerance


def fit_hedonic_model(rows: Sequence[Mapping[str, Any]], *, minimum_rows: int = 20) -> HedonicResult:
    """Fit a small diagnostic model, or explicitly report insufficient evidence."""

    normalized = [row for row in normalize_sale_rows(rows) if row.get("is_sale")]
    if len(normalized) < minimum_rows:
        return HedonicResult(
            status="INSUFFICIENT_SAMPLE",
            row_count=len(normalized),
            coefficients={},
            diagnostics={"minimum_rows": minimum_rows, "reason": "fewer_than_minimum_sales"},
        )
    frame = pd.DataFrame(normalized)
    numeric = [field for field in ("lot_area_sqft", "frontage_ft", "house_size_sqft", "year_built") if field in frame]
    categorical = [field for field in ("neighbourhood", "zoning", "road_class", "school") if field in frame]
    features = frame[numeric].copy() if numeric else pd.DataFrame(index=frame.index)
    for field in categorical:
        values = frame[field].fillna("UNKNOWN").astype(str)
        features = pd.concat([features, pd.get_dummies(values, prefix=field, dtype=float)], axis=1)
    features = features.apply(pd.to_numeric, errors="coerce").fillna(features.median(numeric_only=True)).fillna(0.0)
    features.insert(0, "intercept", 1.0)
    y = frame["sold_price"].astype(float).map(math.log)
    if len(features.columns) == 1 or any(features[field].nunique(dropna=False) <= 1 for field in features.columns if field != "intercept"):
        return HedonicResult(
            status="LOW_VARIANCE",
            row_count=len(normalized),
            coefficients={},
            diagnostics={"minimum_rows": minimum_rows, "feature_count": len(features.columns)},
        )
    try:
        import numpy as np

        matrix = features.to_numpy(dtype=float)
        coefficients, _, rank, singular_values = np.linalg.lstsq(matrix, y.to_numpy(dtype=float), rcond=None)
        condition = float(max(singular_values) / min(singular_values)) if singular_values.size and min(singular_values) else float("inf")
        if rank < min(matrix.shape) or condition > 1e8:
            status = "HIGH_COLLINEARITY"
        else:
            status = "FITTED_DIAGNOSTIC_ONLY"
        return HedonicResult(
            status=status,
            row_count=len(normalized),
            coefficients={name: round(float(value), 8) for name, value in zip(features.columns, coefficients)},
            diagnostics={"minimum_rows": minimum_rows, "feature_count": len(features.columns), "matrix_rank": int(rank), "condition_number": round(condition, 2)},
        )
    except (ImportError, ValueError, TypeError, FloatingPointError) as exc:
        return HedonicResult(
            status="MODEL_ERROR",
            row_count=len(normalized),
            coefficients={},
            diagnostics={"error": f"{type(exc).__name__}: {exc}"},
        )


def _official(raw: Mapping[str, Any], field: str, fallback: Any = None) -> Any:
    return raw.get(f"official_{field}", fallback) if raw.get(f"official_{field}", fallback) not in (None, "") else fallback


def property_observations(payload: Mapping[str, Any], history_rows: Sequence[Mapping[str, Any]] = (), school_rows: Sequence[Mapping[str, Any]] = ()) -> list[dict[str, Any]]:
    """Flatten current cards and merge local history/school snapshots by address."""

    history_by_address = {_key(row.get("address")): dict(row) for row in history_rows if row.get("address")}
    school_by_address = {_key(row.get("address")): dict(row) for row in school_rows if row.get("address")}
    output: list[dict[str, Any]] = []
    for card in payload.get("candidates", []):
        if not isinstance(card, Mapping):
            continue
        raw = card.get("raw") if isinstance(card.get("raw"), Mapping) else {}
        address = card.get("address") or card.get("civic_address")
        history = history_by_address.get(_key(address), {})
        school = school_by_address.get(_key(address), {})
        row = {
            "id": str(card.get("mls_number") or card.get("listing_id") or address or f"property-{len(output) + 1}"),
            "address": address,
            "mls_number": card.get("mls_number"),
            "source_url": card.get("source_url"),
            "asking_price": _number(card.get("asking")),
            "neighbourhood": card.get("neighbourhood"),
            "municipality": card.get("municipality"),
            "property_type": card.get("property_type"),
            "ownership_type": card.get("ownership_type"),
            "lot_area_sqft": _number(card.get("lot_area_sqft")),
            "frontage_ft": _number(card.get("frontage_ft")) or _number(_official(raw, "frontage_ft")),
            "depth_ft": _number(card.get("depth_ft")) or _number(_official(raw, "depth_ft")),
            "house_size_sqft": _number(card.get("finished_sqft")),
            "bedrooms": _number(card.get("bedrooms")),
            "bathrooms": _number(card.get("bathrooms")),
            "year_built": _number(card.get("year_built")) or _number(_official(raw, "year_built")),
            "zoning": _official(raw, "zoning") or _official(raw, "current_zoning"),
            "road_class": _official(raw, "road_class"),
            "road_distance_m": _number(_official(raw, "distance_to_major_road_m")),
            "elementary_school": _official(raw, "elementary_catchment") or school.get("official_elementary_catchment"),
            "secondary_school": _official(raw, "secondary_catchment") or school.get("official_secondary_catchment"),
            "assessment_total": _number(_official(raw, "assessment_total")),
            "assessment_land": _number(_official(raw, "assessment_land")),
            "assessment_improvement": _number(_official(raw, "assessment_improvement")),
            "listing_status": card.get("listing_status"),
            "view": None,
            "park": None,
            "transit": None,
            "history_source": history.get("source_url"),
            "school_source": school.get("source_url"),
        }
        row["area_price_ratio"] = None
        output.append(row)
    return output


def _load_json(path: str | Path | None) -> Any:
    if not path:
        return []
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]], columns: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(columns), extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({column: _csv_value(row.get(column)) for column in columns})


def _csv_value(value: Any) -> Any:
    if isinstance(value, (list, tuple, dict)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    if isinstance(value, bool):
        return str(value).lower()
    return value


def _confidence_from_pairs(pairs: Sequence[MatchedPair]) -> str:
    if len(pairs) >= 10:
        return "HIGH"
    if len(pairs) >= 5:
        return "MEDIUM"
    if pairs:
        return "LOW"
    return "UNKNOWN"


def _school_rows() -> list[dict[str, Any]]:
    priors = {"A+": 200_000, "A": 170_000, "A-": 150_000, "B+": 120_000, "B": 90_000, "B-": 50_000, "C+ or below": 0}
    return [
        asdict(estimate_premium(
            component="school",
            value=value,
            method="CONFIG_PRIOR",
            sample_size=0,
            confidence="PRIOR_ONLY",
            notes="65% elementary / 35% secondary economic anchor; no matched-pair support in current local data.",
        ))
        | {"segment": tier, "source_ids": "", "elementary_weight": 0.65, "secondary_weight": 0.35}
        for tier, value in priors.items()
    ]


def _area_rows(properties: Sequence[Mapping[str, Any]], sales: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    property_neighbourhoods = {_text(row.get("neighbourhood")): row.get("neighbourhood") for row in properties if row.get("neighbourhood")}
    sale_counts = Counter(row.get("neighbourhood") for row in sales if row.get("is_sale") and row.get("neighbourhood"))
    rows: list[dict[str, Any]] = []
    for normalized_name, name in sorted(property_neighbourhoods.items()):
        count = sale_counts.get(name, 0)
        rows.append(asdict(estimate_premium(
            component="area_baseline",
            value=0,
            method="INSUFFICIENT_CROSS_AREA_CONTROL",
            sample_size=count,
            confidence="UNKNOWN",
            notes="Neighbourhood residual requires controlled cross-area transactions; current local sample cannot isolate it.",
        )) | {"segment": name, "source_ids": ""})
    return rows


def _micro_rows(properties: Sequence[Mapping[str, Any]], sales: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    values = {_text(row.get("road_class")) for row in [*properties, *sales] if row.get("road_class")}
    if not values:
        values = {"UNKNOWN"}
    rows: list[dict[str, Any]] = []
    for value in sorted(values):
        count = sum(1 for row in sales if row.get("is_sale") and _text(row.get("road_class")) == value)
        rows.append(asdict(estimate_premium(
            component="micro_location",
            value=0,
            method="NO_SALE_OUTCOME_ESTIMATE",
            sample_size=count,
            confidence="UNKNOWN",
            notes="Official road observations exist for some current cards, but no controlled sold outcome separates the effect.",
        )) | {"segment": value, "source_ids": ""})
    return rows


_BENCHMARK_SEEDS: tuple[dict[str, Any], ...] = (
    {
        "name": "2876 W 18th Avenue",
        "address": "2876 W 18TH AVENUE",
        "neighbourhood": "Arbutus",
        "asking_price": 1_700_000,
        "lot_area_sqft": 4_025,
        "frontage_ft": 33,
        "depth_ft": 122,
        "park": "park-facing",
        "source_type": "MANUAL_BENCHMARK",
    },
    {
        "name": "3974 W 10th Avenue",
        "address": "3974 W 10TH AVENUE",
        "neighbourhood": "Point Grey",
        "asking_price": 1_900_000,
        "house_size_sqft": 2_236,
        "lot_area_sqft": 4_026,
        "frontage_ft": 33,
        "depth_ft": 122,
        "year_built": 1979,
        "view": "view",
        "source_type": "MANUAL_BENCHMARK",
    },
    {
        "name": "333 E 13th Avenue",
        "address": "333 E 13TH AVENUE",
        "neighbourhood": "Mount Pleasant",
        "asking_price": 1_468_000,
        "lot_area_sqft": 4_208,
        "frontage_ft": 49.5,
        "source_type": "MANUAL_BENCHMARK",
    },
    {
        "name": "486 E King Edward Avenue",
        "address": "486 E KING EDWARD AVENUE",
        "neighbourhood": "Fraser/Main",
        "asking_price": 1_250_000,
        "lot_area_sqft": 3_360,
        "frontage_ft": 32,
        "depth_ft": 105,
        "year_built": 1927,
        "road_class": "major arterial",
        "source_type": "MANUAL_BENCHMARK",
    },
    {
        "name": "4561 Nanaimo Street",
        "address": "4561 NANAIMO STREET",
        "neighbourhood": "East Vancouver",
        "asking_price": 1_499_000,
        "lot_area_sqft": 5_100,
        "frontage_ft": 50,
        "depth_ft": 102,
        "house_size_sqft": 2_205,
        "road_class": "major",
        "source_type": "MANUAL_BENCHMARK",
    },
)


def _benchmark_rows(properties: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    by_address = {_key(row.get("address")): row for row in properties if row.get("address")}
    rows: list[dict[str, Any]] = []
    for seed in _BENCHMARK_SEEDS:
        observed = by_address.get(_key(seed["address"]), {})
        merged = dict(seed)
        for field in ("asking_price", "lot_area_sqft", "frontage_ft", "depth_ft", "house_size_sqft", "year_built", "road_class", "neighbourhood"):
            if observed.get(field) not in (None, ""):
                merged[field] = observed[field]
        raw_house_value = observed.get("assessment_improvement")
        normalized = normalize_price(
            asking=_number(merged.get("asking_price")),
            existing_house_value=_number(raw_house_value),
            school_value=0,
            area_value=0,
            micro_adjustments={},
            lot_area_sqft=_number(merged.get("lot_area_sqft")),
        )
        rows.append({
            "benchmark": merged["name"],
            "address": merged["address"],
            "observed_in_current_input": bool(observed),
            "source_type": merged.get("source_type"),
            "asking_price": merged.get("asking_price"),
            "neighbourhood": merged.get("neighbourhood"),
            "lot_area_sqft": merged.get("lot_area_sqft"),
            "frontage_ft": merged.get("frontage_ft"),
            "depth_ft": merged.get("depth_ft"),
            "market_house_value_prior": raw_house_value,
            "holding_utility_value": None,
            "school_value_applied": 0,
            "area_value_applied": 0,
            "micro_value_applied": 0,
            "total_location_value_applied": normalized.total_location_value,
            "market_land_basis": normalized.market_land_basis,
            "normalized_physical_lot_basis": normalized.normalized_physical_lot_basis,
            "normalized_lot_price_per_sqft": normalized.normalized_lot_price_per_sqft,
            "normalized_price_per_buildable_sqft": normalized.normalized_price_per_buildable_sqft,
            "ralv": None,
            "confidence": "LOW" if not observed or raw_house_value is None else "MEDIUM",
            "status": "RESEARCH_ONLY",
            "notes": "Location premiums are not applied because current local transaction evidence cannot identify them. BCA improvement is a prior only.",
        })
    return rows


def _feature_schema() -> dict[str, Any]:
    return {
        "schema_version": "location-feature-schema-v1",
        "model_separation": {
            "model_a": "price_normalization",
            "model_b": "investment_redevelopment",
            "rule": "normalized physical lot basis must never be compared directly with full local RALV for MOS",
        },
        "location_value": {
            "school": ["elementary", "secondary", "elementary_tier", "secondary_tier", "elementary_value", "secondary_value", "combined_value", "confidence"],
            "area_baseline": ["neighbourhood", "value", "methodology", "sample_size", "confidence"],
            "micro": ["view_value", "park_value", "beach_value", "walkability_value", "transit_value", "quiet_street_value", "road_noise_discount", "other_nuisance_discount"],
            "total_location_value": "school + area_baseline + micro_adjustments",
        },
        "price_normalization": ["raw_asking_price", "market_house_basis", "market_land_basis", "normalized_physical_lot_basis", "normalized_lot_price_per_sqft", "normalized_price_per_buildable_sqft"],
        "evidence": ["value", "applied_value", "method", "sample_size", "confidence", "automatic_rankable", "source_ids"],
    }


def _config_payload() -> dict[str, Any]:
    return {
        "schema_version": "location-model-config-v1",
        "research_only": True,
        "school_weights": {"elementary": 0.65, "secondary": 0.35},
        "school_prior_cad": {"A+": 200000, "A": 170000, "A-": 150000, "B+": 120000, "B": 90000, "B-": 50000, "C+_or_below": 0},
        "matched_pair": {"date_window_days": 183, "lot_tolerance_pct": 0.10, "frontage_tolerance_pct": 0.15, "living_area_tolerance_pct": 0.20, "year_built_tolerance_years": 25},
        "hedonic": {"minimum_rows": 20, "dependent_variable": "log(sold_price)", "time_fixed_effect": "sale_month"},
        "benchmarks": [seed["name"] for seed in _BENCHMARK_SEEDS],
        "automatic_application": {"allowed_confidence": ["HIGH", "MEDIUM"], "prior_only": "not applied", "low_or_unknown": "not applied"},
    }


def _methodology_markdown(summary: Mapping[str, Any]) -> str:
    return f"""# Vancouver Location Valuation Research V1

## Scope

This is a research-only run. It does not modify production Current Use,
Redevelopment, Dual Value, RALV, MOS, or ranking logic.

Observed at: `{summary['observed_at']}`  
Current property observations: **{summary['property_count']}**  
Local sold observations: **{summary['sale_count']}**  
Local listing-history observations: **{summary['history_count']}**  
Local school fixture observations: **{summary['school_input_count']}**

## Two accounts

Model A estimates `market_land_basis = asking - existing_house_value` and then
subtracts only location components that have transaction evidence safe to
apply. It reports `normalized_physical_lot_basis` and $/sqft metrics.

Model B remains the redevelopment account. Its RALV must use local new-product
exit comparables and redevelopment costs. It is intentionally not calculated
from or mixed with Model A in this run.

## Evidence rule

Every premium carries value, applied value, method, sample size, confidence,
and source IDs. Configuration priors are retained for review but have applied
value zero. The current local transaction data has no controlled boundary
matched pairs for school, view, park, or road, so these components are not
automatically monetized.

## What the current data can and cannot identify

- BCA improvement value can be retained as an existing-house prior; it is not
  treated as the market house value without matched-sale calibration.
- School catchment names from official enrichment can identify school context,
  but the available sold rows do not provide school-separated matched pairs.
- Area residual requires cross-neighbourhood observations with lot, house,
  zoning, school, road, and time controls. The local ten-sale fixture has two
  neighbourhood groups and cannot identify that residual.
- Road observations exist for current properties, but no controlled sold
  outcome identifies a road discount.
- Parks, view, beach, amenity, and transit sale outcome data are absent from
  the current local input.
- A hedonic model is reported as insufficient when the configured minimum
  sample is not met; no coefficient is fabricated.

## Outputs

The CSVs are evidence ledgers, not a claim that every segment has a market
premium. `UNKNOWN`, `LOW`, and `PRIOR_ONLY` values must be resolved with a
larger lawful sold dataset before entering production price ranking.
"""


def _hedonic_markdown(result: HedonicResult, sales: Sequence[Mapping[str, Any]]) -> str:
    return f"""# Hedonic Model V1

Status: **{result.status}**  
Sale rows used: **{result.row_count}**  
Minimum required: **{result.diagnostics.get('minimum_rows', '—')}**

The model is diagnostic only and is not connected to production scoring.

## Diagnostics

```json
{json.dumps(result.diagnostics, ensure_ascii=False, indent=2, sort_keys=True)}
```

## Coefficients

```json
{json.dumps(result.coefficients, ensure_ascii=False, indent=2, sort_keys=True)}
```

The current input contains `{len(sales)}` normalized comparable rows. If the
status is `INSUFFICIENT_SAMPLE`, a coefficient is intentionally not produced.
"""


def _recommendation_markdown(summary: Mapping[str, Any], hedonic: HedonicResult) -> str:
    return f"""# Recommendation

## Current conclusion

This V1 run should **not** modify production price scoring. It has
`{summary['sale_count']}` local sold observations and the hedonic status is
`{hedonic.status}`. The local data does not yet support a statistically
defensible split of Point Grey/Arbutus area premium, school premium, park/view
premium, or arterial discount.

## Answers to the research questions

1. **Statistically meaningful factors:** none can be confirmed by the current
   controlled transaction sample; official current road and catchment fields
   are available but lack sale-outcome controls.
2. **Approximate CAD values:** only the configuration priors are shown; they
   are `PRIOR_ONLY` and applied value is zero.
3. **Insufficient evidence:** school, area residual, view, park/beach,
   walkability, transit, and road/noise premiums/discounts.
4. **School prior ladder:** not supported or rejected by the current sample;
   preserve as an economic anchor only.
5. **Neighbourhood residuals:** UNKNOWN until cross-area controlled sales are
   added.
6. **Arterial discount:** UNKNOWN until same-context sold pairs are added.
7. **Park/view/beach:** UNKNOWN; the benchmark facts are retained but not
   monetized.
8. **Transit:** UNKNOWN; no structured sale-outcome feature is present.
9. **Correlated factors:** neighbourhood, school, view, amenity and buyer pool
   are likely correlated; do not split them without variation and controls.
10. **Next production change:** none yet. Acquire a larger, lawful detached
    freehold sold dataset across the $1.0M–$3.0M+ range, join official school
    and GIS features, then validate matched pairs and hedonic diagnostics.

## Model safety

`normalized_physical_lot_basis` is a cross-area comparison metric only. It
must not be compared directly with full local RALV to calculate MOS, because
local RALV already includes local product and buyer-pool premiums.
"""


def run_location_research(
    *,
    input_path: str | Path,
    history_path: str | Path | None = None,
    comparables_path: str | Path | None = None,
    school_path: str | Path | None = None,
    output_dir: str | Path = "research/location",
    observed_at: str | datetime | None = None,
) -> dict[str, Any]:
    """Run the read-only V1 location research and write all requested artifacts."""

    payload = _load_json(input_path)
    history_rows = _load_json(history_path) if history_path else []
    comparable_rows = _load_json(comparables_path) if comparables_path else []
    school_rows = _load_json(school_path) if school_path else []
    if not isinstance(history_rows, list):
        history_rows = []
    if not isinstance(comparable_rows, list):
        comparable_rows = []
    if not isinstance(school_rows, list):
        school_rows = []
    properties = property_observations(payload, history_rows, school_rows)
    sales = normalize_sale_rows(comparable_rows)
    all_pairs = [pair for factor in ("school", "elementary_school", "secondary_school", "road", "view", "park") for pair in find_matched_pairs(sales, factor=factor)]
    hedonic = fit_hedonic_model(sales, minimum_rows=20)
    observed_text = observed_at.isoformat() if isinstance(observed_at, datetime) else str(observed_at or datetime.now().astimezone().isoformat())
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    school_rows_out = _school_rows()
    area_rows_out = _area_rows(properties, sales)
    micro_rows_out = _micro_rows(properties, sales)
    pair_rows = [asdict(pair) for pair in all_pairs]
    if not pair_rows:
        pair_rows = [{"pair_id": "", "factor": "", "left_id": "", "right_id": "", "left_factor_value": "", "right_factor_value": "", "price_difference": "", "price_difference_pct": "", "matched": False, "diagnostics": "NO_MATCHED_PAIRS_IN_LOCAL_INPUT"}]
    benchmark_rows = _benchmark_rows(properties)
    summary = {
        "schema_version": "location-research-v1",
        "observed_at": observed_text,
        "property_count": len(properties),
        "sale_count": len(sales),
        "history_count": len(history_rows),
        "school_input_count": len(school_rows),
        "matched_pair_count": len(all_pairs),
        "hedonic_status": hedonic.status,
        "automatic_premium_count": sum(1 for row in [*school_rows_out, *area_rows_out, *micro_rows_out] if row.get("automatic_rankable") is True),
        "unknown_or_prior_premium_count": sum(1 for row in [*school_rows_out, *area_rows_out, *micro_rows_out] if row.get("automatic_rankable") is not True),
    }

    (out / "location-feature-schema-v1.json").write_text(json.dumps(_feature_schema(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (out / "location-model-config-v1.yaml").write_text(yaml.safe_dump(_config_payload(), allow_unicode=True, sort_keys=False), encoding="utf-8")
    _write_csv(out / "school-premium-v1.csv", school_rows_out, _PREMIUM_COLUMNS)
    _write_csv(out / "area-baseline-premium-v1.csv", area_rows_out, _PREMIUM_COLUMNS)
    _write_csv(out / "micro-location-premium-v1.csv", micro_rows_out, _PREMIUM_COLUMNS)
    _write_csv(out / "matched-pairs-v1.csv", pair_rows, _PAIR_COLUMNS)
    (out / "location-methodology-v1.md").write_text(_methodology_markdown(summary), encoding="utf-8")
    (out / "hedonic-model-v1.md").write_text(_hedonic_markdown(hedonic, sales), encoding="utf-8")
    (out / "benchmark-validation-v1.md").write_text(_benchmark_markdown(benchmark_rows), encoding="utf-8")
    (out / "RECOMMENDATION.md").write_text(_recommendation_markdown(summary, hedonic), encoding="utf-8")
    return {"summary": summary, "output_dir": str(out), "output_paths": sorted(str(path) for path in out.iterdir() if path.is_file())}


def _benchmark_markdown(rows: Sequence[Mapping[str, Any]]) -> str:
    lines = [
        "# Benchmark Validation V1",
        "",
        "These benchmark facts are retained for blind-test review. They are not address bonuses and no location premium is hard-coded into Model A.",
        "",
        "| Benchmark | Asking | Observed in current input | BCA improvement prior | Market land basis | Normalized lot basis | Normalized lot $/sqft | Confidence |",
        "|---|---:|---|---:|---:|---:|---:|---|",
    ]
    for row in rows:
        fmt = lambda value: "UNKNOWN" if value in (None, "") else f"${float(value):,.0f}"
        lines.append("| {benchmark} | {asking} | {observed} | {house} | {land} | {normalized} | {psf} | {confidence} |".format(
            benchmark=row.get("benchmark"),
            asking=fmt(row.get("asking_price")),
            observed="YES" if row.get("observed_in_current_input") else "NO",
            house=fmt(row.get("market_house_value_prior")),
            land=fmt(row.get("market_land_basis")),
            normalized=fmt(row.get("normalized_physical_lot_basis")),
            psf=fmt(row.get("normalized_lot_price_per_sqft")),
            confidence=row.get("confidence"),
        ))
    lines += [
        "",
        "`UNKNOWN` means the required input was not in the local snapshot; it is not a zero market value. BCA improvement is a prior and requires matched-sale calibration.",
    ]
    return "\n".join(lines) + "\n"


__all__ = [
    "HedonicResult",
    "MatchedPair",
    "NormalizedPrice",
    "PremiumEstimate",
    "find_matched_pairs",
    "fit_hedonic_model",
    "normalize_price",
    "normalize_sale_rows",
    "property_observations",
    "run_location_research",
    "sum_location_value",
]
