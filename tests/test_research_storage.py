from datetime import datetime, timezone

from northshore_watchdog.research.models import (
    CandidateStatus,
    PropertyObservation,
    PropertyRecord,
    RedevelopmentScenario,
    ScenarioType,
    ScoreSnapshot,
    VerificationItem,
    WatchlistEvent,
)
from northshore_watchdog.research.provenance import DataConflict, SourceRecord, SourceRegistryEntry
from northshore_watchdog.research.enrichment import EnrichedPropertyResult
from northshore_watchdog.research.sources import SourceRunResult, SourceRunStatus
from northshore_watchdog.research.storage import ResearchStorage


def _record(**updates) -> PropertyRecord:
    values = {
        "property_id": "property:stable",
        "address": "123 Stable Street",
        "municipality": "City of Vancouver",
        "neighbourhood": "Knight",
        "asking": 1_500_000,
        "listing_status": "Active",
    }
    values.update(updates)
    return PropertyRecord(**values)


def _observation(record: PropertyRecord, observation_id: str, raw: dict) -> PropertyObservation:
    return PropertyObservation(
        observation_id=observation_id,
        property_id=record.property_id,
        observed_at=datetime(2026, 8, 29, tzinfo=timezone.utc),
        source_registry_id="source:local-case",
        raw_payload=raw,
        parser_version="research-v1",
        normalized_record=record,
    )


def test_research_database_creates_all_append_only_tables(tmp_path):
    storage = ResearchStorage(tmp_path / "research.sqlite")
    names = {
        row[0]
        for row in storage.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }

    assert {
        "properties",
        "property_observations",
        "field_provenance",
        "data_conflicts",
        "comparable_records",
        "redevelopment_scenarios",
        "score_snapshots",
        "watchlist_events",
        "source_registry",
        "weekly_runs",
        "source_runs",
        "enrichment_observations",
        "identity_alias_observations",
    } <= names
    storage.close()


def test_second_observation_preserves_first_payload_and_updates_current_projection(tmp_path):
    storage = ResearchStorage(tmp_path / "research.sqlite")
    first = _record(asking=1_500_000)
    second = _record(asking=1_425_000, listing_status="Price Reduced")

    storage.save_observation(_observation(first, "observation:1", {"asking": 1_500_000}))
    storage.save_observation(_observation(second, "observation:2", {"asking": 1_425_000}))

    rows = storage.conn.execute(
        "SELECT raw_payload FROM property_observations ORDER BY observation_id"
    ).fetchall()
    current = storage.get_current_property("property:stable")
    events = storage.list_watchlist_events("property:stable")

    assert len(rows) == 2
    assert '1,500,000' not in rows[0][0]  # JSON stores the raw integer without formatting.
    assert '"asking": 1500000' in rows[0][0]
    assert current is not None
    assert current.asking == 1_425_000
    assert {event.event_type for event in events} >= {"first_seen", "price_change", "status_change"}
    storage.close()


def test_refresh_cannot_clear_unresolved_manual_verification_fields(tmp_path):
    storage = ResearchStorage(tmp_path / "research.sqlite")
    first = _record(
        manual_verification_required=True,
        manual_verification_reasons=["LISTING_SOURCE_CROSS_CHECK_DEFERRED"],
        data_verification_status="PROVISIONAL_SINGLE_SOURCE",
        acquisition_channel="PUBLIC_REALTOR_CA",
        feed_class="PUBLIC_REPUBLISHED",
    )
    refresh = _record(
        manual_verification_required=False,
        manual_verification_reasons=[],
        data_verification_status="UNKNOWN",
        acquisition_channel="UNKNOWN",
        feed_class="UNKNOWN",
    )

    storage.save_observation(_observation(first, "observation:manual", {}))
    storage.save_observation(_observation(refresh, "observation:refresh", {}))

    current = storage.get_current_property(first.property_id)
    assert current is not None
    assert current.manual_verification_required is True
    assert current.manual_verification_reasons == ["LISTING_SOURCE_CROSS_CHECK_DEFERRED"]
    assert current.data_verification_status == "PROVISIONAL_SINGLE_SOURCE"
    assert current.acquisition_channel == "PUBLIC_REALTOR_CA"
    assert current.feed_class == "PUBLIC_REPUBLISHED"
    storage.close()


def test_refresh_can_clear_explicitly_resolved_conflict_flags(tmp_path):
    storage = ResearchStorage(tmp_path / "research.sqlite")
    conflicted = _record(data_conflict=True, major_data_conflict=True)
    resolved = _record(data_conflict=False, major_data_conflict=False)

    storage.save_observation(_observation(conflicted, "observation:conflicted", {}))
    storage.save_observation(_observation(resolved, "observation:resolved", {}))

    current = storage.get_current_property(conflicted.property_id)
    assert current is not None
    assert current.data_conflict is False
    assert current.major_data_conflict is False
    storage.close()


def test_fresh_scored_observation_can_replace_stale_reject_after_conflict_clear(tmp_path):
    storage = ResearchStorage(tmp_path / "research.sqlite")
    stale = _record(
        candidate_status=CandidateStatus.REJECT,
        data_conflict=False,
        major_data_conflict=False,
    )
    refreshed = _record(
        candidate_status=CandidateStatus.NEED_VERIFICATION,
        data_conflict=False,
        major_data_conflict=False,
        total_score=56,
        live_score=56,
        land_score=57,
        investment_score=55,
        data_confidence_score=65,
        last_updated=datetime(2026, 8, 30, tzinfo=timezone.utc),
    )

    storage.save_observation(_observation(stale, "observation:stale-reject", {}))
    storage.save_observation(_observation(refreshed, "observation:scored-refresh", {}))

    current = storage.get_current_property(stale.property_id)
    assert current is not None
    assert current.candidate_status is CandidateStatus.NEED_VERIFICATION
    storage.close()


def test_actionable_verification_queue_deduplicates_manual_refreshes(tmp_path):
    storage = ResearchStorage(tmp_path / "research.sqlite")
    for index, observed_at in enumerate([
        datetime(2026, 8, 29, tzinfo=timezone.utc),
        datetime(2026, 8, 30, tzinfo=timezone.utc),
    ]):
        storage.save_verification_item(VerificationItem(
            verification_id=f"verification:manual:{index}",
            property_id="property:stable",
            source_registry_id="source:realtor-ca-public",
            source_url=f"https://realtor.ca/{index}",
            status="PENDING_MANUAL",
            reason="cross-check deferred",
            observed_at=observed_at,
            details={"verification_type": "MANUAL_PROVISIONAL_LISTING"},
        ))
    storage.save_verification_item(VerificationItem(
        verification_id="verification:official:1",
        property_id="property:stable",
        source_registry_id="source:bca-assessment",
        source_url="https://bcassessment.ca/1",
        status="NEED_VERIFICATION",
        reason="manual lookup required",
        observed_at=datetime(2026, 8, 30, tzinfo=timezone.utc),
        details={"verification_type": "OFFICIAL_ASSESSMENT"},
    ))

    actionable = storage.list_actionable_verification_items()
    assert len(actionable) == 2
    assert {item.verification_id for item in actionable} == {
        "verification:manual:1",
        "verification:official:1",
    }
    storage.close()


def test_property_observation_persists_weekly_run_lineage(tmp_path):
    storage = ResearchStorage(tmp_path / "research.sqlite")
    record = _record()

    storage.save_observation(_observation(record, "observation:weekly-lineage", {}), weekly_run_id=7)

    row = storage.conn.execute(
        "SELECT weekly_run_id FROM property_observations WHERE observation_id = ?",
        ("observation:weekly-lineage",),
    ).fetchone()
    assert row[0] == 7
    storage.close()


def test_provenance_conflicts_scenarios_and_score_snapshots_are_persisted(tmp_path):
    storage = ResearchStorage(tmp_path / "research.sqlite")
    record = _record()
    storage.save_observation(_observation(record, "observation:1", {"address": record.address}))
    provenance = SourceRecord(
        property_id=record.property_id,
        field_name="pid",
        source_url="https://example.gov/property/1",
        source_type="OFFICIAL_GOV",
        retrieved_at=datetime(2026, 8, 29, tzinfo=timezone.utc),
        raw_value=" PID-1 ",
        normalized_value="PID-1",
    )
    storage.save_provenance(provenance)
    conflict = DataConflict(
        conflict_id="conflict:1",
        property_id=record.property_id,
        field_name="frontage_ft",
        conflict_type="VALUE_MISMATCH",
        source_values={"official": 33, "aggregator": 40},
        conflicting_sources=["official", "aggregator"],
    )
    storage.save_conflict(conflict)
    scenario = RedevelopmentScenario(
        property_id=record.property_id,
        scenario_type=ScenarioType.KEEP_EXISTING_HOUSE,
        legal_status="CURRENT_USE",
    )
    storage.save_scenario(scenario, recorded_at=datetime(2026, 8, 30, tzinfo=timezone.utc))
    snapshot = ScoreSnapshot(
        property_id=record.property_id,
        generated_at=datetime(2026, 8, 29, tzinfo=timezone.utc),
        scoring_version="research-v1",
        config_hash="abc123",
        total_score=71,
        live_score=75,
        land_score=70,
        investment_score=68,
        data_confidence_score=80,
        data_confidence_band="Medium",
    )
    storage.save_score_snapshot(snapshot)

    assert storage.conn.execute("SELECT COUNT(*) FROM field_provenance").fetchone()[0] == 1
    assert storage.conn.execute("SELECT COUNT(*) FROM data_conflicts").fetchone()[0] == 1
    assert storage.conn.execute("SELECT COUNT(*) FROM redevelopment_scenarios").fetchone()[0] == 1
    assert storage.conn.execute("SELECT recorded_at FROM redevelopment_scenarios").fetchone()[0] == "2026-08-30T00:00:00+00:00"
    assert len(storage.list_score_snapshots(record.property_id)) == 1
    event_types = {event.event_type for event in storage.list_watchlist_events(record.property_id)}
    assert {"conflict_opened"} <= event_types
    storage.close()


def test_field_provenance_persists_weekly_and_source_run_lineage(tmp_path):
    storage = ResearchStorage(tmp_path / "research.sqlite")
    provenance = SourceRecord(
        property_id="property:stable",
        field_name="pid",
        source_url="https://example.gov/property/1",
        source_type="OFFICIAL_GOV",
        source_registry_id="source:official",
        source_run_id=11,
        weekly_run_id=7,
        retrieved_at=datetime(2026, 8, 29, tzinfo=timezone.utc),
        source_version="title-v1",
    )

    storage.save_provenance(provenance)

    row = storage.conn.execute(
        "SELECT source_run_id, weekly_run_id, source_registry_id, source_version FROM field_provenance"
    ).fetchone()
    assert tuple(row) == (11, 7, "source:official", "title-v1")
    storage.close()


def test_score_snapshots_and_conflict_resolution_events_are_not_overwritten(tmp_path):
    storage = ResearchStorage(tmp_path / "research.sqlite")
    record = _record()
    storage.save_observation(_observation(record, "observation:1", {}))
    for score in (70, 82):
        storage.save_score_snapshot(
            ScoreSnapshot(
                property_id=record.property_id,
                generated_at=datetime(2026, 8, 29, tzinfo=timezone.utc),
                scoring_version="research-v1",
                config_hash=f"hash-{score}",
                total_score=score,
                live_score=score,
                land_score=score,
                investment_score=score,
                data_confidence_score=90,
                data_confidence_band="High",
            )
        )
    conflict = DataConflict(
        conflict_id="conflict:stable",
        property_id=record.property_id,
        field_name="pid",
        conflict_type="PID_MISMATCH",
        source_values={"a": "1", "b": "2"},
        conflicting_sources=["a", "b"],
    )
    storage.save_conflict(conflict)
    storage.save_conflict(conflict.model_copy(update={"resolution_status": "RESOLVED", "resolution_note": "official source wins"}))

    assert len(storage.list_score_snapshots(record.property_id)) == 2
    events = storage.list_watchlist_events(record.property_id)
    assert [event.event_type for event in events].count("conflict_opened") == 1
    assert [event.event_type for event in events].count("conflict_resolved") == 1
    storage.close()


def test_stable_property_identity_does_not_duplicate_across_listing_cycles(tmp_path):
    storage = ResearchStorage(tmp_path / "research.sqlite")
    first = _record(address="123 Stable Street", asking=1_500_000)
    second = _record(address="123 STABLE ST", asking=1_475_000)
    storage.save_observation(_observation(first, "observation:1", {}))
    storage.save_observation(_observation(second, "observation:2", {}))

    assert storage.conn.execute("SELECT COUNT(*) FROM properties").fetchone()[0] == 1
    assert len(storage.list_watchlist_events(first.property_id)) >= 2
    storage.close()


def test_pidless_address_aliases_share_a_normalized_property_identity(tmp_path):
    from northshore_watchdog.research.normalization import normalize_property

    storage = ResearchStorage(tmp_path / "research.sqlite")
    first = normalize_property({
        "address": "123 Stable Street",
        "municipality": "City of Vancouver",
        "asking": 1_500_000,
    }).property
    second = normalize_property({
        "address": "123 STABLE ST",
        "municipality": "City of Vancouver",
        "asking": 1_475_000,
    }).property
    storage.save_observation(_observation(first, "observation:alias-1", {}))
    storage.save_observation(_observation(second, "observation:alias-2", {}))

    assert first.property_id == second.property_id
    assert storage.conn.execute("SELECT COUNT(*) FROM properties").fetchone()[0] == 1
    storage.close()


def test_identity_aliases_preserve_address_first_identity_when_pid_arrives(tmp_path):
    storage = ResearchStorage(tmp_path / "research.sqlite")
    address_record = _record(property_id="property:address", pid=None)
    pid_record = _record(property_id="property:pid", pid="PID-1", pids=["PID-1"])

    storage.register_identity_aliases(address_record, observed_at=datetime(2026, 8, 29, tzinfo=timezone.utc))
    aliases = storage.list_identity_aliases()

    assert aliases["address:123 stable st city of vancouver"] == "property:address"
    assert "pid:PID-1" not in aliases
    storage.register_identity_aliases(pid_record.model_copy(update={"property_id": "property:address"}), observed_at=datetime(2026, 8, 30, tzinfo=timezone.utc))
    assert storage.list_identity_aliases()["pid:PID-1"] == "property:address"
    storage.close()


def test_identity_alias_observations_preserve_historical_and_conflicting_mls_values(tmp_path):
    storage = ResearchStorage(tmp_path / "research.sqlite")
    first = _record(property_id="property:first", mls_number="R1000001")
    historical = _record(property_id="property:first", mls_number="R1000002")
    conflicting = _record(property_id="property:second", mls_number="R1000001", address="999 Other Street")

    storage.register_identity_aliases(first, observed_at=datetime(2026, 8, 28, tzinfo=timezone.utc))
    storage.register_identity_aliases(historical, observed_at=datetime(2026, 8, 29, tzinfo=timezone.utc))
    storage.register_identity_aliases(conflicting, observed_at=datetime(2026, 8, 30, tzinfo=timezone.utc))

    rows = storage.list_identity_alias_observations(alias_key="mls:R1000001")

    assert [(row["property_id"], row["alias_value"]) for row in rows] == [
        ("property:first", "R1000001"),
        ("property:second", "R1000001"),
    ]
    assert storage.list_identity_aliases()["mls:R1000001"] == "property:first"
    assert storage.list_identity_aliases()["mls:R1000002"] == "property:first"
    storage.close()


def test_operational_runs_enrichment_and_current_projection_are_queryable(tmp_path):
    storage = ResearchStorage(tmp_path / "research.sqlite")
    record = _record()
    storage.save_observation(_observation(record, "observation:1", {}))
    source_run = SourceRunResult(
        source_registry_id="source:fixture",
        source_name="fixture",
        status=SourceRunStatus.SUCCESS,
        weekly_run_id=7,
        started_at=datetime(2026, 8, 29, tzinfo=timezone.utc),
        finished_at=datetime(2026, 8, 29, tzinfo=timezone.utc),
        records=[{"property_id": record.property_id}],
    )
    storage.register_source(
        SourceRegistryEntry(
            source_registry_id="source:fixture",
            name="fixture",
            source_type="MLS",
            access_method="local_fixture",
            authorization_status="NOT_APPLICABLE",
        )
    )
    storage.save_source_run(source_run)
    enrichment = EnrichedPropertyResult(property=record, warnings=["fixture"])
    storage.save_enrichment_observation(enrichment, observed_at=datetime(2026, 8, 29, tzinfo=timezone.utc))
    storage.save_comparable(record.property_id, {"comparable_id": "comp-1", "sold_price": 1_600_000})
    storage.record_watchlist_events(
        [
            WatchlistEvent(
                property_id=record.property_id,
                event_type="custom_delta",
                created_at=datetime(2026, 8, 29, tzinfo=timezone.utc),
                payload={"ok": True},
            )
        ]
    )

    assert [item.property_id for item in storage.list_current_properties()] == [record.property_id]
    assert storage.conn.execute("SELECT COUNT(*) FROM source_runs").fetchone()[0] == 1
    assert storage.conn.execute("SELECT weekly_run_id FROM source_runs").fetchone()[0] == 7
    registry = storage.conn.execute(
        "SELECT last_run_status FROM source_registry WHERE source_registry_id = ?",
        ("source:fixture",),
    ).fetchone()
    assert registry[0] == "SUCCESS"
    assert storage.conn.execute("SELECT COUNT(*) FROM enrichment_observations").fetchone()[0] == 1
    assert storage.conn.execute("SELECT comp_id FROM comparable_records").fetchone()[0] == "comp-1"
    assert any(event.event_type == "custom_delta" for event in storage.list_watchlist_events(record.property_id))
    storage.close()


def test_append_only_research_rows_keep_weekly_source_lineage(tmp_path):
    storage = ResearchStorage(tmp_path / "research.sqlite")
    record = _record()
    storage.save_observation(_observation(record, "observation:lineage", {}), weekly_run_id=7)
    enrichment = EnrichedPropertyResult(property=record)
    storage.save_enrichment_observation(
        enrichment,
        observed_at=datetime(2026, 8, 29, tzinfo=timezone.utc),
        weekly_run_id=7,
        source_registry_id="source:official",
        source_version="official-v1",
    )
    storage.save_comparable(
        record.property_id,
        {"comparable_id": "comp-lineage", "sold_price": 1_600_000},
        weekly_run_id=7,
        source_registry_id="source:comps",
        source_version="comps-v1",
    )
    storage.save_scenario(
        RedevelopmentScenario(
            property_id=record.property_id,
            scenario_type=ScenarioType.KEEP_EXISTING_HOUSE,
        ),
        recorded_at=datetime(2026, 8, 29, tzinfo=timezone.utc),
        weekly_run_id=7,
        source_registry_id="source:official",
        source_version="bylaw-v1",
    )
    storage.record_watchlist_event(
        WatchlistEvent(
            property_id=record.property_id,
            event_type="lineage_test",
            created_at=datetime(2026, 8, 29, tzinfo=timezone.utc),
            weekly_run_id=7,
            source_registry_id="source:comps",
        )
    )

    assert storage.conn.execute("SELECT weekly_run_id FROM comparable_records").fetchone()[0] == 7
    assert storage.conn.execute("SELECT source_registry_id FROM comparable_records").fetchone()[0] == "source:comps"
    assert storage.conn.execute("SELECT source_version FROM redevelopment_scenarios").fetchone()[0] == "bylaw-v1"
    assert storage.conn.execute("SELECT weekly_run_id FROM enrichment_observations").fetchone()[0] == 7
    assert storage.conn.execute("SELECT weekly_run_id FROM watchlist_events WHERE event_type='lineage_test'").fetchone()[0] == 7
    storage.close()


def test_open_conflicts_are_listed_without_erasing_history(tmp_path):
    storage = ResearchStorage(tmp_path / "research.sqlite")
    record = _record()
    storage.save_observation(_observation(record, "observation:1", {}))
    conflict = DataConflict(
        conflict_id="conflict:open",
        property_id=record.property_id,
        field_name="pid",
        conflict_type="PID_MISMATCH",
        source_values={"a": "1", "b": "2"},
        conflicting_sources=["a", "b"],
    )
    storage.save_conflict(conflict)
    storage.save_conflict(conflict.model_copy(update={"resolution_status": "RESOLVED"}))

    assert storage.list_open_conflicts() == []
    rows = storage.conn.execute("SELECT COUNT(*) FROM data_conflicts").fetchone()[0]
    assert rows == 1
    storage.close()


def test_conflict_value_changes_are_append_only_in_conflict_history(tmp_path):
    storage = ResearchStorage(tmp_path / "research.sqlite")
    record = _record()
    storage.save_observation(_observation(record, "observation:conflict-history", {}))
    conflict = DataConflict(
        conflict_id="conflict:history",
        property_id=record.property_id,
        field_name="pid",
        conflict_type="PID_MISMATCH",
        source_values={"a": "1", "b": "2"},
        conflicting_sources=["a", "b"],
    )
    storage.save_conflict(conflict, weekly_run_id=7)
    storage.save_conflict(
        conflict.model_copy(update={"source_values": {"a": "1", "b": "3"}}),
        weekly_run_id=8,
    )

    rows = storage.conn.execute(
        "SELECT source_values, weekly_run_id FROM data_conflict_history ORDER BY id"
    ).fetchall()
    assert len(rows) == 2
    assert '"b": "2"' in rows[0][0]
    assert '"b": "3"' in rows[1][0]
    assert rows[1][1] == 8
    storage.close()


def test_single_watchlist_event_is_committed_before_storage_close(tmp_path):
    path = tmp_path / "research.sqlite"
    storage = ResearchStorage(path)
    storage.record_watchlist_event(
        "property:stable",
        "manual_review",
        {"reason": "title verification"},
        created_at=datetime(2026, 8, 29, tzinfo=timezone.utc),
    )
    storage.close()

    with ResearchStorage(path) as reopened:
        events = reopened.list_watchlist_events("property:stable")
    assert [event.event_type for event in events] == ["manual_review"]


def test_sparse_stage_observation_does_not_erase_current_enrichment(tmp_path):
    storage = ResearchStorage(tmp_path / "research.sqlite")
    enriched = _record(zoning="R1-1", official_zoning_confirmed=True, asking=1_500_000)
    sparse = _record(asking=1_425_000)

    storage.save_observation(_observation(enriched, "observation:enriched", {"zoning": "R1-1"}))
    storage.save_observation(_observation(sparse, "observation:sparse", {"asking": 1_425_000}))

    current = storage.get_current_property(enriched.property_id)
    assert current is not None
    assert current.asking == 1_425_000
    assert current.zoning == "R1-1"
    storage.close()


def test_sparse_observation_does_not_reset_explicit_zero_or_false_values(tmp_path):
    storage = ResearchStorage(tmp_path / "research.sqlite")
    enriched = _record(relist_count=2, corner_lot=True)
    sparse = _record(asking=1_425_000)

    storage.save_observation(_observation(enriched, "observation:enriched", {"relist_count": 2}))
    storage.save_observation(_observation(sparse, "observation:sparse", {"asking": 1_425_000}))

    current = storage.get_current_property(enriched.property_id)
    assert current is not None
    assert current.relist_count == 2
    assert current.corner_lot is True
    storage.close()


def test_older_observation_is_kept_in_history_but_cannot_rewind_projection(tmp_path):
    storage = ResearchStorage(tmp_path / "research.sqlite")
    newer = _record(asking=1_500_000)
    older = _record(asking=1_425_000)
    storage.save_observation(_observation(newer, "observation:newer", {"asking": 1_500_000}).model_copy(update={"observed_at": datetime(2026, 8, 30, tzinfo=timezone.utc)}))
    storage.save_observation(_observation(older, "observation:older", {"asking": 1_425_000}).model_copy(update={"observed_at": datetime(2026, 8, 29, tzinfo=timezone.utc)}))

    current = storage.get_current_property(newer.property_id)
    assert current is not None
    assert current.asking == 1_500_000
    assert storage.conn.execute("SELECT COUNT(*) FROM property_observations").fetchone()[0] == 2
    storage.close()
