from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Protocol

from pydantic import BaseModel, ConfigDict, Field

from .models import ApprovedUse, PropertyRecord, SourceType
from .normalization import (
    calculate_lot_geometry_score,
    calculate_road_exposure,
    detect_material_conflicts,
    detect_zoning_threshold_advantage,
    derive_assessment_metrics,
    derive_carrying_cost_metrics,
    derive_listing_metrics,
    classify_approved_use,
    classify_ownership,
)
from .provenance import DataConflict, SourceRecord, SourceRegistryEntry, evidence_status_for_source, registry_allows_evidence


class EnrichmentResult(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    property_id: str
    provider_name: str
    status: str
    field_updates: dict[str, Any] = Field(default_factory=dict)
    source_records: list[SourceRecord] = Field(default_factory=list)
    conflicts: list[DataConflict] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class EnrichedPropertyResult(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    property: PropertyRecord
    provider_results: list[EnrichmentResult] = Field(default_factory=list)
    provenance: list[SourceRecord] = Field(default_factory=list)
    conflicts: list[DataConflict] = Field(default_factory=list)
    resolved_conflicts: list[DataConflict] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class EnrichmentProvider(Protocol):
    provider_name: str

    def enrich(self, record: PropertyRecord, observed_at: datetime) -> EnrichmentResult:
        ...


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo and value.utcoffset() is not None else value.replace(tzinfo=timezone.utc)


def _source_type(value: SourceType | str | None) -> SourceType:
    if isinstance(value, SourceType):
        return value
    try:
        return SourceType(str(value or SourceType.MANUAL.value))
    except ValueError:
        return SourceType.MANUAL


def _rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [dict(item) for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("records", "items", "data", "rows"):
            if isinstance(payload.get(key), list):
                return [dict(item) for item in payload[key] if isinstance(item, dict)]
        # A mapping keyed by property id is a convenient local fixture format.
        if any(isinstance(value, dict) for value in payload.values()):
            result: list[dict[str, Any]] = []
            for key, value in payload.items():
                if isinstance(value, dict):
                    item = dict(value)
                    item.setdefault("property_id", key)
                    result.append(item)
            return result
        return [dict(payload)]
    raise ValueError("enrichment payload must be a mapping or list")


def _normalized_address(address: Any) -> str:
    return " ".join(str(address or "").lower().replace(",", " ").split())


def _matches(row: dict[str, Any], record: PropertyRecord) -> bool:
    row_id = row.get("property_id")
    if row_id and str(row_id) == record.property_id:
        return True
    row_pid = row.get("pid") or row.get("PID")
    if row_pid and record.pid and str(row_pid).strip() == str(record.pid).strip():
        return True
    row_address = row.get("address") or row.get("civic_address")
    return bool(row_address and _normalized_address(row_address) == _normalized_address(record.address))


_META_KEYS = {
    "property_id",
    "pid",
    "PID",
    "address",
    "civic_address",
    "source_url",
    "source_type",
    "source_registry_id",
    "source_version",
    "observed_at",
    "retrieved_at",
    "fields",
    "status",
    "match_method",
    "query_scope",
    "raw_snapshot_path",
    "warnings",
}


_ALIASES = {
    "total": "assessment_total",
    "land": "assessment_land",
    "improvement": "assessment_improvement",
    "building": "assessment_improvement",
    "previous_total": "previous_year_total",
    "previous_land": "previous_year_land",
    "road_distance": "distance_to_major_road_m",
    "road_classification": "road_class",
    "fronts_major_road": "property_fronts_major_road",
    "outdoor_noise": "outdoor_noise_exposure",
    "indoor_mitigation": "indoor_noise_mitigation_potential",
    "elementary": "official_elementary_catchment",
    "secondary": "official_secondary_catchment",
    "current_score": "current_school_score",
    "future_score": "school_future_score",
    "max_fsr": "maximum_fsr",
    "max_units": "maximum_units",
    "min_frontage": "minimum_frontage",
    "min_site_area": "minimum_site_area",
    "distance_to_SkyTrain": "distance_to_skytrain_m",
    "distance_to_skytrain": "distance_to_skytrain_m",
    "distance_to_skytrain_m": "distance_to_skytrain_m",
    "assembly": "assembly_requirement",
    "drainage_levy": "drainage_charge",
    "drainage_fee": "drainage_charge",
    "municipal_drainage": "drainage_charge",
    "lot_size_sqft": "lot_area_sqft",
    "lane": "lane_access",
    "corner": "corner_lot",
    "current_use": "approved_use",
}


# These are derived evidence flags, never source-supplied facts.  Allowing a
# provider to write them would let listing copy manufacture legal entitlement.
_DERIVED_EVIDENCE_FIELDS = {
    "official_zoning_confirmed",
    "official_approved_use_confirmed",
    "official_zoning_source_url",
    "official_zoning_retrieved_at",
    "official_approved_use_source_url",
    "official_approved_use_retrieved_at",
    "official_pid_confirmed",
    "official_ownership_confirmed",
    "official_title_source_url",
    "official_title_retrieved_at",
}

_OFFICIAL_TITLE_FIELDS = {
    "pid",
    "PID",
    "pids",
    "legal_description",
    "secondary_civic_addresses",
    "number_of_pids",
}

_OFFICIAL_OWNERSHIP_FIELDS = {
    "ownership_type",
    "tenure",
}

_OFFICIAL_LEGAL_FIELDS = {
    "zoning",
    "approved_use",
    "bylaw_version",
    "permitted_uses",
    "conditional_uses",
    "minimum_site_area",
    "minimum_frontage",
    "maximum_fsr",
    "maximum_units",
    "maximum_height",
    "setbacks",
    "lot_coverage",
    "parking",
    "tree_constraints",
    "character_rules",
}

_VERIFIED_PLANNING_FIELDS = {
    "building_permits",
    "occupancy_permits",
    "secondary_suite_permits",
    "two_family_dwelling_status",
    "outstanding_permits",
    "bylaw_violations",
    "toa",
    "toa_status",
    "toa_tier",
    "theoretical_height",
    "theoretical_fsr",
    "current_zoning",
    "rezoning_requirement",
    "rental_requirement",
    "below_market_requirement",
    "distance_to_skytrain_m",
    "assembly_requirement",
    "assembly_score",
    "tree_constraints",
    "servicing_constraints",
    "neighbouring_lot_context",
    "recent_assembly_evidence",
    "rezoning_application_evidence",
    "heritage_register",
    "heritage_status",
    "character_status",
    "retention_incentives",
    "demolition_rules",
    "infill_options",
}

_VERIFIED_LOCATION_FIELDS = {
    "nearest_major_road",
    "distance_to_major_road_m",
    "property_fronts_major_road",
    "road_class",
    "road_exposure_level",
    "road_buffer_type",
    "road_buffer_stability",
    "buffer_at_risk",
    "indoor_noise_mitigation_potential",
    "outdoor_noise_exposure",
    "noise_risk",
    "official_elementary_catchment",
    "official_secondary_catchment",
    "elementary_distance",
    "secondary_distance",
    "kindergarten_capacity",
    "overflow",
    "school_plan",
    "enrolment",
    "literacy",
    "numeracy",
    "special_programs",
    "capital_projects",
    "seismic_upgrade",
    "nearby_private_schools",
    "current_school_score",
    "school_future_score",
    "family_location_score",
}


def _canonical_field(name: str) -> str:
    return _ALIASES.get(name, name)


def _canonical_cost_field(name: str, value: Any) -> str:
    """Disambiguate numeric municipal drainage from house drainage condition."""

    field = _canonical_field(name)
    if field == "drainage" and isinstance(value, (int, float)):
        return "drainage_charge"
    if field == "drainage" and isinstance(value, str):
        try:
            float(value.replace(",", "").replace("$", "").strip())
        except ValueError:
            return field
        return "drainage_charge"
    return field


def _provider_allowed_field(field: str, source_type: SourceType, provider_name: str) -> bool:
    if field in _DERIVED_EVIDENCE_FIELDS:
        return False
    if field in {
        "pure_property_tax",
        "water",
        "sewer",
        "garbage",
        "drainage_charge",
        "other_levies",
        "tax",
        "utilities",
        "annual_municipal_carrying_cost",
    }:
        return source_type in {SourceType.OFFICIAL_GOV, SourceType.MANUAL}
    if field in _OFFICIAL_LEGAL_FIELDS:
        return source_type is SourceType.OFFICIAL_GOV
    if field in _OFFICIAL_OWNERSHIP_FIELDS:
        return source_type is SourceType.OFFICIAL_GOV
    if field in _OFFICIAL_TITLE_FIELDS:
        return source_type in {SourceType.OFFICIAL_GOV, SourceType.BC_ASSESSMENT}
    if field in _VERIFIED_PLANNING_FIELDS:
        return source_type in {SourceType.OFFICIAL_GOV, SourceType.MANUAL}
    if field in _VERIFIED_LOCATION_FIELDS:
        return source_type in {SourceType.OFFICIAL_GOV, SourceType.MANUAL}
    if field.startswith("assessment_") or field in {
        "assessment_year",
        "assessment_history",
        "previous_year_total",
        "previous_year_land",
        "previous_year_improvement",
    }:
        return source_type is SourceType.BC_ASSESSMENT
    # Provider names are descriptive routing hints only; field-level official gates above are authoritative.
    del provider_name
    return True


def _coerce_value(field: str, value: Any, source_type: SourceType) -> Any:
    if field == "ownership_type":
        return classify_ownership(value)
    if field == "approved_use":
        return classify_approved_use(value) if source_type is SourceType.OFFICIAL_GOV else ApprovedUse.UNKNOWN
    if field in {"assessment_total", "assessment_land", "assessment_improvement", "previous_year_total", "previous_year_land"}:
        if isinstance(value, str):
            text = value.replace(",", "").replace("$", "").strip()
            try:
                value = float(text)
            except ValueError:
                return value
        if isinstance(value, float) and value.is_integer():
            return int(value)
    return value


class JsonMappingProvider:
    """Map an explicitly supplied local JSON enrichment file to one property."""

    def __init__(
        self,
        *,
        name: str,
        path: str | Path | None = None,
        source_type: SourceType | str = SourceType.MANUAL,
        data: Any | None = None,
        enabled: bool = True,
        source_url: str | None = None,
        source_registry_id: str | None = None,
        config: dict[str, Any] | None = None,
    ) -> None:
        self.provider_name = name
        self.path = Path(path) if path is not None else None
        self.source_type = _source_type(source_type)
        self.data = data
        self.enabled = enabled
        self.source_url = source_url or (f"local://{self.path}" if self.path else f"local://{name}")
        self.source_registry_id = source_registry_id or f"source:{name}"
        self.config = dict(config or {})

    def _load(self) -> Any:
        if self.data is not None:
            return self.data
        if self.path is None:
            raise FileNotFoundError("provider has neither data nor path")
        with self.path.open("r", encoding="utf-8") as handle:
            if self.path.suffix.lower() in {".jsonl", ".ndjson"}:
                return [json.loads(line) for line in handle if line.strip()]
            return json.load(handle)

    def enrich(self, record: PropertyRecord, observed_at: datetime) -> EnrichmentResult:
        observed_at = _aware(observed_at)
        if not self.enabled or self.config.get("enabled") is False:
            return EnrichmentResult(
                property_id=record.property_id,
                provider_name=self.provider_name,
                status="SKIPPED_DISABLED",
            )
        try:
            rows = _rows(self._load())
        except Exception as exc:
            return EnrichmentResult(
                property_id=record.property_id,
                provider_name=self.provider_name,
                status="FAILED",
                warnings=[f"{type(exc).__name__}: {exc}"],
            )
        row = next((item for item in rows if _matches(item, record)), None)
        if row is None:
            return EnrichmentResult(
                property_id=record.property_id,
                provider_name=self.provider_name,
                status="SUCCESS",
                warnings=["no matching enrichment row"],
            )
        row_status = str(row.get("status") or "SUCCESS").upper()
        if row_status != "SUCCESS":
            return EnrichmentResult(
                property_id=record.property_id,
                provider_name=self.provider_name,
                status=row_status,
                warnings=[str(item) for item in row.get("warnings", []) if str(item).strip()],
            )
        # ``fields`` is already the provider's field namespace.  Metadata
        # filtering applies only to the outer row; applying it here silently
        # discarded official PID/legal-description fields because ``pid`` and
        # ``address`` are also valid nested fields.
        nested_metadata = _META_KEYS - {"pid", "PID", "address", "civic_address"}
        values = {
            key: value
            for key, value in dict(row.get("fields") or {}).items()
            if key not in nested_metadata
        }
        values.update({key: value for key, value in row.items() if key not in _META_KEYS})
        # Provider configuration is the trust boundary; row metadata is not
        # allowed to upgrade a listing/copy feed into official evidence.
        source_type = self.source_type
        source_url = str(row.get("source_url") or self.source_url)
        retrieved_at = row.get("retrieved_at") or row.get("observed_at") or observed_at.isoformat()
        try:
            retrieved = datetime.fromisoformat(str(retrieved_at))
        except ValueError:
            retrieved = observed_at
        retrieved = _aware(retrieved)
        updates: dict[str, Any] = {}
        records: list[SourceRecord] = []
        warnings: list[str] = []
        for raw_field, raw_value in values.items():
            field = _canonical_cost_field(str(raw_field), raw_value)
            if field not in PropertyRecord.model_fields:
                warnings.append(f"ignored unsupported field: {field}")
                continue
            if raw_value in (None, "", [], {}):
                continue
            if not _provider_allowed_field(field, source_type, self.provider_name):
                warnings.append(f"ignored {field}: source type {source_type.value} is not authoritative")
                continue
            value = _coerce_value(field, raw_value, source_type)
            updates[field] = value
            records.append(
                SourceRecord(
                    property_id=record.property_id,
                    field_name=field,
                    source_url=source_url,
                    source_type=source_type,
                    source_registry_id=self.source_registry_id,
                    retrieved_at=retrieved,
                    raw_value=raw_value,
                    normalized_value=value,
                    source_version=row.get("source_version"),
                    match_method=row.get("match_method"),
                    query_scope=row.get("query_scope"),
                    raw_snapshot_path=row.get("raw_snapshot_path"),
                )
            )
        return EnrichmentResult(
            property_id=record.property_id,
            provider_name=self.provider_name,
            status="SUCCESS",
            field_updates=updates,
            source_records=records,
            warnings=warnings,
        )


def _existing_source(record: PropertyRecord, field: str) -> SourceRecord | None:
    # Listing/history fields are expected to move between observations; a later
    # authoritative history row is a delta, not a title/fact conflict.
    if field in {
        "asking",
        "original_asking",
        "listing_status",
        "transaction_date",
        "dom",
        "cumulative_dom",
        "relist_count",
        "previous_listing_prices",
        "previous_listing_sources",
        "terminated_history",
    }:
        return None
    value = getattr(record, field, None)
    if value in (None, "", [], {}):
        return None
    if getattr(value, "value", None) == "UNKNOWN":
        return None
    return SourceRecord(
        property_id=record.property_id,
        field_name=field,
        source_url="current://property-projection",
        source_type=SourceType.MANUAL,
        retrieved_at=_aware(record.last_updated or datetime.now(timezone.utc)),
        raw_value=value,
        normalized_value=value,
    )


def _rederive(record: PropertyRecord, config: dict[str, Any] | None) -> PropertyRecord:
    cfg = config or {}
    updated = derive_assessment_metrics(record)
    updated = derive_listing_metrics(updated)
    updated = derive_carrying_cost_metrics(updated)
    threshold = float(cfg.get("zoning", {}).get("threshold_tolerance_m", 0.05))
    threshold_tags = detect_zoning_threshold_advantage(
        updated.frontage_ft * 0.3048 if updated.frontage_ft is not None else None,
        updated.minimum_frontage,
        tolerance_m=threshold,
    )
    return updated.model_copy(update={
        "lot_geometry_score": calculate_lot_geometry_score(updated, cfg),
        "road_exposure_level": calculate_road_exposure(
            updated.road_class,
            updated.distance_to_major_road_m,
            updated.property_fronts_major_road,
        ),
        "zoning_threshold_advantages": list(dict.fromkeys([*updated.zoning_threshold_advantages, *threshold_tags])),
    })


def _numeric_count(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def _reconcile_compatible_conflicts(
    record: PropertyRecord,
    conflicts: Iterable[DataConflict],
    provenance: Iterable[SourceRecord],
    config: dict[str, Any] | None,
) -> tuple[list[DataConflict], list[DataConflict], dict[str, Any], list[str]]:
    """Resolve bounded, non-material bedroom count differences.

    Listing descriptions and BC Assessment can count finished attics or other
    rooms differently.  When the disagreement is only one bedroom and both
    values satisfy the family's minimum, retain both source observations but
    use the confirmed BCA value for the normalized record.  Identity, title,
    geometry, zoning, and larger disagreements remain unresolved conflicts.
    """

    minimum_bedrooms = float(
        (config or {}).get("research_policy", {})
        .get("current_house_minimums", {})
        .get("bedrooms", 3)
    )
    source_records = list(provenance)
    unresolved: list[DataConflict] = []
    resolved: list[DataConflict] = []
    updates: dict[str, Any] = {}
    notes: list[str] = []
    for conflict in conflicts:
        if conflict.field_name != "bedrooms":
            unresolved.append(conflict)
            continue
        official_values = {
            number
            for source in source_records
            if source.field_name == "bedrooms"
            and source.source_type is SourceType.BC_ASSESSMENT
            for number in [_numeric_count(source.normalized_value)]
            if number is not None
        }
        all_values = {
            number
            for value in conflict.source_values.values()
            for number in [_numeric_count(value)]
            if number is not None
        }
        if (
            len(official_values) != 1
            or len(all_values) < 2
            or min(all_values) < minimum_bedrooms
            or max(all_values) - min(all_values) > 1
        ):
            unresolved.append(conflict)
            continue
        selected = next(iter(official_values))
        updates["bedrooms"] = int(selected) if selected.is_integer() else selected
        sources = ", ".join(sorted(conflict.source_values))
        note = (
            f"BEDROOM_COUNT_RECONCILED: source values={sorted(all_values)}; "
            f"selected BC_ASSESSMENT={selected:g}; sources={sources}"
        )
        notes.append(note)
        resolved.append(conflict.model_copy(update={
            "resolution_status": "RESOLVED",
            "resolution_note": (
                "Confirmed BC Assessment value selected; one-bedroom source "
                "difference is non-blocking because all values meet the configured minimum."
            ),
        }))
    return unresolved, resolved, updates, notes


def enrich_property(
    record: PropertyRecord,
    *,
    providers: Iterable[EnrichmentProvider],
    observed_at: datetime | None = None,
    config: dict[str, Any] | None = None,
    source_registry: Mapping[str, SourceRegistryEntry] | None = None,
) -> EnrichedPropertyResult:
    observed_at = _aware(observed_at or datetime.now(timezone.utc))
    providers = list(providers)
    effective_registry = dict(source_registry or {})
    provider_results: list[EnrichmentResult] = []
    provenance: list[SourceRecord] = []
    warnings: list[str] = []
    for provider in providers:
        try:
            result = provider.enrich(record, observed_at)
        except Exception as exc:
            result = EnrichmentResult(
                property_id=record.property_id,
                provider_name=getattr(provider, "provider_name", provider.__class__.__name__),
                status="FAILED",
                warnings=[f"{type(exc).__name__}: {exc}"],
            )
        provider_results.append(result)
        for source in result.source_records:
            registry = effective_registry.get(source.source_registry_id) if source.source_registry_id else None
            if source.source_type is SourceType.OFFICIAL_GOV and not registry_allows_evidence(
                registry,
                expected_source_type=SourceType.OFFICIAL_GOV,
            ):
                warnings.append(
                    f"ignored unregistered or unconfirmed official source: {source.source_registry_id or source.source_url}"
                )
                continue
            if source.source_type is SourceType.BC_ASSESSMENT and not registry_allows_evidence(
                registry,
                expected_source_type=SourceType.BC_ASSESSMENT,
            ):
                warnings.append(
                    f"ignored unregistered or unconfirmed assessment source: {source.source_registry_id or source.source_url}"
                )
                continue
            linked = source.model_copy(update={
                "evidence_status": evidence_status_for_source(
                    effective_registry,
                    source.source_registry_id,
                    source.source_type,
                )
            })
            provenance.append(linked)
        warnings.extend(result.warnings)

    values_by_field: dict[str, dict[str, Any]] = {}
    for source in provenance:
        values_by_field.setdefault(source.field_name, {})[source.source_url] = source.normalized_value
    for field in {source.field_name for source in provenance}:
        existing = _existing_source(record, field)
        if existing:
            values_by_field.setdefault(field, {})[existing.source_url] = existing.normalized_value
    conflicts = detect_material_conflicts(record.property_id, values_by_field)
    conflicts, resolved_conflicts, reconciliation_updates, reconciliation_notes = _reconcile_compatible_conflicts(
        record,
        conflicts,
        provenance,
        config,
    )
    conflict_fields = {item.field_name for item in conflicts}
    updates: dict[str, Any] = dict(reconciliation_updates)
    if reconciliation_notes:
        updates["data_reconciliation_notes"] = list(dict.fromkeys([
            *record.data_reconciliation_notes,
            *reconciliation_notes,
        ]))
    for result in provider_results:
        for field, value in result.field_updates.items():
            if field in conflict_fields or value in (None, "", [], {}):
                continue
            # Do not trust a provider implementation that bypasses field-level source gates.
            source = next((item for item in result.source_records if item.field_name == field), None)
            if field in _OFFICIAL_LEGAL_FIELDS and (source is None or source.source_type is not SourceType.OFFICIAL_GOV):
                continue
            if field in _OFFICIAL_LEGAL_FIELDS and not registry_allows_evidence(
                effective_registry.get(source.source_registry_id) if source and source.source_registry_id else None,
                expected_source_type=SourceType.OFFICIAL_GOV,
            ):
                continue
            if field in _OFFICIAL_OWNERSHIP_FIELDS and (source is None or source.source_type is not SourceType.OFFICIAL_GOV):
                continue
            if field in _OFFICIAL_OWNERSHIP_FIELDS and not registry_allows_evidence(
                effective_registry.get(source.source_registry_id) if source and source.source_registry_id else None,
                expected_source_type=SourceType.OFFICIAL_GOV,
            ):
                continue
            if field in _OFFICIAL_TITLE_FIELDS and (source is None or source.source_type not in {SourceType.OFFICIAL_GOV, SourceType.BC_ASSESSMENT}):
                continue
            if field in _OFFICIAL_TITLE_FIELDS and not registry_allows_evidence(
                effective_registry.get(source.source_registry_id) if source and source.source_registry_id else None,
                expected_source_type=source.source_type,
            ):
                continue
            if field in _VERIFIED_PLANNING_FIELDS and (source is None or source.source_type not in {SourceType.OFFICIAL_GOV, SourceType.MANUAL}):
                continue
            if field in _VERIFIED_LOCATION_FIELDS and (source is None or source.source_type not in {SourceType.OFFICIAL_GOV, SourceType.MANUAL}):
                continue
            if field.startswith("assessment_") and (source is None or source.source_type is not SourceType.BC_ASSESSMENT):
                continue
            if field.startswith("assessment_") and not registry_allows_evidence(
                effective_registry.get(source.source_registry_id) if source and source.source_registry_id else None,
                expected_source_type=SourceType.BC_ASSESSMENT,
            ):
                continue
            if field in _DERIVED_EVIDENCE_FIELDS:
                continue
            updates[field] = value
    zoning_source = next(
        (
            item
            for item in provenance
            if item.field_name == "zoning" and item.source_type is SourceType.OFFICIAL_GOV
        ),
        None,
    )
    if updates.get("zoning") is not None and zoning_source is not None:
        updates["official_zoning_confirmed"] = True
        updates["official_zoning_source_url"] = zoning_source.source_url
        updates["official_zoning_source_registry_id"] = zoning_source.source_registry_id
        updates["official_zoning_evidence_status"] = zoning_source.evidence_status
        updates["official_zoning_retrieved_at"] = zoning_source.retrieved_at
        updates["current_zoning"] = updates.get("zoning")
    approved_source = next(
        (
            item
            for item in provenance
            if item.field_name == "approved_use" and item.source_type is SourceType.OFFICIAL_GOV
        ),
        None,
    )
    if updates.get("approved_use") not in (None, ApprovedUse.UNKNOWN) and approved_source is not None:
        updates["official_approved_use_confirmed"] = True
        updates["official_approved_use_source_url"] = approved_source.source_url
        updates["official_approved_use_source_registry_id"] = approved_source.source_registry_id
        updates["official_approved_use_evidence_status"] = approved_source.evidence_status
        updates["official_approved_use_retrieved_at"] = approved_source.retrieved_at
    pid_source = next(
        (
            item
            for item in provenance
            if item.field_name in {"pid", "PID", "pids"}
            and item.source_type in {SourceType.OFFICIAL_GOV, SourceType.BC_ASSESSMENT}
        ),
        None,
    )
    ownership_source = next(
        (
            item
            for item in provenance
            if item.field_name in {"ownership", "ownership_type", "tenure"}
            and item.source_type is SourceType.OFFICIAL_GOV
        ),
        None,
    )
    confirmed_pid = updates.get("pid", record.pid)
    confirmed_pids = updates.get("pids", record.pids)
    if pid_source is not None and (confirmed_pid is not None or confirmed_pids):
        updates["official_pid_confirmed"] = True
        updates["official_title_source_url"] = pid_source.source_url
        updates["official_title_source_registry_id"] = pid_source.source_registry_id
        updates["official_title_evidence_status"] = pid_source.evidence_status
        updates["official_title_retrieved_at"] = pid_source.retrieved_at
    if ownership_source is not None and updates.get("ownership_type", record.ownership_type) is not None:
        ownership = updates.get("ownership_type", record.ownership_type)
        if ownership is not None and getattr(ownership, "value", ownership) != "UNKNOWN":
            updates["official_ownership_confirmed"] = True
            updates["official_title_source_url"] = ownership_source.source_url
            updates["official_title_source_registry_id"] = ownership_source.source_registry_id
            updates["official_title_evidence_status"] = ownership_source.evidence_status
            updates["official_title_retrieved_at"] = ownership_source.retrieved_at
    listing_history_fields = {
        "listing_date",
        "listing_status",
        "transaction_date",
        "dom",
        "cumulative_dom",
        "relist_count",
        "previous_listing_prices",
        "terminated_history",
    }
    listing_history_sources = [
        source.source_url
        for source in provenance
        if source.field_name in listing_history_fields
    ]
    if listing_history_sources:
        updates["previous_listing_sources"] = list(dict.fromkeys([
            *record.previous_listing_sources,
            *listing_history_sources,
        ]))
    sold_history_fields = {
        "previous_sale_dates",
        "previous_sale_prices",
        "transaction_price",
        "transaction_date",
        "sold_history",
        # A successful empty query still has auditable evidence through its
        # checked time window and source URL.
        "sold_history_scope",
    }
    sold_history_sources = [
        source.source_url
        for source in provenance
        if source.field_name in sold_history_fields
    ]
    if sold_history_sources:
        updates["sold_history_sources"] = list(dict.fromkeys([
            *record.sold_history_sources,
            *sold_history_sources,
        ]))
    if conflicts:
        updates["data_conflict"] = True
        updates["major_data_conflict"] = True
    enriched = _rederive(record.model_copy(update=updates), config)
    return EnrichedPropertyResult(
        property=enriched,
        provider_results=provider_results,
        provenance=provenance,
        conflicts=conflicts,
        resolved_conflicts=resolved_conflicts,
        warnings=warnings,
    )
