from __future__ import annotations

from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Any, Iterable, Mapping

from pydantic import BaseModel, ConfigDict, Field

from .models import EvidenceStatus, OwnershipType, PropertyRecord, SourceType
from .normalization import calculate_lot_size_percentile, classify_ownership
from .provenance import SourceRecord, SourceRegistryEntry, load_source_registry_entries, registry_allows_evidence


class ComparableRecord(BaseModel):
    model_config = ConfigDict(extra="allow")

    comparable_id: str
    subject_property_id: str
    property_id: str | None = None
    address: str | None = None
    transaction_id: str | None = None
    municipality: str
    neighbourhood: str | None = None
    property_type: str | None = None
    ownership_type: OwnershipType = OwnershipType.UNKNOWN
    observed_at: datetime | None = None
    sold_date: datetime | None = None
    sold_price: int | None = Field(default=None, ge=0)
    asking_price: int | None = Field(default=None, ge=0)
    lot_area_sqft: float | None = Field(default=None, ge=0)
    frontage_ft: float | None = Field(default=None, ge=0)
    depth_ft: float | None = Field(default=None, ge=0)
    house_size_sqft: float | None = Field(default=None, ge=0)
    year_built: int | None = None
    condition: str | None = None
    lot_shape: str | None = None
    zoning: str | None = None
    road_exposure_level: int | None = Field(default=None, ge=1, le=5)
    current_use_building_value: int | None = Field(default=None, ge=0)
    assessment_improvement: int | None = Field(default=None, ge=0)
    assessment_total: int | None = Field(default=None, ge=0)
    assessment_land: int | None = Field(default=None, ge=0)
    assessment_ratio: float | None = None
    similarity_score: float | None = Field(default=None, ge=0, le=100)
    selection_tier: str | None = None
    reliable: bool = True
    conflict: bool = False
    sold_history_source_registry_ids: list[str] = Field(default_factory=list)
    exclusion_reasons: list[str] = Field(default_factory=list)
    source_records: list[SourceRecord] = Field(default_factory=list)


class ComparableResult(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    subject_property_id: str
    property: PropertyRecord
    selected_comparables: list[ComparableRecord] = Field(default_factory=list)
    neighbourhood_benchmark_comparables: list[ComparableRecord] = Field(default_factory=list)
    excluded_comparables: list[ComparableRecord] = Field(default_factory=list)
    comp_window_days: int | None = None
    comp_count: int = 0
    comp_confidence: str = "LOW_COMP_CONFIDENCE"
    valuation_status: str = "UNKNOWN"
    manual_verification_required: bool = False
    manual_verification_reasons: list[str] = Field(default_factory=list)
    median_sold_price: int | None = None
    median_lot_size: float | None = None
    median_house_size: float | None = None
    median_price_per_lot_sqft: float | None = None
    median_assessment_ratio: float | None = None
    neighbourhood_comparable_median: int | None = None
    neighbourhood_benchmark_count: int = 0
    neighbourhood_benchmark_window_days: int | None = None
    neighbourhood_benchmark_confidence: str = "LOW_COMP_CONFIDENCE"
    expected_market_value: int | None = None
    relative_affordability_index: float | None = None
    price_dislocation_pct: float | None = None
    price_safety_margin_pct: float | None = None
    land_value_proxy: int | None = None
    adjusted_land_price_per_sqft: float | None = None
    land_discount_or_premium_pct: float | None = None
    current_use_building_value_method: str | None = None
    comparable_conflict_count: int = 0
    comparable_conflict_ids: list[str] = Field(default_factory=list)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo and value.utcoffset() is not None else value.replace(tzinfo=timezone.utc)


def _text(value: Any) -> str:
    return " ".join(str(value or "").lower().replace("-", " ").split())


def _number(value: Any) -> Any:
    if value in (None, "") or isinstance(value, (int, float)):
        return value
    text = str(value).replace(",", "").replace("$", "").strip()
    try:
        parsed = float(text)
        return int(parsed) if parsed.is_integer() else parsed
    except ValueError:
        return value


def _as_comparable(
    value: ComparableRecord | dict[str, Any],
    subject_id: str,
    *,
    retrieved_at: datetime,
    source_registry_id: str | None = None,
    source_registry_ids: Iterable[str] | None = None,
    trusted_source_registry_ids: Iterable[str] | None = None,
    trusted_source_type: SourceType | str | None = None,
    source_registry: Mapping[str, SourceRegistryEntry] | None = None,
) -> ComparableRecord:
    if isinstance(value, ComparableRecord):
        data = value.model_dump(mode="python")
    else:
        data = dict(value)
    aliases = {
        "sale_price": "sold_price",
        "sold": "sold_price",
        "sale_date": "sold_date",
        "lot_size_sqft": "lot_area_sqft",
        "house_sqft": "house_size_sqft",
        "assessment_building": "current_use_building_value",
        "building_assessment": "assessment_improvement",
        "house_condition": "condition",
    }
    for old, new in aliases.items():
        if data.get(new) in (None, "") and data.get(old) not in (None, ""):
            data[new] = data[old]
    for field in ("sold_price", "asking_price", "lot_area_sqft", "frontage_ft", "depth_ft", "house_size_sqft", "year_built", "current_use_building_value", "assessment_improvement", "assessment_total", "assessment_land"):
        if field in data:
            data[field] = _number(data[field])
    if data.get("ownership_type") not in (None, "") and not isinstance(data.get("ownership_type"), OwnershipType):
        data["ownership_type"] = classify_ownership(data["ownership_type"])
    data.setdefault("subject_property_id", subject_id)
    if not data.get("observed_at"):
        data["observed_at"] = _aware(retrieved_at)
    if data.get("sold_date") and not isinstance(data["sold_date"], datetime):
        data["sold_date"] = datetime.fromisoformat(str(data["sold_date"]))
    # Source IDs configured for cross-checking are a whitelist, never a
    # provenance generator.  A comparable gets at most the source evidence
    # that was actually attached to its row; a single row cannot become two
    # independent observations merely because two IDs appear in config.
    recorded_at = data.get("retrieved_at") or retrieved_at
    if not isinstance(recorded_at, datetime):
        try:
            recorded_at = datetime.fromisoformat(str(recorded_at))
        except (TypeError, ValueError):
            recorded_at = retrieved_at
    raw_sources = data.get("source_records") or []
    source_records: list[SourceRecord] = []
    for raw_source in raw_sources:
        try:
            source = raw_source if isinstance(raw_source, SourceRecord) else SourceRecord.model_validate(raw_source)
        except (TypeError, ValueError):
            continue
        if source.field_name == "sold_history":
            source_records.append(source)
    trusted_ids = {
        str(item).strip()
        for item in (trusted_source_registry_ids or ())
        if str(item).strip()
    }
    if not source_records:
        fallback_source_id = data.get("source_registry_id") or source_registry_id
        try:
            fallback_type = trusted_source_type or data.get("source_type", SourceType.MANUAL)
            fallback_type = fallback_type if isinstance(fallback_type, SourceType) else SourceType(str(fallback_type))
        except ValueError:
            fallback_type = SourceType.MANUAL
        # A row-level registry ID is metadata, not independent corroboration.
        # Only materialize it as evidence when it is explicitly whitelisted;
        # otherwise retain an auditable anonymous fallback record.
        if fallback_source_id and trusted_ids and str(fallback_source_id) not in trusted_ids:
            fallback_source_id = None
        source_records = [
            SourceRecord(
                property_id=str(data.get("property_id") or data.get("comparable_id") or data["comparable_id"]),
                field_name="sold_history",
                source_url=str(data.get("source_url") or "UNKNOWN"),
                source_type=fallback_type,
                source_registry_id=str(fallback_source_id) if fallback_source_id else None,
                retrieved_at=_aware(recorded_at),
                raw_value=data.get("sold_price"),
                normalized_value=data.get("sold_price"),
                source_version=data.get("source_version"),
            )
        ]
    # Preserve all attached source records for auditability, but expose only
    # registered, confirmed records as cross-check evidence.  In particular,
    # a configured source ID can never upgrade an unregistered or unconfirmed
    # record.
    expected_type = trusted_source_type or SourceType.MLS
    if not isinstance(expected_type, SourceType):
        try:
            expected_type = SourceType(str(expected_type))
        except ValueError:
            expected_type = SourceType.MLS
    verified_source_ids = {
        source.source_registry_id
        for source in source_records
        if source.field_name == "sold_history"
        and source.source_registry_id in trusted_ids
        and source.evidence_status is EvidenceStatus.CONFIRMED
        and registry_allows_evidence(
            source_registry.get(source.source_registry_id) if source_registry else None,
            expected_source_type=expected_type,
        )
        and source.source_type is expected_type
    }
    data["sold_history_source_registry_ids"] = list(dict.fromkeys(
        source_id for source_id in verified_source_ids if source_id
    ))
    data["source_records"] = source_records
    return ComparableRecord.model_validate(data)


def _is_detached(value: str | None) -> bool:
    text = _text(value)
    if not text:
        return True
    if any(term in text for term in ("condo", "townhouse", "town home", "apartment", "strata")):
        return False
    return any(term in text for term in ("detached", "single family", "single family home", "house"))


def _same_context(subject: PropertyRecord, comp: ComparableRecord) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if _text(subject.municipality) != _text(comp.municipality):
        reasons.append("MUNICIPALITY_MISMATCH")
    if subject.neighbourhood:
        if not comp.neighbourhood:
            reasons.append("NEIGHBOURHOOD_UNKNOWN")
        elif _text(subject.neighbourhood) != _text(comp.neighbourhood):
            reasons.append("NEIGHBOURHOOD_MISMATCH")
    if subject.property_type and not comp.property_type:
        reasons.append("PROPERTY_TYPE_UNKNOWN")
    if not _is_detached(comp.property_type):
        reasons.append("PROPERTY_TYPE_MISMATCH")
    if comp.ownership_type in {
        OwnershipType.LEASEHOLD,
        OwnershipType.NON_PREPAID_LEASE,
        OwnershipType.PARTIAL_INTEREST,
        OwnershipType.UNDIVIDED_INTEREST,
        OwnershipType.FRACTIONAL_OWNERSHIP,
    }:
        reasons.append("TITLE_NOT_COMPARABLE")
    if subject.zoning:
        if not comp.zoning:
            reasons.append("ZONING_UNKNOWN")
        elif _text(subject.zoning) != _text(comp.zoning):
            reasons.append("ZONING_MISMATCH")
    if subject.frontage_ft is not None:
        if comp.frontage_ft is None:
            reasons.append("FRONTAGE_UNKNOWN")
        elif abs(subject.frontage_ft - comp.frontage_ft) > max(5.0, subject.frontage_ft * 0.20):
            reasons.append("FRONTAGE_MISMATCH")
    if subject.depth_ft is not None:
        if comp.depth_ft is None:
            reasons.append("DEPTH_UNKNOWN")
        elif abs(subject.depth_ft - comp.depth_ft) > max(10.0, subject.depth_ft * 0.20):
            reasons.append("DEPTH_MISMATCH")
    subject_shape = _text(getattr(subject, "lot_shape", None))
    if subject_shape and comp.lot_shape:
        if subject_shape != _text(comp.lot_shape):
            reasons.append("LOT_SHAPE_MISMATCH")
    subject_condition = _text(getattr(subject, "condition", None))
    if subject_condition:
        if not comp.condition:
            reasons.append("CONDITION_UNKNOWN")
        elif subject_condition != _text(comp.condition):
            reasons.append("CONDITION_MISMATCH")
    subject_year = getattr(subject, "year_built", None)
    if subject_year is not None and comp.year_built is not None and abs(subject_year - comp.year_built) > 30:
        reasons.append("YEAR_BUILT_MISMATCH")
    if subject.road_exposure_level is not None:
        if comp.road_exposure_level is None:
            reasons.append("ROAD_EXPOSURE_UNKNOWN")
        elif abs(subject.road_exposure_level - comp.road_exposure_level) > 1:
            reasons.append("ROAD_EXPOSURE_MISMATCH")
    return not reasons, reasons


def _lot_preferred(subject: PropertyRecord, comp: ComparableRecord, tolerance: float) -> bool:
    if subject.lot_area_sqft is None or comp.lot_area_sqft is None:
        return True
    lower = float(subject.lot_area_sqft) * (1 - tolerance)
    upper = float(subject.lot_area_sqft) * (1 + tolerance)
    return lower <= float(comp.lot_area_sqft) <= upper


def _median(values: Iterable[float | int | None]) -> float | None:
    materialized = [float(value) for value in values if value is not None]
    return median(materialized) if materialized else None


def _first_non_none(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def _source_numeric_value(value: Any) -> float | None:
    normalized = _number(value)
    return float(normalized) if isinstance(normalized, (int, float)) else None


def _transaction_key(comp: ComparableRecord) -> tuple[str, str | None]:
    identity = (
        _text(comp.transaction_id)
        or _text(comp.address)
        or _text(comp.property_id)
        or _text(comp.comparable_id)
    )
    sold_day = _aware(comp.sold_date).date().isoformat() if comp.sold_date else None
    return identity, sold_day


def _sold_observation_values(comp: ComparableRecord) -> set[float]:
    values: set[float] = set()
    if comp.sold_price is not None:
        values.add(float(comp.sold_price))
    for source in comp.source_records:
        if source.field_name != "sold_history":
            continue
        value = _source_numeric_value(source.normalized_value)
        if value is None:
            value = _source_numeric_value(source.raw_value)
        if value is not None:
            values.add(value)
    return values


def _mark_transaction_conflicts(comparables: Iterable[ComparableRecord]) -> list[ComparableRecord]:
    """Mark disagreeing observations before duplicate rows are merged.

    A source row can contain two sold-history observations itself, and two
    source rows can describe the same address/transaction/date.  Both cases
    must be excluded before any median is calculated.
    """

    materialized = list(comparables)
    grouped: dict[tuple[str, str | None], list[ComparableRecord]] = {}
    for comp in materialized:
        grouped.setdefault(_transaction_key(comp), []).append(comp)
    conflict_keys = {
        key
        for key, rows in grouped.items()
        if len({value for row in rows for value in _sold_observation_values(row)}) > 1
    }
    marked: list[ComparableRecord] = []
    for comp in materialized:
        if _transaction_key(comp) in conflict_keys:
            marked.append(comp.model_copy(update={
                "conflict": True,
                "exclusion_reasons": list(dict.fromkeys([
                    *comp.exclusion_reasons,
                    "SOLD_PRICE_CONFLICT",
                ])),
            }))
        else:
            marked.append(comp)
    return marked


def _proxy_value_config(comparable_config: Mapping[str, Any]) -> dict[str, Any]:
    value = comparable_config.get("building_value_proxy") or {}
    return dict(value) if isinstance(value, Mapping) else {}


def _proxy_rate(proxy_config: Mapping[str, Any], record: Any) -> float | None:
    rates = proxy_config.get("value_per_sqft_by_condition") or {}
    condition = _text(
        getattr(record, "condition", None)
        or getattr(record, "renovation_tier", None)
    )
    if isinstance(rates, Mapping) and condition:
        for key, value in rates.items():
            if _text(key) == condition:
                numeric = _source_numeric_value(value)
                if numeric is not None:
                    return numeric
    return _source_numeric_value(proxy_config.get("value_per_sqft"))


def _current_use_building_value(
    record: Any,
    comparable_config: Mapping[str, Any],
    *,
    subject: bool,
) -> tuple[int | None, str | None]:
    explicit = getattr(record, "current_use_building_value", None)
    if explicit is not None:
        return int(round(float(explicit))), "explicit"
    if subject:
        estimated = getattr(record, "estimated_current_use_building_value", None)
        if estimated is not None:
            return int(round(float(estimated))), "explicit_estimate"

    proxy_config = _proxy_value_config(comparable_config)
    method = _text(proxy_config.get("method"))
    if method in {"assessment improvement", "assessment building", "assessment_improvement"}:
        assessment_improvement = getattr(record, "assessment_improvement", None)
        if assessment_improvement is not None:
            label = "assessment_improvement" if subject else "comparable_assessment_improvement"
            return int(round(float(assessment_improvement))), label
    if method in {
        "finished sqft per sqft",
        "finished_sqft_per_sqft",
        "finished area per sqft",
    }:
        area = getattr(record, "finished_sqft", None) if subject else getattr(record, "house_size_sqft", None)
        rate = _proxy_rate(proxy_config, record)
        if area is not None and rate is not None:
            label = "finished_sqft_per_sqft" if subject else "comparable_finished_sqft_per_sqft"
            return int(round(float(area) * rate)), label
    return None, None


def _similarity_score(
    subject: PropertyRecord,
    comp: ComparableRecord,
    tolerance: float,
) -> float:
    """Return a transparent 0–100 closeness indicator for audit output."""

    score = 100.0
    if subject.lot_area_sqft and comp.lot_area_sqft:
        score -= min(25.0, abs(subject.lot_area_sqft - comp.lot_area_sqft) / subject.lot_area_sqft * 25.0 / max(tolerance, 0.01))
    if subject.frontage_ft is not None and comp.frontage_ft is not None:
        score -= min(15.0, abs(subject.frontage_ft - comp.frontage_ft) / max(subject.frontage_ft, 1.0) * 15.0 / 0.20)
    if subject.depth_ft is not None and comp.depth_ft is not None:
        score -= min(15.0, abs(subject.depth_ft - comp.depth_ft) / max(subject.depth_ft, 1.0) * 15.0 / 0.20)
    if subject.year_built is not None and comp.year_built is not None:
        score -= min(10.0, abs(subject.year_built - comp.year_built) / 30.0 * 10.0)
    if subject.road_exposure_level is not None and comp.road_exposure_level is not None:
        score -= min(15.0, abs(subject.road_exposure_level - comp.road_exposure_level) * 7.5)
    if subject_condition := _text(getattr(subject, "condition", None)):
        if subject_condition != _text(comp.condition):
            score -= 10.0
    return round(max(0.0, min(100.0, score)), 2)


def _assessment_ratio(comp: ComparableRecord) -> float | None:
    if comp.assessment_ratio is not None:
        return comp.assessment_ratio
    if comp.assessment_total and comp.assessment_land is not None:
        return comp.assessment_land / comp.assessment_total if comp.assessment_total else None
    return None


def _cross_checked(
    comp: ComparableRecord,
    *,
    source_registry: Mapping[str, SourceRegistryEntry],
    trusted_source_registry_ids: set[str],
    trusted_source_type: SourceType,
) -> bool:
    source_ids = {
        item.source_registry_id
        for item in comp.source_records
        if item.field_name == "sold_history"
        and item.source_registry_id in trusted_source_registry_ids
        and item.evidence_status is EvidenceStatus.CONFIRMED
        and item.source_type is trusted_source_type
        and registry_allows_evidence(
            source_registry.get(item.source_registry_id) if item.source_registry_id else None,
            expected_source_type=trusted_source_type,
        )
    }
    if len(source_ids) < 2:
        return False
    values = {
        item.normalized_value
        for item in comp.source_records
        if item.field_name == "sold_history"
        and item.source_registry_id in trusted_source_registry_ids
        and item.evidence_status is EvidenceStatus.CONFIRMED
        and item.source_type is trusted_source_type
        and registry_allows_evidence(
            source_registry.get(item.source_registry_id) if item.source_registry_id else None,
            expected_source_type=trusted_source_type,
        )
        and item.normalized_value is not None
    }
    return len(values) <= 1


def _merge_cross_source_observations(comparables: Iterable[ComparableRecord]) -> list[ComparableRecord]:
    """Combine identical transactions observed by independent source feeds."""

    merged: dict[tuple[Any, ...], ComparableRecord] = {}
    for comparable in comparables:
        address = _text(getattr(comparable, "address", None))
        transaction_id = _text(getattr(comparable, "transaction_id", None))
        identity = (
            transaction_id
            or address
            or _text(comparable.property_id)
            or _text(comparable.comparable_id)
        )
        sold_date = _aware(comparable.sold_date).isoformat() if comparable.sold_date else None
        key = (identity, sold_date, comparable.sold_price)
        existing = merged.get(key)
        if existing is None:
            merged[key] = comparable
            continue
        source_records = list(existing.source_records)
        seen = {
            (item.source_registry_id, item.source_url)
            for item in source_records
            if item.field_name == "sold_history"
        }
        for source in comparable.source_records:
            marker = (source.source_registry_id, source.source_url)
            if source.field_name == "sold_history" and marker not in seen:
                source_records.append(source)
                seen.add(marker)
        merged[key] = existing.model_copy(update={
            "source_records": source_records,
            "sold_history_source_registry_ids": list(dict.fromkeys(
                [*existing.sold_history_source_registry_ids, *comparable.sold_history_source_registry_ids]
            )),
        })
    return list(merged.values())


def evaluate_comparables(
    subject: PropertyRecord,
    comparables: Iterable[ComparableRecord | dict[str, Any]],
    config: dict[str, Any],
    *,
    observed_at: datetime,
    source_registry: Mapping[str, SourceRegistryEntry] | None = None,
) -> ComparableResult:
    observed_at = _aware(observed_at)
    comparable_config = config.get("comparables", {})
    registry = dict(source_registry or load_source_registry_entries(config))
    source_registry_id = comparable_config.get("source_registry_id")
    trusted_source_ids = {
        str(item)
        for item in comparable_config.get("cross_check_source_registry_ids", [])
        if str(item).strip()
    }
    if source_registry_id:
        trusted_source_ids.add(str(source_registry_id))
    trusted_source_type = comparable_config.get("source_type", SourceType.MLS)
    if not isinstance(trusted_source_type, SourceType):
        try:
            trusted_source_type = SourceType(str(trusted_source_type))
        except ValueError:
            trusted_source_type = SourceType.MLS
    normalized = [
        _as_comparable(
            item,
            subject.property_id,
            retrieved_at=observed_at,
            source_registry_id=str(source_registry_id) if source_registry_id else None,
            trusted_source_registry_ids=trusted_source_ids,
            trusted_source_type=trusted_source_type,
            source_registry=registry,
        )
        for item in comparables
    ]
    materialized = _merge_cross_source_observations(
        _mark_transaction_conflicts(normalized)
    )
    tolerance = float(comparable_config.get("lot_tolerance_pct", 0.20))
    minimum = int(comparable_config.get("min_reliable_comps", 5))
    primary_window = int(comparable_config.get("primary_window_days", 180))
    fallback_window = int(comparable_config.get("fallback_window_days", 365))
    allow_single_source_provisional = bool(
        comparable_config.get("allow_single_source_provisional", False)
    )
    if fallback_window < primary_window:
        fallback_window = primary_window
    conflict_keys = {
        _transaction_key(comp)
        for comp in materialized
        if comp.conflict or "SOLD_PRICE_CONFLICT" in comp.exclusion_reasons
    }
    conflict_ids = sorted({
        comp.comparable_id
        for comp in materialized
        if _transaction_key(comp) in conflict_keys
    })
    excluded: list[ComparableRecord] = []
    context_valid: list[ComparableRecord] = []
    for comp in materialized:
        reasons = list(comp.exclusion_reasons)
        if comp.sold_price is None or comp.sold_date is None:
            reasons.append("NOT_SOLD")
        if comp.conflict:
            reasons.append("CONFLICT")
        if not comp.reliable:
            reasons.append("UNRELIABLE")
        ok, context_reasons = _same_context(subject, comp)
        if not ok:
            reasons.extend(context_reasons)
        if reasons:
            excluded.append(comp.model_copy(update={"exclusion_reasons": list(dict.fromkeys(reasons))}))
        else:
            context_valid.append(comp)

    def within(days: int) -> list[ComparableRecord]:
        cutoff = observed_at - timedelta(days=days)
        return [
            comp for comp in context_valid
            if comp.sold_date is not None and cutoff <= _aware(comp.sold_date) <= observed_at
        ]

    window = primary_window
    selected = within(window)
    preferred = [comp for comp in selected if _lot_preferred(subject, comp, tolerance)]
    selected_uses_preferred = False
    if len(preferred) >= minimum:
        selected = preferred
        selected_uses_preferred = True
    elif len(selected) < minimum:
        window = fallback_window
        selected = within(fallback_window)
        preferred = [comp for comp in selected if _lot_preferred(subject, comp, tolerance)]
        if len(preferred) >= minimum:
            selected = preferred
            selected_uses_preferred = True

    benchmark_config = comparable_config.get("benchmark") or {}
    if not isinstance(benchmark_config, Mapping):
        benchmark_config = {}
    benchmark = within(window)
    if not bool(benchmark_config.get("include_lot_outliers", True)):
        benchmark = [
            comp for comp in benchmark
            if _lot_preferred(subject, comp, tolerance)
        ]
    selected_tier = "LIKE_FOR_LIKE" if selected_uses_preferred else "BROADER_CONTEXT"
    selected = [
        comp.model_copy(update={
            "similarity_score": _similarity_score(subject, comp, tolerance),
            "selection_tier": selected_tier,
        })
        for comp in selected
    ]
    benchmark = [
        comp.model_copy(update={
            "similarity_score": _similarity_score(subject, comp, tolerance),
            "selection_tier": "NEIGHBOURHOOD_BENCHMARK",
        })
        for comp in benchmark
    ]
    selected_ids = {item.comparable_id for item in selected}
    for comp in context_valid:
        if comp.comparable_id not in selected_ids:
            reason = (
                f"OUTSIDE_{primary_window}_DAY_WINDOW"
                if window == primary_window
                else f"OUTSIDE_{fallback_window}_DAY_WINDOW"
            )
            if not _lot_preferred(subject, comp, tolerance):
                reason = "LOT_SIZE_OUTSIDE_PREFERENCE"
            excluded.append(comp.model_copy(update={"exclusion_reasons": [*comp.exclusion_reasons, reason]}))

    sold_prices = [item.sold_price for item in selected]
    median_sold = _median(sold_prices)
    median_lot = _median(item.lot_area_sqft for item in selected)
    median_house = _median(item.house_size_sqft for item in selected)
    price_per_lot = _median(
        item.sold_price / item.lot_area_sqft
        for item in selected
        if item.sold_price is not None and item.lot_area_sqft
    )
    assessment_ratio = _median(_assessment_ratio(item) for item in selected)
    cross_checked = len({
        item.source_registry_id
        for comp in selected
        for item in comp.source_records
        if item.field_name == "sold_history"
        and item.source_registry_id
        and item.source_registry_id in trusted_source_ids
        and item.evidence_status is EvidenceStatus.CONFIRMED
        and item.source_type is trusted_source_type
        and registry_allows_evidence(
            registry.get(item.source_registry_id),
            expected_source_type=trusted_source_type,
        )
    }) >= 2 and all(
        _cross_checked(
            comp,
            source_registry=registry,
            trusted_source_registry_ids=trusted_source_ids,
            trusted_source_type=trusted_source_type,
        )
        for comp in selected
    )
    benchmark_cross_checked = len(benchmark) >= minimum and len({
        item.source_registry_id
        for comp in benchmark
        for item in comp.source_records
        if item.field_name == "sold_history"
        and item.source_registry_id
        and item.source_registry_id in trusted_source_ids
        and item.evidence_status is EvidenceStatus.CONFIRMED
        and item.source_type is trusted_source_type
        and registry_allows_evidence(
            registry.get(item.source_registry_id),
            expected_source_type=trusted_source_type,
        )
    }) >= 2 and all(
        _cross_checked(
            comp,
            source_registry=registry,
            trusted_source_registry_ids=trusted_source_ids,
            trusted_source_type=trusted_source_type,
        )
        for comp in benchmark
    )
    if len(selected) >= minimum and cross_checked:
        confidence = "HIGH_COMP_CONFIDENCE"
    elif len(selected) >= minimum and allow_single_source_provisional:
        confidence = "PROVISIONAL_SINGLE_SOURCE"
    else:
        confidence = "LOW_COMP_CONFIDENCE"
    require_benchmark_cross_check = bool(
        benchmark_config.get("require_cross_checked_for_relative_affordability", True)
    )
    if len(benchmark) >= minimum and (
        benchmark_cross_checked or not require_benchmark_cross_check
    ):
        benchmark_confidence = "HIGH_COMP_CONFIDENCE"
    elif len(benchmark) >= minimum and allow_single_source_provisional:
        benchmark_confidence = "PROVISIONAL_SINGLE_SOURCE"
    else:
        benchmark_confidence = "LOW_COMP_CONFIDENCE"
    expected = (
        int(round(median_sold))
        if median_sold is not None
        and confidence in {"HIGH_COMP_CONFIDENCE", "PROVISIONAL_SINGLE_SOURCE"}
        else None
    )
    neighbourhood_median = _median(item.sold_price for item in benchmark)
    neighbourhood_median_int = (
        int(round(neighbourhood_median))
        if neighbourhood_median is not None
        else None
    )
    relative = (
        subject.asking / neighbourhood_median_int
        if subject.asking is not None
        and neighbourhood_median_int
        and benchmark_confidence in {"HIGH_COMP_CONFIDENCE", "PROVISIONAL_SINGLE_SOURCE"}
        else None
    )
    dislocation = subject.asking / expected - 1 if subject.asking is not None and expected else None
    safety_margin = -dislocation if dislocation is not None else None
    provisional_valuation = (
        confidence == "PROVISIONAL_SINGLE_SOURCE"
        or benchmark_confidence == "PROVISIONAL_SINGLE_SOURCE"
    )
    valuation_status = (
        "PROVISIONAL"
        if provisional_valuation
        else "VERIFIED"
        if confidence == "HIGH_COMP_CONFIDENCE"
        and benchmark_confidence == "HIGH_COMP_CONFIDENCE"
        else "UNKNOWN"
    )
    manual_verification_reasons = (
        ["SOLD_COMPARABLE_CROSS_CHECK_DEFERRED"]
        if provisional_valuation
        else []
    )
    building_value, building_value_method = _current_use_building_value(
        subject,
        comparable_config,
        subject=True,
    )
    transaction_value = _first_non_none(
        subject.transaction_price,
        getattr(subject, "current_transaction_price", None),
        getattr(subject, "last_sale_price", None),
    )
    # An asking price is not a transaction value.  Keep this proxy unknown
    # until the subject has an explicit, non-conflicted sale/transaction
    # observation.
    land_proxy = (
        transaction_value - building_value
        if transaction_value is not None
        and building_value is not None
        and not subject.transaction_history_conflict
        else None
    )
    adjusted_land = land_proxy / subject.lot_area_sqft if land_proxy is not None and subject.lot_area_sqft else None
    comp_land_prices = []
    for item in selected:
        comp_building_value, _ = _current_use_building_value(
            item,
            comparable_config,
            subject=False,
        )
        if item.sold_price is not None and comp_building_value is not None and item.lot_area_sqft:
            comp_land_prices.append(
                (item.sold_price - comp_building_value) / item.lot_area_sqft
            )
    median_comp_land = _median(comp_land_prices)
    land_delta = adjusted_land / median_comp_land - 1 if adjusted_land is not None and median_comp_land else None
    lot_percentile = calculate_lot_size_percentile(
        subject.lot_area_sqft,
        [item.lot_area_sqft for item in benchmark if item.lot_area_sqft is not None],
    )
    updates = {
        "median_sold_price": int(round(median_sold)) if median_sold is not None else None,
        "median_lot_size": median_lot,
        "median_house_size": median_house,
        "median_price_per_lot_sqft": price_per_lot,
        "median_assessment_ratio": assessment_ratio,
        "neighbourhood_comparable_median": neighbourhood_median_int,
        "neighbourhood_benchmark_count": len(benchmark),
        "neighbourhood_benchmark_window_days": window,
        "neighbourhood_benchmark_confidence": benchmark_confidence,
        "comp_window_days": window,
        "comp_count": len(selected),
        "comp_confidence": confidence,
        "comparable_valuation_status": valuation_status,
        "expected_market_value": expected,
        "relative_affordability_index": relative,
        "price_dislocation_pct": dislocation,
        "price_safety_margin_pct": safety_margin,
        "land_value_proxy": int(round(land_proxy)) if land_proxy is not None else None,
        "current_use_building_value": building_value,
        "current_use_building_value_method": building_value_method,
        "adjusted_land_price_per_sqft": adjusted_land,
        "land_discount_or_premium_pct": land_delta,
        "comparable_conflict_count": len(conflict_keys),
        "comparable_conflict_ids": conflict_ids,
        "lot_size_percentile_in_neighbourhood": lot_percentile,
    }
    manual_required = bool(subject.manual_verification_required or provisional_valuation)
    manual_reasons = list(dict.fromkeys([
        *(subject.manual_verification_reasons or []),
        *manual_verification_reasons,
    ]))
    updates["manual_verification_required"] = manual_required
    updates["manual_verification_reasons"] = manual_reasons
    updated_subject = subject.model_copy(update=updates)
    return ComparableResult(
        subject_property_id=subject.property_id,
        property=updated_subject,
        selected_comparables=selected,
        neighbourhood_benchmark_comparables=benchmark,
        excluded_comparables=excluded,
        comp_window_days=window,
        comp_count=len(selected),
        comp_confidence=confidence,
        valuation_status=valuation_status,
        manual_verification_required=manual_required,
        manual_verification_reasons=manual_reasons,
        median_sold_price=updates["median_sold_price"],
        median_lot_size=median_lot,
        median_house_size=median_house,
        median_price_per_lot_sqft=price_per_lot,
        median_assessment_ratio=assessment_ratio,
        neighbourhood_comparable_median=neighbourhood_median_int,
        neighbourhood_benchmark_count=len(benchmark),
        neighbourhood_benchmark_window_days=window,
        neighbourhood_benchmark_confidence=benchmark_confidence,
        expected_market_value=expected,
        relative_affordability_index=relative,
        price_dislocation_pct=dislocation,
        price_safety_margin_pct=safety_margin,
        land_value_proxy=updates["land_value_proxy"],
        adjusted_land_price_per_sqft=adjusted_land,
        land_discount_or_premium_pct=land_delta,
        current_use_building_value_method=building_value_method,
        comparable_conflict_count=len(conflict_keys),
        comparable_conflict_ids=conflict_ids,
    )
