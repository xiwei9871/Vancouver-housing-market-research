"""Sensitivity analysis for the combined Vancouver location residual.

This module is research-only.  It deliberately keeps school inside the
combined neighbourhood/location residual because the current transaction data
cannot reliably separate school from neighbourhood, buyer-pool, and other
correlated location effects.  The output compares time windows and a
physical-property cohort instead of turning one model run into a false point
estimate.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from statistics import median
from typing import Any, Mapping, Sequence

from .location_premium import build_standard_property_profile, estimate_initial_location_premiums


_WINDOWS = (36, 24, 12)
_COHORT = "OLD_HOUSE_STANDARD_LOT"


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


def _number(value: Any) -> float | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _eligible(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in rows
        if row.get("statistical_eligible") is True
        and _number(row.get("sale_price")) is not None
        and _number(row.get("sale_price")) > 0
        and row.get("neighbourhood") not in (None, "")
        and _date(row.get("sale_date")) is not None
    ]


def _cutoff(reference_end: date, months: int) -> date:
    return reference_end - timedelta(days=round(months * 365.25 / 12))


def _window(rows: Sequence[Mapping[str, Any]], reference_end: date, months: int) -> list[dict[str, Any]]:
    start = _cutoff(reference_end, months)
    return [row for row in rows if start <= _date(row.get("sale_date")) <= reference_end]


def _standard_old_house(row: Mapping[str, Any]) -> bool:
    lot = _number(row.get("lot_area_sqft"))
    frontage = _number(row.get("frontage_ft"))
    living = _number(row.get("living_area_sqft"))
    year = _number(row.get("year_built"))
    return (
        lot is not None and 3_700 <= lot <= 4_300
        and frontage is not None and 30 <= frontage <= 36
        and living is not None and 1_500 <= living <= 2_500
        and year is not None and year < 1985
    )


def _area_values(result: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(row["segment"]): dict(row)
        for row in result.get("estimates", [])
        if row.get("component") == "area_location_residual"
    }


def _confidence(sample_size: int, stability: str) -> str:
    if sample_size >= 200 and stability == "STABLE":
        return "HIGH"
    if sample_size >= 50 and stability in {"STABLE", "MODERATELY_STABLE"}:
        return "MEDIUM"
    if sample_size >= 20:
        return "LOW"
    return "UNKNOWN"


def _stability(values: Sequence[float]) -> str:
    if len(values) < 2:
        return "INSUFFICIENT_VARIANTS"
    spread = max(values) - min(values)
    center = abs(float(median(values)))
    if spread <= 150_000 or (center > 0 and spread / center <= 0.25):
        return "STABLE"
    if spread <= 250_000 or (center > 0 and spread / center <= 0.50):
        return "MODERATELY_STABLE"
    return "UNSTABLE"


def _row(
    *,
    row_type: str,
    analysis_variant: str,
    segment: str,
    value: float,
    sample_size: int,
    method: str,
    confidence: str,
    cohort: str = "ALL_ELIGIBLE",
    reference_segment: str = "CITY_SAMPLE_WEIGHTED_MEAN",
    notes: str = "",
    **extra: Any,
) -> dict[str, Any]:
    return {
        "row_type": row_type,
        "analysis_variant": analysis_variant,
        "cohort": cohort,
        "component": "area_location_residual",
        "segment": segment,
        "value": round(float(value), -3),
        "applied_value": 0.0,
        "reference_segment": reference_segment,
        "method": method,
        "sample_size": int(sample_size),
        "confidence": confidence,
        "automatic_rankable": False,
        "school_split": False,
        "notes": notes,
        **extra,
    }


def estimate_location_premium_stability(
    rows: Sequence[Mapping[str, Any]],
    *,
    end_date: date | None = None,
    ridge_lambda: float = 10.0,
) -> dict[str, Any]:
    """Compare combined neighbourhood residuals across time/cohort variants.

    The full sample is limited to the observed BCA transaction window.  The
    12/24/36-month variants are nested subsets of that data, while the cohort
    variant is the standard old-house/standard-lot physical cohort.  Every
    estimate is diagnostic only and has ``applied_value=0``.
    """

    eligible = _eligible(rows)
    observed_dates = [_date(row.get("sale_date")) for row in eligible]
    reference_end = end_date or max(observed_dates, default=date.today())
    variant_rows: list[dict[str, Any]] = []
    variant_values: dict[str, dict[str, float]] = {}
    variant_sizes: dict[str, int] = {}
    variant_neutral_values: dict[str, float] = {}
    variant_age_sensitivities: dict[str, dict[str, float]] = {}
    standard_profile = build_standard_property_profile(eligible) if eligible else {}
    variants: list[tuple[str, list[dict[str, Any]], str]] = []
    for months in _WINDOWS:
        subset = _window(eligible, reference_end, months)
        variants.append((f"RECENT_{months}M" if months != 36 else "FULL_36M", subset, "ALL_ELIGIBLE"))
    cohort_rows = [row for row in _window(eligible, reference_end, 36) if _standard_old_house(row)]
    variants.append((_COHORT, cohort_rows, _COHORT))

    for variant, subset, cohort in variants:
        variant_sizes[variant] = len(subset)
        result = estimate_initial_location_premiums(
            subset,
            ridge_lambda=ridge_lambda,
            include_road=True,
            standard_profile=standard_profile,
        )
        values = _area_values(result)
        if result.get("diagnostics", {}).get("neutral_standard_value") not in (None, ""):
            variant_neutral_values[variant] = float(result["diagnostics"]["neutral_standard_value"])
        if result.get("diagnostics", {}).get("neutral_standard_value_age_sensitivity"):
            variant_age_sensitivities[variant] = dict(
                result["diagnostics"]["neutral_standard_value_age_sensitivity"]
            )
        # A value from a tiny neighbourhood subgroup is not a useful
        # sensitivity observation even if the overall variant is large. Keep
        # the base model's low-n rows visible in its own artifact, but do not
        # let them create a false stability range for the summary.
        variant_values[variant] = {
            segment: float(item["value"])
            for segment, item in values.items()
            if int(item.get("sample_size", 0)) >= 10
        }
        for segment, item in values.items():
            variant_rows.append(_row(
                row_type="VARIANT",
                analysis_variant=variant,
                cohort=cohort,
                segment=segment,
                value=float(item["value"]),
                sample_size=int(item["sample_size"]),
                method="SENSITIVITY_RIDGE_CONTROLLED_COMBINED_RESIDUAL",
                confidence=str(item["confidence"]),
                notes=(
                    "Combined neighbourhood/location residual; school is intentionally not split. "
                    "Road class is controlled in this V1.4 sensitivity run to reduce composition bias."
                ),
                neutral_standard_value=result.get("diagnostics", {}).get("neutral_standard_value"),
                standard_property_value=item.get("standard_property_value"),
                standard_property_value_age_1940=item.get("standard_property_value_age_1940"),
                standard_property_value_age_1960=item.get("standard_property_value_age_1960"),
                standard_property_value_age_1980=item.get("standard_property_value_age_1980"),
            ))

    segments = sorted({segment for values in variant_values.values() for segment in values})
    summary_rows: list[dict[str, Any]] = []
    neutral_standard_value = variant_neutral_values.get("FULL_36M")
    if neutral_standard_value is None and variant_neutral_values:
        neutral_standard_value = float(median(variant_neutral_values.values()))
    for segment in segments:
        values = [variant_values[variant][segment] for variant in variant_values if segment in variant_values[variant]]
        if not values:
            continue
        full = variant_values.get("FULL_36M", {}).get(segment)
        sample_sizes = {
            variant: sum(1 for row in _window(eligible, reference_end, months) if str(row.get("neighbourhood")) == segment)
            for variant, months in (("FULL_36M", 36), ("RECENT_24M", 24), ("RECENT_12M", 12))
        }
        sample_sizes[_COHORT] = sum(1 for row in cohort_rows if str(row.get("neighbourhood")) == segment)
        status = _stability(values)
        summary_rows.append(_row(
            row_type="SUMMARY",
            analysis_variant="ROBUST_SUMMARY",
            segment=segment,
            value=float(median(values)),
            sample_size=sample_sizes.get("FULL_36M", 0),
            method="MEDIAN_ACROSS_TIME_AND_PHYSICAL_COHORT_SENSITIVITY",
            confidence=_confidence(sample_sizes.get("FULL_36M", 0), status),
            notes=(
                "Combined residual only; school premium is not separately estimated. "
                "The standard-property values below use the FULL_36M neutral standard value as the common display anchor. "
                "Use sensitivity range for review, not as an automatic production adjustment."
            ),
            full_36m_value=full,
            recent_24m_value=variant_values.get("RECENT_24M", {}).get(segment),
            recent_12m_value=variant_values.get("RECENT_12M", {}).get(segment),
            old_house_cohort_value=variant_values.get(_COHORT, {}).get(segment),
            sample_size_full_36m=sample_sizes.get("FULL_36M", 0),
            sample_size_recent_24m=sample_sizes.get("RECENT_24M", 0),
            sample_size_recent_12m=sample_sizes.get("RECENT_12M", 0),
            sample_size_old_house_cohort=sample_sizes.get(_COHORT, 0),
            sensitivity_low=round(min(values), -3),
            sensitivity_high=round(max(values), -3),
            sensitivity_range=round(max(values) - min(values), -3),
            variant_count=len(values),
            stability_status=status,
            neutral_standard_value=neutral_standard_value,
            standard_property_value=(
                round(neutral_standard_value + float(median(values)), -3)
                if neutral_standard_value is not None else None
            ),
            standard_property_value_low=(
                round(neutral_standard_value + min(values), -3)
                if neutral_standard_value is not None else None
            ),
            standard_property_value_high=(
                round(neutral_standard_value + max(values), -3)
                if neutral_standard_value is not None else None
            ),
        ))

    diagnostics = {
        "eligible_rows": len(eligible),
        "model_rows_by_variant": variant_sizes,
        "reference_end_date": reference_end.isoformat(),
        "standard_property_profile": standard_profile,
        "neutral_standard_value": neutral_standard_value,
        "neutral_standard_value_age_sensitivity": variant_age_sensitivities.get("FULL_36M", {}),
        "neutral_standard_values_by_variant": variant_neutral_values,
        "window_definitions": {
            "FULL_36M": "observed transactions within 36 months before reference_end",
            "RECENT_24M": "observed transactions within 24 months before reference_end",
            "RECENT_12M": "observed transactions within 12 months before reference_end",
            _COHORT: "36-month rows with 3,700-4,300 sqft, 30-36 ft frontage, 1,500-2,500 sqft living area, pre-1985 house",
        },
        "automatic_application": False,
        "school_split": False,
        "status": "READY_FOR_REVIEW" if summary_rows else "INSUFFICIENT_SAMPLE",
    }
    return {
        "method": "COMBINED_LOCATION_RESIDUAL_SENSITIVITY",
        "automatic_application": False,
        "school_split": False,
        "diagnostics": diagnostics,
        "rows": [*summary_rows, *variant_rows],
    }


def render_location_premium_stability_markdown(result: Mapping[str, Any], *, version: str = "V1.3") -> str:
    diagnostics = result.get("diagnostics", {})
    summary = [row for row in result.get("rows", []) if row.get("row_type") == "SUMMARY"]
    profile = diagnostics.get("standard_property_profile", {})
    def money(value: Any) -> str:
        return "UNKNOWN" if value in (None, "") else f"${float(value):,.0f}"
    def scalar(value: Any) -> str:
        return "UNKNOWN" if value in (None, "") else f"{value:,.0f}" if isinstance(value, (int, float)) else str(value)
    lines = [
        f"# Combined Location Premium Stability Research {version}",
        "",
        "This is research-only. It does not modify production scoring, RALV, MOS, or candidate ranking.",
        "",
        "## Interpretation",
        "",
        "The value below is the combined location residual (a combined neighbourhood/location residual) for one fixed standard property. School is not separately priced because it is not identified independently from neighbourhood, buyer-pool, and other correlated location effects. Road class is controlled in this sensitivity run; it is not added again to the combined residual.",
        "",
        f"- Eligible Vancouver detached/freehold sales: `{diagnostics.get('eligible_rows', 0)}`",
        f"- Reference end date: `{diagnostics.get('reference_end_date', 'UNKNOWN')}`",
        f"- Status: `{diagnostics.get('status', 'UNKNOWN')}`",
        "- Automatic application: `DISABLED`",
        "- School split: `FALSE`",
        "",
        "## Standard property and neutral value",
        "",
        f"- Profile: `{profile.get('profile_name', 'UNKNOWN')}`",
        f"- Standard property: `{json.dumps(profile, ensure_ascii=False, sort_keys=True)}`",
        f"- Standard geometry/use: `{scalar(profile.get('lot_area_sqft'))} sqft; {scalar(profile.get('frontage_ft'))}×{scalar(profile.get('depth_ft'))} ft; {scalar(profile.get('living_area_sqft'))} sqft; {scalar(profile.get('beds'))} beds / {scalar(profile.get('baths'))} baths`",
        f"- Neutral standard value (FULL_36M common anchor): `{money(diagnostics.get('neutral_standard_value'))}`",
        f"- Neutral age sensitivity: `{json.dumps(diagnostics.get('neutral_standard_value_age_sensitivity', {}), ensure_ascii=False, sort_keys=True)}`",
        "",
        "## Stable summary",
        "",
        "The reported value is the median of available time-window/cohort estimates. The sensitivity interval is the observed min/max across those variants; it is not a formal confidence interval.",
        "",
        "| Neighbourhood | Standard property value | Standard value range | Median residual | 36M | 24M | 12M | Old-house cohort | Samples (36M) | Stability | Confidence |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|",
    ]
    for row in sorted(summary, key=lambda item: float(item.get("value", 0)), reverse=True):
        lines.append(
            f"| {row['segment']} | {money(row.get('standard_property_value'))} | "
            f"{money(row.get('standard_property_value_low'))}–{money(row.get('standard_property_value_high'))} | "
            f"{money(row.get('value'))} | {money(row.get('full_36m_value'))} | "
            f"{money(row.get('recent_24m_value'))} | {money(row.get('recent_12m_value'))} | "
            f"{money(row.get('old_house_cohort_value'))} | {row.get('sample_size_full_36m', 0)} | "
            f"{row.get('stability_status', 'UNKNOWN')} | {row.get('confidence', 'UNKNOWN')} |"
        )
    if not summary:
        lines += ["", "`INSUFFICIENT_SAMPLE`: no neighbourhood reached the model minimum in the requested variants."]
    lines += [
        "",
        "## Variants",
        "",
        "| Variant | Rows | Definition |",
        "|---|---:|---|",
    ]
    for variant, count in diagnostics.get("model_rows_by_variant", {}).items():
        definition = diagnostics.get("window_definitions", {}).get(variant, "")
        lines.append(f"| {variant} | {count} | {definition} |")
    lines += [
        "",
        "## Limitations",
        "",
        "- The BCA sales layer currently exposes an observed window beginning in 2023-01-01 for this run; requesting an earlier start date did not produce earlier observations. This is therefore an expanded price-range sample, not a verified five-year history.",
        "- BCA provides sale price and property/assessment controls, but transaction MLS identifiers remain unavailable for the bulk sample.",
        "- Current school catchments and current zoning are spatial context, not necessarily historical as-of-sale attributes.",
        "- The residual is not a causal school premium and must not be applied together with an independently estimated school premium.",
        "",
    ]
    return "\n".join(lines)


__all__ = ["estimate_location_premium_stability", "render_location_premium_stability_markdown"]
