from datetime import datetime, timezone

from northshore_watchdog.research.discovery import DiscoveryCard
from northshore_watchdog.research.discovery_official import (
    enrich_discovery_cards,
    normalize_discovery_card,
    replay_cached_official_cards,
)
from northshore_watchdog.research.official_enrichment import OfficialEnrichmentBatch, OfficialLookupRow
from northshore_watchdog.research.models import OfficialQueryStatus, SourceType
from northshore_watchdog.research.sources import SourceRunResult, SourceRunStatus


OBSERVED_AT = datetime(2026, 9, 4, tzinfo=timezone.utc)


def _card() -> DiscoveryCard:
    return DiscoveryCard(
        address="100 Official Data Street",
        municipality="Vancouver",
        mls_number="R3000999",
        asking=1_600_000,
        bedrooms=4,
        bathrooms=3,
        finished_sqft=2_000,
        lot_dimensions="33 x 122 ft",
        property_type="DETACHED_HOUSE",
        ownership_type="FREEHOLD",
        listing_status="ACTIVE",
        source_url="https://example.test/listing/R3000999",
    )


class _FakeOfficialAdapter:
    name = "fake-official"
    source_registry_id = "source:fake-official"
    source_type = SourceType.OFFICIAL_GOV
    source_url = "https://official.example.test/lookup"

    def collect_for_records(self, records, observed_at):
        assert len(records) == 1
        row = OfficialLookupRow(
            property_id=records[0].property_id,
            status=OfficialQueryStatus.SUCCESS,
            fields={
                "assessment_total": 2_000_000,
                "assessment_land": 1_700_000,
                "assessment_improvement": 300_000,
                "zoning": "R1-1",
                "official_elementary_catchment": "Official Elementary",
                "official_secondary_catchment": "Official Secondary",
                "nearest_major_road": "Quiet Local Street",
                "distance_to_major_road_m": 300,
                "property_fronts_major_road": False,
                "road_class": "E",
                "source_url": self.source_url,
            },
            source_url=self.source_url,
            retrieved_at=observed_at,
            source_version="test-official-v1",
            match_method="TEST_IDENTITY",
        )
        source_run = SourceRunResult(
            source_registry_id=self.source_registry_id,
            source_name=self.name,
            source_type=self.source_type,
            status=SourceRunStatus.SUCCESS,
            started_at=observed_at,
            finished_at=observed_at,
            records=[row.model_dump(mode="json")],
        )
        return OfficialEnrichmentBatch(source_run=source_run, rows=[row])


class _ConflictingOfficialAdapter(_FakeOfficialAdapter):
    name = "fake-conflicting-official"
    source_registry_id = "source:fake-conflicting-official"

    def collect_for_records(self, records, observed_at):
        batch = super().collect_for_records(records, observed_at)
        row = batch.rows[0].model_copy(update={"fields": {**batch.rows[0].fields, "bedrooms": 2}})
        return OfficialEnrichmentBatch(
            source_run=batch.source_run,
            rows=[row],
        )


def test_discovery_cards_are_enriched_from_official_adapters_before_scoring():
    config = {
        "official_enrichment": {
            "enabled": True,
            "max_properties_per_run": 0,
            "discovery_batch_size": 20,
        }
    }

    result = enrich_discovery_cards(
        [_card()],
        config,
        observed_at=OBSERVED_AT,
        adapters=[_FakeOfficialAdapter()],
    )

    assert result.cards[0].raw["official_zoning"] == "R1-1"
    assert result.cards[0].raw["official_elementary_catchment"] == "Official Elementary"
    assert result.cards[0].raw["official_road_class"] == "E"
    assert result.cards[0].raw["official_distance_to_major_road_m"] == 300
    assert result.cards[0].raw["official_assessment_total"] == 2_000_000
    assert result.cards[0].elementary_school == "Official Elementary"
    assert result.cards[0].lot_area_sqft == 4_026
    assert result.evidence[0]["status"] == "SUCCESS"
    assert result.source_runs[0].source_name == "fake-official"


def test_discovery_card_repairs_implausible_area_using_listing_dimensions():
    card = _card()
    card = card.model_copy(update={"lot_area_sqft": 1, "lot_dimensions": "33 x 122 ft"})

    normalized = normalize_discovery_card(card)

    assert normalized.lot_area_sqft == 4_026
    assert normalized.lot_area_source == "CALCULATED_FROM_DIMENSIONS_CONFLICT_REPAIRED"
    assert normalized.raw["lot_area_original_sqft"] == 1
    assert "LOT_AREA_REPAIRED_FROM_DIMENSIONS" in normalized.reason_codes


def test_official_bedroom_conflict_is_recorded_without_overwriting_listing_value():
    result = enrich_discovery_cards(
        [_card()],
        {
            "official_enrichment": {
                "enabled": True,
                "max_properties_per_run": 0,
                "discovery_batch_size": 20,
            }
        },
        observed_at=OBSERVED_AT,
        adapters=[_ConflictingOfficialAdapter()],
    )

    card = result.cards[0]
    assert card.bedrooms == 4
    assert card.raw["official_bedrooms"] == 2
    assert any(item["field"] == "official_bedrooms" for item in card.raw["_official_conflicts"])


def test_cached_official_cards_replay_onto_fresh_listing_cards_without_remote_queries():
    listing_card = _card()
    cached_card = _card().model_copy(update={
        "bedrooms": 2,
        "raw": {
            "official_assessment_total": 2_000_000,
            "official_bedrooms": 2,
            "_official_evidence": [
                {
                    "adapter": "fake-official",
                    "status": "SUCCESS",
                    "fields": {"assessment_total": 2_000_000, "bedrooms": 2},
                }
            ],
        },
    })

    replayed = replay_cached_official_cards([listing_card], [cached_card])

    assert len(replayed) == 1
    assert replayed[0].bedrooms == 4
    assert replayed[0].raw["official_bedrooms"] == 2
    assert replayed[0].raw["official_assessment_total"] == 2_000_000
    assert replayed[0].raw["_official_replay"] is True
