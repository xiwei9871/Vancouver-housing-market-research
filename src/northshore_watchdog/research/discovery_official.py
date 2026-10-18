"""Official-data bridge for filter-first listing discovery.

Discovery cards are intentionally small listing observations.  This module
turns them into the existing ``PropertyRecord`` shape, runs the configured
official adapters, writes the returned evidence back onto the cards, and only
then hands the cards to the scoring layer.  A failed official lookup is kept
as evidence; it is never replaced with a listing-site guess.
"""

from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any, Iterable, Mapping

from pydantic import BaseModel, ConfigDict, Field

from .discovery import DiscoveryCard
from .models import OfficialQueryStatus, OwnershipType, PropertyRecord
from .normalization import classify_ownership, normalize_address
from .official_enrichment import OfficialEnrichmentBatch, OfficialLookupRow
from .sources import SourceRunResult, SourceRunStatus


_LOT_DIMENSIONS_RE = re.compile(
    r"(?<!\w)(?P<width>\d[\d,]*(?:\.\d+)?)\s*(?:'|ft\.?|feet)?\s*"
    r"(?:x|×)\s*(?P<depth>\d[\d,]*(?:\.\d+)?)\s*(?:'|ft\.?|feet)?",
    re.IGNORECASE,
)


class DiscoveryOfficialEnrichmentResult(BaseModel):
    """Auditable result of the official data phase preceding screening."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    cards: list[DiscoveryCard] = Field(default_factory=list)
    source_runs: list[SourceRunResult] = Field(default_factory=list)
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    status: str = "SKIPPED_DISABLED"
    attempted_card_count: int = 0
    enriched_card_count: int = 0
    status_counts: dict[str, int] = Field(default_factory=dict)
    field_coverage: dict[str, int] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


def _aware(value: datetime | None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        return current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc)


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"[-+]?\d[\d,]*(?:\.\d+)?", str(value))
    if not match:
        return None
    try:
        return float(match.group(0).replace(",", ""))
    except ValueError:
        return None


def _raw_value(card: DiscoveryCard, *names: str) -> Any:
    for name in names:
        value = card.raw.get(name) if isinstance(card.raw, Mapping) else None
        if value not in (None, "", [], {}):
            return value
        value = getattr(card, name, None)
        if value not in (None, "", [], {}):
            return value
    return None


def _municipality_name(value: Any) -> str:
    text = " ".join(str(value or "").strip().split())
    key = text.lower()
    return {
        "vancouver": "City of Vancouver",
        "city of vancouver": "City of Vancouver",
        "burnaby": "City of Burnaby",
        "city of burnaby": "City of Burnaby",
        "north vancouver": "City of North Vancouver",
        "city of north vancouver": "City of North Vancouver",
        "district of north vancouver": "District of North Vancouver",
        "west vancouver": "District of West Vancouver",
        "district of west vancouver": "District of West Vancouver",
    }.get(key, text or "Unknown municipality")


def _dimensions(card: DiscoveryCard) -> tuple[float | None, float | None]:
    if card.frontage_ft is not None and card.depth_ft is not None:
        return _number(card.frontage_ft), _number(card.depth_ft)
    match = _LOT_DIMENSIONS_RE.search(str(card.lot_dimensions or ""))
    if not match:
        return None, None
    return _number(match.group("width")), _number(match.group("depth"))


def _property_id(card: DiscoveryCard) -> str:
    if card.mls_number:
        return f"discovery:mls:{card.mls_number}"
    return f"discovery:address:{normalize_address(card.address, card.municipality)}"


def _as_property_record(card: DiscoveryCard) -> PropertyRecord:
    width, depth = _dimensions(card)
    lot_area = _number(card.lot_area_sqft)
    if lot_area is None and width is not None and depth is not None:
        lot_area = round(width * depth, 2)
    ownership_claim = card.ownership_type
    ownership = classify_ownership(ownership_claim)
    if ownership is OwnershipType.UNKNOWN and "freehold" in str(ownership_claim or "").lower():
        ownership = OwnershipType.FREEHOLD_NON_STRATA
    raw_pid = _raw_value(card, "pid", "PID", "official_pid")
    return PropertyRecord(
        property_id=_property_id(card),
        address=card.address,
        municipality=_municipality_name(card.municipality),
        neighbourhood=card.neighbourhood,
        property_type=card.property_type,
        pid=str(raw_pid) if raw_pid not in (None, "") else None,
        ownership_type=ownership,
        unverified_ownership_claim=ownership_claim,
        listing_id=card.mls_number,
        mls_number=card.mls_number,
        source_url=card.source_url,
        description=card.description,
        listing_status=card.listing_status,
        asking=int(_number(card.asking)) if _number(card.asking) is not None else None,
        dom=int(_number(card.dom)) if _number(card.dom) is not None else None,
        lot_area_sqft=lot_area,
        frontage_ft=width,
        depth_ft=depth,
        year_built=int(_number(card.year_built)) if _number(card.year_built) is not None else None,
        finished_sqft=_number(card.finished_sqft),
        bedrooms=_number(card.bedrooms),
        bathrooms=_number(card.bathrooms),
        official_elementary_catchment=_raw_value(card, "official_elementary_catchment"),
        official_secondary_catchment=_raw_value(card, "official_secondary_catchment"),
        latitude=card.latitude,
        longitude=card.longitude,
        geometry_source=card.geometry_source,
        realtor_price_sale_history=list(card.realtor_price_sale_history or []),
        realtor_history_status=card.realtor_history_status,
    )


def normalize_discovery_card(card: DiscoveryCard) -> DiscoveryCard:
    """Normalize dimensions and repair only obvious lot-area parser errors.

    The listing observation remains the primary value.  If an explicit area
    is present, it is retained unless it is less than half of the area implied
    by an explicit frontage/depth pair; that pattern is characteristic of a
    broken card parser (for example ``1`` beside ``33 x 122 ft``), not a
    legitimate irregular lot.  Every repair is retained in ``raw`` and in a
    reason code so the later human review can see exactly what happened.
    """

    width, depth = _dimensions(card)
    updates: dict[str, Any] = {}
    raw = dict(card.raw or {})
    reason_codes = list(card.reason_codes or [])
    if card.frontage_ft is None and width is not None:
        updates["frontage_ft"] = width
    if card.depth_ft is None and depth is not None:
        updates["depth_ft"] = depth

    area = _number(card.lot_area_sqft)
    if width is not None and depth is not None:
        expected = round(width * depth, 2)
        if area is None:
            updates["lot_area_sqft"] = expected
            updates["lot_area_source"] = "DERIVED_FROM_LISTING_DIMENSIONS"
        elif expected > 0 and area / expected < 0.5:
            raw.setdefault("lot_area_original_sqft", card.lot_area_sqft)
            raw["lot_area_derived_sqft"] = expected
            raw["lot_area_consistency_ratio"] = round(area / expected, 6)
            updates["lot_area_sqft"] = expected
            updates["lot_area_source"] = "CALCULATED_FROM_DIMENSIONS_CONFLICT_REPAIRED"
            if "LOT_AREA_REPAIRED_FROM_DIMENSIONS" not in reason_codes:
                reason_codes.append("LOT_AREA_REPAIRED_FROM_DIMENSIONS")

    if raw != dict(card.raw or {}):
        updates["raw"] = raw
    if raw.get("official_frontage_ft") is not None and raw.get("official_depth_ft") is not None:
        updates["geometry_source"] = "OFFICIAL_BCA"
    elif width is not None and depth is not None:
        updates["geometry_source"] = "LISTING_OBSERVED"
    else:
        updates["geometry_source"] = "UNKNOWN"
    if reason_codes != list(card.reason_codes or []):
        updates["reason_codes"] = reason_codes
    return card.model_copy(update=updates, deep=True) if updates else card


# Keep the private name as a compatibility alias for callers that imported it
# before the normalizer became part of the public discovery boundary.
_normalize_discovery_card = normalize_discovery_card


def _source_run_failure(adapter: Any, records: list[PropertyRecord], observed_at: datetime, exc: Exception) -> SourceRunResult:
    return SourceRunResult(
        source_registry_id=str(getattr(adapter, "source_registry_id", "source:official")),
        source_name=str(getattr(adapter, "name", "official")),
        status=SourceRunStatus.FAILED,
        started_at=observed_at,
        finished_at=observed_at,
        records=[
            {
                "property_id": record.property_id,
                "status": OfficialQueryStatus.FAILED.value,
                "warnings": [f"{type(exc).__name__}: {exc}"],
            }
            for record in records
        ],
        errors=[f"{type(exc).__name__}: {exc}"],
    )


def _official_field_name(name: str) -> str:
    # Listing observations remain the primary card values when the official
    # source disagrees. Keep official measurements in their own namespace so
    # the conflict is auditable and can be manually resolved later.
    if name in {
        "bedrooms", "bathrooms", "finished_sqft", "lot_area_sqft",
        "frontage_ft", "depth_ft", "year_built",
    }:
        return f"official_{name}"
    if name in {"zoning", "current_zoning"}:
        return f"official_{name}"
    if name in {"road_class", "distance_to_major_road_m", "nearest_major_road", "property_fronts_major_road"}:
        return f"official_{name}"
    if name.startswith("official_"):
        return name
    if name.startswith(("assessment_", "previous_year_", "previous_sale_", "sold_history_")):
        return f"official_{name}"
    return name


def _merge_row(card: DiscoveryCard, row: OfficialLookupRow, adapter: Any) -> DiscoveryCard:
    raw = dict(card.raw or {})
    evidence = list(raw.get("_official_evidence", [])) if isinstance(raw.get("_official_evidence"), list) else []
    fields = dict(row.fields or {})
    direct = {
        "official_elementary_catchment": "elementary_school",
        "official_secondary_catchment": "secondary_school",
        "lot_area_sqft": "lot_area_sqft",
        "frontage_ft": "frontage_ft",
        "depth_ft": "depth_ft",
        "year_built": "year_built",
        "finished_sqft": "finished_sqft",
        "bedrooms": "bedrooms",
        "bathrooms": "bathrooms",
    }

    def present(value: Any) -> bool:
        return value not in (None, "", [], {})

    def same(left: Any, right: Any) -> bool:
        if isinstance(left, (int, float)) and isinstance(right, (int, float)):
            return float(left) == float(right)
        return left == right

    for key, value in fields.items():
        if not present(value):
            continue
        target = _official_field_name(str(key))
        existing = raw.get(target)
        direct_target = direct.get(str(key))
        listing_value = getattr(card, direct_target, None) if direct_target else None
        comparison_value = existing if present(existing) else listing_value
        if present(comparison_value) and not same(comparison_value, value):
            raw.setdefault("_official_conflicts", []).append({"field": target, "left": comparison_value, "right": value})
        # Always retain the official observation in its namespaced field;
        # only the listing-facing card field is protected from overwrite.
        raw[target] = value
        # Preserve exact official names as well.  This makes the provenance
        # obvious and lets downstream scoring prefer official evidence.
        raw.setdefault(str(key), value)
    if raw.get("official_frontage_ft") is not None and raw.get("official_depth_ft") is not None:
        raw["geometry_source"] = "OFFICIAL_BCA"
    elif card.frontage_ft is not None and card.depth_ft is not None:
        raw.setdefault("geometry_source", "LISTING_OBSERVED")
    else:
        raw.setdefault("geometry_source", "UNKNOWN")
    evidence.append({
        "adapter": str(getattr(adapter, "name", "official")),
        "source_registry_id": str(getattr(adapter, "source_registry_id", "")),
        "source_type": str(getattr(getattr(adapter, "source_type", None), "value", getattr(adapter, "source_type", "OFFICIAL_GOV"))),
        "status": row.status.value,
        "source_url": row.source_url,
        "retrieved_at": row.retrieved_at.isoformat() if row.retrieved_at else None,
        "source_version": row.source_version,
        "match_method": row.match_method,
        "query_scope": row.query_scope,
        "warnings": list(row.warnings),
        "fields": fields,
    })
    raw["_official_evidence"] = evidence
    updates: dict[str, Any] = {"raw": raw}
    updates["geometry_source"] = str(raw.get("geometry_source") or "UNKNOWN")
    for source, target in direct.items():
        source_key = source.removeprefix("official_")
        value = fields.get(source)
        if value in (None, "", [], {}) and source_key != source:
            value = fields.get(source_key)
        current = getattr(card, target, None)
        if present(value) and not present(current):
            updates[target] = value
    return card.model_copy(update=updates, deep=True)


_OFFICIAL_DIRECT_FIELDS = {
    "official_elementary_catchment": "elementary_school",
    "official_secondary_catchment": "secondary_school",
    "lot_area_sqft": "lot_area_sqft",
    "frontage_ft": "frontage_ft",
    "depth_ft": "depth_ft",
    "year_built": "year_built",
    "finished_sqft": "finished_sqft",
    "bedrooms": "bedrooms",
    "bathrooms": "bathrooms",
}


def _card_cache_keys(card: DiscoveryCard) -> tuple[str, ...]:
    values = (card.mls_number, card.source_url, card.address)
    return tuple(
        str(value).strip().casefold()
        for value in values
        if value not in (None, "")
    )


def replay_cached_official_cards(
    cards: Iterable[DiscoveryCard | Mapping[str, Any]],
    cached_cards: Iterable[DiscoveryCard | Mapping[str, Any]],
) -> list[DiscoveryCard]:
    """Replay official evidence locally without issuing adapter requests.

    The cached artifact may have been generated by an older merge policy that
    copied official measurements onto the listing-facing card fields.  The
    fresh listing cards therefore remain authoritative for display, while
    official values are reconstructed under ``official_*`` and conflicts are
    retained for later manual review.
    """

    materialized = [
        normalize_discovery_card(card if isinstance(card, DiscoveryCard) else DiscoveryCard.model_validate(card))
        for card in cards
    ]
    cached = [
        card if isinstance(card, DiscoveryCard) else DiscoveryCard.model_validate(card)
        for card in cached_cards
    ]
    by_key: dict[str, DiscoveryCard] = {}
    for card in cached:
        for key in _card_cache_keys(card):
            by_key.setdefault(key, card)

    def present(value: Any) -> bool:
        return value not in (None, "", [], {})

    def same(left: Any, right: Any) -> bool:
        if isinstance(left, (int, float)) and isinstance(right, (int, float)):
            return float(left) == float(right)
        return left == right

    replayed: list[DiscoveryCard] = []
    for card in materialized:
        cached_card = next((by_key[key] for key in _card_cache_keys(card) if key in by_key), None)
        if cached_card is None:
            replayed.append(normalize_discovery_card(card))
            continue

        source_raw = dict(card.raw or {})
        cached_raw = dict(cached_card.raw or {})
        for key, value in cached_raw.items():
            if key.startswith("official_") and present(value):
                source_raw[key] = value
        cached_evidence = cached_raw.get("_official_evidence", [])
        if isinstance(cached_evidence, list):
            source_raw["_official_evidence"] = cached_evidence
            conflicts = source_raw.setdefault("_official_conflicts", [])
            if not isinstance(conflicts, list):
                conflicts = []
                source_raw["_official_conflicts"] = conflicts
            for evidence in cached_evidence:
                fields = evidence.get("fields", {}) if isinstance(evidence, Mapping) else {}
                if not isinstance(fields, Mapping):
                    continue
                for key, value in fields.items():
                    if not present(value):
                        continue
                    target = _official_field_name(str(key))
                    source_raw[target] = value
                    direct_target = _OFFICIAL_DIRECT_FIELDS.get(str(key))
                    listing_value = getattr(card, direct_target, None) if direct_target else None
                    if present(listing_value) and not same(listing_value, value):
                        conflict = {"field": target, "left": listing_value, "right": value}
                        if conflict not in conflicts:
                            conflicts.append(conflict)
        cached_conflicts = cached_raw.get("_official_conflicts", [])
        if isinstance(cached_conflicts, list):
            conflicts = source_raw.setdefault("_official_conflicts", [])
            if not isinstance(conflicts, list):
                conflicts = []
                source_raw["_official_conflicts"] = conflicts
            for conflict in cached_conflicts:
                if conflict not in conflicts:
                    conflicts.append(conflict)
        source_raw["_official_replay"] = True
        replayed.append(normalize_discovery_card(card.model_copy(update={"raw": source_raw}, deep=True)))
    return replayed


def enrich_discovery_cards(
    cards: Iterable[DiscoveryCard | Mapping[str, Any]],
    config: Mapping[str, Any],
    *,
    observed_at: datetime | None = None,
    adapters: list[Any] | None = None,
) -> DiscoveryOfficialEnrichmentResult:
    """Run all configured official adapters before any screening takes place."""

    materialized = [
        normalize_discovery_card(card if isinstance(card, DiscoveryCard) else DiscoveryCard.model_validate(card))
        for card in cards
    ]
    observed = _aware(observed_at)
    section = config.get("official_enrichment", {})
    section = section if isinstance(section, Mapping) else {}
    if adapters is None:
        from .pipeline import build_official_enrichment_adapters

        discovery_section = config.get("discovery_official_enrichment", {})
        discovery_section = discovery_section if isinstance(discovery_section, Mapping) else {}
        configured_names = discovery_section.get("source_names")
        source_names = (
            {str(name) for name in configured_names if str(name).strip()}
            if isinstance(configured_names, (list, tuple, set))
            else None
        )
        adapters = build_official_enrichment_adapters(dict(config), source_names=source_names)
    if not adapters or not bool(section.get("enabled", True)):
        return DiscoveryOfficialEnrichmentResult(cards=materialized, status="SKIPPED_DISABLED")

    max_properties = int(section.get("max_properties_per_run", 0) or 0)
    target_cards = materialized if max_properties <= 0 else materialized[:max_properties]
    batch_size = max(1, int(section.get("discovery_batch_size", 20) or 20))
    by_id = {_property_id(card): card for card in target_cards}
    source_runs: list[SourceRunResult] = []
    evidence: list[dict[str, Any]] = []
    warnings: list[str] = []
    status_counts: dict[str, int] = {}
    field_coverage: dict[str, int] = {}
    enriched_ids: set[str] = set()

    for start in range(0, len(target_cards), batch_size):
        batch_cards = target_cards[start : start + batch_size]
        for adapter in adapters:
            # Earlier adapters (notably catchment) may add official fields
            # consumed by a later adapter (notably school performance).
            records = [_as_property_record(by_id[_property_id(card)]) for card in batch_cards]
            try:
                batch = adapter.collect_for_records(records, observed)
            except Exception as exc:
                batch = OfficialEnrichmentBatch(
                    source_run=_source_run_failure(adapter, records, observed, exc),
                    rows=[
                        OfficialLookupRow(
                            property_id=record.property_id,
                            status=OfficialQueryStatus.FAILED,
                            source_url=str(getattr(adapter, "source_url", "")),
                            warnings=[f"{type(exc).__name__}: {exc}"],
                        )
                        for record in records
                    ],
                )
                warnings.append(f"{getattr(adapter, 'name', 'official')}: {type(exc).__name__}: {exc}")
            source_runs.append(batch.source_run)
            rows = {row.property_id: row for row in batch.rows}
            for record, card in zip(records, batch_cards):
                row = rows.get(record.property_id)
                if row is None:
                    row = OfficialLookupRow(
                        property_id=record.property_id,
                        status=OfficialQueryStatus.FAILED,
                        source_url=str(getattr(adapter, "source_url", "")),
                        warnings=["adapter returned no row for requested property"],
                    )
                status = row.status.value
                status_counts[status] = status_counts.get(status, 0) + 1
                evidence.append({
                    "property_id": record.property_id,
                    "address": card.address,
                    "adapter": str(getattr(adapter, "name", "official")),
                    "status": status,
                    "source_url": row.source_url,
                    "warnings": list(row.warnings),
                })
                updated = _merge_row(by_id[record.property_id], row, adapter)
                by_id[record.property_id] = updated
                if row.status is OfficialQueryStatus.SUCCESS:
                    enriched_ids.add(record.property_id)
                    for field in row.fields:
                        field_coverage[field] = field_coverage.get(field, 0) + 1

    final_cards = [by_id.get(_property_id(card), card) for card in materialized]
    successful_adapters = sum(1 for run in source_runs if run.status is SourceRunStatus.SUCCESS)
    status = "COMPLETED" if source_runs and successful_adapters == len(source_runs) else "PARTIAL"
    return DiscoveryOfficialEnrichmentResult(
        cards=final_cards,
        source_runs=source_runs,
        evidence=evidence,
        status=status,
        attempted_card_count=len(target_cards),
        enriched_card_count=len(enriched_ids),
        status_counts=status_counts,
        field_coverage=field_coverage,
        warnings=warnings,
    )


__all__ = ["DiscoveryOfficialEnrichmentResult", "enrich_discovery_cards", "normalize_discovery_card", "replay_cached_official_cards"]
