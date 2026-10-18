from datetime import datetime, timedelta, timezone

import httpx

from northshore_watchdog.research.location_providers.base import LocationSnapshot, ProviderResult
from northshore_watchdog.research.location_providers.scoring import REQUIRED_EXTERNAL_SCORES, build_location_composite, normalize_walk_score
from northshore_watchdog.research.location_providers.store import LocationSnapshotStore
from northshore_watchdog.research.location_providers.walk_score import WalkScoreProvider


def test_walk_score_only_composite_uses_fixed_urban_access_weights():
    assert REQUIRED_EXTERNAL_SCORES == ("walk", "transit", "bike")
    result = build_location_composite({"walk": 100, "transit": 100, "bike": 100})
    assert result.status == "COMPLETE"
    assert result.model_name == "FAMILY_DETACHED_WALK_SCORE"
    assert result.external_location_score == 100.0
    assert result.urban_access_score == 100.0
    assert result.residential_environment_score is None
    assert result.residential_environment_status == "NOT_AVAILABLE"


def test_walk_score_composite_applies_walk_transit_bike_weights():
    result = build_location_composite({"walk": 80, "transit": 60, "bike": 40})
    assert result.external_location_score == 67.5
    assert result.urban_access_score == 67.5
    assert result.normalized_scores == {"walk": 80.0, "transit": 60.0, "bike": 40.0}


def test_walk_score_missing_core_score_is_unknown_not_zero():
    result = build_location_composite({"walk": 100, "transit": 100})
    assert result.status == "UNKNOWN"
    assert result.external_location_score is None
    assert result.urban_access_score is None
    assert result.residential_environment_score is None
    assert result.missing_scores == ["bike"]


def test_walk_score_normalization_rejects_invalid_scale():
    assert normalize_walk_score("91") == 91.0


def test_walk_score_provider_returns_canonical_scores_from_response():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["transit"] == "1"
        assert request.url.params["bike"] == "1"
        return httpx.Response(200, json={
            "status": 1, "walkscore": 91, "updated": "2026-09-08 00:00:00",
            "transit": {"score": 83}, "bike": {"score": 78},
        })

    provider = WalkScoreProvider(api_key="test-key", client=httpx.Client(transport=httpx.MockTransport(handler)))
    result = provider.get_location_scores(address="1 Test Street, Vancouver, BC", latitude=49.25, longitude=-123.1)
    assert result.status == "OK"
    assert result.scores == {"walk": 91.0, "transit": 83.0, "bike": 78.0}
    assert "wsapikey" not in (result.source_url or "")


def test_provider_without_credentials_is_explicitly_not_configured():
    result = WalkScoreProvider(api_key=None).get_location_scores(address="1 Test Street", latitude=49.25, longitude=-123.1)
    assert result.status == "NOT_CONFIGURED"
    assert result.scores == {}


def test_snapshot_store_is_append_only_and_refreshes_not_configured(tmp_path):
    store = LocationSnapshotStore(tmp_path / "location-snapshots.jsonl")
    first = LocationSnapshot(
        property_id="mls:R1", address="1 Test Street",
        queried_at=datetime.now(timezone.utc) - timedelta(days=1),
        walk_score=ProviderResult(provider="walk_score", status="NOT_CONFIGURED"),
    )
    second = first.model_copy(update={
        "queried_at": datetime.now(timezone.utc),
        "walk_score": ProviderResult(provider="walk_score", status="OK", scores={"walk": 90.0, "transit": 80.0, "bike": 70.0}),
    })
    store.append(first)
    store.append(second)
    assert len(store.all()) == 2
    assert store.latest("mls:R1").walk_score.status == "OK"
    assert store.should_refresh(first, now=datetime.now(timezone.utc)) is True
    assert store.should_refresh(second, now=datetime.now(timezone.utc)) is False
