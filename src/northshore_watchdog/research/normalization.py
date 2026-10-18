from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from ..models import Listing
from .adapters import adapt_legacy_listing
from .models import ApprovedUse, EvidenceStatus, LotShape, OwnershipType, PropertyRecord, SourceType
from .provenance import (
    DataConflict,
    SourceRecord,
    SourceRegistryEntry,
    evidence_status_for_source,
    load_source_registry_entries,
    registry_allows_evidence,
)

SQFT_TO_M2 = 0.092903
M2_TO_SQFT = 10.7639104167


@dataclass
class NormalizationResult:
    property: PropertyRecord
    provenance: list[SourceRecord] = field(default_factory=list)
    conflicts: list[DataConflict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _slug(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def _canonical_text(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", _slug(value)).strip()


def normalize_address(address: str, municipality: str | None = None) -> str:
    """Normalize common civic-address suffix aliases without changing display text."""

    replacements = {
        "street": "st",
        "avenue": "ave",
        "road": "rd",
        "drive": "dr",
        "boulevard": "blvd",
        "place": "pl",
        "crescent": "cres",
        "court": "ct",
        "lane": "ln",
        "highway": "hwy",
        "terrace": "ter",
        "mount": "mt",
    }
    tokens = _canonical_text(address).split()
    normalized = [replacements.get(token, token) for token in tokens]
    if municipality:
        normalized.extend(_canonical_text(municipality).split())
    return " ".join(normalized)


def _first_present(data: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if data.get(key) not in (None, ""):
            return data[key]
    return None


def _as_list(value: Any) -> list[Any]:
    if value in (None, ""):
        return []
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return [value]


def resolve_property_identity(address: str, municipality: str, pids: Iterable[str] | None = None) -> str:
    normalized_pids = sorted({str(pid).strip() for pid in (pids or ()) if str(pid).strip()})
    if normalized_pids:
        return f"pid:{'+'.join(normalized_pids)}"
    identity_text = normalize_address(address, municipality)
    digest = hashlib.sha256(identity_text.encode("utf-8")).hexdigest()[:20]
    return f"property:{digest}"


def classify_ownership(value: Any) -> OwnershipType:
    text = _slug(value)
    canonical = _canonical_text(value)
    if not text:
        return OwnershipType.UNKNOWN
    if "partial interest" in canonical:
        return OwnershipType.PARTIAL_INTEREST
    if "undivided interest" in canonical:
        return OwnershipType.UNDIVIDED_INTEREST
    if "fractional" in canonical:
        return OwnershipType.FRACTIONAL_OWNERSHIP
    if "non prepaid" in canonical:
        return OwnershipType.NON_PREPAID_LEASE
    if "prepaid lease" in canonical:
        return OwnershipType.PREPAID_LEASE
    if "foreclosure" in canonical or "court ordered" in canonical:
        return OwnershipType.FORECLOSURE_COURT_ORDERED_SALE
    if "court sale" in canonical:
        return OwnershipType.COURT_SALE
    if "bare land strata" in canonical:
        return OwnershipType.BARE_LAND_STRATA
    if "cooperative" in canonical or "co operative" in canonical or "corporate share" in canonical:
        return OwnershipType.COOPERATIVE_SHARE
    if "leasehold" in canonical or "lease" in canonical or "leased" in canonical:
        return OwnershipType.LEASEHOLD
    if "freehold non strata" in canonical:
        return OwnershipType.FREEHOLD_NON_STRATA
    if "strata" in canonical:
        return OwnershipType.FREEHOLD_STRATA
    return OwnershipType.UNKNOWN


def classify_approved_use(value: Any) -> ApprovedUse:
    text = _slug(value)
    if not text:
        return ApprovedUse.UNKNOWN
    if "non conforming" in text or "non-conforming" in text:
        return ApprovedUse.NON_CONFORMING_USE
    if "laneway" in text or "lane way" in text:
        return ApprovedUse.LANEWAY_HOUSE
    if "multiple dwelling" in text or "multiplex" in text:
        return ApprovedUse.MULTIPLE_DWELLING
    if "two family" in text or "two-family" in text or "duplex" in text:
        return ApprovedUse.TWO_FAMILY_DWELLING
    if "secondary suite" in text or "suite" in text:
        return ApprovedUse.SINGLE_DETACHED_SECONDARY_SUITE
    if "strata" in text:
        return ApprovedUse.STRATA
    if "single detached" in text or "detached house" in text or "single family" in text:
        return ApprovedUse.SINGLE_DETACHED
    return ApprovedUse.UNKNOWN


def classify_research_asset(
    property_type: Any,
    *,
    capture_note: Any = None,
    description: Any = None,
    approved_use: Any = None,
) -> tuple[str, bool | None, str, list[str]]:
    """Classify the research product without claiming legal entitlement.

    The listing category is useful for routing a row to the detached-house
    workflow, but it is not a substitute for title or municipal approved-use
    evidence.  In particular, a ``Detached House`` label with a strata/
    duplex note must not enter the primary house universe.
    """

    listing_text = " ".join(
        value
        for value in (
            _canonical_text(property_type),
            _canonical_text(capture_note),
            _canonical_text(description),
        )
        if value
    )
    type_text = _canonical_text(property_type)
    approved_text = _canonical_text(approved_use)
    notes = ["LISTING_CLASSIFICATION_NOT_LEGAL_CONFIRMATION"]

    if "strata" in listing_text and any(
        marker in listing_text for marker in ("duplex", "two family", "two unit")
    ):
        return (
            "STRATA_DUPLEX",
            False,
            "LISTING_PROPERTY_TYPE_AND_CAPTURE_NOTE",
            [*notes, "STRATA_DUPLEX_REQUIRES_TITLE_REVIEW"],
        )
    if any(marker in type_text for marker in ("triplex", "fourplex", "multiplex", "multiple dwelling")):
        return "MULTIPLEX", False, "LISTING_PROPERTY_TYPE", notes
    if any(marker in type_text for marker in ("duplex", "two family", "two unit")):
        return "DUPLEX", False, "LISTING_PROPERTY_TYPE", notes
    if any(marker in type_text for marker in ("coach house", "coachhome", "townhouse", "town home", "row house", "condo", "apartment")):
        return "NON_TARGET", False, "LISTING_PROPERTY_TYPE", notes
    if any(marker in listing_text for marker in ("strata duplex", "strata unit", "strata townhouse")):
        return "NON_TARGET", False, "LISTING_PROPERTY_TYPE_AND_CAPTURE_NOTE", notes
    if any(marker in type_text for marker in ("detached", "single family", "single-family", "house")):
        return "DETACHED_HOUSE", True, "LISTING_PROPERTY_TYPE", notes
    if approved_text in {"two family dwelling", "duplex"}:
        return "DUPLEX", False, "OFFICIAL_APPROVED_USE", [*notes, "APPROVED_USE_REQUIRES_SOURCE_CHECK"]
    if approved_text in {"multiple dwelling", "multiplex"}:
        return "MULTIPLEX", False, "OFFICIAL_APPROVED_USE", [*notes, "APPROVED_USE_REQUIRES_SOURCE_CHECK"]
    return "UNKNOWN", None, "INSUFFICIENT_CLASSIFICATION_DATA", [*notes, "PROPERTY_TYPE_REQUIRES_MANUAL_REVIEW"]


def _normalize_pids(data: dict[str, Any]) -> list[str]:
    values: list[Any] = []
    for key in ("pids", "PID", "pid"):
        candidate = data.get(key)
        if candidate not in (None, "", []):
            values = _as_list(candidate)
            break
    return [str(value).strip() for value in values if str(value).strip()]


def _normalize_common_aliases(data: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(data)
    aliases = {
        "city": "municipality",
        "civic_address": "address",
        "list_price": "asking",
    "sale_price": "transaction_price",
    "sold_price": "transaction_price",
    "transaction_value": "transaction_price",
    "sold_date": "transaction_date",
        "house_size_sqft": "finished_sqft",
        "lot_size_sqft": "lot_area_sqft",
        "assessment_building": "assessment_improvement",
        "tenure": "ownership_type",
        "url": "source_url",
        "source_id": "listing_id",
        "days_on_market": "dom",
        "listed_date": "listing_date",
        "TOA": "toa",
        "TOA_tier": "toa_tier",
        "max_FSR": "maximum_fsr",
        "max_units": "maximum_units",
        "number_of_PIDs": "number_of_pids",
        "lot_shape": "lot_shape",
    }
    for old, new in aliases.items():
        if normalized.get(new) in (None, "") and normalized.get(old) not in (None, ""):
            normalized[new] = normalized[old]
    if normalized.get("distance_to_skytrain_m") in (None, ""):
        for key in ("distance_to_SkyTrain", "distance_to_skytrain"):
            if normalized.get(key) not in (None, ""):
                normalized["distance_to_skytrain_m"] = normalized[key]
                break
    if normalized.get("assembly_requirement") in (None, "") and normalized.get("assembly") not in (None, ""):
        normalized["assembly_requirement"] = normalized["assembly"]
    drainage_value = normalized.get("drainage")
    if normalized.get("drainage_charge") in (None, "") and isinstance(drainage_value, (int, float)):
        normalized["drainage_charge"] = drainage_value
        normalized.pop("drainage", None)
    return normalized


def normalize_property(
    payload: Listing | PropertyRecord | dict[str, Any],
    provenance: Iterable[SourceRecord] | None = None,
    config: dict[str, Any] | None = None,
    source_registry: Mapping[str, SourceRegistryEntry] | None = None,
) -> NormalizationResult:
    if isinstance(payload, Listing):
        data = adapt_legacy_listing(payload)
    elif isinstance(payload, PropertyRecord):
        data = payload.model_dump(mode="python", by_alias=False)
    else:
        data = dict(payload)
    source_records = list(provenance or ())
    data = _normalize_common_aliases(data)
    cfg = config or {}
    if source_registry is None:
        source_registry = load_source_registry_entries(cfg)

    address = str(_first_present(data, "address") or "UNKNOWN ADDRESS").strip()
    municipality = str(_first_present(data, "municipality") or "UNKNOWN MUNICIPALITY").strip()
    pids = _normalize_pids(data)
    explicit_pid = _first_present(data, "pid", "PID")
    if not pids and explicit_pid not in (None, ""):
        pids = [str(explicit_pid).strip()]
    secondary_addresses = [str(item).strip() for item in _as_list(data.get("secondary_civic_addresses")) if str(item).strip()]
    strata_lots = [str(item).strip() for item in _as_list(data.get("strata_lot_identifiers")) if str(item).strip()]
    number_of_pids = data.get("number_of_pids")
    if number_of_pids is None and pids:
        number_of_pids = len(pids)
    property_id = data.get("property_id") or resolve_property_identity(address, municipality, pids)

    ownership_value = _first_present(data, "ownership_type", "tenure")
    if isinstance(ownership_value, OwnershipType):
        ownership = ownership_value
    else:
        ownership = classify_ownership(ownership_value)

    approved_value = data.get("approved_use")
    official_zoning_source = next(
        (
            item
            for item in source_records
            if item.field_name in {"zoning", "current_zoning"}
            and item.source_type is SourceType.OFFICIAL_GOV
            and item.evidence_status is EvidenceStatus.CONFIRMED
            and registry_allows_evidence(
                source_registry.get(item.source_registry_id) if source_registry and item.source_registry_id else None,
                expected_source_type=SourceType.OFFICIAL_GOV,
            )
        ),
        None,
    )
    official_use_source = next(
        (
            item
            for item in source_records
            if item.field_name in {"approved_use", "current_approved_use"}
            and item.source_type is SourceType.OFFICIAL_GOV
            and item.evidence_status is EvidenceStatus.CONFIRMED
            and registry_allows_evidence(
                source_registry.get(item.source_registry_id) if source_registry and item.source_registry_id else None,
                expected_source_type=SourceType.OFFICIAL_GOV,
            )
        ),
        None,
    )
    official_pid_source = next(
        (
            item
            for item in source_records
            if item.field_name in {"pid", "PID", "pids"}
            and item.source_type is SourceType.OFFICIAL_GOV
            and item.evidence_status is EvidenceStatus.CONFIRMED
            and registry_allows_evidence(
                source_registry.get(item.source_registry_id) if source_registry and item.source_registry_id else None,
                expected_source_type=SourceType.OFFICIAL_GOV,
            )
        ),
        None,
    )
    official_ownership_source = next(
        (
            item
            for item in source_records
            if item.field_name in {"ownership", "ownership_type", "tenure"}
            and item.source_type is SourceType.OFFICIAL_GOV
            and item.evidence_status is EvidenceStatus.CONFIRMED
            and registry_allows_evidence(
                source_registry.get(item.source_registry_id) if source_registry and item.source_registry_id else None,
                expected_source_type=SourceType.OFFICIAL_GOV,
            )
        ),
        None,
    )
    official_use_evidence = official_use_source is not None
    approved = (
        approved_value
        if isinstance(approved_value, ApprovedUse) and official_use_evidence
        else classify_approved_use(approved_value)
        if official_use_evidence
        else ApprovedUse.UNKNOWN
    )
    if secondary_addresses and number_of_pids == 1:
        single_title_multi_address: bool | None = True
        independently_tradable: bool | None = False
        if not official_use_evidence:
            approved = ApprovedUse.UNKNOWN
    elif len(pids) > 1 or len(strata_lots) > 1:
        single_title_multi_address = False
        independently_tradable = True
    else:
        single_title_multi_address = data.get("single_title_multi_address")
        independently_tradable = data.get("independently_tradable")

    lot_area_sqft = _first_present(data, "lot_area_sqft", "lot_size_sqft")
    lot_area_m2 = _first_present(data, "lot_area_m2")
    if lot_area_sqft is None and lot_area_m2 is not None:
        lot_area_sqft = round(float(lot_area_m2) * M2_TO_SQFT, 2)
    elif lot_area_sqft is not None:
        lot_area_sqft = float(lot_area_sqft)
        lot_area_m2 = round(lot_area_sqft * SQFT_TO_M2, 2)

    (
        asset_class,
        primary_detached_eligible,
        asset_basis,
        asset_notes,
    ) = classify_research_asset(
        data.get("property_type"),
        capture_note=data.get("source_capture_note"),
        description=data.get("description"),
        approved_use=approved,
    )
    existing_asset_class = str(data.get("research_asset_class") or "UNKNOWN").upper()
    if existing_asset_class != "UNKNOWN":
        asset_class = existing_asset_class
        primary_detached_eligible = (
            True if asset_class == "DETACHED_HOUSE"
            else False if asset_class != "UNKNOWN"
            else None
        )
        asset_basis = data.get("research_asset_classification_basis") or "NORMALIZED_RECORD"
        asset_notes = list(data.get("research_asset_classification_notes") or asset_notes)

    construction_data = dict(data)
    construction_data.update({
        "property_id": property_id,
        "address": address,
        "municipality": municipality,
        "secondary_civic_addresses": secondary_addresses,
        "pid": pids[0] if pids else _first_present(data, "pid", "PID"),
        "pids": pids,
        "number_of_pids": number_of_pids,
        "strata_lot_identifiers": strata_lots,
        "ownership_type": ownership,
        "approved_use": approved,
        "single_title_multi_address": single_title_multi_address,
        "independently_tradable": independently_tradable,
        "lot_area_sqft": lot_area_sqft,
        "lot_area_m2": lot_area_m2,
        "lane_access": data.get("lane_access", data.get("lane")),
        "corner_lot": data.get("corner_lot", data.get("corner")),
        "research_asset_class": asset_class,
        "primary_detached_eligible": primary_detached_eligible,
        "research_asset_classification_basis": asset_basis,
        "research_asset_classification_notes": asset_notes,
        "research_scope": (
            "PRIMARY_DETACHED"
            if asset_class == "DETACHED_HOUSE"
            else "SUPPLEMENTARY_DUPLEX"
            if asset_class == "DUPLEX"
            else "EXCLUDED_NON_TARGET"
            if primary_detached_eligible is False
            else "NEED_VERIFICATION"
        ),
        # Legal evidence flags are derived only from an attached official
        # source record; source payloads cannot self-attest entitlement.
        "official_zoning_confirmed": True if official_zoning_source else None,
        "official_zoning_source_url": official_zoning_source.source_url if official_zoning_source else None,
        "official_zoning_source_registry_id": official_zoning_source.source_registry_id if official_zoning_source else None,
        "official_zoning_evidence_status": (
            evidence_status_for_source(
                source_registry,
                official_zoning_source.source_registry_id,
                SourceType.OFFICIAL_GOV,
            )
            if official_zoning_source
            else EvidenceStatus.UNKNOWN
        ),
        "official_zoning_retrieved_at": official_zoning_source.retrieved_at if official_zoning_source else None,
        "official_approved_use_confirmed": True if official_use_source else None,
        "official_approved_use_source_url": official_use_source.source_url if official_use_source else None,
        "official_approved_use_source_registry_id": official_use_source.source_registry_id if official_use_source else None,
        "official_approved_use_evidence_status": (
            evidence_status_for_source(
                source_registry,
                official_use_source.source_registry_id,
                SourceType.OFFICIAL_GOV,
            )
            if official_use_source
            else EvidenceStatus.UNKNOWN
        ),
        "official_approved_use_retrieved_at": official_use_source.retrieved_at if official_use_source else None,
        "official_pid_confirmed": True if official_pid_source and pids else None,
        "official_ownership_confirmed": True if official_ownership_source and ownership is not OwnershipType.UNKNOWN else None,
        "official_title_source_url": (
            official_ownership_source or official_pid_source
        ).source_url if (official_ownership_source or official_pid_source) else None,
        "official_title_source_registry_id": (
            official_ownership_source or official_pid_source
        ).source_registry_id if (official_ownership_source or official_pid_source) else None,
        "official_title_evidence_status": (
            evidence_status_for_source(
                source_registry,
                (official_ownership_source or official_pid_source).source_registry_id,
                SourceType.OFFICIAL_GOV,
            )
            if (official_ownership_source or official_pid_source)
            else EvidenceStatus.UNKNOWN
        ),
        "official_title_retrieved_at": (
            official_ownership_source or official_pid_source
        ).retrieved_at if (official_ownership_source or official_pid_source) else None,
    })
    for key in ("PID", "tenure", "city", "list_price", "house_size_sqft", "lot_size_sqft", "assessment_building", "url", "source_id", "days_on_market", "listed_date"):
        construction_data.pop(key, None)

    permanent_from_description, fixable_from_description = classify_defects(
        str(data.get("description") or ""), cfg
    )
    if not construction_data.get("permanent_defects"):
        construction_data["permanent_defects"] = permanent_from_description
    if not construction_data.get("fixable_defects"):
        construction_data["fixable_defects"] = fixable_from_description
    record = PropertyRecord.model_validate(construction_data)
    record = derive_assessment_metrics(record)
    record = derive_listing_metrics(record)
    record = derive_carrying_cost_metrics(record)
    threshold_tolerance = float(cfg.get("zoning", {}).get("threshold_tolerance_m", 0.05))
    threshold_tags = detect_zoning_threshold_advantage(
        record.frontage_ft * 0.3048 if record.frontage_ft is not None else None,
        record.minimum_frontage,
        tolerance_m=threshold_tolerance,
    )
    record = record.model_copy(update={
        "lot_area_score": calculate_lot_area_score(record, cfg),
        "frontage_score": calculate_frontage_score(record),
        "depth_score": calculate_depth_score(record),
        "lot_geometry_score": calculate_lot_geometry_score(record, cfg),
        "road_exposure_level": calculate_road_exposure(
            record.road_class,
            record.distance_to_major_road_m,
            record.property_fronts_major_road,
        ),
        "zoning_threshold_advantages": list(dict.fromkeys(
            [*record.zoning_threshold_advantages, *threshold_tags]
        )),
        "data_conflict": bool(record.transaction_history_conflict),
    })
    record = derive_current_house_use_status(record, cfg)
    conflicts = detect_material_conflicts_from_provenance(property_id, source_records)
    if conflicts:
        record = record.model_copy(update={"data_conflict": True})
    return NormalizationResult(property=record, provenance=source_records, conflicts=conflicts)


def derive_assessment_metrics(record: PropertyRecord) -> PropertyRecord:
    updates: dict[str, Any] = {}
    if record.assessment_total and record.assessment_total > 0:
        labels: list[str] = []
        if record.assessment_land is not None:
            updates["land_ratio"] = round(record.assessment_land / record.assessment_total, 4)
            if updates["land_ratio"] >= 0.85:
                labels.append("LAND_DOMINANT")
            if updates["land_ratio"] >= 0.95:
                labels.append("HIGHLY_LAND_DOMINANT")
        if record.asking is not None:
            total_delta = round(record.asking / record.assessment_total - 1, 4)
            land_delta = round(record.asking / record.assessment_land - 1, 4) if record.assessment_land else None
            updates["asking_vs_total_assessment"] = round(total_delta, 4)
            updates["asking_vs_land_assessment"] = round(land_delta, 4) if land_delta is not None else None
            if total_delta < 0:
                labels.append("ASK_BELOW_ASSESSMENT")
            if total_delta <= -0.10:
                labels.append("ASK_10PCT_BELOW_ASSESSMENT")
            if land_delta is not None and land_delta < 0:
                labels.append("ASK_BELOW_LAND_ASSESSMENT")
        updates["assessment_labels"] = labels
        if record.asking is not None:
            updates["assessment_valuation_status"] = "ASSESSMENT_ANCHOR_ONLY"
    elif record.asking is not None:
        updates["assessment_valuation_status"] = "UNKNOWN"
    return record.model_copy(update=updates)


def derive_listing_metrics(record: PropertyRecord) -> PropertyRecord:
    updates: dict[str, Any] = {}
    if record.asking is not None and record.original_asking is not None and record.original_asking > 0:
        if record.asking < record.original_asking:
            updates["price_reduction_amount"] = record.original_asking - record.asking
            updates["price_reduction_pct"] = round(record.asking / record.original_asking - 1, 4)
    if record.cumulative_dom is None and record.dom is not None:
        updates["cumulative_dom"] = record.dom
    return record.model_copy(update=updates)


def derive_current_house_use_status(
    record: PropertyRecord,
    config: dict[str, Any] | None = None,
) -> PropertyRecord:
    """Classify the current-house fit without hiding unknown inspection data."""

    policy = (config or {}).get("research_policy", {})
    minimums = policy.get("current_house_minimums", {})
    required = {
        "bedrooms": record.bedrooms,
        "bathrooms": record.bathrooms,
        "finished_sqft": record.finished_sqft,
    }
    missing = [name for name, value in required.items() if value is None]
    if missing:
        return record.model_copy(update={
            "current_house_use_status": "NEED_VERIFICATION",
            "current_house_use_reasons": [f"{name.upper()}_UNKNOWN" for name in missing],
        })
    reasons: list[str] = []
    if float(record.bedrooms) < float(minimums.get("bedrooms", 3)):
        reasons.append("BEDROOMS_BELOW_MINIMUM")
    if float(record.bathrooms) < float(minimums.get("bathrooms", 2.5)):
        reasons.append("BATHROOMS_BELOW_MINIMUM")
    if float(record.finished_sqft) < float(minimums.get("finished_sqft", 1500)):
        reasons.append("FINISHED_SQFT_BELOW_MINIMUM")
    disqualifying_terms = (
        "foundation",
        "structural",
        "major electrical",
        "major plumbing",
        "major drainage",
        "major sewer",
    )
    defects_text = " ".join(str(item).lower() for item in record.permanent_defects)
    if any(term in defects_text for term in disqualifying_terms):
        reasons.append("DISQUALIFYING_SYSTEM_OR_STRUCTURE_RISK")
    return record.model_copy(update={
        "current_house_use_status": "BELOW_MINIMUM_OR_DISQUALIFYING" if reasons else "MEETS_SIZE_MINIMUMS_SYSTEMS_PENDING",
        "current_house_use_reasons": reasons or ["SYSTEMS_REQUIRE_INSPECTION"],
    })


def derive_carrying_cost_metrics(record: PropertyRecord) -> PropertyRecord:
    """Keep municipal tax and utility components auditable while deriving totals."""

    updates: dict[str, Any] = {}
    pure_tax = record.pure_property_tax
    utility_values = [
        record.water,
        record.sewer,
        record.garbage,
        record.drainage_charge,
        record.other_levies,
    ]
    if pure_tax is not None and record.tax is None:
        updates["tax"] = pure_tax
    if any(value is not None for value in utility_values):
        utility_total = sum(int(value) for value in utility_values if value is not None)
        updates["utilities"] = utility_total
    else:
        utility_total = record.utilities
    tax_total = pure_tax if pure_tax is not None else record.tax
    if tax_total is not None or utility_total is not None:
        updates["annual_municipal_carrying_cost"] = int(tax_total or 0) + int(utility_total or 0)
    return record.model_copy(update=updates)


def _band_score(value: float | None, bands: dict[str, list[float]] | None, fallback: list[tuple[float, float]] | None = None) -> float:
    if value is None:
        return 0
    for _, limits in (bands or {}).items():
        if limits[0] <= value <= limits[1]:
            position = list((bands or {}).values()).index(limits)
            return min(30, 5 + position * 5)
    for upper, score in fallback or ():
        if value <= upper:
            return score
    return 30


def calculate_lot_area_score(
    record: PropertyRecord,
    config: dict[str, Any] | None = None,
) -> float | None:
    """Score lot area on the final 3,000/4,000/6,000 sqft policy.

    North/West Vancouver still require a local neighbourhood percentile.  The
    absolute Vancouver/Burnaby thresholds are not silently transplanted into
    those markets.
    """

    cfg = config or {}
    geometry_cfg = cfg.get("geometry", {})
    percentiles_required = set(geometry_cfg.get("municipality_percentiles_required", []))
    if record.municipality in percentiles_required:
        if record.lot_size_percentile_in_neighbourhood is None:
            return None
        return round(max(0.0, min(100.0, float(record.lot_size_percentile_in_neighbourhood))), 2)
    area = record.lot_area_sqft
    if area is None:
        return None
    thresholds = geometry_cfg.get("lot_area_score", {})
    fail_below = float(thresholds.get("fail_below_sqft", 3000))
    pass_area = float(thresholds.get("pass_sqft", 4000))
    full_area = float(thresholds.get("full_score_sqft", 6000))
    if area < fail_below:
        return 0.0
    if area <= pass_area:
        if pass_area <= fail_below:
            return 80.0
        return round(40.0 + (area - fail_below) / (pass_area - fail_below) * 40.0, 2)
    if area >= full_area:
        return 100.0
    if full_area <= pass_area:
        return 100.0
    return round(80.0 + (area - pass_area) / (full_area - pass_area) * 20.0, 2)


def calculate_frontage_score(record: PropertyRecord) -> float | None:
    if record.frontage_ft is None:
        return None
    frontage = float(record.frontage_ft)
    if frontage < 33:
        return round(max(0.0, frontage / 33 * 60), 2)
    return round(min(100.0, 60.0 + (frontage - 33) / 17 * 40), 2)


def calculate_depth_score(record: PropertyRecord) -> float | None:
    if record.depth_ft is None:
        return None
    depth = float(record.depth_ft)
    if depth < 100:
        return round(max(0.0, depth / 100 * 60), 2)
    return round(min(100.0, 60.0 + (depth - 100) / 25 * 40), 2)


def calculate_lot_geometry_score(record: PropertyRecord, config: dict[str, Any] | None = None) -> float | None:
    cfg = config or {}
    area_score_100 = calculate_lot_area_score(record, cfg)
    if area_score_100 is None:
        return None
    frontage_score_100 = calculate_frontage_score(record)
    depth_score_100 = calculate_depth_score(record)
    area_score = area_score_100 / 100 * 30
    frontage_score = (frontage_score_100 or 0) / 100 * 25
    depth_score = (depth_score_100 or 0) / 100 * 20
    shape = _slug(record.lot_shape)
    shape_score = {
        "regular_rectangular": 10,
        "corner_regular": 8,
        "irregular": 5,
        "pie": 4,
        "flag": 3,
        "steep": 1,
    }.get(shape, 5 if record.lot_shape is not None else 0)
    if record.corner_lot is True and shape == "regular_rectangular":
        shape_score *= 0.9
    lane_score = 8 if (record.lane_access or record.lane) else 0
    slope_text = _slug(record.slope)
    slope_score = 4 if slope_text in {"flat", "level", "gentle"} else 0 if "steep" in slope_text else 2 if record.slope is not None else 0
    return round(min(100, area_score + frontage_score + depth_score + shape_score + lane_score + slope_score), 2)


def calculate_road_exposure(
    road_class: Any,
    distance_m: float | None,
    property_fronts_major_road: bool | None,
) -> int | None:
    if property_fronts_major_road is True:
        return 5
    if distance_m is None:
        return None
    base_level: int
    if distance_m < 50:
        base_level = 4
    elif distance_m <= 100:
        base_level = 3
    elif distance_m <= 200:
        base_level = 2
    else:
        base_level = 1
    road_value = road_class.value if hasattr(road_class, "value") else str(road_class or "").upper()
    class_bonus = {"A": 1, "B": 1, "C": 0, "D": 0, "E": 0}.get(road_value, 0) if base_level <= 2 else 0
    return min(5, base_level + class_bonus)


def calculate_lot_size_percentile(
    lot_area_sqft: float | None,
    neighbourhood_lot_areas: Iterable[float] | None,
) -> float | None:
    if lot_area_sqft is None:
        return None
    values = sorted(float(value) for value in (neighbourhood_lot_areas or ()) if value is not None)
    if not values:
        return None
    return round(sum(1 for value in values if value <= lot_area_sqft) / len(values) * 100, 2)


def detect_zoning_threshold_advantage(
    frontage_m: float | None,
    minimum_frontage_m: float | None,
    tolerance_m: float = 0.05,
) -> list[str]:
    if frontage_m is None or minimum_frontage_m is None or frontage_m < minimum_frontage_m:
        return []
    difference = frontage_m - minimum_frontage_m
    tags: list[str] = []
    if 0 < difference <= tolerance_m:
        tags.append("JUST_ABOVE_ZONING_THRESHOLD")
    tags.append("ZONING_THRESHOLD_ADVANTAGE")
    return tags


def detect_material_conflicts(property_id: str, values_by_field: dict[str, dict[str, Any]]) -> list[DataConflict]:
    conflicts: list[DataConflict] = []
    for field_name, source_values in values_by_field.items():
        def comparable_value(value: Any) -> Any:
            if hasattr(value, "value"):
                return comparable_value(value.value)
            if field_name in {"address", "civic_address"}:
                return normalize_address(str(value), None)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return round(float(value), 6)
            if isinstance(value, str):
                numeric_text = value.replace(",", "").replace("$", "").strip()
                try:
                    return round(float(numeric_text), 6)
                except ValueError:
                    pass
            if isinstance(value, dict):
                return tuple(sorted((str(key), comparable_value(item)) for key, item in value.items()))
            if isinstance(value, (list, tuple, set)):
                return tuple(comparable_value(item) for item in value)
            return value

        material_values = [value for value in source_values.values() if value not in (None, "")]
        if field_name in {"address", "civic_address"}:
            normalized_addresses = [normalize_address(str(value), None) for value in material_values]
            if normalized_addresses and all(
                left == right or left.startswith(f"{right} ") or right.startswith(f"{left} ")
                for index, left in enumerate(normalized_addresses)
                for right in normalized_addresses[index + 1:]
            ):
                continue
        if field_name in {"frontage_ft", "depth_ft"}:
            numeric_values = []
            for value in material_values:
                try:
                    numeric_values.append(float(value))
                except (TypeError, ValueError):
                    numeric_values = []
                    break
            # Public property records commonly round lot dimensions to a
            # tenth of a foot.  Treat that measurement noise as equivalent,
            # while retaining a conflict for genuinely different geometry.
            if numeric_values and max(numeric_values) - min(numeric_values) <= 0.5:
                continue
        comparable = {repr(comparable_value(value)) for value in material_values}
        if len(comparable) <= 1:
            continue
        conflicts.append(DataConflict(
            conflict_id=f"conflict:{property_id}:{field_name}",
            property_id=property_id,
            field_name=field_name,
            conflict_type=f"{field_name.upper()}_MISMATCH",
            source_values=source_values,
            conflicting_sources=sorted(source_values),
        ))
    return conflicts


def detect_material_conflicts_from_provenance(
    property_id: str,
    provenance: Iterable[SourceRecord],
) -> list[DataConflict]:
    grouped: dict[str, dict[str, Any]] = {}
    for record in provenance:
        grouped.setdefault(record.field_name, {})[record.source_url] = record.normalized_value
    return detect_material_conflicts(property_id, grouped)


def classify_defects(text: str, config: dict[str, Any] | None = None) -> tuple[list[str], list[str]]:
    cfg = config or {}
    lowered = _slug(text)
    permanent: list[str] = []
    fixable: list[str] = []
    clauses = [part.strip() for part in re.split(r"[,;.!]+", text.replace("\n", ",")) if part.strip()]
    for category, target in (("permanent", permanent), ("fixable", fixable)):
        for keyword in cfg.get("defects", {}).get(category, []):
            if _slug(keyword) not in lowered:
                continue
            matched_clause = next((_slug(clause) for clause in clauses if _slug(keyword) in _slug(clause)), _slug(keyword))
            if matched_clause not in target:
                target.append(matched_clause)
    return permanent, fixable
