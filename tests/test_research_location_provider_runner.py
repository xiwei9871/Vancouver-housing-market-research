import json
from datetime import datetime, timezone

from northshore_watchdog.research.location_providers.runner import enrich_properties, write_provider_artifacts
from northshore_watchdog.research.location_providers.store import LocationSnapshotStore
from northshore_watchdog.research.location_providers.walk_score import WalkScoreProvider


def _records():
    return [
        {"property_id": "mls:R1", "address": "5454 MELBOURNE STREET", "mls": "R1", "latitude": 49.235, "longitude": -123.03},
        {"property_id": "mls:R2", "address": "4561 NANAIMO STREET", "mls": "R2", "latitude": 49.245, "longitude": -123.04},
    ]


def test_enrich_properties_writes_unknown_snapshots_without_credentials(tmp_path):
    store = LocationSnapshotStore(tmp_path / "snapshots.jsonl")
    snapshots = enrich_properties(
        _records(), walk_score=WalkScoreProvider(api_key=None), store=store,
        observed_at=datetime(2026, 9, 8, tzinfo=timezone.utc),
    )
    assert len(snapshots) == 2
    assert all(item.composite.status == "UNKNOWN" for item in snapshots)
    assert all(item.walk_score.status == "NOT_CONFIGURED" for item in snapshots)
    assert len(store.all()) == 2


def test_artifact_writer_uses_walk_score_only_schema(tmp_path):
    store = LocationSnapshotStore(tmp_path / "snapshots.jsonl")
    snapshots = enrich_properties(
        _records(), walk_score=WalkScoreProvider(api_key=None), store=store,
        observed_at=datetime(2026, 9, 8, tzinfo=timezone.utc),
    )
    output = write_provider_artifacts(snapshots, tmp_path / "location-provider")
    assert {path.name for path in output.values()} == {
        "provider-comparison.md", "provider-schema.json", "benchmark-results.csv",
        "double-counting-audit.md", "provider-recommendation.md",
    }
    schema = json.loads(output["schema"].read_text())
    assert schema["model_name"] == "FAMILY_DETACHED_WALK_SCORE"
    assert schema["required_external_scores"] == ["walk", "transit", "bike"]
    assert "Local Logic" not in output["audit"].read_text()


def test_invalid_coordinates_do_not_abort_batch_and_unidentified_rows_do_not_share_cache_id(tmp_path):
    records = [
        {"address": "", "latitude": "UNKNOWN", "longitude": ""},
        {"address": "", "latitude": None, "longitude": None},
    ]
    store = LocationSnapshotStore(tmp_path / "snapshots.jsonl")
    snapshots = enrich_properties(
        records, walk_score=WalkScoreProvider(api_key="unused"), store=store,
        observed_at=datetime(2026, 9, 8, tzinfo=timezone.utc),
    )
    assert len(snapshots) == 2
    assert snapshots[0].walk_score.status == "INVALID_REQUEST"
    assert snapshots[1].walk_score.status == "INVALID_REQUEST"
    assert snapshots[0].property_id != snapshots[1].property_id


def test_cache_mismatch_on_coordinates_is_refreshed(tmp_path):
    store = LocationSnapshotStore(tmp_path / "snapshots.jsonl")
    first = enrich_properties(
        [{"property_id": "mls:R1", "address": "1 TEST STREET", "latitude": 49.2, "longitude": -123.1}],
        walk_score=WalkScoreProvider(api_key=None), store=store,
        observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )[0]
    refreshed = enrich_properties(
        [{"property_id": "mls:R1", "address": "1 TEST STREET", "latitude": 49.3, "longitude": -123.1}],
        walk_score=WalkScoreProvider(api_key=None), store=store,
        observed_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
    )[0]
    assert first.latitude == 49.2
    assert refreshed.latitude == 49.3
    assert len(store.all()) == 2


def test_location_provider_config_is_walk_score_only_and_externalized():
    from northshore_watchdog.config import load_config

    config = load_config("config/research-discovery.yaml")["location_providers"]
    assert config["model_name"] == "FAMILY_DETACHED_WALK_SCORE"
    assert config["cache_ttl_days"] == 180
    assert config["walk_score"]["api_key_env"] == "WALK_SCORE_API_KEY"
    assert config["walk_score"]["enabled"] is True
    assert "api_key" not in config["walk_score"]
    assert "local_logic" not in config
