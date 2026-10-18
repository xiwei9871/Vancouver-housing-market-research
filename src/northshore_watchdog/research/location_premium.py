"""Research-only initial location premium estimates.

The estimates in this module are deliberately not production scores.  They
use the available sold transactions to produce directional, controlled
residuals while keeping school, park, view, beach, and transit components
unidentified when the current data cannot separate them.
"""

from __future__ import annotations

import json
import math
from collections import Counter
from datetime import date, datetime
from typing import Any, Mapping, Sequence

import numpy as np


_NUMERIC_FIELDS = (
    "lot_area_sqft",
    "frontage_ft",
    "depth_ft",
    "living_area_sqft",
    "year_built",
    "bca_improvement",
    "beds",
    "baths",
)
_ZONE_GROUPS = ("R", "RT", "RM", "CD", "OTHER")
_ROAD_LEVELS = ("E", "B", "D")
_UNKNOWN_COMPONENTS = ("school", "park", "view", "beach", "transit")

STANDARD_PROPERTY_PROFILE_CONFIG: dict[str, Any] = {
    "profile_name": "VANCOUVER_STANDARD_PROPERTY_V2",
    "property_type": "DETACHED_FREEHOLD",
    "lot_area_sqft": 4026,
    "frontage_ft": 33,
    "depth_ft": 122,
    "lot_shape": "REGULAR_RECTANGULAR",
    "living_area_sqft": 1700,
    "beds": 3,
    "baths": 2,
    "year_built": 1960,
    "year_built_reference": 1960,
    "year_built_sensitivity": [1940, 1960, 1980],
    "condition": "NEUTRAL",
    "condition_definition": "Normally habitable, ordinary maintenance, no luxury renovation premium, no major deferred maintenance.",
    "zoning": "R1-1",
    "zoning_group": "R",
    "road_class": "E",
    "view": "NONE",
    "park_facing": False,
    "waterfront": False,
    "bca_improvement_reference": "ELIGIBLE_SAMPLE_MEDIAN_CONTROL",
}


def _number(value: Any) -> float | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _month(value: Any) -> str:
    if isinstance(value, datetime):
        return value.strftime("%Y-%m")
    if isinstance(value, date):
        return value.strftime("%Y-%m")
    return str(value or "UNKNOWN")[:7]


def _zoning_group(value: Any) -> str:
    text = str(value or "").upper()
    if text.startswith(("R1", "R3")):
        return "R"
    if text.startswith("RT"):
        return "RT"
    if text.startswith("RM"):
        return "RM"
    if text.startswith("CD"):
        return "CD"
    return "OTHER"


def _confidence(sample_size: int) -> str:
    if sample_size >= 50:
        return "MEDIUM"
    if sample_size >= 10:
        return "LOW"
    return "UNKNOWN"


def _source_ids(rows: Sequence[Mapping[str, Any]]) -> list[str]:
    return [str(row.get("record_id") or row.get("source_id") or row.get("address")) for row in rows[:100]]


def _profile(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for field in _NUMERIC_FIELDS:
        values = sorted(_number(row.get(field)) for row in rows if _number(row.get(field)) is not None)
        result[field] = values[len(values) // 2] if values else 0.0
    months = sorted(_month(row.get("sale_date")) for row in rows if _month(row.get("sale_date")) != "UNKNOWN")
    result["sale_date"] = f"{months[-1]}-01" if months else "2026-01-01"
    result["zoning"] = "R1-1"
    result["road_class"] = "E"
    return result


def build_standard_property_profile(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Resolve the fixed Vancouver counterfactual used by V1.4.

    The physical and use fields are fixed by the research contract.  BCA
    improvement is a control variable rather than a user-specified part of
    the standard property, so it uses the eligible sample median and is
    explicitly labelled as such.
    """

    sample = _profile(rows)
    profile = dict(STANDARD_PROPERTY_PROFILE_CONFIG)
    profile["year_built_sensitivity"] = list(STANDARD_PROPERTY_PROFILE_CONFIG["year_built_sensitivity"])
    profile["bca_improvement"] = sample.get("bca_improvement", 0.0)
    profile["sale_date"] = sample.get("sale_date", "2026-01-01")
    return profile


def _feature_value(row: Mapping[str, Any], field: str) -> str:
    if field == "month":
        return _month(row.get("sale_date"))
    if field == "zoning_group":
        return _zoning_group(row.get("zoning"))
    return str(row.get(field) or "UNKNOWN")


def _fit_ridge(
    rows: Sequence[Mapping[str, Any]],
    categorical: Sequence[tuple[str, Sequence[str]]],
    *,
    ridge_lambda: float = 10.0,
) -> dict[str, Any] | None:
    if len(rows) < 2:
        return None
    medians: dict[str, float] = {}
    means: dict[str, float] = {}
    scales: dict[str, float] = {}
    numeric_values: list[list[float]] = []
    for field in _NUMERIC_FIELDS:
        values = sorted(_number(row.get(field)) for row in rows if _number(row.get(field)) is not None)
        medians[field] = values[len(values) // 2] if values else 0.0
    for row in rows:
        numeric_values.append([_number(row.get(field)) or medians[field] for field in _NUMERIC_FIELDS])
    numeric = np.asarray(numeric_values, dtype=float)
    for index, field in enumerate(_NUMERIC_FIELDS):
        means[field] = float(numeric[:, index].mean())
        scale = float(numeric[:, index].std())
        scales[field] = scale if scale > 0 else 1.0
        numeric[:, index] = (numeric[:, index] - means[field]) / scales[field]

    blocks = [np.ones((len(rows), 1), dtype=float), numeric]
    for field, levels in categorical:
        values = [_feature_value(row, field) for row in rows]
        blocks.extend(np.asarray([[float(value == level)] for value in values]) for level in levels[1:])
    design = np.hstack(blocks)
    target = np.asarray([math.log(float(row["sale_price"])) for row in rows], dtype=float)
    penalty = np.diag([0.0] + [ridge_lambda] * (design.shape[1] - 1))
    try:
        beta = np.linalg.solve(design.T @ design + penalty, design.T @ target)
    except np.linalg.LinAlgError:
        return None
    residuals = target - design @ beta
    return {
        "beta": beta,
        "medians": medians,
        "means": means,
        "scales": scales,
        "categorical": [(field, tuple(levels)) for field, levels in categorical],
        "rmse_log": float(np.sqrt(np.mean(residuals**2))),
        "row_count": len(rows),
    }


def _predict(model: Mapping[str, Any], row: Mapping[str, Any]) -> float:
    numeric = [
        ((_number(row.get(field)) or model["medians"][field]) - model["means"][field]) / model["scales"][field]
        for field in _NUMERIC_FIELDS
    ]
    values: list[float] = [1.0, *numeric]
    for field, levels in model["categorical"]:
        value = _feature_value(row, field)
        values.extend(float(value == level) for level in levels[1:])
    return math.exp(float(np.asarray(values) @ model["beta"]))


def _estimate_row(
    *,
    component: str,
    segment: str,
    value: float,
    sample_size: int,
    method: str,
    reference_segment: str,
    source_ids: Sequence[str] = (),
    notes: str = "",
    **extra: Any,
) -> dict[str, Any]:
    return {
        "component": component,
        "segment": segment,
        "value": round(float(value), -3),
        "applied_value": 0.0,
        "reference_segment": reference_segment,
        "method": method,
        "sample_size": sample_size,
        "confidence": _confidence(sample_size),
        "automatic_rankable": False,
        "source_ids": list(source_ids),
        "notes": notes,
        **extra,
    }


def estimate_initial_location_premiums(
    rows: Sequence[Mapping[str, Any]],
    *,
    ridge_lambda: float = 10.0,
    include_road: bool = False,
    standard_profile: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Estimate initial neighbourhood and road values from eligible sales.

    Neighbourhood values are conditional residuals at the fixed V1.4 standard
    property profile and are centred on the sample-weighted City of Vancouver
    prediction for that same profile.
    They include location effects that cannot yet be separated, especially
    school and micro-location effects.  Road values are conditional on
    neighbourhood and use quiet residential class ``E`` as the reference. If
    ``include_road`` is true, the area model is fit only on rows with a known
    road class and controls road class so the area residual is not also
    carrying a road-composition effect.
    All returned estimates remain research-only with ``applied_value=0``.
    """

    eligible = [
        dict(row)
        for row in rows
        if row.get("statistical_eligible") is True
        and _number(row.get("sale_price")) is not None
        and _number(row.get("sale_price")) > 0
        and row.get("neighbourhood") not in (None, "")
    ]
    estimates: list[dict[str, Any]] = []
    diagnostics: dict[str, Any] = {
        "method": "INITIAL_RIDGE_CONTROLLED_RESIDUAL",
        "eligible_rows": len(eligible),
        "ridge_lambda": ridge_lambda,
        "automatic_application": False,
        "road_controlled": include_road,
        "standard_profile": build_standard_property_profile(eligible) if eligible else {},
    }
    if standard_profile:
        diagnostics["standard_profile"] = {
            **diagnostics["standard_profile"],
            **dict(standard_profile),
        }
    if len(eligible) >= 20:
        model_rows = (
            [row for row in eligible if str(row.get("road_class") or "") in _ROAD_LEVELS]
            if include_road else eligible
        )
        areas = sorted({str(row["neighbourhood"]) for row in model_rows})
        months = sorted({_month(row.get("sale_date")) for row in model_rows})
        area_categories: list[tuple[str, Sequence[str]]] = [
            ("neighbourhood", areas),
            ("month", months),
            ("zoning_group", _ZONE_GROUPS),
        ]
        if include_road:
            area_categories.append(("road_class", _ROAD_LEVELS))
        area_model = _fit_ridge(model_rows, area_categories, ridge_lambda=ridge_lambda)
        counts = Counter(str(row["neighbourhood"]) for row in model_rows)
        profile = dict(diagnostics["standard_profile"])
        profile["year_built"] = int(profile.get("year_built_reference", profile.get("year_built", 1960)))
        predictions: dict[str, float] = {}
        if area_model:
            for area in areas:
                counterfactual = {**profile, "neighbourhood": area}
                predictions[area] = _predict(area_model, counterfactual)
            weighted_mean = sum(predictions[area] * counts[area] for area in areas) / len(model_rows)
            age_sensitivity: dict[str, float] = {}
            for age in profile.get("year_built_sensitivity", [1940, 1960, 1980]):
                age_profile = {**profile, "year_built": int(age)}
                age_predictions = {
                    area: _predict(area_model, {**age_profile, "neighbourhood": area})
                    for area in areas
                }
                age_neutral = sum(age_predictions[area] * counts[area] for area in areas) / len(model_rows)
                age_sensitivity[str(age)] = round(age_neutral, -3)
            diagnostics["neutral_standard_value"] = round(weighted_mean, -3)
            diagnostics["neutral_standard_value_age_sensitivity"] = age_sensitivity
            for area in areas:
                group = [row for row in model_rows if str(row["neighbourhood"]) == area]
                estimates.append(_estimate_row(
                    component="area_location_residual",
                    segment=area,
                    value=predictions[area] - weighted_mean,
                    sample_size=counts[area],
                    method="INITIAL_RIDGE_CONTROLLED_NEIGHBOURHOOD_RESIDUAL",
                    reference_segment="CITY_SAMPLE_WEIGHTED_MEAN",
                    source_ids=_source_ids(group),
                    notes="Combined neighbourhood residual for the fixed V1.4 standard property; school, view, park, amenity, transit, and other correlated location effects are not separately identified.",
                    standard_property_value=round(predictions[area], -3),
                    neutral_standard_value=round(weighted_mean, -3),
                    standard_property_value_age_1940=round(_predict(area_model, {**profile, "year_built": 1940, "neighbourhood": area}), -3),
                    standard_property_value_age_1960=round(_predict(area_model, {**profile, "year_built": 1960, "neighbourhood": area}), -3),
                    standard_property_value_age_1980=round(_predict(area_model, {**profile, "year_built": 1980, "neighbourhood": area}), -3),
                ))
            diagnostics["area_model_rows"] = area_model["row_count"]
            diagnostics["area_model_rmse_log"] = round(area_model["rmse_log"], 6)
        road_rows = [row for row in eligible if str(row.get("road_class") or "") in _ROAD_LEVELS]
        road_counts = Counter(str(row["road_class"]) for row in road_rows)
        if len(road_rows) >= 20 and len(road_counts) >= 2:
            road_model = _fit_ridge(
                road_rows,
                [("neighbourhood", areas), ("road_class", _ROAD_LEVELS), ("month", months), ("zoning_group", _ZONE_GROUPS)],
                ridge_lambda=ridge_lambda,
            )
            if road_model:
                reference_area = counts.most_common(1)[0][0]
                road_profile = {**profile, "neighbourhood": reference_area}
                reference_value = _predict(road_model, {**road_profile, "road_class": "E"})
                for road in _ROAD_LEVELS:
                    road_value = _predict(road_model, {**road_profile, "road_class": road}) - reference_value
                    estimates.append(_estimate_row(
                        component="road_adjustment",
                        segment=road,
                        value=road_value,
                        sample_size=road_counts.get(road, 0),
                        method="INITIAL_RIDGE_CONTROLLED_ROAD_ADJUSTMENT",
                        reference_segment="E",
                        source_ids=_source_ids([row for row in road_rows if str(row.get("road_class")) == road]),
                        notes="Conditional adjustment at the modal neighbourhood and median physical profile; E is quiet residential reference.",
                    ))
                diagnostics["road_model_rows"] = road_model["row_count"]
                diagnostics["road_model_rmse_log"] = round(road_model["rmse_log"], 6)

    for component in _UNKNOWN_COMPONENTS:
        estimates.append(_estimate_row(
            component=component,
            segment="UNIDENTIFIED",
            value=0,
            sample_size=0,
            method="INSUFFICIENT_DIRECTIONAL_EVIDENCE",
            reference_segment="NONE",
            notes="Current data does not identify this factor independently; no value is applied.",
        ))
    return {"estimates": estimates, "diagnostics": diagnostics}


def render_initial_location_premium_markdown(result: Mapping[str, Any], *, version: str = "V1.2") -> str:
    estimates = list(result.get("estimates", []))
    area = [row for row in estimates if row.get("component") == "area_location_residual"]
    road = [row for row in estimates if row.get("component") == "road_adjustment"]
    def money(value: Any) -> str:
        return "UNKNOWN" if value in (None, "") else f"${float(value):,.0f}"
    lines = [
        f"# Initial Location Premium Research {version}",
        "",
        "This is a research-only directional estimate. No value is written into production scoring, RALV, or MOS.",
        "",
        "## Method",
        "",
        "A ridge-controlled log-price model controls lot area, frontage, depth, living area, year built, BCA improvement, beds, baths, sale month, and zoning group. Neighbourhood output is a combined residual for the fixed V1.4 standard property and is centred on the sample-weighted City of Vancouver prediction for that profile. Road output is conditional on neighbourhood and uses quiet residential class E as the reference.",
        "",
        f"- Eligible rows: `{result.get('diagnostics', {}).get('eligible_rows', 0)}`",
        f"- Area model rows: `{result.get('diagnostics', {}).get('area_model_rows', 0)}`",
        f"- Road model rows: `{result.get('diagnostics', {}).get('road_model_rows', 0)}`",
        f"- Neutral standard value: `{money(result.get('diagnostics', {}).get('neutral_standard_value'))}`",
        "- Automatic application: `DISABLED`",
        "",
        "## V1.4 standard property",
        "",
        f"`{json.dumps(result.get('diagnostics', {}).get('standard_profile', {}), ensure_ascii=False, sort_keys=True)}`",
        "",
        "The primary residual uses the 1960 midpoint. 1940 and 1980 are reported as age sensitivity only; they are not averaged into the primary value.",
        "",
        "## Area residuals",
        "",
        "| Neighbourhood | Standard property value | Neutral standard value | Residual | Samples | Confidence |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for row in sorted(area, key=lambda item: float(item.get("value", 0)), reverse=True):
        lines.append(
            f"| {row['segment']} | {money(row.get('standard_property_value'))} | "
            f"{money(row.get('neutral_standard_value'))} | {money(row.get('value'))} | "
            f"{row['sample_size']} | {row['confidence']} |"
        )
    lines += [
        "",
        "## Road adjustments",
        "",
        "| Road class | Initial CAD adjustment vs E | Samples | Confidence |",
        "|---|---:|---:|---|",
    ]
    for row in sorted(road, key=lambda item: item["segment"]):
        lines.append(f"| {row['segment']} | ${float(row['value']):,.0f} | {row['sample_size']} | {row['confidence']} |")
    lines += [
        "",
        "## Not separately identified",
        "",
        "School, park, view, beach, and transit are retained as zero/unknown until controlled transaction evidence can separate them from neighbourhood and road effects.",
        "",
        "## Safety rule",
        "",
        "The combined neighbourhood residual must not be added to a separately estimated school premium unless the latter is independently identified; otherwise location would be double-counted.",
        "",
    ]
    return "\n".join(lines)


__all__ = [
    "STANDARD_PROPERTY_PROFILE_CONFIG",
    "build_standard_property_profile",
    "estimate_initial_location_premiums",
    "render_initial_location_premium_markdown",
]
