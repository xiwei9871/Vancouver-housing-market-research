from datetime import datetime, timezone
import json

from northshore_watchdog.research.models import SourceType
from northshore_watchdog.research.sources import (
    AuthorizedJsonSource,
    LocalJsonSource,
    SourceRunStatus,
)
from northshore_watchdog.research.pipeline import build_source_adapters


OBSERVED_AT = datetime(2026, 8, 30, tzinfo=timezone.utc)


def test_disabled_source_does_not_invoke_transport():
    calls = []
    source = AuthorizedJsonSource(
        name="disabled-api",
        source_type=SourceType.MLS,
        config={"enabled": False, "authorization_status": "AUTHORIZED"},
        transport=lambda: calls.append("called"),
    )

    result = source.collect(OBSERVED_AT)

    assert result.status is SourceRunStatus.SKIPPED_DISABLED
    assert calls == []
    assert result.records == []


def test_unauthorized_source_does_not_invoke_transport():
    calls = []
    source = AuthorizedJsonSource(
        name="unauthorized-api",
        source_type=SourceType.MLS,
        config={"enabled": True, "authorization_status": "UNKNOWN"},
        transport=lambda: calls.append("called"),
    )

    result = source.collect(OBSERVED_AT)

    assert result.status is SourceRunStatus.SKIPPED_UNAUTHORIZED
    assert calls == []


def test_local_json_source_returns_rows_and_source_metadata(tmp_path):
    payload_path = tmp_path / "listings.json"
    payload_path.write_text(
        json.dumps(
            [
                {
                    "property_id": "pid:1",
                    "address": "1 Example Street",
                    "municipality": "City of Vancouver",
                    "source_url": "local://listings#1",
                }
            ]
        ),
        encoding="utf-8",
    )
    source = LocalJsonSource(
        name="fixture-listings",
        path=payload_path,
        source_type=SourceType.MLS,
    )

    result = source.collect(OBSERVED_AT)

    assert result.status is SourceRunStatus.SUCCESS
    assert result.records[0]["source_url"] == "local://listings#1"
    assert result.records[0]["source_type"] == "MLS"
    assert result.records[0]["observed_at"] == OBSERVED_AT.isoformat()


def test_local_jsonl_source_reads_one_json_object_per_line(tmp_path):
    payload_path = tmp_path / "listings.jsonl"
    payload_path.write_text(
        '{"address": "1 JSONL Street", "municipality": "City of Vancouver"}\n'
        '{"address": "2 JSONL Street", "municipality": "City of Vancouver"}\n',
        encoding="utf-8",
    )
    source = LocalJsonSource(name="jsonl-listings", path=payload_path, source_type=SourceType.MLS)

    result = source.collect(OBSERVED_AT)

    assert result.status is SourceRunStatus.SUCCESS
    assert [row["address"] for row in result.records] == ["1 JSONL Street", "2 JSONL Street"]


def test_local_json_source_replaces_null_source_metadata_with_adapter_defaults(tmp_path):
    payload_path = tmp_path / "listings.json"
    payload_path.write_text(
        json.dumps([{
            "address": "2 Example Street",
            "municipality": "City of Vancouver",
            "source_url": None,
            "source_type": None,
            "observed_at": None,
        }]),
        encoding="utf-8",
    )
    source = LocalJsonSource(name="fixture-null-metadata", path=payload_path, source_type=SourceType.MLS)

    result = source.collect(OBSERVED_AT)

    assert result.records[0]["source_url"] == "local://" + str(payload_path)
    assert result.records[0]["source_type"] == "MLS"
    assert result.records[0]["observed_at"] == OBSERVED_AT.isoformat()


def test_local_source_envelope_cannot_be_overridden_by_row_metadata(tmp_path):
    payload_path = tmp_path / "spoofed.json"
    payload_path.write_text(
        json.dumps([{
            "address": "1 Example Street",
            "source_type": "OFFICIAL_GOV",
            "source_registry_id": "source:fake-official",
            "source_url": "https://fake.gov/title",
        }]),
        encoding="utf-8",
    )

    result = LocalJsonSource(
        name="trusted-listings",
        path=payload_path,
        source_type=SourceType.MLS,
        source_registry_id="source:trusted-listings",
    ).collect(OBSERVED_AT)

    assert result.records[0]["source_type"] == "MLS"
    assert result.records[0]["source_registry_id"] == "source:trusted-listings"
    assert result.records[0]["source_url"] == "https://fake.gov/title"


def test_registry_metadata_is_authoritative_for_adapter_identity(tmp_path):
    registry_path = tmp_path / "registry.yaml"
    registry_path.write_text(
        """
sources:
  - source_registry_id: source:registered
    name: Registered feed
    source_type: OFFICIAL_GOV
    authorization_status: AUTHORIZED
    enabled: true
""",
        encoding="utf-8",
    )
    listing_path = tmp_path / "listings.json"
    listing_path.write_text("[]", encoding="utf-8")
    config = {
        "source_registry_path": str(registry_path),
        "acquisition": {
            "sources": [{
                "name": "registered",
                "type": "LOCAL_JSON",
                "path": str(listing_path),
                "source_registry_id": "source:registered",
                "source_type": "MLS",
            }],
        },
    }

    adapter = build_source_adapters(config)[0]

    assert adapter.source_type is SourceType.OFFICIAL_GOV
    assert adapter.source_registry_id == "source:registered"
    assert adapter.config["authorization_status"] == "AUTHORIZED"


def test_local_json_source_marks_malformed_rows_partial(tmp_path):
    payload_path = tmp_path / "listings.json"
    payload_path.write_text(
        json.dumps(
            [
                {"address": "1 Good Street", "municipality": "City of Vancouver"},
                "not-a-row",
            ]
        ),
        encoding="utf-8",
    )
    source = LocalJsonSource(name="bad-fixture", path=payload_path)

    result = source.collect(OBSERVED_AT)

    assert result.status is SourceRunStatus.PARTIAL
    assert len(result.records) == 1
    assert result.errors


def test_disabled_local_source_does_not_read_its_file(tmp_path):
    source = LocalJsonSource(
        name="disabled-local",
        path=tmp_path / "missing.json",
        config={"enabled": False},
    )

    result = source.collect(OBSERVED_AT)

    assert result.status is SourceRunStatus.SKIPPED_DISABLED
    assert result.records == []


def test_authorized_injected_transport_is_the_only_remote_execution_path():
    calls = []

    def transport():
        calls.append("called")
        return [{"address": "1 Authorized Street", "municipality": "City of Vancouver"}]

    source = AuthorizedJsonSource(
        name="authorized-api",
        source_type=SourceType.VOW,
        config={"enabled": True, "authorization_status": "AUTHORIZED"},
        transport=transport,
    )

    result = source.collect(OBSERVED_AT)

    assert calls == ["called"]
    assert result.status is SourceRunStatus.SUCCESS
    assert result.records[0]["source_type"] == "VOW"


def test_unsupported_source_type_is_visible_as_a_failed_source_run():
    adapters = build_source_adapters(
        {"acquisition": {"sources": [{"name": "future-adapter", "type": "NOT_IMPLEMENTED"}]}}
    )

    assert len(adapters) == 1
    result = adapters[0].collect(OBSERVED_AT)

    assert result.status is SourceRunStatus.FAILED
    assert "unsupported source type" in result.errors[0]


def test_legacy_source_without_injected_collector_is_visible_as_failed():
    adapters = build_source_adapters(
        {"acquisition": {"sources": [{"name": "missing-legacy", "type": "LEGACY_COLLECTOR"}]}}
    )

    assert len(adapters) == 1
    result = adapters[0].collect(OBSERVED_AT)

    assert result.status is SourceRunStatus.FAILED
    assert "unsupported source type" in result.errors[0]
