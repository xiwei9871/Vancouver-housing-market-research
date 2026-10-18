from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any, Iterable, Mapping

from pydantic import BaseModel, ConfigDict, Field

from .models import CandidateStatus, OwnershipType, PropertyRecord, SourceRole, SourceType
from .normalization import classify_ownership, normalize_address, normalize_property, resolve_property_identity
from .provenance import DataConflict, SourceRecord, SourceRegistryEntry, evidence_status_for_source
from .sources import SourceAdapter, SourceRunResult, SourceRunStatus


class AcquisitionResult(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    records: list[PropertyRecord] = Field(default_factory=list)
    source_runs: list[SourceRunResult] = Field(default_factory=list)
    provenance: list[SourceRecord] = Field(default_factory=list)
    parse_errors: list[str] = Field(default_factory=list)
    source_count: int = 0
    raw_count: int = 0
    normalized_count: int = 0
    duplicate_count: int = 0
    conflicts: list[DataConflict] = Field(default_factory=list)
    primary_source_status: str = "NOT_CONFIGURED"
    primary_source_names: list[str] = Field(default_factory=list)
    fallback_used: bool = False
    source_priority: list[str] = Field(default_factory=list)
    provisional_property_ids: list[str] = Field(default_factory=list)


class Stage1Candidate(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    property_id: str
    address: str
    market_bucket: str | None = None
    included: bool = False
    gate_state: str = "UNKNOWN"
    reason_codes: list[str] = Field(default_factory=list)
    trigger_tags: list[str] = Field(default_factory=list)
    range_classification: str = "UNKNOWN"
    research_scope: str = "UNKNOWN"
    primary_candidate: bool = False
    candidate_status: CandidateStatus = CandidateStatus.NEED_VERIFICATION
    record: PropertyRecord


class Stage1ScanResult(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    observed_at: datetime
    records: list[PropertyRecord] = Field(default_factory=list)
    candidates: list[Stage1Candidate] = Field(default_factory=list)
    normalized_count: int = 0
    duplicate_count: int = 0
    included_count: int = 0
    research_candidate_count: int = 0
    verification_queue_count: int = 0
    special_situation_count: int = 0
    excluded_count: int = 0
    conflicts: list[DataConflict] = Field(default_factory=list)


_MATERIAL_CONFLICT_FIELDS = {
    "address",
    "municipality",
    "property_type",
    "pid",
    "pids",
    "legal_description",
    "secondary_civic_addresses",
    "number_of_pids",
    "strata_lot_identifiers",
    "ownership_type",
    "approved_use",
    "title_status",
    "lot_area_sqft",
    "frontage_ft",
    "depth_ft",
    "year_built",
    "zoning",
    "maximum_fsr",
    "maximum_units",
    "mls_number",
    "asking",
    "original_asking",
    "listing_date",
    "dom",
    "cumulative_dom",
    "finished_sqft",
    "bedrooms",
    "bathrooms",
    "listing_status",
}


def _material_value(value: Any) -> bool:
    if value in (None, "", [], {}):
        return False
    return getattr(value, "value", None) != "UNKNOWN"


def _field_conflicts(
    left: PropertyRecord,
    right: PropertyRecord,
    *,
    observed_at: datetime,
    ignored_fields: set[str] | None = None,
) -> list[DataConflict]:
    conflicts: list[DataConflict] = []
    left_source = left.source_url or "current://left-observation"
    right_source = right.source_url or "current://right-observation"
    for field_name in _MATERIAL_CONFLICT_FIELDS:
        if field_name in (ignored_fields or set()):
            continue
        old_value = getattr(left, field_name, None)
        new_value = getattr(right, field_name, None)
        if not (_material_value(old_value) and _material_value(new_value)) or old_value == new_value:
            continue
        conflicts.append(
            DataConflict(
                conflict_id=f"conflict:{left.property_id}:{field_name}",
                property_id=left.property_id,
                field_name=field_name,
                conflict_type=f"{field_name.upper()}_MISMATCH",
                source_values={left_source: old_value, right_source: new_value},
                conflicting_sources=list(dict.fromkeys([left_source, right_source])),
                detected_at=_aware(observed_at),
            )
        )
    return conflicts


def _civic_address_variants_equivalent(left: str, right: str) -> bool:
    """Recognize a range address versus one civic number on the same street."""

    def parse(value: str) -> tuple[list[int], str] | None:
        text = " ".join(str(value).strip().lower().replace("/", "-").split())
        match = re.match(r"^([0-9]+(?:-[0-9]+)?)\s+(.+)$", text)
        if not match:
            return None
        numbers = [int(item) for item in match.group(1).split("-")]
        return numbers, match.group(2)

    left_parsed = parse(left)
    right_parsed = parse(right)
    if left_parsed is None or right_parsed is None:
        return False
    left_numbers, left_street = left_parsed
    right_numbers, right_street = right_parsed
    if left_street != right_street:
        return False
    if len(left_numbers) == 2 and len(right_numbers) == 1:
        return left_numbers[0] <= right_numbers[0] <= left_numbers[1]
    if len(right_numbers) == 2 and len(left_numbers) == 1:
        return right_numbers[0] <= left_numbers[0] <= right_numbers[1]
    return False


def _ignored_merge_conflicts(left: PropertyRecord, right: PropertyRecord) -> set[str]:
    if (
        left.mls_number
        and right.mls_number
        and str(left.mls_number).strip().upper() == str(right.mls_number).strip().upper()
        and _civic_address_variants_equivalent(left.address, right.address)
    ):
        return {"address"}
    return set()


def _address_collision_conflicts(
    left: PropertyRecord,
    right: PropertyRecord,
    *,
    observed_at: datetime,
) -> list[DataConflict]:
    left_pids = set(left.pids or ([left.pid] if left.pid else []))
    right_pids = set(right.pids or ([right.pid] if right.pid else []))
    if not left_pids or not right_pids or left_pids.intersection(right_pids):
        return []
    left_source = left.source_url or "current://left-observation"
    right_source = right.source_url or "current://right-observation"
    source_values = {left_source: sorted(left_pids), right_source: sorted(right_pids)}
    sources = list(source_values)
    collision_key = ":".join(sorted((left.property_id, right.property_id)))
    conflicts: list[DataConflict] = []
    for record in (left, right):
        conflicts.append(
            DataConflict(
                conflict_id=f"conflict:{record.property_id}:pid_address_collision:{collision_key}",
                property_id=record.property_id,
                field_name="pid",
                conflict_type="PID_ADDRESS_COLLISION",
                source_values=source_values,
                conflicting_sources=sources,
                detected_at=_aware(observed_at),
            )
        )
    return conflicts


def _mls_observations_consistent(left: PropertyRecord, right: PropertyRecord) -> bool:
    """Return whether two same-MLS observations can refer to one property."""

    if not left.mls_number or not right.mls_number:
        return True
    if str(left.mls_number).strip().upper() != str(right.mls_number).strip().upper():
        return True
    same_address = normalize_address(left.address, left.municipality) == normalize_address(
        right.address,
        right.municipality,
    ) or _civic_address_variants_equivalent(left.address, right.address)
    if not same_address:
        return False
    left_pids = set(left.pids or ([left.pid] if left.pid else []))
    right_pids = set(right.pids or ([right.pid] if right.pid else []))
    return not left_pids or not right_pids or bool(left_pids.intersection(right_pids))


def _mls_identity_collision_conflicts(
    left: PropertyRecord,
    right: PropertyRecord,
    *,
    observed_at: datetime,
) -> list[DataConflict]:
    if not left.mls_number or not right.mls_number:
        return []
    if str(left.mls_number).strip().upper() != str(right.mls_number).strip().upper():
        return []
    if _mls_observations_consistent(left, right):
        return []
    left_source = left.source_url or "current://left-observation"
    right_source = right.source_url or "current://right-observation"
    source_values = {
        left_source: str(left.mls_number).strip().upper(),
        right_source: str(right.mls_number).strip().upper(),
    }
    collision_key = ":".join(sorted((left.property_id, right.property_id)))
    sources = list(source_values)
    return [
        DataConflict(
            conflict_id=f"conflict:{record.property_id}:mls_identity_collision:{collision_key}",
            property_id=record.property_id,
            field_name="mls_number",
            conflict_type="MLS_IDENTITY_COLLISION",
            source_values=source_values,
            conflicting_sources=sources,
            detected_at=_aware(observed_at),
        )
        for record in (left, right)
    ]


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo and value.utcoffset() is not None else value.replace(tzinfo=timezone.utc)


def _source_type(value: Any) -> SourceType:
    try:
        return value if isinstance(value, SourceType) else SourceType(str(value))
    except ValueError:
        return SourceType.MANUAL


def _source_role(value: Any) -> str:
    if isinstance(value, SourceRole):
        return value.value
    if value in (None, ""):
        return SourceRole.UNSPECIFIED.value
    text = str(value)
    if text.startswith("SourceRole."):
        text = text.split(".", 1)[1]
    return text.upper()


def configured_source_priority(config: Mapping[str, Any] | None = None) -> list[str]:
    """Return the configured source-role order used by acquisition."""

    acquisition_config = (config or {}).get("acquisition", {})
    configured = acquisition_config.get("priority_order")
    if configured:
        return [_source_role(item) for item in configured]
    return [
        SourceRole.MLS_PRIMARY.value,
        SourceRole.REALTOR_CA_PUBLIC.value,
        SourceRole.ZOLO_PUBLIC.value,
    ]


def _adapter_priority_key(
    adapter: SourceAdapter,
    *,
    config: Mapping[str, Any] | None,
    position: int,
) -> tuple[int, int, int]:
    order = configured_source_priority(config)
    role = _source_role(getattr(adapter, "source_role", None))
    role_rank = order.index(role) if role in order else len(order)
    explicit_priority = getattr(adapter, "priority", None)
    numeric_priority = int(explicit_priority) if explicit_priority is not None else position
    return role_rank, numeric_priority, position


def order_source_adapters(
    adapters: Iterable[SourceAdapter],
    config: Mapping[str, Any] | None = None,
) -> list[SourceAdapter]:
    """Order adapters by source role while retaining stable configured order."""

    materialized = list(adapters)
    return [
        adapter
        for _, adapter in sorted(
            (
                (_adapter_priority_key(adapter, config=config, position=position), adapter)
                for position, adapter in enumerate(materialized)
            ),
            key=lambda item: item[0],
        )
    ]


_LISTING_FIELD_ALIASES = {
    "pid": "pid",
    "pids": "pids",
    "legal_description": "legal_description",
    "secondary_addresses": "secondary_civic_addresses",
    "secondary_civic_addresses": "secondary_civic_addresses",
    "number_of_pids": "number_of_pids",
    "ownership": "ownership_type",
    "ownership_type": "ownership_type",
    "tenure": "ownership_type",
    "approved_use": "approved_use",
    "current_approved_use": "approved_use",
    "list_price": "asking",
    "current_asking": "asking",
    "asking": "asking",
    "original_list_price": "original_asking",
    "original_asking": "original_asking",
    "days_on_market": "dom",
    "dom": "dom",
    "cumulative_dom": "cumulative_dom",
    "sale_price": "transaction_price",
    "sold_price": "transaction_price",
    "transaction_price": "transaction_price",
    "sold_date": "transaction_date",
    "transaction_date": "transaction_date",
    "lot_size_sqft": "lot_area_sqft",
    "lot_area_sqft": "lot_area_sqft",
    "lot_area_m2": "lot_area_m2",
    "house_size_sqft": "finished_sqft",
    "finished_sqft": "finished_sqft",
    "assessment_building": "assessment_improvement",
    "max_fsr": "maximum_fsr",
    "max_units": "maximum_units",
}

# These are calculated by later research stages.  A listing row may contain
# them as cached values, but they are not source facts for this acquisition.
_DERIVED_LISTING_FIELDS = {
    "property_id",
    "last_updated",
    "research_asset_class",
    "research_asset_classification_basis",
    "research_asset_classification_notes",
    "primary_detached_eligible",
    "research_scope",
    "research_scope_reasons",
    "market_bucket",
    "range_classification",
    "single_title_multi_address",
    "independently_tradable",
    "land_ratio",
    "asking_vs_total_assessment",
    "asking_vs_land_assessment",
    "assessment_labels",
    "assessment_valuation_status",
    "current_house_use_status",
    "current_house_use_reasons",
    "lot_area_score",
    "frontage_score",
    "depth_score",
    "lot_size_percentile_in_neighbourhood",
    "lot_geometry_score",
    "current_house_utility_score",
    "buildability_score",
    "theoretical_buildable_sqft",
    "realistic_buildable_sqft",
    "max_duplex_area",
    "estimated_front_unit_area",
    "estimated_rear_unit_area",
    "land_to_product_score",
    "candidate_status",
    "opportunity_tags",
    "data_confidence_score",
    "data_confidence_band",
    "live_score",
    "land_score",
    "investment_score",
    "total_score",
    "total_score_label",
}

_MUNICIPAL_COST_FIELDS = {
    "annual_municipal_carrying_cost",
    "pure_property_tax",
    "tax",
    "utilities",
    "water",
    "sewer",
    "garbage",
    "drainage_charge",
    "other_levies",
}

_ASSESSMENT_FIELDS = {
    "assessment_year",
    "assessment_total",
    "assessment_land",
    "assessment_improvement",
    "assessment_building",
    "previous_year_total",
    "previous_year_land",
    "previous_year_improvement",
    "assessment_history",
}

_UNVERIFIED_PLANNING_FIELDS = {
    "building_permits",
    "occupancy_permits",
    "secondary_suite_permits",
    "two_family_dwelling_status",
    "outstanding_permits",
    "bylaw_violations",
    "toa",
    "TOA",
    "toa_status",
    "toa_tier",
    "theoretical_height",
    "theoretical_fsr",
    "current_zoning",
    "rezoning_requirement",
    "rental_requirement",
    "below_market_requirement",
    "distance_to_skytrain_m",
    "distance_to_SkyTrain",
    "assembly",
    "assembly_requirement",
    "assembly_score",
    "neighbouring_lot_context",
    "recent_assembly_evidence",
    "rezoning_application_evidence",
    "heritage_register",
    "heritage_status",
    "tree_constraints",
    "servicing_constraints",
    "character_status",
    "retention_incentives",
    "demolition_rules",
    "infill_options",
}

_UNVERIFIED_LOCATION_FIELDS = {
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


def _listing_field_name(raw_name: str) -> str | None:
    key = "_".join(str(raw_name).strip().replace("-", "_").split()).lower()
    field_name = _LISTING_FIELD_ALIASES.get(key, key)
    if field_name not in PropertyRecord.model_fields or field_name in _DERIVED_LISTING_FIELDS:
        return None
    return field_name


def _has_listing_value(value: Any) -> bool:
    return value is not None and value != "" and value != [] and value != {}


def _acquisition_payload(
    row: dict[str, Any],
    *,
    source_type: SourceType | None = None,
    source_registry_id: str | None = None,
) -> dict[str, Any]:
    payload = dict(row)
    trusted_source_type = source_type or _source_type(row.get("source_type"))
    source_role = _source_role(row.get("source_role"))
    if trusted_source_type is not SourceType.BC_ASSESSMENT:
        for field_name in _ASSESSMENT_FIELDS:
            payload.pop(field_name, None)
    if trusted_source_type not in {SourceType.OFFICIAL_GOV, SourceType.MANUAL}:
        for field_name in _MUNICIPAL_COST_FIELDS:
            payload.pop(field_name, None)
        for field_name in _UNVERIFIED_PLANNING_FIELDS:
            payload.pop(field_name, None)
        for field_name in _UNVERIFIED_LOCATION_FIELDS:
            payload.pop(field_name, None)
        # A numeric `drainage` value is the legacy municipal levy alias;
        # textual drainage remains a house/site condition field.
        if isinstance(payload.get("drainage"), (int, float)):
            payload.pop("drainage", None)
    if source_role in {
        SourceRole.MLS_PRIMARY.value,
        SourceRole.REALTOR_CA_PUBLIC.value,
        SourceRole.ZOLO_PUBLIC.value,
    }:
        # Listing feeds may expose legal-looking fields, but they are not
        # title/municipal evidence. Keep the original row in provenance and
        # remove these claims from the normalized legal projection until an
        # official source confirms them.
        ownership_claim = payload.get("ownership_type", payload.get("tenure"))
        if ownership_claim not in (None, ""):
            payload["unverified_ownership_claim"] = ownership_claim
        for field_name in {
            "pid",
            "PID",
            "pids",
            "legal_description",
            "ownership",
            "ownership_type",
            "tenure",
            "approved_use",
            "current_approved_use",
            "zoning",
            "current_zoning",
            "max_FSR",
            "maximum_fsr",
            "max_units",
            "maximum_units",
            "permitted_uses",
            "conditional_uses",
            "minimum_site_area",
            "minimum_frontage",
            "maximum_height",
            "setbacks",
            "lot_coverage",
            "parking",
            "character_rules",
        }:
            payload.pop(field_name, None)
    if source_type is not None:
        payload["source_type"] = trusted_source_type.value
    if source_registry_id:
        payload["source_registry_id"] = source_registry_id
    return payload


def _as_source_records(
    row: dict[str, Any],
    record: PropertyRecord,
    observed_at: datetime,
    *,
    source_type: SourceType | None = None,
    source_registry_id: str | None = None,
    source_role: str | None = None,
    source_registry: Mapping[str, SourceRegistryEntry] | None = None,
) -> list[SourceRecord]:
    source_url = str(row.get("source_url") or row.get("url") or "UNKNOWN")
    trusted_source_type = source_type or _source_type(row.get("source_type"))
    trusted_registry_id = source_registry_id or (
        str(row.get("source_registry_id")) if row.get("source_registry_id") else None
    )
    trusted_source_role = _source_role(source_role or row.get("source_role"))
    if trusted_source_role == SourceRole.UNSPECIFIED.value and trusted_registry_id:
        registry_entry = (source_registry or {}).get(trusted_registry_id)
        if registry_entry is not None:
            trusted_source_role = _source_role(registry_entry.source_role)
    retrieved_at = _aware(_timestamp_from_row(row.get("observed_at"), observed_at))
    source_version = str(row.get("source_version")) if row.get("source_version") else None
    normalized_values = record.model_dump(mode="json", by_alias=False)
    records = [
        SourceRecord(
            property_id=record.property_id,
            field_name="listing_snapshot",
            source_url=source_url,
            source_type=trusted_source_type,
            source_registry_id=trusted_registry_id,
            source_role=trusted_source_role,
            retrieved_at=retrieved_at,
            raw_value=dict(row),
            normalized_value=normalized_values,
            source_version=source_version,
            evidence_status=evidence_status_for_source(
                dict(source_registry or {}),
                trusted_registry_id,
                trusted_source_type,
            ),
        )
    ]
    for raw_name, raw_value in row.items():
        if raw_name in {"source_url", "url", "source_type", "source_registry_id", "observed_at", "source_version"}:
            continue
        if not _has_listing_value(raw_value):
            continue
        field_name = _listing_field_name(raw_name)
        if field_name is None:
            continue
        records.append(
            SourceRecord(
                property_id=record.property_id,
                field_name=field_name,
                source_url=source_url,
                source_type=trusted_source_type,
                source_registry_id=trusted_registry_id,
                source_role=trusted_source_role,
                retrieved_at=retrieved_at,
                raw_value=raw_value,
                normalized_value=normalized_values.get(field_name),
                source_version=source_version,
                evidence_status=evidence_status_for_source(
                    dict(source_registry or {}),
                    trusted_registry_id,
                    trusted_source_type,
                ),
            )
        )
    return records


def _provisional_source_roles(config: Mapping[str, Any]) -> set[str]:
    configured = config.get("acquisition", {}).get("provisional_source_roles")
    if configured:
        return {_source_role(item) for item in configured}
    return {
        SourceRole.MLS_PRIMARY.value,
        SourceRole.REALTOR_CA_PUBLIC.value,
        SourceRole.ZOLO_PUBLIC.value,
    }


def _acquisition_channel_for_role(role: str) -> tuple[str, str]:
    return {
        SourceRole.MLS_PRIMARY.value: ("DIRECT_MLS", "AUTHORIZED_DIRECT"),
        SourceRole.REALTOR_CA_PUBLIC.value: ("PUBLIC_REALTOR_CA", "PUBLIC_REPUBLISHED"),
        SourceRole.ZOLO_PUBLIC.value: ("PUBLIC_ZOLO", "PUBLIC_AGGREGATED"),
    }.get(role, ("UNKNOWN", "UNKNOWN"))


def _mark_single_source_records(
    records: Iterable[PropertyRecord],
    provenance: Iterable[SourceRecord],
    *,
    config: Mapping[str, Any],
    source_registry: Mapping[str, SourceRegistryEntry] | None,
) -> tuple[list[PropertyRecord], list[str]]:
    """Mark listing observations as provisional without changing evidence trust.

    The public/MLS row is useful for discovery immediately, but the listing
    itself does not prove title, zoning, approved use, or a verified valuation.
    This marker is deliberately separate from ``data_confidence_score`` so a
    later official/manual review can upgrade individual claims without
    rewriting the original observation.
    """

    acquisition_config = config.get("acquisition", {})
    if not bool(acquisition_config.get("allow_single_source_provisional", False)):
        return list(records), []

    role_by_registry: dict[str, str] = {}
    for registry_id, entry in (source_registry or {}).items():
        role_by_registry[str(registry_id)] = _source_role(entry.source_role)
    for definition in acquisition_config.get("sources", []) or []:
        if not isinstance(definition, Mapping):
            continue
        registry_id = str(
            definition.get("source_registry_id")
            or f"source:{definition.get('name', 'unnamed-source')}"
        )
        role_by_registry.setdefault(registry_id, _source_role(definition.get("source_role")))

    listing_provenance: dict[str, list[SourceRecord]] = {}
    for item in provenance:
        if item.field_name != "listing_snapshot":
            continue
        listing_provenance.setdefault(item.property_id, []).append(item)
    allowed_roles = _provisional_source_roles(config)
    marked: list[PropertyRecord] = []
    provisional_ids: list[str] = []
    for record in records:
        source_items = listing_provenance.get(record.property_id, [])
        source_ids = []
        source_roles: dict[str, str] = {}
        for item in source_items:
            if not item.source_registry_id:
                continue
            role = role_by_registry.get(str(item.source_registry_id))
            if role in (None, SourceRole.UNSPECIFIED.value):
                role = _source_role(item.source_role)
            if role == SourceRole.UNSPECIFIED.value:
                role = None
            # Direct callers may supply an adapter object without repeating
            # its registry definition in config.  In provisional mode that
            # still counts as a listing observation, but remains manual-only.
            if role is None or role in allowed_roles:
                source_id = str(item.source_registry_id)
                source_ids.append(source_id)
                source_roles[source_id] = role or SourceRole.UNSPECIFIED.value
        source_ids = list(dict.fromkeys(source_ids))
        if not source_ids:
            marked.append(record)
            continue
        if len(source_ids) == 1:
            verification_status = "PROVISIONAL_SINGLE_SOURCE"
            verification_reason = "LISTING_SOURCE_CROSS_CHECK_DEFERRED"
            channel, feed_class = _acquisition_channel_for_role(source_roles[source_ids[0]])
        else:
            verification_status = "MULTI_SOURCE_UNVERIFIED"
            verification_reason = "LISTING_SOURCE_CROSS_CHECK_PENDING"
            channel, feed_class = "MULTI_SOURCE", "MULTI_SOURCE_UNVERIFIED"
        reasons = list(dict.fromkeys([
            *(record.manual_verification_reasons or []),
            verification_reason,
        ]))
        marked.append(record.model_copy(update={
            "data_verification_status": verification_status,
            "manual_verification_required": True,
            "manual_verification_reasons": reasons,
            "provisional_source_registry_ids": source_ids,
            "acquisition_channel": channel,
            "feed_class": feed_class,
        }))
        provisional_ids.append(record.property_id)
    return marked, provisional_ids


def _timestamp_from_row(value: Any, fallback: datetime) -> datetime:
    if value in (None, ""):
        return fallback
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return fallback


def _number(value: Any) -> Any:
    if value in (None, ""):
        return value
    if isinstance(value, (int, float)):
        return value
    text = str(value).strip().replace(",", "").replace("$", "")
    try:
        number = float(text)
    except ValueError:
        return value
    return int(number) if number.is_integer() else number


def _coerce_payload(row: Any) -> Any:
    if isinstance(row, PropertyRecord):
        return row
    if not isinstance(row, dict):
        return row
    item = dict(row)
    for key in (
        "asking",
        "list_price",
        "transaction_price",
        "sale_price",
        "sold_price",
        "original_asking",
        "assessment_total",
        "assessment_land",
        "assessment_improvement",
        "assessment_building",
        "lot_area_sqft",
        "lot_size_sqft",
        "lot_area_m2",
        "frontage_ft",
        "depth_ft",
        "finished_sqft",
        "house_size_sqft",
        "year_built",
        "effective_year",
        "bedrooms",
        "bathrooms",
        "dom",
        "days_on_market",
        "previous_year_improvement",
        "maximum_units",
        "max_units",
    ):
        if key in item:
            item[key] = _number(item[key])
    return item


def _identity(record: PropertyRecord) -> str:
    if record.pid or record.pids:
        pids = record.pids or record.strata_lot_identifiers or [record.pid]
        return resolve_property_identity(record.address, record.municipality, pids)
    return resolve_property_identity(record.address, record.municipality)


def _listing_alias_key(record: PropertyRecord) -> str | None:
    if record.mls_number and str(record.mls_number).strip():
        return f"mls:{str(record.mls_number).strip().upper()}"
    return None


def _identity_alias_keys(record: PropertyRecord) -> list[str]:
    keys = [f"address:{normalize_address(record.address, record.municipality)}"]
    pids = record.pids or ([record.pid] if record.pid else [])
    keys.extend(f"pid:{str(pid).strip()}" for pid in pids if str(pid).strip())
    listing_key = _listing_alias_key(record)
    if listing_key:
        keys.append(listing_key)
    return keys


def _canonical_identity(
    record: PropertyRecord,
    aliases: Mapping[str, str],
    records_by_identity: Mapping[str, PropertyRecord],
    alias_pids: Mapping[str, set[str]] | None = None,
) -> str:
    """Keep an address-first property stable when a PID appears later.

    An address alias is used only for the pidless-to-PID transition.  If the
    existing address already has a different PID, the new identity remains
    distinct and the normal PID/address collision detector can flag it.
    """

    candidate = _identity(record)
    pids = set(record.pids or ([record.pid] if record.pid else []))
    for key in _identity_alias_keys(record):
        canonical = aliases.get(key)
        if not canonical:
            continue
        existing = records_by_identity.get(canonical)
        if key.startswith("mls:"):
            if existing is not None and _mls_observations_consistent(existing, record):
                return canonical
            continue
        if key.startswith("pid:"):
            existing_pids = (
                set(existing.pids or ([existing.pid] if existing.pid else []))
                if existing
                else set((alias_pids or {}).get(key, set()))
            )
            if existing_pids and pids and existing_pids.intersection(pids):
                return canonical
            continue
        existing_pids = (
            set(existing.pids or ([existing.pid] if existing.pid else []))
            if existing
            else set((alias_pids or {}).get(key, set()))
        )
        if not pids or not existing_pids or existing_pids.intersection(pids):
            return canonical
    return candidate


def _remember_identity_aliases(record: PropertyRecord, aliases: dict[str, str]) -> None:
    for key in _identity_alias_keys(record):
        aliases.setdefault(key, record.property_id)


def _record_value_present(value: Any) -> bool:
    if value in (None, "", [], {}):
        return False
    if getattr(value, "value", None) == "UNKNOWN":
        return False
    return str(value).strip().upper() != "UNKNOWN" if isinstance(value, str) else True


def _merge_records(
    left: PropertyRecord,
    right: PropertyRecord,
    *,
    field_priorities: dict[str, int] | None = None,
    incoming_priority: int = 0,
    ignored_conflict_fields: set[str] | None = None,
) -> PropertyRecord:
    """Merge sparse observations, preferring the higher-priority source."""

    update: dict[str, Any] = {}
    differing_fields: list[str] = []
    priorities = field_priorities if field_priorities is not None else {}
    for field_name in PropertyRecord.model_fields:
        if field_name in {"property_id", "last_updated"}:
            continue
        old_value = getattr(left, field_name)
        new_value = getattr(right, field_name)
        if (
            field_name in _MATERIAL_CONFLICT_FIELDS
            and field_name not in (ignored_conflict_fields or set())
            and _material_value(old_value)
            and _material_value(new_value)
            and old_value != new_value
        ):
            differing_fields.append(field_name)
        if _record_value_present(new_value):
            old_priority = priorities.get(field_name)
            if not _record_value_present(old_value) or old_priority is None or incoming_priority <= old_priority:
                update[field_name] = new_value
                priorities[field_name] = incoming_priority
        elif not _record_value_present(old_value):
            # Keep sparse/default values out of the projection, but allow the
            # Pydantic default to remain available on the resulting model.
            continue
    update["property_id"] = left.property_id
    if differing_fields:
        update["data_conflict"] = True
        update["major_data_conflict"] = True
    update["last_updated"] = right.last_updated or left.last_updated
    return left.model_copy(update=update)


def _adapter_priority_value(
    adapter: SourceAdapter,
    *,
    config: Mapping[str, Any] | None,
    position: int,
) -> int:
    role_rank, explicit_or_position, _ = _adapter_priority_key(
        adapter,
        config=config,
        position=position,
    )
    # Keep role rank dominant while allowing multiple adapters in one role to
    # retain their configured order.  This is only an internal merge weight;
    # the human-readable report exposes source roles instead.
    return role_rank * 100_000 + explicit_or_position


def _annotate_source_run(
    result: SourceRunResult,
    adapter: SourceAdapter,
    *,
    effective_priority: int | None = None,
) -> SourceRunResult:
    adapter_coverage = getattr(adapter, "coverage_complete", None)
    return result.model_copy(
        update={
            "source_role": _source_role(getattr(adapter, "source_role", None)),
            "priority": (
                effective_priority
                if effective_priority is not None
                else getattr(adapter, "priority", None)
            ),
            # Crawl-manifest adapters derive coverage from the artifact
            # metrics.  Static source definitions may override it when they
            # explicitly declare a value, but an omitted adapter value must
            # not erase the run-level result.
            "coverage_complete": (
                adapter_coverage
                if adapter_coverage is not None
                else result.coverage_complete
            ),
        }
    )


def _primary_source_status(
    source_runs: Iterable[SourceRunResult],
    primary_source_names: Iterable[str],
) -> str:
    names = set(primary_source_names)
    if not names:
        return "NOT_CONFIGURED"
    primary_runs = [run for run in source_runs if run.source_name in names]
    if any(run.status is SourceRunStatus.SUCCESS for run in primary_runs):
        return "AVAILABLE"
    if any(run.status is SourceRunStatus.PARTIAL and run.records for run in primary_runs):
        return "PARTIAL"
    return "UNAVAILABLE"


def acquire_from_sources(
    adapters: Iterable[SourceAdapter],
    *,
    observed_at: datetime,
    config: dict[str, Any] | None = None,
    identity_aliases: Mapping[str, str] | None = None,
    identity_alias_pids: Mapping[str, set[str]] | None = None,
    source_registry: Mapping[str, SourceRegistryEntry] | None = None,
) -> AcquisitionResult:
    """Collect explicitly configured sources and normalize their rows once."""

    ordered_adapters = order_source_adapters(adapters, config)
    records_by_identity: dict[str, PropertyRecord] = {}
    field_priorities_by_identity: dict[str, dict[str, int]] = {}
    provenance: list[SourceRecord] = []
    source_runs: list[SourceRunResult] = []
    parse_errors: list[str] = []
    conflicts: list[DataConflict] = []
    raw_count = 0
    duplicate_count = 0
    records_by_address: dict[str, list[PropertyRecord]] = {}
    records_by_mls: dict[str, list[PropertyRecord]] = {}
    aliases = dict(identity_aliases or {})
    for position, adapter in enumerate(ordered_adapters):
        adapter_priority = _adapter_priority_value(
            adapter,
            config=config,
            position=position,
        )
        started_at = datetime.now(timezone.utc)
        try:
            result = adapter.collect(_aware(observed_at))
        except Exception as exc:
            finished_at = datetime.now(timezone.utc)
            result = SourceRunResult(
                source_registry_id=str(getattr(adapter, "source_registry_id", f"source:{adapter.source_name}")),
                source_name=str(getattr(adapter, "source_name", adapter.__class__.__name__)),
                status=SourceRunStatus.FAILED,
                started_at=started_at,
                finished_at=finished_at,
                errors=[f"{type(exc).__name__}: {exc}"],
            )
        result = _annotate_source_run(
            result,
            adapter,
            effective_priority=(
                getattr(adapter, "priority", None)
                if getattr(adapter, "priority", None) is not None
                else _adapter_priority_key(
                    adapter,
                    config=config,
                    position=position,
                )[0]
            ),
        )
        source_runs.append(result)
        raw_count += len(result.records)
        for index, row in enumerate(result.records):
            try:
                adapter_source_type = _source_type(getattr(adapter, "source_type", None))
                adapter_registry_id = str(
                    getattr(adapter, "source_registry_id", None)
                    or result.source_registry_id
                )
                payload = _acquisition_payload(
                    _coerce_payload(row),
                    source_type=adapter_source_type,
                    source_registry_id=adapter_registry_id,
                )
                provisional = normalize_property(
                    payload,
                    config=config or {},
                    source_registry=source_registry,
                )
                identity_record = provisional.property.model_copy(
                    update={"property_id": _identity(provisional.property)}
                )
                canonical_id = _canonical_identity(
                    identity_record,
                    aliases,
                    records_by_identity,
                    identity_alias_pids,
                )
                canonical_identity_record = identity_record.model_copy(update={"property_id": canonical_id})
                row_provenance = _as_source_records(
                    row,
                    canonical_identity_record,
                    observed_at,
                    source_type=adapter_source_type,
                    source_registry_id=adapter_registry_id,
                    source_role=_source_role(getattr(adapter, "source_role", None)),
                    source_registry=source_registry,
                )
                # Re-normalize with the source envelope attached.  This lets
                # an explicitly official acquisition feed establish official
                # zoning/title evidence without allowing a listing payload to
                # self-attest legal facts.
                normalized = normalize_property(
                    payload,
                    row_provenance,
                    config=config or {},
                    source_registry=source_registry,
                )
                record = normalized.property.model_copy(update={"property_id": canonical_id})
                provenance.extend(_as_source_records(
                    row,
                    record,
                    observed_at,
                    source_type=adapter_source_type,
                    source_registry_id=adapter_registry_id,
                    source_role=_source_role(getattr(adapter, "source_role", None)),
                    source_registry=source_registry,
                ))
                conflicts.extend(normalized.conflicts)
                address_key = normalize_address(record.address, record.municipality)
                for existing in records_by_address.get(address_key, []):
                    conflicts.extend(_address_collision_conflicts(existing, record, observed_at=observed_at))
                records_by_address.setdefault(address_key, []).append(record)
                mls_key = _listing_alias_key(record)
                if mls_key:
                    for existing in records_by_mls.get(mls_key, []):
                        if existing.property_id != record.property_id:
                            conflicts.extend(
                                _mls_identity_collision_conflicts(
                                    existing,
                                    record,
                                    observed_at=observed_at,
                                )
                            )
                    records_by_mls.setdefault(mls_key, []).append(record)
                if record.property_id in records_by_identity:
                    duplicate_count += 1
                    existing = records_by_identity[record.property_id]
                    ignored_conflicts = _ignored_merge_conflicts(existing, record)
                    conflicts.extend(
                        _field_conflicts(
                            existing,
                            record,
                            observed_at=observed_at,
                            ignored_fields=ignored_conflicts,
                        )
                    )
                    priorities = field_priorities_by_identity.setdefault(
                        record.property_id,
                        {
                            field_name: adapter_priority
                            for field_name in PropertyRecord.model_fields
                            if _record_value_present(getattr(existing, field_name))
                        },
                    )
                    records_by_identity[record.property_id] = _merge_records(
                        existing,
                        record,
                        field_priorities=priorities,
                        incoming_priority=adapter_priority,
                        ignored_conflict_fields=ignored_conflicts,
                    )
                else:
                    records_by_identity[record.property_id] = record
                    field_priorities_by_identity[record.property_id] = {
                        field_name: adapter_priority
                        for field_name in PropertyRecord.model_fields
                        if _record_value_present(getattr(record, field_name))
                    }
                _remember_identity_aliases(records_by_identity[record.property_id], aliases)
            except Exception as exc:
                parse_errors.append(f"{adapter.source_name} row {index}: {type(exc).__name__}: {exc}")
    conflict_property_ids = {item.property_id for item in conflicts}
    records = [
        record.model_copy(update={"data_conflict": True, "major_data_conflict": True})
        if record.property_id in conflict_property_ids
        else record
        for record in records_by_identity.values()
    ]
    records, provisional_property_ids = _mark_single_source_records(
        records,
        provenance,
        config=config or {},
        source_registry=source_registry,
    )
    primary_roles = {
        _source_role(item)
        for item in (config or {}).get("acquisition", {}).get(
            "primary_source_roles",
            [SourceRole.MLS_PRIMARY.value],
        )
    }
    primary_source_names = [
        str(getattr(adapter, "source_name", ""))
        for adapter in ordered_adapters
        if _source_role(getattr(adapter, "source_role", None)) in primary_roles
    ]
    primary_status = _primary_source_status(source_runs, primary_source_names)
    fallback_attempt_statuses = {
        SourceRunStatus.SUCCESS,
        SourceRunStatus.PARTIAL,
        SourceRunStatus.FAILED,
        SourceRunStatus.BLOCKED_POLICY,
        SourceRunStatus.SKIPPED_UNAUTHORIZED,
    }
    fallback_used = primary_status != "AVAILABLE" and any(
        _source_role(run.source_role) not in primary_roles
        and run.status in fallback_attempt_statuses
        for run in source_runs
    )
    return AcquisitionResult(
        records=records,
        source_runs=source_runs,
        provenance=provenance,
        parse_errors=parse_errors,
        source_count=len(source_runs),
        raw_count=raw_count,
        normalized_count=len(records),
        duplicate_count=duplicate_count,
        conflicts=list({item.conflict_id: item for item in conflicts}.values()),
        primary_source_status=primary_status,
        primary_source_names=primary_source_names,
        fallback_used=fallback_used,
        source_priority=configured_source_priority(config),
        provisional_property_ids=provisional_property_ids,
    )


def _text(value: Any) -> str:
    return " ".join(str(value or "").replace("/", " ").replace("-", " ").lower().split())


def _matches(value: Any, expected: Any) -> bool:
    return _text(value) == _text(expected)


def _market_bucket(record: PropertyRecord, config: dict[str, Any]) -> str | None:
    buckets = config.get("market_buckets", {})
    for name, bucket in buckets.items():
        if bucket.get("municipality") and not _matches(record.municipality, bucket.get("municipality")):
            continue
        neighbourhoods = bucket.get("neighbourhoods") or []
        if not neighbourhoods or any(_matches(record.neighbourhood, item) for item in neighbourhoods):
            return str(name)
    return None


def _range_classification(asking: int | None, bucket: dict[str, Any] | None) -> str:
    if asking is None or bucket is None:
        return "UNKNOWN"
    core = bucket.get("core_range") or []
    extended = bucket.get("extended_range") or core
    if len(core) >= 2 and core[0] <= asking <= core[1]:
        return "CORE"
    if len(extended) >= 2 and extended[0] <= asking <= extended[1]:
        return "EXTENDED"
    return "OUT_OF_RANGE"


def _research_scope(
    record: PropertyRecord,
    *,
    bucket_name: str | None,
    config: dict[str, Any],
) -> tuple[str, list[str]]:
    """Route an observation to the final detached-house research tracks."""

    asset_class = str(record.research_asset_class or "UNKNOWN").upper()
    if asset_class == "DUPLEX":
        return "SUPPLEMENTARY_DUPLEX", ["SUPPLEMENTARY_DUPLEX_NOT_PRIMARY_HOUSE"]
    if asset_class != "DETACHED_HOUSE":
        if asset_class == "UNKNOWN":
            return "NEED_VERIFICATION", ["ASSET_CLASS_UNKNOWN"]
        if asset_class == "STRATA_DUPLEX":
            return "EXCLUDED_NON_TARGET", ["STRATA_DUPLEX_OUT_OF_PRIMARY_SCOPE"]
        return "EXCLUDED_NON_TARGET", ["ASSET_CLASS_OUT_OF_SCOPE"]

    if (
        bool(config.get("research_policy", {}).get("exclude_steep_lots", False))
        and "steep" in _text(record.slope)
    ):
        return "EXCLUDED_STEEP_LOT", ["STEEP_LOT_OUT_OF_CURRENT_SCOPE"]

    policy = config.get("research_policy", {})
    area = record.lot_area_sqft
    frontage = record.frontage_ft
    depth = record.depth_ft
    minimum_area = float(policy.get("standard_redevelopment_min_lot_area_sqft", 3000))
    minimum_frontage = float(policy.get("standard_redevelopment_min_frontage_ft", 33))
    minimum_depth = float(policy.get("standard_redevelopment_min_depth_ft", 100))
    exception_buckets = set(policy.get("house_rebuild_only_buckets", ["vancouver_west"]))
    if area is None or frontage is None or depth is None:
        return "NEED_VERIFICATION", ["LOT_GATE_REQUIRES_AREA_FRONTAGE_DEPTH"]
    below_standard = []
    if area < minimum_area:
        below_standard.append("LOT_AREA_BELOW_3000_SQFT")
    if frontage < minimum_frontage:
        below_standard.append("FRONTAGE_BELOW_33_FT")
    if depth < minimum_depth:
        below_standard.append("DEPTH_BELOW_100_FT")
    if not below_standard:
        return "STANDARD_REDEVELOPMENT", ["STANDARD_33X100_LOT_GATE"]
    if bucket_name in exception_buckets:
        return "HOUSE_REBUILD_ONLY", [*below_standard, "WESTSIDE_SINGLE_HOUSE_REBUILD_EXCEPTION"]
    return "CURRENT_USE_ONLY", below_standard


def _current_house_gate(
    record: PropertyRecord,
    config: dict[str, Any],
) -> tuple[str, list[str]]:
    """Gate detached primary candidates on the current-house minimums."""

    minimums = config.get("research_policy", {}).get("current_house_minimums", {})
    checks = {
        "bedrooms": (
            record.bedrooms,
            float(minimums.get("bedrooms", 3)),
            "BEDROOMS_BELOW_MINIMUM",
        ),
        "bathrooms": (
            record.bathrooms,
            float(minimums.get("bathrooms", 2.5)),
            "BATHROOMS_BELOW_MINIMUM",
        ),
        "finished_sqft": (
            record.finished_sqft,
            float(minimums.get("finished_sqft", 1500)),
            "FINISHED_SQFT_BELOW_MINIMUM",
        ),
    }
    unknown = [
        field_name
        for field_name, (value, _minimum, _reason) in checks.items()
        if value is None
    ]
    if unknown:
        return "NEED_VERIFICATION", [
            f"CURRENT_USE_{field_name.upper()}_UNKNOWN"
            for field_name in unknown
        ]
    failed = [
        reason
        for value, minimum, reason in checks.values()
        if float(value) < minimum
    ]
    if failed:
        return "OUT_OF_SCOPE", failed
    return "PASS", ["CURRENT_USE_MINIMUMS_MET"]


def _scope_tags(scope: str, record: PropertyRecord) -> list[str]:
    tags: list[str] = []
    if scope == "HOUSE_REBUILD_ONLY":
        tags.extend(["HOUSE_REBUILD_ONLY", "NO_STANDARD_DUPLEX_OPTION"])
    elif scope == "CURRENT_USE_ONLY":
        tags.append("CURRENT_USE_ONLY")
    elif scope == "STANDARD_REDEVELOPMENT":
        tags.append("STANDARD_REDEVELOPMENT_LOT")
    if record.lot_area_sqft is not None and record.lot_area_sqft < 3000:
        tags.append("SMALL_LOT")
    return tags


def _large_lot(record: PropertyRecord, config: dict[str, Any]) -> bool:
    if record.lot_size_percentile_in_neighbourhood is not None:
        return record.lot_size_percentile_in_neighbourhood >= 75
    area = record.lot_area_sqft
    if area is None:
        return False
    required = set(config.get("geometry", {}).get("municipality_percentiles_required", [])) | {
        "City of North Vancouver",
        "District of North Vancouver",
        "District of West Vancouver",
    }
    if record.municipality in required:
        return False
    configured = config.get("geometry", {}).get("vancouver_proper", {}).get("large_lot_min_sqft")
    return area >= float(configured or 6000)


def _trigger_tags(record: PropertyRecord, config: dict[str, Any]) -> list[str]:
    tags: list[str] = []
    if record.expected_market_value and record.asking and record.asking < record.expected_market_value:
        tags.append("PRICE_DISLOCATION")
    elif record.asking_vs_total_assessment is not None and record.asking_vs_total_assessment <= -0.05:
        tags.append("PRICE_DISLOCATION")
    if record.asking_vs_total_assessment is not None and record.asking_vs_total_assessment < 0:
        tags.append("ASSESSMENT_DISCOUNT")
    if record.asking_vs_land_assessment is not None and record.asking_vs_land_assessment < 0:
        tags.append("LAND_ASSESSMENT_DISCOUNT")
    if _large_lot(record, config):
        tags.append("LARGE_LOT")
    if record.frontage_ft is not None and record.frontage_ft >= 44:
        tags.append("STRONG_FRONTAGE")
    if record.zoning_threshold_advantages:
        tags.append("ZONING_THRESHOLD_ADVANTAGE")
    if record.dom is not None and record.dom > 60:
        tags.append("LONG_DOM")
    if record.price_reduction_pct is not None and record.price_reduction_pct <= -0.05:
        tags.append("PRICE_REDUCTION")
    if record.toa or record.toa_status:
        tags.append("TOA_OPTION")
    if record.assembly_score is not None and record.assembly_score >= 50:
        tags.append("ASSEMBLY_OPTION")
    uses = " ".join(record.permitted_uses).lower()
    zoning = _text(record.zoning)
    if "duplex" in uses or "duplex" in zoning or (record.maximum_units or 0) >= 2:
        tags.append("DUPLEX_OPTION")
    if "multiplex" in uses or "multiple dwelling" in uses or (record.maximum_units or 0) >= 3:
        tags.append("MULTIPLEX_OPTION")
    if record.road_class in {"A", "B", "C"} or record.road_exposure_level and record.road_exposure_level >= 4:
        tags.append("NOISE_RISK")
    return list(dict.fromkeys(tags))


def _title_gate(record: PropertyRecord) -> tuple[str, list[str], CandidateStatus]:
    title = record.ownership_type
    source_role = _source_role(getattr(record, "source_role", None))
    if source_role in {
        SourceRole.MLS_PRIMARY.value,
        SourceRole.REALTOR_CA_PUBLIC.value,
        SourceRole.ZOLO_PUBLIC.value,
    } and getattr(record, "official_ownership_confirmed", None) is not True:
        claimed_title = classify_ownership(getattr(record, "unverified_ownership_claim", None))
        if claimed_title in {
            OwnershipType.LEASEHOLD,
            OwnershipType.PREPAID_LEASE,
            OwnershipType.BARE_LAND_STRATA,
            OwnershipType.FRACTIONAL_OWNERSHIP,
            OwnershipType.UNDIVIDED_INTEREST,
            OwnershipType.PARTIAL_INTEREST,
            OwnershipType.COURT_SALE,
            OwnershipType.FORECLOSURE_COURT_ORDERED_SALE,
            OwnershipType.COOPERATIVE_SHARE,
        }:
            return (
                "SPECIAL_SITUATION",
                ["TITLE_CLAIM_REQUIRES_OFFICIAL_CONFIRMATION"],
                CandidateStatus.SPECIAL_SITUATION,
            )
        return (
            "NEED_VERIFICATION",
            ["OWNERSHIP_REQUIRES_OFFICIAL_CONFIRMATION"],
            CandidateStatus.NEED_VERIFICATION,
        )
    hard_veto = {
        OwnershipType.NON_PREPAID_LEASE,
        OwnershipType.FRACTIONAL_OWNERSHIP,
        OwnershipType.UNDIVIDED_INTEREST,
        OwnershipType.PARTIAL_INTEREST,
    }
    special = {
        OwnershipType.LEASEHOLD,
        OwnershipType.BARE_LAND_STRATA,
        OwnershipType.COURT_SALE,
        OwnershipType.FORECLOSURE_COURT_ORDERED_SALE,
        OwnershipType.COOPERATIVE_SHARE,
    }
    if title in hard_veto:
        return "HARD_VETO", ["TITLE_GATE_HARD_VETO"], CandidateStatus.REJECT
    if title in special:
        return "SPECIAL_SITUATION", ["TITLE_REQUIRES_SPECIAL_REVIEW"], CandidateStatus.SPECIAL_SITUATION
    if title is OwnershipType.UNKNOWN:
        return "NEED_VERIFICATION", ["OWNERSHIP_UNKNOWN"], CandidateStatus.NEED_VERIFICATION
    if title is OwnershipType.FREEHOLD_STRATA:
        return "SPECIAL_SITUATION", ["FREEHOLD_STRATA_SUPPLEMENTARY"], CandidateStatus.SPECIAL_SITUATION
    return "PASS", [], CandidateStatus.B_WATCH


def scan_stage1(
    listings: Iterable[Any],
    config: dict[str, Any],
    *,
    observed_at: datetime,
) -> Stage1ScanResult:
    """Run the bounded, high-recall first pass without inventing market records."""

    by_identity: dict[str, PropertyRecord] = {}
    duplicate_count = 0
    conflicts: list[DataConflict] = []
    records_by_address: dict[str, list[PropertyRecord]] = {}
    records_by_mls: dict[str, list[PropertyRecord]] = {}
    listing_aliases: dict[str, str] = {}
    for index, listing in enumerate(listings):
        try:
            payload = _coerce_payload(listing)
            if isinstance(payload, dict) and payload.get("source_role") not in (None, ""):
                payload = _acquisition_payload(
                    payload,
                    source_type=_source_type(payload.get("source_type")),
                    source_registry_id=payload.get("source_registry_id"),
                )
            normalized = normalize_property(payload, config=config)
            identity = (
                normalized.property.property_id
                if isinstance(listing, PropertyRecord) and normalized.property.property_id
                else _identity(normalized.property)
            )
            listing_key = _listing_alias_key(normalized.property)
            if listing_key and listing_key in listing_aliases:
                existing = by_identity.get(listing_aliases[listing_key])
                if existing is not None and _mls_observations_consistent(existing, normalized.property):
                    identity = listing_aliases[listing_key]
            record = normalized.property.model_copy(update={"property_id": identity})
        except Exception:
            # Stage 1 has no authority to turn an invalid row into a candidate.
            continue
        address_key = normalize_address(record.address, record.municipality)
        for existing in records_by_address.get(address_key, []):
            conflicts.extend(_address_collision_conflicts(existing, record, observed_at=observed_at))
        records_by_address.setdefault(address_key, []).append(record)
        mls_key = _listing_alias_key(record)
        if mls_key:
            for existing in records_by_mls.get(mls_key, []):
                if existing.property_id != record.property_id:
                    conflicts.extend(
                        _mls_identity_collision_conflicts(
                            existing,
                            record,
                            observed_at=observed_at,
                        )
                    )
            records_by_mls.setdefault(mls_key, []).append(record)
        if record.property_id in by_identity:
            duplicate_count += 1
            existing = by_identity[record.property_id]
            conflicts.extend(_field_conflicts(existing, record, observed_at=observed_at))
            by_identity[record.property_id] = _merge_records(existing, record)
        else:
            by_identity[record.property_id] = record
        listing_key = _listing_alias_key(record)
        if listing_key:
            listing_aliases.setdefault(listing_key, record.property_id)

    conflict_property_ids = {item.property_id for item in conflicts}
    records = [
        record.model_copy(update={"data_conflict": True, "major_data_conflict": True})
        if record.property_id in conflict_property_ids
        else record
        for record in by_identity.values()
    ]
    candidates: list[Stage1Candidate] = []
    routed_records: list[PropertyRecord] = []
    for record in records:
        bucket_name = _market_bucket(record, config)
        bucket = config.get("market_buckets", {}).get(bucket_name, {}) if bucket_name else None
        range_class = _range_classification(record.asking, bucket)
        trigger_tags = _trigger_tags(record, config)
        gate_state, reasons, candidate_status = _title_gate(record)
        scope, scope_reasons = _research_scope(
            record,
            bucket_name=bucket_name,
            config=config,
        )
        reasons.extend(scope_reasons)
        primary_candidate = (
            record.research_asset_class == "DETACHED_HOUSE"
            and scope not in {"EXCLUDED_NON_TARGET", "EXCLUDED_STEEP_LOT", "SUPPLEMENTARY_DUPLEX"}
        )
        if primary_candidate:
            current_gate, current_reasons = _current_house_gate(record, config)
            reasons.extend(current_reasons)
            if current_gate == "OUT_OF_SCOPE" and gate_state in {"PASS", "NEED_VERIFICATION"}:
                gate_state = "OUT_OF_SCOPE"
                candidate_status = CandidateStatus.D_REJECT
            elif current_gate == "NEED_VERIFICATION" and gate_state == "PASS":
                gate_state = "NEED_VERIFICATION"
                candidate_status = CandidateStatus.NEED_VERIFICATION
        if scope == "SUPPLEMENTARY_DUPLEX":
            gate_state = "SPECIAL_SITUATION"
            candidate_status = CandidateStatus.SPECIAL_SITUATION
        elif scope == "EXCLUDED_NON_TARGET":
            if record.research_asset_class == "STRATA_DUPLEX":
                gate_state = "SPECIAL_SITUATION"
                candidate_status = CandidateStatus.SPECIAL_SITUATION
            else:
                gate_state = "OUT_OF_SCOPE"
                candidate_status = CandidateStatus.D_REJECT
        if not bucket_name:
            reasons.append("NO_NAMED_MARKET_BUCKET")
            if gate_state == "PASS":
                if record.neighbourhood is None:
                    gate_state = "NEED_VERIFICATION"
                    candidate_status = CandidateStatus.NEED_VERIFICATION
                else:
                    gate_state = "OUT_OF_SCOPE"
                    candidate_status = CandidateStatus.D_REJECT
        property_type = _text(record.property_type)
        if (
            property_type
            and record.research_asset_class == "UNKNOWN"
            and not any(token in property_type for token in ("detached", "single family", "house", "ground oriented"))
        ):
            reasons.append("PROPERTY_TYPE_OUT_OF_SCOPE")
            if gate_state == "PASS":
                gate_state = "OUT_OF_SCOPE"
                candidate_status = CandidateStatus.D_REJECT
        if range_class == "OUT_OF_RANGE":
            special_trigger = set(trigger_tags) & {
                "PRICE_DISLOCATION",
                "ASSESSMENT_DISCOUNT",
                "LAND_ASSESSMENT_DISCOUNT",
                "PRICE_REDUCTION",
                "LONG_DOM",
                "LARGE_LOT",
                "STRONG_FRONTAGE",
                "ZONING_THRESHOLD_ADVANTAGE",
                "TOA_OPTION",
                "ASSEMBLY_OPTION",
                "DUPLEX_OPTION",
                "MULTIPLEX_OPTION",
            }
            allow_out_of_range_special = bool(
                config.get("research_policy", {}).get(
                    "allow_out_of_range_special_triggers",
                    True,
                )
            )
            if special_trigger and gate_state == "PASS" and allow_out_of_range_special:
                range_class = "SPECIAL"
                reasons.append("SPECIAL_ASSET_TRIGGER_OUTSIDE_SEARCH_RANGE")
            else:
                reasons.append("OUTSIDE_EXTENDED_SEARCH_RANGE")
            if record.research_asset_class == "DETACHED_HOUSE":
                scope = "OUT_OF_RANGE"
        if range_class == "UNKNOWN":
            reasons.append("ASKING_OR_BUCKET_UNKNOWN")
        included = (
            gate_state == "PASS"
            and primary_candidate
            and range_class in {"CORE", "EXTENDED", "SPECIAL"}
        )
        if gate_state == "NEED_VERIFICATION":
            included = False
        if included:
            reasons.append("STAGE1_IN_SCOPE")
        record = record.model_copy(update={
            "candidate_status": candidate_status,
            "market_bucket": bucket_name,
            "range_classification": range_class,
            "research_scope": scope,
            "research_scope_reasons": list(dict.fromkeys(scope_reasons)),
            "opportunity_tags": list(dict.fromkeys([*record.opportunity_tags, *trigger_tags])),
        })
        record = record.model_copy(update={
            "opportunity_tags": list(dict.fromkeys([
                *record.opportunity_tags,
                *_scope_tags(scope, record),
            ])),
        })
        routed_records.append(record)
        candidates.append(
            Stage1Candidate(
                property_id=record.property_id,
                address=record.address,
                market_bucket=bucket_name,
                included=included,
                gate_state=gate_state,
                reason_codes=list(dict.fromkeys(reasons)),
                trigger_tags=trigger_tags,
                range_classification=range_class,
                research_scope=scope,
                primary_candidate=primary_candidate,
                candidate_status=candidate_status,
                record=record,
            )
        )
    return Stage1ScanResult(
        observed_at=_aware(observed_at),
        # Return the routed projection, not the pre-routing normalized rows.
        # Downstream enrichment/reporting must see the same range and asset
        # scope decisions that Stage 1 used for its candidates.
        records=routed_records,
        candidates=candidates,
        normalized_count=len(by_identity),
        duplicate_count=duplicate_count,
        included_count=sum(1 for item in candidates if item.included),
        research_candidate_count=sum(
            1
            for item in candidates
            if item.gate_state in {"PASS", "NEED_VERIFICATION", "SPECIAL_SITUATION"}
            and item.range_classification in {"CORE", "EXTENDED", "SPECIAL"}
        ),
        verification_queue_count=sum(
            1
            for item in candidates
            if item.candidate_status is CandidateStatus.NEED_VERIFICATION
            and item.range_classification in {"CORE", "EXTENDED", "SPECIAL"}
        ),
        special_situation_count=sum(
            1
            for item in candidates
            if item.candidate_status is CandidateStatus.SPECIAL_SITUATION
        ),
        excluded_count=sum(1 for item in candidates if not item.included),
        conflicts=list({item.conflict_id: item for item in conflicts}.values()),
    )
