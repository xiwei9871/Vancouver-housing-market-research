from datetime import datetime, timezone
import json

from northshore_watchdog.research.enrichment import JsonMappingProvider, enrich_property
from northshore_watchdog.research.models import ApprovedUse, EvidenceStatus, OwnershipType, PropertyRecord, SourceType
from northshore_watchdog.research.provenance import SourceRegistryEntry


OBSERVED_AT = datetime(2026, 8, 30, tzinfo=timezone.utc)


def _confirmed_registry(*providers):
    return {
        provider.source_registry_id: SourceRegistryEntry(
            source_registry_id=provider.source_registry_id,
            name=provider.provider_name,
            source_type=provider.source_type,
            authorization_status="NOT_APPLICABLE",
            evidence_status=EvidenceStatus.CONFIRMED,
        )
        for provider in providers
    }


def _record(**updates):
    values = {
        "property_id": "pid:ENRICH-1",
        "address": "1 Enrichment Street",
        "municipality": "City of Vancouver",
        "neighbourhood": "Knight",
        "asking": 1_500_000,
        "listing_status": "Active",
    }
    values.update(updates)
    return PropertyRecord(**values)


def test_enrichment_updates_only_evidenced_fields_and_keeps_unknowns(tmp_path):
    assessment_path = tmp_path / "assessment.json"
    assessment_path.write_text(
        json.dumps(
            [
                {
                    "property_id": "pid:ENRICH-1",
                    "assessment_total": 1_700_000,
                    "assessment_land": 1_500_000,
                    "assessment_year": 2026,
                    "source_url": "local://assessment/1",
                }
            ]
        ),
        encoding="utf-8",
    )
    zoning_path = tmp_path / "zoning.json"
    zoning_path.write_text(
        json.dumps(
            [
                {
                    "property_id": "pid:ENRICH-1",
                    "zoning": "R1-1",
                    "approved_use": "Single Detached",
                    "minimum_frontage": 10.0,
                    "building_permits": [{"permit": "BP-1", "status": "closed"}],
                    "occupancy_permits": [{"permit": "OP-1", "status": "issued"}],
                    "secondary_suite_permits": [{"permit": "SP-1", "status": "issued"}],
                    "two_family_dwelling_status": "not approved",
                    "outstanding_permits": [],
                    "bylaw_violations": [],
                    "source_url": "local://zoning/1",
                }
            ]
        ),
        encoding="utf-8",
    )

    result = enrich_property(
        _record(),
        providers=[
            assessment_provider := JsonMappingProvider(name="assessment", path=assessment_path, source_type=SourceType.BC_ASSESSMENT),
            zoning_provider := JsonMappingProvider(name="zoning", path=zoning_path, source_type=SourceType.OFFICIAL_GOV),
        ],
        observed_at=OBSERVED_AT,
        source_registry=_confirmed_registry(assessment_provider, zoning_provider),
    )

    assert result.property.assessment_total == 1_700_000
    assert result.property.ownership_type is OwnershipType.UNKNOWN
    assert result.property.official_zoning_confirmed is True
    assert result.property.approved_use is ApprovedUse.SINGLE_DETACHED
    assert result.property.building_permits == [{"permit": "BP-1", "status": "closed"}]
    assert result.property.occupancy_permits == [{"permit": "OP-1", "status": "issued"}]
    assert result.property.secondary_suite_permits == [{"permit": "SP-1", "status": "issued"}]
    assert result.property.two_family_dwelling_status == "not approved"
    assert result.property.official_elementary_catchment is None
    assert {item.field_name for item in result.provenance} >= {"assessment_total", "zoning"}


def test_bc_assessment_identity_fields_are_merged_and_confirm_pid(tmp_path):
    provider = JsonMappingProvider(
        name="bca-assessment",
        source_type=SourceType.BC_ASSESSMENT,
        source_registry_id="source:bca-assessment",
        data=[{
            "property_id": "pid:ENRICH-1",
            "fields": {
                "pid": "014-801-850",
                "legal_description": "LOT 20, BLOCK 3, PLAN VAP1383",
                "pids": ["014-801-850"],
                "number_of_pids": 1,
            },
            "source_url": "https://www.bcassessment.ca/Property/PropertyInformation",
        }],
    )

    result = enrich_property(
        _record(),
        providers=[provider],
        observed_at=OBSERVED_AT,
        source_registry=_confirmed_registry(provider),
    )

    assert result.property.pid == "014-801-850"
    assert result.property.pids == ["014-801-850"]
    assert result.property.number_of_pids == 1
    assert result.property.legal_description == "LOT 20, BLOCK 3, PLAN VAP1383"
    assert result.property.official_pid_confirmed is True
    assert result.property.official_title_source_registry_id == "source:bca-assessment"


def test_jsonl_enrichment_provider_maps_one_json_object_per_line(tmp_path):
    path = tmp_path / "assessment.jsonl"
    path.write_text(
        '{"property_id": "pid:ENRICH-1", "assessment_total": 1700000}\n'
        '{"property_id": "pid:ENRICH-2", "assessment_total": 1800000}\n',
        encoding="utf-8",
    )

    provider = JsonMappingProvider(name="assessment", path=path, source_type=SourceType.BC_ASSESSMENT)
    result = enrich_property(
        _record(),
        providers=[provider],
        observed_at=OBSERVED_AT,
        source_registry=_confirmed_registry(provider),
    )

    assert result.property.assessment_total == 1_700_000
    assert result.provider_results[0].status == "SUCCESS"


def test_listing_source_cannot_set_official_zoning_or_approved_use(tmp_path):
    path = tmp_path / "listing-copy.json"
    path.write_text(
        json.dumps(
            [
                {
                    "property_id": "pid:ENRICH-1",
                    "zoning": "R1-1",
                    "approved_use": "Duplex",
                    "source_url": "local://listing/1",
                }
            ]
        ),
        encoding="utf-8",
    )

    result = enrich_property(
        _record(),
        providers=[JsonMappingProvider(name="listing-copy", path=path, source_type=SourceType.MLS)],
        observed_at=OBSERVED_AT,
    )

    assert result.property.zoning is None
    assert result.property.approved_use is ApprovedUse.UNKNOWN
    assert result.property.official_zoning_confirmed is not True


def test_non_official_provider_cannot_claim_zoning_evidence_flags(tmp_path):
    path = tmp_path / "untrusted-zoning.json"
    path.write_text(
        json.dumps(
            [
                {
                    "property_id": "pid:ENRICH-1",
                    "zoning": "R1-1",
                    "maximum_units": 6,
                    "bylaw_version": "listing-copy-v1",
                    "official_zoning_confirmed": True,
                    "official_approved_use_confirmed": True,
                    "source_type": "OFFICIAL_GOV",
                    "source_registry_id": "source:fake-official",
                    "source_url": "local://listing/claim",
                }
            ]
        ),
        encoding="utf-8",
    )

    result = enrich_property(
        _record(),
        providers=[JsonMappingProvider(name="listing-copy", path=path, source_type=SourceType.MLS)],
        observed_at=OBSERVED_AT,
    )

    assert result.property.zoning is None
    assert result.property.maximum_units is None
    assert result.property.bylaw_version is None
    assert result.property.official_zoning_confirmed is not True
    assert result.property.official_approved_use_confirmed is not True


def test_non_official_provider_cannot_supply_assessment_history_or_municipal_costs(tmp_path):
    path = tmp_path / "untrusted-costs.json"
    path.write_text(
        json.dumps([{
            "property_id": "pid:ENRICH-1",
            "assessment_history": [{"year": 2025, "total": 1_600_000}],
            "annual_municipal_carrying_cost": 9999,
            "pure_property_tax": 9000,
            "toa": True,
            "toa_tier": "Tier 2",
            "assembly_score": 90,
            "heritage_status": "character",
            "infill_options": "retention",
            "official_elementary_catchment": "Listing Elementary",
            "current_school_score": 100,
            "road_class": "E",
            "distance_to_major_road_m": 999,
            "outdoor_noise_exposure": 0,
            "source_url": "local://listing/claims",
        }]),
        encoding="utf-8",
    )

    result = enrich_property(
        _record(),
        providers=[JsonMappingProvider(name="listing-copy", path=path, source_type=SourceType.MLS)],
        observed_at=OBSERVED_AT,
    )

    assert result.property.assessment_history == []
    assert result.property.annual_municipal_carrying_cost is None
    assert result.property.pure_property_tax is None
    assert result.property.toa is None
    assert result.property.toa_tier is None
    assert result.property.assembly_score is None
    assert result.property.heritage_status is None
    assert result.property.infill_options is None
    assert result.property.official_elementary_catchment is None
    assert result.property.current_school_score is None
    assert result.property.road_class is None
    assert result.property.distance_to_major_road_m is None
    assert result.property.outdoor_noise_exposure is None


def test_enrichment_detects_conflicting_values_and_leaves_field_unmerged(tmp_path):
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    first.write_text(json.dumps([{"property_id": "pid:ENRICH-1", "frontage_ft": 33}]), encoding="utf-8")
    second.write_text(json.dumps([{"property_id": "pid:ENRICH-1", "frontage_ft": 40}]), encoding="utf-8")

    first_provider = JsonMappingProvider(name="official-a", path=first, source_type=SourceType.OFFICIAL_GOV)
    second_provider = JsonMappingProvider(name="official-b", path=second, source_type=SourceType.OFFICIAL_GOV)
    result = enrich_property(
        _record(),
        providers=[first_provider, second_provider],
        observed_at=OBSERVED_AT,
        source_registry=_confirmed_registry(first_provider, second_provider),
    )

    assert result.property.frontage_ft is None
    assert result.conflicts
    assert result.property.data_conflict is True


def test_missing_provider_is_non_fatal_and_preserves_unknowns(tmp_path):
    result = enrich_property(
        _record(),
        providers=[JsonMappingProvider(name="missing", path=tmp_path / "missing.json")],
        observed_at=OBSERVED_AT,
    )

    assert result.property.assessment_total is None
    assert result.provider_results[0].status == "FAILED"
    assert result.warnings


def test_enrichment_maps_toa_and_municipal_carrying_cost_components(tmp_path):
    path = tmp_path / "location-costs.json"
    path.write_text(
        json.dumps([{
            "property_id": "pid:ENRICH-1",
            "distance_to_SkyTrain": 420,
            "toa_tier": "Tier 2",
            "assembly_requirement": "not required for single lot",
            "pure_property_tax": 8000,
            "water": 900,
            "sewer": 700,
            "garbage": 300,
            "drainage": 100,
            "other_levies": 50,
            "source_url": "local://municipal/location-costs",
            "source_type": "OFFICIAL_GOV",
        }]),
        encoding="utf-8",
    )

    provider = JsonMappingProvider(name="municipal", path=path, source_type=SourceType.OFFICIAL_GOV)
    result = enrich_property(
        _record(),
        providers=[provider],
        observed_at=OBSERVED_AT,
        source_registry=_confirmed_registry(provider),
    )

    assert result.property.distance_to_skytrain_m == 420
    assert result.property.toa_tier == "Tier 2"
    assert result.property.assembly_requirement == "not required for single lot"
    assert result.property.pure_property_tax == 8000
    assert result.property.utilities == 2050
    assert result.property.annual_municipal_carrying_cost == 10050


def test_history_enrichment_keeps_source_lists_for_audit(tmp_path):
    path = tmp_path / "history.json"
    path.write_text(
        json.dumps([{
            "property_id": "pid:ENRICH-1",
            "previous_listing_prices": [1_600_000],
            "previous_sale_dates": ["2020-01-01T00:00:00+00:00"],
            "previous_sale_prices": [1_200_000],
            "source_url": "local://history/one",
            "source_type": "MLS",
        }]),
        encoding="utf-8",
    )

    result = enrich_property(
        _record(),
        providers=[JsonMappingProvider(name="history", path=path, source_type=SourceType.MLS)],
        observed_at=OBSERVED_AT,
    )

    assert result.property.previous_listing_sources == ["local://history/one"]
    assert result.property.sold_history_sources == ["local://history/one"]


def test_history_enrichment_records_sources_when_checked_fields_are_empty(tmp_path):
    listing_path = tmp_path / "checked-listing.json"
    listing_path.write_text(
        json.dumps([{
            "property_id": "pid:ENRICH-1",
            "listing_status": "Sold",
            "source_url": "local://listing/checked",
        }]),
        encoding="utf-8",
    )
    sold_path = tmp_path / "checked-history.json"
    sold_path.write_text(
        json.dumps([{
            "property_id": "pid:ENRICH-1",
            "sold_history_scope": "last_three_full_calendar_years",
            "source_url": "local://history/checked",
        }]),
        encoding="utf-8",
    )

    listing_provider = JsonMappingProvider(name="checked-listing", path=listing_path, source_type=SourceType.MLS)
    sold_provider = JsonMappingProvider(name="checked-history", path=sold_path, source_type=SourceType.BC_ASSESSMENT)
    result = enrich_property(
        _record(),
        providers=[listing_provider, sold_provider],
        observed_at=OBSERVED_AT,
        source_registry=_confirmed_registry(listing_provider, sold_provider),
    )

    assert result.property.previous_listing_sources == ["local://listing/checked"]
    assert result.property.sold_history_sources == ["local://history/checked"]


def test_confirmed_bca_bedroom_difference_is_reconciled_when_both_values_meet_minimum(tmp_path):
    listing_path = tmp_path / "bedroom-listing.json"
    listing_path.write_text(
        json.dumps([{
            "property_id": "pid:ENRICH-1",
            "bedrooms": 3,
            "source_url": "local://listing/bedrooms",
        }]),
        encoding="utf-8",
    )
    assessment_path = tmp_path / "bedroom-assessment.json"
    assessment_path.write_text(
        json.dumps([{
            "property_id": "pid:ENRICH-1",
            "bedrooms": 4,
            "source_url": "local://assessment/bedrooms",
        }]),
        encoding="utf-8",
    )
    listing_provider = JsonMappingProvider(name="listing", path=listing_path, source_type=SourceType.MLS)
    assessment_provider = JsonMappingProvider(
        name="assessment",
        path=assessment_path,
        source_type=SourceType.BC_ASSESSMENT,
    )

    result = enrich_property(
        _record(bedrooms=3),
        providers=[listing_provider, assessment_provider],
        observed_at=OBSERVED_AT,
        config={"research_policy": {"current_house_minimums": {"bedrooms": 3}}},
        source_registry=_confirmed_registry(listing_provider, assessment_provider),
    )

    assert result.property.bedrooms == 4
    assert result.property.data_conflict is False
    assert result.property.major_data_conflict is not True
    assert result.conflicts == []
    assert len(result.resolved_conflicts) == 1
    assert result.resolved_conflicts[0].resolution_status == "RESOLVED"
    assert "BEDROOM_COUNT_RECONCILED" in result.property.data_reconciliation_notes[0]
