from datetime import datetime, timezone
import json
from pathlib import Path

from northshore_watchdog.config import load_config
from northshore_watchdog.research.acquisition import acquire_from_sources
from northshore_watchdog.research.models import CandidateStatus, OwnershipType, SourceRole, SourceType
from northshore_watchdog.research.pipeline import build_source_adapters
from northshore_watchdog.research.pipeline import run_research_pipeline
from northshore_watchdog.research.sources import AuthorizedJsonSource, LocalJsonSource
from northshore_watchdog.research.storage import ResearchStorage


OBSERVED_AT = datetime(2026, 8, 30, tzinfo=timezone.utc)


def _listing_path(tmp_path, *, asking=1_500_000, source_url="https://example.test/listing"):
    path = tmp_path / f"listing-{asking}-{abs(hash(source_url))}.json"
    path.write_text(
        json.dumps(
            [{
                "mls_number": "R9999999",
                "address": "1 Example Street",
                "municipality": "City of Vancouver",
                "neighbourhood": "Knight",
                "property_type": "Detached House",
                "ownership_type": "Freehold Non-Strata",
                "asking": asking,
                "source_url": source_url,
            }]
        ),
        encoding="utf-8",
    )
    return path


def _public_source(tmp_path, *, name, role, source_type, asking, source_url):
    return LocalJsonSource(
        name=name,
        path=_listing_path(tmp_path, asking=asking, source_url=source_url),
        source_type=source_type,
        source_registry_id=f"source:{name}",
        config={"source_role": role},
    )


def test_build_source_adapters_orders_mls_then_realtor_then_zolo(tmp_path):
    config = {
        "acquisition": {
            "priority_order": [
                SourceRole.MLS_PRIMARY.value,
                SourceRole.REALTOR_CA_PUBLIC.value,
                SourceRole.ZOLO_PUBLIC.value,
            ],
            "sources": [
                {
                    "name": "zolo-public",
                    "type": "LOCAL_JSON",
                    "path": str(_listing_path(tmp_path, source_url="https://zolo.ca/1")),
                    "source_type": "AGGREGATOR",
                    "source_role": "ZOLO_PUBLIC",
                },
                {
                    "name": "realtor-public",
                    "type": "LOCAL_JSON",
                    "path": str(_listing_path(tmp_path, source_url="https://realtor.ca/1")),
                    "source_type": "MLS",
                    "source_role": "REALTOR_CA_PUBLIC",
                },
                {
                    "name": "mls-primary",
                    "type": "AUTHORIZED_JSON",
                    "source_type": "MLS",
                    "source_role": "MLS_PRIMARY",
                    "enabled": False,
                    "authorization_status": "UNKNOWN",
                },
            ],
        }
    }

    adapters = build_source_adapters(config)

    assert [adapter.source_name for adapter in adapters] == [
        "mls-primary",
        "realtor-public",
        "zolo-public",
    ]
    assert [adapter.source_role for adapter in adapters] == [
        "MLS_PRIMARY",
        "REALTOR_CA_PUBLIC",
        "ZOLO_PUBLIC",
    ]


def test_unavailable_mls_is_explicit_and_public_sources_are_marked_fallback(tmp_path):
    mls = AuthorizedJsonSource(
        name="mls-primary",
        source_type=SourceType.MLS,
        source_registry_id="source:mls-primary",
        config={
            "enabled": False,
            "authorization_status": "UNKNOWN",
            "source_role": "MLS_PRIMARY",
        },
    )
    realtor = _public_source(
        tmp_path,
        name="realtor-public",
        role="REALTOR_CA_PUBLIC",
        source_type=SourceType.MLS,
        asking=1_500_000,
        source_url="https://www.realtor.ca/real-estate/1/example",
    )
    zolo = _public_source(
        tmp_path,
        name="zolo-public",
        role="ZOLO_PUBLIC",
        source_type=SourceType.AGGREGATOR,
        asking=1_500_000,
        source_url="https://www.zolo.ca/vancouver-real-estate/1-example",
    )

    result = acquire_from_sources(
        [zolo, realtor, mls],
        observed_at=OBSERVED_AT,
        config={
            "acquisition": {
                "priority_order": ["MLS_PRIMARY", "REALTOR_CA_PUBLIC", "ZOLO_PUBLIC"],
                "primary_source_roles": ["MLS_PRIMARY"],
            }
        },
    )

    assert result.primary_source_status == "UNAVAILABLE"
    assert result.fallback_used is True
    assert result.source_priority == ["MLS_PRIMARY", "REALTOR_CA_PUBLIC", "ZOLO_PUBLIC"]
    assert [run.source_role for run in result.source_runs] == [
        "MLS_PRIMARY",
        "REALTOR_CA_PUBLIC",
        "ZOLO_PUBLIC",
    ]
    assert result.source_runs[0].status.value == "SKIPPED_DISABLED"
    assert {record.mls_number for record in result.records} == {"R9999999"}


def test_public_single_source_mode_marks_records_and_creates_manual_queue(tmp_path):
    source = _public_source(
        tmp_path,
        name="realtor-public",
        role="REALTOR_CA_PUBLIC",
        source_type=SourceType.MLS,
        asking=1_500_000,
        source_url="https://www.realtor.ca/real-estate/1/example",
    )

    result = acquire_from_sources(
        [source],
        observed_at=OBSERVED_AT,
        config={
            "acquisition": {
                "priority_order": ["REALTOR_CA_PUBLIC"],
                "allow_single_source_provisional": True,
            }
        },
    )

    record = result.records[0]
    assert record.data_verification_status == "PROVISIONAL_SINGLE_SOURCE"
    assert record.manual_verification_required is True
    assert record.acquisition_channel == "PUBLIC_REALTOR_CA"
    assert record.feed_class == "PUBLIC_REPUBLISHED"
    assert record.provisional_source_registry_ids == ["source:realtor-public"]
    assert result.provisional_property_ids == [record.property_id]


def test_multiple_listing_sources_are_not_labelled_single_source(tmp_path):
    realtor = _public_source(
        tmp_path,
        name="realtor-public",
        role="REALTOR_CA_PUBLIC",
        source_type=SourceType.MLS,
        asking=1_500_000,
        source_url="https://www.realtor.ca/real-estate/1/example",
    )
    zolo = _public_source(
        tmp_path,
        name="zolo-public",
        role="ZOLO_PUBLIC",
        source_type=SourceType.AGGREGATOR,
        asking=1_500_000,
        source_url="https://www.zolo.ca/vancouver-real-estate/1-example",
    )

    result = acquire_from_sources(
        [realtor, zolo],
        observed_at=OBSERVED_AT,
        config={
            "acquisition": {
                "priority_order": ["REALTOR_CA_PUBLIC", "ZOLO_PUBLIC"],
                "allow_single_source_provisional": True,
            }
        },
    )

    record = result.records[0]
    assert record.data_verification_status == "MULTI_SOURCE_UNVERIFIED"
    assert record.manual_verification_required is True
    assert record.acquisition_channel == "MULTI_SOURCE"
    assert record.feed_class == "MULTI_SOURCE_UNVERIFIED"
    assert set(record.provisional_source_registry_ids) == {
        "source:realtor-public",
        "source:zolo-public",
    }


def test_public_legal_claims_remain_unverified_and_cannot_pass_title_gate(tmp_path):
    path = tmp_path / "public-legal-claims.json"
    path.write_text(
        json.dumps([{
            "mls_number": "R9999998",
            "address": "2 Example Street",
            "municipality": "City of Vancouver",
            "neighbourhood": "Knight",
            "property_type": "Detached House",
            "ownership_type": "Freehold Non-Strata",
            "pid": "PID-PUBLIC-CLAIM",
            "legal_description": "LOT 1 BLOCK 2",
            "zoning": "RS-1",
            "approved_use": "Single Detached",
            "asking": 1_500_000,
            "source_url": "https://www.realtor.ca/real-estate/2/example",
        }]),
        encoding="utf-8",
    )
    source = LocalJsonSource(
        name="realtor-public",
        path=path,
        source_type=SourceType.MLS,
        source_registry_id="source:realtor-public",
        config={"source_role": "REALTOR_CA_PUBLIC"},
    )

    result = acquire_from_sources(
        [source],
        observed_at=OBSERVED_AT,
        config={"acquisition": {"priority_order": ["REALTOR_CA_PUBLIC"]}},
    )

    record = result.records[0]
    assert record.pid is None
    assert record.legal_description is None
    assert record.ownership_type is OwnershipType.UNKNOWN
    assert record.zoning is None
    assert record.approved_use.value == "UNKNOWN"
    raw_snapshot = next(item for item in result.provenance if item.field_name == "listing_snapshot")
    assert raw_snapshot.raw_value["pid"] == "PID-PUBLIC-CLAIM"
    assert any(item.field_name == "ownership_type" for item in result.provenance)

    from northshore_watchdog.research.acquisition import scan_stage1

    candidate = scan_stage1(
        [record],
        {
            "market_buckets": {
                "vancouver_east": {
                    "municipality": "City of Vancouver",
                    "neighbourhoods": ["Knight"],
                    "core_range": [1_000_000, 2_000_000],
                    "extended_range": [1_000_000, 2_200_000],
                }
            }
        },
        observed_at=OBSERVED_AT,
    ).candidates[0]
    assert candidate.gate_state == "NEED_VERIFICATION"
    assert candidate.candidate_status is CandidateStatus.NEED_VERIFICATION


def test_reused_mls_number_with_inconsistent_addresses_stays_separate_and_conflicted(tmp_path):
    first_path = _listing_path(tmp_path, source_url="https://realtor.ca/first")
    second_path = tmp_path / "second-address.json"
    second_path.write_text(
        json.dumps([{
            "mls_number": "R9999999",
            "address": "9 Different Street",
            "municipality": "City of Vancouver",
            "neighbourhood": "Knight",
            "property_type": "Detached House",
            "asking": 1_500_000,
            "source_url": "https://zolo.ca/second",
        }]),
        encoding="utf-8",
    )
    first = LocalJsonSource(
        name="realtor-public",
        path=first_path,
        source_type=SourceType.MLS,
        source_registry_id="source:realtor-public",
        config={"source_role": "REALTOR_CA_PUBLIC"},
    )
    second = LocalJsonSource(
        name="zolo-public",
        path=second_path,
        source_type=SourceType.AGGREGATOR,
        source_registry_id="source:zolo-public",
        config={"source_role": "ZOLO_PUBLIC"},
    )

    result = acquire_from_sources(
        [first, second],
        observed_at=OBSERVED_AT,
        config={"acquisition": {"priority_order": ["REALTOR_CA_PUBLIC", "ZOLO_PUBLIC"]}},
    )

    assert len(result.records) == 2
    assert any(conflict.conflict_type == "MLS_IDENTITY_COLLISION" for conflict in result.conflicts)
    assert all(record.data_conflict for record in result.records)


def test_same_address_with_different_mls_numbers_is_a_material_conflict(tmp_path):
    first = _public_source(
        tmp_path,
        name="realtor-public",
        role="REALTOR_CA_PUBLIC",
        source_type=SourceType.MLS,
        asking=1_500_000,
        source_url="https://realtor.ca/same-address",
    )
    second_path = tmp_path / "same-address-different-mls.json"
    second_path.write_text(
        json.dumps([{
            "mls_number": "R8888888",
            "address": "1 Example Street",
            "municipality": "City of Vancouver",
            "neighbourhood": "Knight",
            "property_type": "Detached House",
            "asking": 1_500_000,
            "source_url": "https://zolo.ca/same-address",
        }]),
        encoding="utf-8",
    )
    second = LocalJsonSource(
        name="zolo-public",
        path=second_path,
        source_type=SourceType.AGGREGATOR,
        source_registry_id="source:zolo-public",
        config={"source_role": "ZOLO_PUBLIC"},
    )

    result = acquire_from_sources(
        [first, second],
        observed_at=OBSERVED_AT,
        config={"acquisition": {"priority_order": ["REALTOR_CA_PUBLIC", "ZOLO_PUBLIC"]}},
    )

    assert len(result.records) == 1
    assert any(conflict.field_name == "mls_number" for conflict in result.conflicts)


def test_snapshot_profile_metadata_is_preserved_on_rows(tmp_path):
    source = LocalJsonSource(
        name="realtor-snapshot",
        path=_listing_path(tmp_path),
        source_type=SourceType.MLS,
        config={
            "source_role": "REALTOR_CA_PUBLIC",
            "snapshot_observed_at": "2026-08-30T00:00:00+00:00",
            "source_version": "web-snapshot-2026-08-30",
        },
    )

    run = source.collect(datetime(2026, 9, 1, tzinfo=timezone.utc))

    assert run.records[0]["observed_at"] == "2026-08-30T00:00:00+00:00"
    assert run.records[0]["source_version"] == "web-snapshot-2026-08-30"


def test_source_run_keeps_configured_numeric_priority_for_same_role(tmp_path):
    first = LocalJsonSource(
        name="realtor-one",
        path=_listing_path(tmp_path, source_url="https://realtor.ca/one"),
        source_type=SourceType.MLS,
        config={"source_role": "REALTOR_CA_PUBLIC", "priority": 7},
    )
    second = LocalJsonSource(
        name="realtor-two",
        path=_listing_path(tmp_path, source_url="https://realtor.ca/two"),
        source_type=SourceType.MLS,
        config={"source_role": "REALTOR_CA_PUBLIC", "priority": 9},
    )

    result = acquire_from_sources(
        [second, first],
        observed_at=OBSERVED_AT,
        config={"acquisition": {"priority_order": ["REALTOR_CA_PUBLIC"]}},
    )

    assert [run.priority for run in result.source_runs] == [7, 9]


def test_available_mls_primary_wins_over_public_field_value(tmp_path):
    mls = AuthorizedJsonSource(
        name="mls-primary",
        source_type=SourceType.MLS,
        source_registry_id="source:mls-primary",
        config={
            "enabled": True,
            "authorization_status": "AUTHORIZED",
            "source_role": "MLS_PRIMARY",
        },
        transport=lambda: [{
            "mls_number": "R9999999",
            "address": "1 Example Street",
            "municipality": "City of Vancouver",
            "property_type": "Detached House",
            "ownership_type": "Freehold Non-Strata",
            "asking": 1_600_000,
            "source_url": "mls://R9999999",
        }],
    )
    public = LocalJsonSource(
        name="realtor-public",
        path=_listing_path(
            Path(tmp_path),
            asking=1_500_000,
            source_url="https://www.realtor.ca/real-estate/1/example",
        ),
        source_type=SourceType.MLS,
        source_registry_id="source:realtor-public",
        config={"source_role": "REALTOR_CA_PUBLIC"},
    )

    result = acquire_from_sources(
        [public, mls],
        observed_at=OBSERVED_AT,
        config={
            "acquisition": {
                "priority_order": ["MLS_PRIMARY", "REALTOR_CA_PUBLIC"],
                "primary_source_roles": ["MLS_PRIMARY"],
            }
        },
    )

    assert result.primary_source_status == "AVAILABLE"
    assert result.fallback_used is False
    assert result.records[0].asking == 1_600_000


def test_same_mls_public_cross_check_conflict_is_retained(tmp_path):
    realtor = _public_source(
        tmp_path,
        name="realtor-public",
        role="REALTOR_CA_PUBLIC",
        source_type=SourceType.MLS,
        asking=1_500_000,
        source_url="https://www.realtor.ca/real-estate/1/example",
    )
    zolo = _public_source(
        tmp_path,
        name="zolo-public",
        role="ZOLO_PUBLIC",
        source_type=SourceType.AGGREGATOR,
        asking=1_425_000,
        source_url="https://www.zolo.ca/vancouver-real-estate/1-example",
    )

    result = acquire_from_sources(
        [realtor, zolo],
        observed_at=OBSERVED_AT,
        config={
            "acquisition": {
                "priority_order": ["REALTOR_CA_PUBLIC", "ZOLO_PUBLIC"],
                "primary_source_roles": ["MLS_PRIMARY"],
            }
        },
    )

    assert result.duplicate_count == 1
    assert any(conflict.field_name == "asking" for conflict in result.conflicts)
    asking_conflict = next(conflict for conflict in result.conflicts if conflict.field_name == "asking")
    assert set(asking_conflict.conflicting_sources) == {
        "https://www.realtor.ca/real-estate/1/example",
        "https://www.zolo.ca/vancouver-real-estate/1-example",
    }
    assert result.records[0].data_conflict is True
    assert result.records[0].asking == 1_500_000


def test_mls_first_profile_contains_bounded_public_http_sources():
    config = load_config("config/research-mls-first.yaml")

    assert config["acquisition"]["priority_order"] == [
        "MLS_PRIMARY",
        "REALTOR_CA_PUBLIC",
        "ZOLO_PUBLIC",
    ]
    assert [item["name"] for item in config["acquisition"]["sources"]] == [
        "mls-primary",
        "realtor-ca-public",
        "zolo-public",
    ]
    assert config["acquisition"]["sources"][0]["enabled"] is False
    assert config["acquisition"]["sources"][1]["source_role"] == "REALTOR_CA_PUBLIC"
    assert config["acquisition"]["sources"][2]["source_role"] == "ZOLO_PUBLIC"
    public_sources = config["acquisition"]["sources"][1:]
    assert all(item["type"] == "PUBLIC_HTTP" for item in public_sources)
    assert all(item["coverage_complete"] is False for item in public_sources)
    assert all(item["respect_robots"] is True for item in public_sources)
    assert all(item["max_requests_per_run"] > 0 for item in public_sources)
    assert all(item["source_version"] == "public-feed-2026-08-31" for item in public_sources)


def test_mls_first_public_http_run_writes_explicit_source_policy_report(tmp_path):
    config = load_config("config/research-mls-first.yaml")
    config["storage"]["sqlite_path"] = str(tmp_path / "research.sqlite")
    config["reports"]["weekly_dir"] = str(tmp_path / "weekly")
    config["reports"]["latest_path"] = str(tmp_path / "latest.md")
    for definition in config["acquisition"]["sources"][1:]:
        definition["max_requests_per_run"] = 1
        definition["raw_snapshot_dir"] = str(tmp_path / f"raw-{definition['name']}")

    def allowed_robots(url):
        return {
            "url": url,
            "status_code": 200,
            "headers": {"content-type": "text/plain"},
            "content": b"User-agent: *\\nAllow: /\\n",
        }

    def empty_public_response(request):
        return {
            "url": request["url"],
            "status_code": 200,
            "headers": {"content-type": "application/json"},
            "content": b'{"Results": []}',
            "method": request.get("method", "GET"),
        }

    config["_public_robots_transports"] = {
        "realtor-ca-public": allowed_robots,
        "zolo-public": allowed_robots,
    }
    config["_public_transports"] = {
        "realtor-ca-public": empty_public_response,
        "zolo-public": empty_public_response,
    }

    result = run_research_pipeline(
        config,
        observed_at=OBSERVED_AT,
        stages={"acquire", "report"},
    )

    assert result.status == "PARTIAL"
    assert result.primary_source_status == "UNAVAILABLE"
    assert result.fallback_used is True
    assert result.summary["properties_scanned"] == 0
    assert result.summary["special_situation_count"] == 0
    assert result.summary["SPECIAL_SITUATION"] == 0
    assert not any(conflict.field_name == "address" for conflict in result.conflicts)
    assert result.report_path and result.report_path.exists()
    report = result.report_path.read_text(encoding="utf-8")
    assert "## Acquisition Source Policy" in report
    assert "primary_source_status: UNAVAILABLE" in report
    assert "role=MLS_PRIMARY" in report
    assert "role=REALTOR_CA_PUBLIC" in report
    assert "role=ZOLO_PUBLIC" in report
    assert "realtor-ca-public: role=REALTOR_CA_PUBLIC" in report
    assert "status=PARTIAL" in report
    assert "coverage=UNKNOWN" in report
    with ResearchStorage(tmp_path / "research.sqlite") as storage:
        registry_roles = {
            row["source_registry_id"]: json.loads(row["payload"])["source_role"]
            for row in storage.conn.execute(
                "SELECT source_registry_id, payload FROM source_registry"
            ).fetchall()
        }
        assert registry_roles["source:mls-primary"] == "MLS_PRIMARY"
        assert registry_roles["source:realtor-ca-public"] == "REALTOR_CA_PUBLIC"
        assert registry_roles["source:zolo-public"] == "ZOLO_PUBLIC"
        run_roles = [
            json.loads(row["payload"])["source_role"]
            for row in storage.conn.execute(
                "SELECT payload FROM source_runs ORDER BY id"
            ).fetchall()
        ]
        assert run_roles == ["MLS_PRIMARY", "REALTOR_CA_PUBLIC", "ZOLO_PUBLIC"]
        snapshots = storage.conn.execute(
            "SELECT source_registry_id, request_kind, status_code, source_version, raw_path FROM source_snapshots ORDER BY id"
        ).fetchall()
        assert len(snapshots) == 4
        assert {row["source_registry_id"] for row in snapshots} == {
            "source:realtor-ca-public",
            "source:zolo-public",
        }
        assert all(row["status_code"] == 200 for row in snapshots)
        assert all(row["source_version"] == "public-feed-2026-08-31" for row in snapshots)
        assert {row["request_kind"] for row in snapshots} == {"target", "robots"}
        assert all(Path(row["raw_path"]).exists() for row in snapshots)
