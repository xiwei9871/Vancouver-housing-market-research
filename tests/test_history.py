import json
from datetime import datetime, timezone

from northshore_watchdog.history import HistoryListing, RadarHistory


START = datetime(2026, 8, 30, 12, tzinfo=timezone.utc)
END = datetime(2026, 8, 30, 12, 1, tzinfo=timezone.utc)


def history_listing(identity_key: str, address: str, price: int | None) -> HistoryListing:
    return HistoryListing(
        identity_key=identity_key,
        address=address,
        city="North Vancouver",
        price=price,
        grade="Review",
        score_total=60,
        payload={"identity_key": identity_key, "address": address, "price": price},
    )


def test_first_successful_run_is_a_baseline(tmp_path):
    history = RadarHistory(tmp_path / "radar.sqlite", radar="purchase")
    current = [history_listing("mls:R1", "1 Main Street", 1_500_000)]

    delta = history.compare(current)
    run_id = history.record_success(
        started_at=START,
        finished_at=END,
        source_files=[{"path": "fresh.html", "sha256": "abc"}],
        input_fingerprint="fingerprint-1",
        entries=current,
        delta=delta,
    )

    assert run_id == 1
    assert delta.baseline is True
    assert delta.new == []
    assert history.latest_successful_run_id() == 1
    history.close()


def test_second_run_reports_new_price_changes_and_not_seen(tmp_path):
    history = RadarHistory(tmp_path / "radar.sqlite", radar="purchase")
    previous = [
        history_listing("mls:R1", "1 Main Street", 1_500_000),
        history_listing("mls:R2", "2 Main Street", 1_600_000),
    ]
    baseline = history.compare(previous)
    history.record_success(START, END, [], "one", previous, baseline)

    current = [
        history_listing("mls:R1", "1 Main Street", 1_400_000),
        history_listing("mls:R3", "3 Main Street", 1_700_000),
    ]
    delta = history.compare(current)

    assert [item.identity_key for item in delta.new] == ["mls:R3"]
    assert [(item.previous_price, item.current_price) for item in delta.price_reduced] == [
        (1_500_000, 1_400_000)
    ]
    assert delta.price_increased == []
    assert [item.identity_key for item in delta.not_seen] == ["mls:R2"]
    history.close()


def test_price_increase_and_unknown_prices_do_not_create_false_changes(tmp_path):
    history = RadarHistory(tmp_path / "radar.sqlite", radar="rental")
    previous = [
        history_listing("url:r1", "1 Main Street", 2_500),
        history_listing("url:r2", "2 Main Street", None),
    ]
    history.record_success(START, END, [], "one", previous, history.compare(previous))

    current = [
        history_listing("url:r1", "1 Main Street", 2_700),
        history_listing("url:r2", "2 Main Street", 2_600),
    ]
    delta = history.compare(current)

    assert [(item.previous_price, item.current_price) for item in delta.price_increased] == [(2_500, 2_700)]
    assert delta.price_reduced == []
    history.close()


def test_stale_or_error_runs_are_not_used_as_comparison_baselines(tmp_path):
    history = RadarHistory(tmp_path / "radar.sqlite", radar="purchase")
    first = [history_listing("mls:R1", "1 Main Street", 1_500_000)]
    history.record_success(START, END, [], "one", first, history.compare(first))
    history.record_run(
        started_at=END,
        finished_at=END,
        status="stale",
        data_status="stale",
        source_files=[],
        input_fingerprint="stale",
        entries=[],
        delta=None,
    )

    current = [history_listing("mls:R1", "1 Main Street", 1_400_000)]
    delta = history.compare(current)

    assert delta.baseline is False
    assert [(item.previous_price, item.current_price) for item in delta.price_reduced] == [
        (1_500_000, 1_400_000)
    ]
    history.close()


def test_recorded_delta_and_entries_are_append_only_and_json_serializable(tmp_path):
    database = tmp_path / "radar.sqlite"
    history = RadarHistory(database, radar="purchase")
    entries = [history_listing("mls:R1", "1 Main Street", 1_500_000)]
    delta = history.compare(entries)
    history.record_success(START, END, [{"path": "a.html"}], "fingerprint", entries, delta)
    history.record_success(END, END, [{"path": "b.html"}], "fingerprint-2", entries, history.compare(entries))

    rows = history.conn.execute("select count(*) from radar_runs").fetchone()[0]
    listing_rows = history.conn.execute("select count(*) from radar_run_listings").fetchone()[0]
    stored_delta = history.conn.execute("select delta from radar_runs order by id limit 1").fetchone()[0]

    assert rows == 2
    assert listing_rows == 2
    assert json.loads(stored_delta)["baseline"] is True
    history.close()
