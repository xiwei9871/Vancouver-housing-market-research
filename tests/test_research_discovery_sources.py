from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from northshore_watchdog.research import discovery_sources as discovery_sources_module
from northshore_watchdog.research import public_feeds
from northshore_watchdog.research.discovery import (
    DiscoveryCapture,
    DiscoveryFilterPlan,
    DiscoveryCard,
    DiscoveryStatus,
    validate_capture,
)
from northshore_watchdog.research.discovery_browser import (
    PlaywrightNativeDiscoverySource,
    _merge_detail_card,
    _prepare_cdp_page,
    replay_cached_realtor_details,
    _select_cdp_page,
)
from northshore_watchdog.research.realtor_map import build_realtor_ca_map_url
from northshore_watchdog.research.discovery_sources import (
    DiscoverySource,
    HtmlFilteredCaptureSource,
    JsonFilteredCaptureSource,
    parse_filtered_html,
)


OBSERVED_AT = datetime(2026, 9, 3, 1, 30, tzinfo=timezone.utc)
SAMPLES = Path(__file__).parents[1] / "data" / "samples"
FILTERED_CAPTURE = SAMPLES / "discovery_filtered_capture.json"
SOURCE_NAME = "filtered-capture-replay"
SOURCE_URL = "https://listings.example/vancouver/detached"
LIGHTWEIGHT_CARD_FIELDS = {
    "source_name",
    "mls_number",
    "address",
    "municipality",
    "asking",
    "bedrooms",
    "bathrooms",
    "finished_sqft",
    "lot_area_sqft",
    "lot_dimensions",
    "frontage_ft",
    "depth_ft",
    "property_type",
    "ownership_type",
    "listing_status",
    "dom",
    "year_built",
    "taxes",
    "description",
    "neighbourhood",
    "elementary_school",
    "secondary_school",
    "source_url",
    "feed_url",
    "latitude",
    "longitude",
    "observed_at",
    "reason_codes",
    "raw",
}


def _plan(preset_name: str = "detached-core") -> DiscoveryFilterPlan:
    return DiscoveryFilterPlan(
        preset_name=preset_name,
        source_name=SOURCE_NAME,
        native_filters={"property_type": "Detached"},
        post_filters={"minimum_bedrooms": 3},
    )


def _write_json(path: Path, payload: object) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _json_source(path: Path, **config: object) -> JsonFilteredCaptureSource:
    return JsonFilteredCaptureSource(
        SOURCE_NAME,
        path,
        {"source_url": SOURCE_URL, "max_result_cards": 100, **config},
    )


def _write_html(path: Path, content: bytes) -> Path:
    path.write_bytes(content)
    return path


def _html_card_page(card_count: int, *, result_markup: str = "") -> bytes:
    cards = "".join(
        f'''<article class="listing-card" data-address="{index} Bounded Street"
                     data-url="https://listings.example/listing/{index}"></article>'''
        for index in range(1, card_count + 1)
    )
    return f"{result_markup}{cards}".encode("utf-8")


def test_discovery_source_module_exposes_local_capture_api():
    assert DiscoverySource is not None
    assert JsonFilteredCaptureSource is not None
    assert HtmlFilteredCaptureSource is not None
    assert parse_filtered_html is not None


def test_json_replay_fixture_returns_capture_with_selected_preset_and_exact_path():
    capture = _json_source(FILTERED_CAPTURE).execute(_plan(), observed_at=OBSERVED_AT)

    assert isinstance(capture, DiscoveryCapture)
    assert capture.source_name == SOURCE_NAME
    assert capture.preset_name == "detached-core"
    assert capture.source_url == SOURCE_URL
    assert capture.observed_at == OBSERVED_AT
    assert capture.status_code == 200
    assert capture.result_count == 2
    assert capture.count_is_complete is True
    assert capture.applied_filters == {"property_type": "Detached"}
    assert capture.applied_filter_evidence
    assert capture.detail_requests == 0
    assert capture.blocked is False
    assert capture.raw_snapshot_path == str(FILTERED_CAPTURE)
    assert len(capture.cards) == 2
    assert capture.cards[0].source_name == SOURCE_NAME
    assert capture.cards[0].observed_at == OBSERVED_AT
    assert capture.cards[0].source_url.startswith("https://listings.example/listing/")


def test_json_replay_fills_missing_capture_metadata_and_preserves_envelope_fields(tmp_path):
    declared_observed_at = "2026-09-01T08:00:00-07:00"
    payload_path = _write_json(
        tmp_path / "capture.json",
        {
            "source_url": "https://capture.example/filter-results",
            "observed_at": declared_observed_at,
            "status_code": 206,
            "result_count": 1,
            "count_is_complete": True,
            "applied_filters": {"property_type": "Detached"},
            "applied_filter_evidence": {
                "property_type": {"present": True, "label": "Detached"}
            },
            "detail_requests": 3,
            "blocked": False,
            "raw_snapshot_path": "/untrusted/declared/path.json",
            "cards": [{"address": "101 Replay Street"}],
        },
    )

    capture = _json_source(payload_path).execute(_plan(), observed_at=OBSERVED_AT)

    assert capture.source_name == SOURCE_NAME
    assert capture.preset_name == "detached-core"
    assert capture.source_url == "https://capture.example/filter-results"
    assert capture.observed_at.isoformat() == "2026-09-01T15:00:00+00:00"
    assert capture.status_code == 206
    assert capture.result_count == 1
    assert capture.count_is_complete is True
    assert capture.applied_filters == {"property_type": "Detached"}
    assert capture.applied_filter_evidence == {
        "property_type": {"present": True, "label": "Detached"}
    }
    assert capture.detail_requests == 3
    assert capture.blocked is False
    assert capture.raw_snapshot_path == str(payload_path)


def test_json_replay_uses_local_capture_path_when_source_url_is_absent(tmp_path):
    payload_path = _write_json(
        tmp_path / "capture.json",
        {"cards": [{"address": "101 Replay Street"}]},
    )
    source = JsonFilteredCaptureSource(SOURCE_NAME, payload_path, {})

    capture = source.execute(_plan(), observed_at=OBSERVED_AT)

    assert capture.source_url == str(payload_path)


def test_json_replay_preserves_empty_mapping_filter_evidence(tmp_path):
    payload_path = _write_json(
        tmp_path / "capture.json",
        {
            "applied_filter_evidence": {},
            "cards": [{"address": "101 Replay Street"}],
        },
    )

    capture = _json_source(payload_path).execute(_plan(), observed_at=OBSERVED_AT)

    assert capture.applied_filter_evidence == {}


@pytest.mark.parametrize("status_code", [401, 403, 429])
def test_json_replay_blocking_status_returns_before_card_projection(
    tmp_path,
    status_code,
):
    payload_path = _write_json(
        tmp_path / "blocked-capture.json",
        {
            "source_url": "https://capture.example/blocked-results",
            "observed_at": "2026-09-02T18:00:00Z",
            "status_code": status_code,
            "result_count": 1,
            "count_is_complete": True,
            "applied_filters": {"property_type": "Detached"},
            "applied_filter_evidence": ["global: detached filter was visible"],
            "detail_requests": 7,
            "blocked": False,
            "cards": [
                {
                    "description": "Invalid because it has no address",
                    "taxes": 9000,
                }
            ],
        },
    )

    capture = _json_source(payload_path).execute(_plan(), observed_at=OBSERVED_AT)

    assert capture.source_name == SOURCE_NAME
    assert capture.preset_name == "detached-core"
    assert capture.source_url == "https://capture.example/blocked-results"
    assert capture.observed_at.isoformat() == "2026-09-02T18:00:00+00:00"
    assert capture.status_code == status_code
    assert capture.result_count == 1
    assert capture.count_is_complete is True
    assert capture.applied_filters == {"property_type": "Detached"}
    assert capture.applied_filter_evidence == [
        "global: detached filter was visible"
    ]
    assert capture.blocked is True
    assert capture.detail_requests == 0
    assert capture.cards == []
    assert capture.raw_snapshot_path == str(payload_path)


def test_json_replay_declared_blocked_returns_before_card_projection(tmp_path):
    payload_path = _write_json(
        tmp_path / "blocked-capture.json",
        {
            "status_code": 200,
            "result_count": 1,
            "count_is_complete": True,
            "detail_requests": 4,
            "blocked": True,
            "cards": ["not a card object"],
        },
    )

    capture = _json_source(payload_path).execute(_plan(), observed_at=OBSERVED_AT)

    assert capture.status_code == 200
    assert capture.result_count == 1
    assert capture.count_is_complete is True
    assert capture.blocked is True
    assert capture.detail_requests == 0
    assert capture.cards == []
    assert capture.raw_snapshot_path == str(payload_path)


def test_json_replay_rejects_non_mapping_top_level(tmp_path):
    payload_path = _write_json(tmp_path / "capture.json", [{"address": "101 Replay Street"}])

    with pytest.raises(ValueError, match="top-level JSON value must be an object"):
        _json_source(payload_path).execute(_plan(), observed_at=OBSERVED_AT)


@pytest.mark.parametrize(
    ("field", "declared", "message"),
    [
        ("source_name", "some-other-source", "source_name"),
        ("preset_name", "some-other-preset", "preset_name"),
    ],
)
def test_json_replay_rejects_declared_source_or_preset_mismatch(
    tmp_path,
    field,
    declared,
    message,
):
    payload_path = _write_json(
        tmp_path / "capture.json",
        {
            field: declared,
            "cards": [{"address": "101 Replay Street"}],
        },
    )

    with pytest.raises(ValueError, match=message):
        _json_source(payload_path).execute(_plan(), observed_at=OBSERVED_AT)


@pytest.mark.parametrize(
    ("count_fields", "expected_count", "expected_complete"),
    [
        ({}, None, False),
        ({"count_is_complete": False}, None, False),
        ({"result_count": 9}, 9, False),
        ({"count_is_complete": True}, 2, True),
    ],
)
def test_json_replay_preserves_unknown_count_semantics(
    tmp_path,
    count_fields,
    expected_count,
    expected_complete,
):
    payload_path = _write_json(
        tmp_path / "capture.json",
        {
            **count_fields,
            "cards": [
                {"address": "101 Replay Street"},
                {"address": "202 Replay Street"},
            ],
        },
    )

    capture = _json_source(payload_path).execute(_plan(), observed_at=OBSERVED_AT)

    assert capture.result_count == expected_count
    assert capture.count_is_complete is expected_complete
    assert len(capture.cards) == 2


def test_json_replay_complete_missing_count_derives_count_and_validates(tmp_path):
    payload_path = _write_json(
        tmp_path / "capture.json",
        {
            "count_is_complete": True,
            "cards": [
                {
                    "address": "101 Complete Street",
                    "property_type": "Detached",
                    "bedrooms": 3,
                },
                {
                    "address": "202 Complete Street",
                    "property_type": "Detached",
                    "bedrooms": 4,
                },
            ],
        },
    )

    capture = _json_source(payload_path, max_result_cards=2).execute(
        _plan(),
        observed_at=OBSERVED_AT,
    )
    validation = validate_capture(
        capture,
        _plan(),
        sample_size=5,
        max_result_cards=2,
    )

    assert capture.result_count == 2
    assert capture.count_is_complete is True
    assert len(capture.cards) == 2
    assert validation.status is DiscoveryStatus.PASS


def test_json_replay_authoritative_over_cap_count_stops_before_card_projection(tmp_path):
    payload_path = _write_json(
        tmp_path / "capture.json",
        {
            "result_count": 101,
            "count_is_complete": True,
            "cards": [
                {
                    "description": "This invalid card has no address and must never be projected",
                    "taxes": 9000,
                }
            ],
        },
    )

    capture = _json_source(payload_path).execute(_plan(), observed_at=OBSERVED_AT)

    assert capture.result_count == 101
    assert capture.count_is_complete is True
    assert capture.cards == []


def test_json_replay_complete_missing_count_over_cap_stops_before_projection(
    tmp_path,
    monkeypatch,
):
    projection_calls: list[object] = []

    def forbidden_project_card(value, **kwargs):
        projection_calls.append(value)
        raise AssertionError("known over-cap raw cards must not be projected")

    monkeypatch.setattr(
        discovery_sources_module,
        "_project_card",
        forbidden_project_card,
    )
    payload_path = _write_json(
        tmp_path / "capture.json",
        {
            "count_is_complete": True,
            "cards": [
                {"address": "101 Over Cap Street"},
                {"address": "202 Over Cap Street"},
                {"address": "303 Over Cap Street"},
            ],
        },
    )

    capture = _json_source(payload_path, max_result_cards=2).execute(
        _plan(),
        observed_at=OBSERVED_AT,
    )

    assert capture.result_count == 3
    assert capture.count_is_complete is True
    assert capture.cards == []
    assert projection_calls == []


@pytest.mark.parametrize(
    ("count_fields", "expected_count", "expected_complete"),
    [
        ({}, None, False),
        ({"result_count": 9, "count_is_complete": False}, 9, False),
        ({"result_count": 1, "count_is_complete": True}, 1, True),
    ],
    ids=["unknown", "incomplete", "underreported"],
)
def test_json_replay_non_authoritative_or_underreported_count_bounds_projection(
    tmp_path,
    monkeypatch,
    count_fields,
    expected_count,
    expected_complete,
):
    projection_calls: list[object] = []
    original_project_card = discovery_sources_module._project_card

    def recording_project_card(value, **kwargs):
        projection_calls.append(value)
        return original_project_card(value, **kwargs)

    monkeypatch.setattr(
        discovery_sources_module,
        "_project_card",
        recording_project_card,
    )
    payload_path = _write_json(
        tmp_path / "capture.json",
        {
            **count_fields,
            "cards": [
                {"address": "101 Replay Street"},
                {"address": "202 Replay Street"},
                {"address": "303 Replay Street"},
                "invalid card beyond the cap boundary",
                {"address": "505 Replay Street"},
            ],
        },
    )

    capture = _json_source(payload_path, max_result_cards=2).execute(
        _plan(),
        observed_at=OBSERVED_AT,
    )

    assert capture.result_count == expected_count
    assert capture.count_is_complete is expected_complete
    assert len(capture.cards) == 3
    assert len(projection_calls) == 3
    validation = validate_capture(
        capture,
        _plan(),
        sample_size=5,
        max_result_cards=2,
    )
    assert validation.status is DiscoveryStatus.RESULT_SET_TOO_LARGE


def test_json_replay_projects_only_lightweight_fields_and_never_calls_transport(tmp_path):
    transport_calls: list[str] = []

    def forbidden_transport(url: str):
        transport_calls.append(url)
        raise AssertionError("local replay must not use a transport")

    payload_path = _write_json(
        tmp_path / "capture.json",
        {
            "cards": [
                {
                    "address": "303 Lightweight Avenue",
                    "municipality": "Vancouver",
                    "asking": "$1,699,000",
                    "source_url": "https://listings.example/listing/R3123456",
                    "description": "Deep listing copy",
                    "taxes": 8123,
                    "year_built": 1958,
                    "listing_history": [{"event": "listed"}],
                    "photos": ["https://images.example/one.jpg"],
                    "zoning": "R1-1",
                    "raw": {"capture_card_id": "card-303"},
                }
            ]
        },
    )
    source = _json_source(payload_path, transport=forbidden_transport)

    capture = source.execute(_plan(), observed_at=OBSERVED_AT)

    dumped = capture.cards[0].model_dump(exclude_none=True)
    assert set(dumped) <= LIGHTWEIGHT_CARD_FIELDS
    assert dumped["description"] == "Deep listing copy"
    assert dumped["taxes"] == 8123
    assert dumped["year_built"] == 1958
    assert "listing_history" not in dumped
    assert "photos" not in dumped
    assert "zoning" not in dumped
    assert capture.cards[0].raw == {"capture_card_id": "card-303"}
    assert capture.cards[0].source_url == "https://listings.example/listing/R3123456"
    assert transport_calls == []


def test_json_replay_raw_metadata_keeps_only_tiny_scalar_allowlist(tmp_path):
    payload_path = _write_json(
        tmp_path / "capture.json",
        {
            "cards": [
                {
                    "address": "404 Raw Metadata Street",
                    "raw": {
                        "capture_card_id": "capture-404",
                        "source_card_id": "source-404",
                        "result_position": 4,
                        "card_position": 5,
                        "page_number": 2,
                        "description": "Deep listing copy",
                        "taxes": {"annual": 8123},
                        "year_built": 1958,
                        "zoning": "R1-1",
                        "photos": ["https://images.example/one.jpg"],
                        "listing_history": [{"event": "listed"}],
                        "assessment": {"value": 1400000},
                        "nested": {"description": "also deep"},
                    },
                }
            ]
        },
    )

    capture = _json_source(payload_path).execute(_plan(), observed_at=OBSERVED_AT)

    assert capture.cards[0].raw == {
        "capture_card_id": "capture-404",
        "source_card_id": "source-404",
        "result_position": 4,
        "card_position": 5,
        "page_number": 2,
    }


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("23 results", 23),
        ("1,234 listings", 1_234),
        ("Showing 1-24 of 1,234 listings", 1_234),
        ("Results: 87", 87),
        ("No results", 0),
    ],
)
def test_filtered_html_parses_result_count_labels(label, expected):
    capture = parse_filtered_html(
        f"<div class='result-count'>{label}</div>".encode(),
        source_name=SOURCE_NAME,
        source_url=SOURCE_URL,
        observed_at=OBSERVED_AT,
        selectors={"result_count": ".result-count"},
    )

    assert capture.result_count == expected
    assert capture.count_is_complete is True


def test_filtered_html_parses_realtor_and_zolo_cards_with_positive_filter_evidence():
    html = b"""
    <html><body>
      <div class="result-count">2 listings</div>
      <span class="filter-chip active">Detached homes</span>
      <article class="listing-card"
        data-address="101 Public Street"
        data-city="Vancouver"
        data-price="$1,425,000"
        data-mls-number="R3000002"
        data-property-type="Single Family"
        data-tenure="Freehold"
        data-status="For Sale"
        data-dom="8"
        data-description="Deep copy must be dropped"
        data-year-built="1962"
        data-original-price="1500000"
        data-url="https://www.realtor.ca/real-estate/3002/101-public-street-vancouver">
        <span class="beds">4</span><span class="baths">2</span>
        <span class="house-size">2,100 sqft</span>
        <span class="lot-size">33 x 122 FT</span>
      </article>
      <article class="card-listing">
        <a class="tile-overlay-link"
           href="/vancouver-real-estate/202-zolo-street">Open</a>
        <div class="card-listing--location">
          <span class="street">202 Zolo Street</span>
          <span class="city">Vancouver</span>
        </div>
        <ul class="card-listing--values">
          <li class="price">$1,699,000</li>
          <li>3 bed</li><li>2 bath</li><li>1,875 sqft</li><li>Built in 1954</li>
        </ul>
      </article>
    </body></html>
    """

    capture = parse_filtered_html(
        html,
        source_name=SOURCE_NAME,
        source_url=SOURCE_URL,
        observed_at=OBSERVED_AT,
        selectors={
            "result_count": ".result-count",
            "applied_filter": ".filter-chip.active",
        },
        applied_filters={"property_type": "Detached"},
    )

    assert capture.result_count == 2
    assert capture.count_is_complete is True
    assert capture.applied_filters == {}
    assert capture.applied_filter_evidence == [
        "global: applied filter marker found: Detached homes"
    ]
    assert len(capture.cards) == 2
    realtor, zolo = capture.cards
    assert realtor.source_name == SOURCE_NAME
    assert realtor.mls_number == "R3000002"
    assert realtor.asking == 1_425_000
    assert realtor.frontage_ft == 33
    assert realtor.depth_ft == 122
    assert realtor.observed_at == OBSERVED_AT
    assert zolo.address == "202 Zolo Street"
    assert zolo.asking == 1_699_000
    assert zolo.source_url == "https://listings.example/vancouver-real-estate/202-zolo-street"
    for card in capture.cards:
        assert set(card.model_dump(exclude_none=True)) <= LIGHTWEIGHT_CARD_FIELDS
        assert card.model_extra == {}


def test_filtered_html_does_not_infer_filter_evidence_from_requested_filters():
    capture = parse_filtered_html(
        b"<div class='result-count'>0 results</div>",
        source_name=SOURCE_NAME,
        source_url=SOURCE_URL,
        observed_at=OBSERVED_AT,
        selectors={
            "result_count": ".result-count",
            "applied_filter": ".filter-chip.active",
        },
        applied_filters={"property_type": "Detached"},
    )

    assert capture.applied_filters == {}
    assert capture.applied_filter_evidence == []


def test_filtered_html_without_page_evidence_does_not_authorize_unknown_native_value():
    capture = parse_filtered_html(
        b"""
        <div class="result-count">1 result</div>
        <article class="listing-card" data-address="606 Unknown Street"></article>
        """,
        source_name=SOURCE_NAME,
        source_url=SOURCE_URL,
        observed_at=OBSERVED_AT,
        selectors={"result_count": ".result-count"},
        applied_filters={"property_type": "Detached"},
    )

    validation = validate_capture(
        capture,
        _plan(),
        sample_size=5,
        max_result_cards=100,
    )

    assert capture.applied_filters == {}
    assert capture.applied_filter_evidence == []
    assert validation.status is DiscoveryStatus.FILTER_VALIDATION_INCONCLUSIVE
    assert validation.candidate_cards == []
    assert "NATIVE_FILTER_VALUE_UNKNOWN" in validation.reason_codes


@pytest.mark.parametrize(
    "marker_markup",
    [
        '<span class="filter-chip" hidden>Detached</span>',
        '<span class="filter-chip" aria-hidden="true">Detached</span>',
        '<span class="filter-chip" style="display: none">Detached</span>',
        '<span class="filter-chip" style="visibility:hidden">Detached</span>',
        '<div hidden><span class="filter-chip">Detached</span></div>',
        '<div aria-hidden="TRUE"><span class="filter-chip">Detached</span></div>',
        '<div style="DISPLAY: none !important"><span class="filter-chip">Detached</span></div>',
        '<div style="visibility: hidden"><span class="filter-chip">Detached</span></div>',
        '<span class="filter-chip"></span>',
        '<span class="filter-chip">   </span>',
        '<span class="filter-chip" aria-label="Detached"></span>',
        '<span class="filter-chip"><span hidden>Detached</span></span>',
    ],
    ids=[
        "hidden-attribute",
        "aria-hidden",
        "display-none",
        "visibility-hidden",
        "hidden-ancestor",
        "aria-hidden-ancestor",
        "display-none-ancestor",
        "visibility-hidden-ancestor",
        "empty",
        "whitespace-only",
        "empty-with-aria-label",
        "hidden-descendant-only",
    ],
)
def test_filtered_html_hidden_or_empty_filter_markers_are_not_positive_evidence(
    marker_markup,
):
    html = (
        '<div class="result-count">0 results</div>' + marker_markup
    ).encode("utf-8")

    capture = parse_filtered_html(
        html,
        source_name=SOURCE_NAME,
        source_url=SOURCE_URL,
        observed_at=OBSERVED_AT,
        selectors={
            "result_count": ".result-count",
            "applied_filter": ".filter-chip",
        },
        applied_filters={"property_type": "Detached"},
    )

    assert capture.applied_filters == {}
    assert capture.applied_filter_evidence == []


def test_filtered_html_missing_count_is_incomplete():
    capture = parse_filtered_html(
        b"""<article class="listing-card" data-address="303 Countless Street"
                   data-url="https://listings.example/listing/303"></article>""",
        source_name=SOURCE_NAME,
        source_url=SOURCE_URL,
        observed_at=OBSERVED_AT,
        selectors={"result_count": ".missing-result-count"},
    )

    assert capture.result_count is None
    assert capture.count_is_complete is False
    assert len(capture.cards) == 1


@pytest.mark.parametrize(
    "card_selector",
    [None, ".listing-card"],
    ids=["public-parser-rows", "configured-card-nodes"],
)
def test_filtered_html_explicit_complete_missing_count_derives_count_and_validates(
    card_selector,
):
    selectors = {"result_count": ".missing-result-count"}
    if card_selector is not None:
        selectors["card"] = card_selector
    capture = parse_filtered_html(
        b"""
        <article class="listing-card" data-address="303 Counted Street"
                 data-property-type="Detached" data-bedrooms="3"
                 data-url="https://listings.example/listing/303"></article>
        <article class="listing-card" data-address="404 Counted Street"
                 data-property-type="Detached" data-bedrooms="4"
                 data-url="https://listings.example/listing/404"></article>
        """,
        source_name=SOURCE_NAME,
        source_url=SOURCE_URL,
        observed_at=OBSERVED_AT,
        selectors=selectors,
        max_result_cards=2,
        count_is_complete=True,
    )
    validation = validate_capture(
        capture,
        _plan(),
        sample_size=5,
        max_result_cards=2,
    )

    assert capture.result_count == 2
    assert capture.count_is_complete is True
    assert len(capture.cards) == 2
    assert validation.status is DiscoveryStatus.PASS


def test_filtered_html_explicit_incomplete_overrides_found_count():
    capture = parse_filtered_html(
        b"<div class='result-count'>55 results</div>",
        source_name=SOURCE_NAME,
        source_url=SOURCE_URL,
        observed_at=OBSERVED_AT,
        selectors={"result_count": ".result-count"},
        count_is_complete=False,
    )

    assert capture.result_count == 55
    assert capture.count_is_complete is False


@pytest.mark.parametrize("status_code", [401, 403, 429])
def test_filtered_html_blocking_status_stops_before_listing_parser(
    monkeypatch,
    status_code,
):
    parser_calls: list[bytes] = []

    def forbidden_parser(*args, **kwargs):
        parser_calls.append(args[0])
        raise AssertionError("blocked content must not be parsed")

    monkeypatch.setattr(discovery_sources_module, "parse_public_html", forbidden_parser)

    capture = parse_filtered_html(
        b"<article class='listing-card' data-address='505 Blocked Street'></article>",
        source_name=SOURCE_NAME,
        source_url=SOURCE_URL,
        observed_at=OBSERVED_AT,
        selectors={"result_count": ".result-count"},
        status_code=status_code,
        raw_snapshot_path="/captures/blocked.html",
    )

    assert capture.blocked is True
    assert capture.cards == []
    assert capture.status_code == status_code
    assert capture.raw_snapshot_path == "/captures/blocked.html"
    assert capture.source_url == SOURCE_URL
    assert parser_calls == []


@pytest.mark.parametrize(
    "content",
    [
        b"<html><body><h1>Verify you are human</h1></body></html>",
        b"<html><body>CAPTCHA required</body></html>",
        b"<html><title>Just a moment...</title><body>Please wait</body></html>",
        b"<html><body><h1>Sorry, you have been blocked</h1></body></html>",
    ],
)
def test_filtered_html_visible_challenge_stops_before_listing_parser(monkeypatch, content):
    parser_calls: list[bytes] = []

    def forbidden_parser(*args, **kwargs):
        parser_calls.append(args[0])
        raise AssertionError("challenge content must not be parsed")

    monkeypatch.setattr(discovery_sources_module, "parse_public_html", forbidden_parser)

    capture = parse_filtered_html(
        content,
        source_name=SOURCE_NAME,
        source_url=SOURCE_URL,
        observed_at=OBSERVED_AT,
        selectors={"result_count": ".result-count"},
    )

    assert capture.blocked is True
    assert capture.cards == []
    assert parser_calls == []


def test_visible_challenge_helper_and_filtered_parser_ignore_script_and_comment_markers():
    html = b"""
    <html><head>
      <script>const message = "CAPTCHA: verify you are human";</script>
    </head><body>
      <!-- Cloudflare challenge documentation -->
      <div class="result-count">1 result</div>
      <article class="listing-card" data-address="606 Visible Street"></article>
    </body></html>
    """

    assert public_feeds.has_visible_challenge(html) is False
    capture = parse_filtered_html(
        html,
        source_name=SOURCE_NAME,
        source_url=SOURCE_URL,
        observed_at=OBSERVED_AT,
        selectors={"result_count": ".result-count"},
    )

    assert capture.blocked is False
    assert [card.address for card in capture.cards] == ["606 Visible Street"]


def test_filtered_html_authoritative_over_cap_count_avoids_public_card_parser(monkeypatch):
    parser_calls: list[bytes] = []

    def forbidden_parser(*args, **kwargs):
        parser_calls.append(args[0])
        raise AssertionError("over-cap result sets must not ingest cards")

    monkeypatch.setattr(discovery_sources_module, "parse_public_html", forbidden_parser)

    capture = parse_filtered_html(
        b"""
        <div class="result-count">101 listings</div>
        <article class="listing-card" data-address="707 Unparsed Street"></article>
        """,
        source_name=SOURCE_NAME,
        source_url=SOURCE_URL,
        observed_at=OBSERVED_AT,
        selectors={"result_count": ".result-count"},
        max_result_cards=100,
    )

    assert capture.result_count == 101
    assert capture.count_is_complete is True
    assert capture.cards == []
    assert parser_calls == []


def test_filtered_html_complete_missing_count_over_cap_card_nodes_skip_public_parser(
    monkeypatch,
):
    parser_calls: list[bytes] = []

    def forbidden_parser(*args, **kwargs):
        parser_calls.append(args[0])
        raise AssertionError("known over-cap card nodes must not be parsed")

    monkeypatch.setattr(discovery_sources_module, "parse_public_html", forbidden_parser)

    capture = parse_filtered_html(
        _html_card_page(3),
        source_name=SOURCE_NAME,
        source_url=SOURCE_URL,
        observed_at=OBSERVED_AT,
        selectors={
            "result_count": ".missing-result-count",
            "card": ".listing-card",
        },
        max_result_cards=2,
        count_is_complete=True,
    )

    assert capture.result_count == 3
    assert capture.count_is_complete is True
    assert capture.cards == []
    assert parser_calls == []


@pytest.mark.parametrize(
    ("result_markup", "complete_override", "expected_count", "expected_complete"),
    [
        ("", None, None, False),
        ('<div class="result-count">9 results</div>', False, 9, False),
        ('<div class="result-count">1 result</div>', None, 1, True),
    ],
    ids=["unknown", "incomplete", "underreported"],
)
def test_filtered_html_card_selector_bounds_parser_for_non_authoritative_counts(
    monkeypatch,
    result_markup,
    complete_override,
    expected_count,
    expected_complete,
):
    parser_inputs: list[bytes] = []
    original_parser = discovery_sources_module.parse_public_html

    def recording_parser(content, requested_url, source_name, observed_at):
        parser_inputs.append(content)
        return original_parser(content, requested_url, source_name, observed_at)

    monkeypatch.setattr(discovery_sources_module, "parse_public_html", recording_parser)
    completeness = (
        {} if complete_override is None else {"count_is_complete": complete_override}
    )
    capture = parse_filtered_html(
        _html_card_page(5, result_markup=result_markup),
        source_name=SOURCE_NAME,
        source_url=SOURCE_URL,
        observed_at=OBSERVED_AT,
        selectors={
            "result_count": ".result-count",
            "card": ".listing-card",
        },
        max_result_cards=2,
        **completeness,
    )

    assert capture.result_count == expected_count
    assert capture.count_is_complete is expected_complete
    assert len(capture.cards) == 3
    assert len(parser_inputs) == 1
    assert parser_inputs[0].count(b'class="listing-card"') == 3
    assert b"4 Bounded Street" not in parser_inputs[0]
    validation = validate_capture(
        capture,
        _plan(),
        sample_size=5,
        max_result_cards=2,
    )
    assert validation.status is DiscoveryStatus.RESULT_SET_TOO_LARGE


def test_filtered_html_without_card_selector_slices_rows_before_projection(monkeypatch):
    projection_calls: list[object] = []
    original_project_card = discovery_sources_module._project_card

    def recording_project_card(value, **kwargs):
        projection_calls.append(value)
        return original_project_card(value, **kwargs)

    monkeypatch.setattr(
        discovery_sources_module,
        "_project_card",
        recording_project_card,
    )

    capture = parse_filtered_html(
        _html_card_page(5),
        source_name=SOURCE_NAME,
        source_url=SOURCE_URL,
        observed_at=OBSERVED_AT,
        selectors={"result_count": ".missing-result-count"},
        max_result_cards=2,
    )

    assert capture.result_count is None
    assert capture.count_is_complete is False
    assert len(capture.cards) == 3
    assert len(projection_calls) == 3
    validation = validate_capture(
        capture,
        _plan(),
        sample_size=5,
        max_result_cards=2,
    )
    assert validation.status is DiscoveryStatus.RESULT_SET_TOO_LARGE


def test_filtered_html_complete_missing_count_without_card_selector_uses_full_row_count(
    monkeypatch,
):
    parser_inputs: list[bytes] = []
    projection_calls: list[object] = []
    original_parser = discovery_sources_module.parse_public_html
    original_project_card = discovery_sources_module._project_card

    def recording_parser(content, requested_url, source_name, observed_at):
        parser_inputs.append(content)
        return original_parser(content, requested_url, source_name, observed_at)

    def recording_project_card(value, **kwargs):
        projection_calls.append(value)
        return original_project_card(value, **kwargs)

    monkeypatch.setattr(discovery_sources_module, "parse_public_html", recording_parser)
    monkeypatch.setattr(discovery_sources_module, "_project_card", recording_project_card)

    capture = parse_filtered_html(
        _html_card_page(5),
        source_name=SOURCE_NAME,
        source_url=SOURCE_URL,
        observed_at=OBSERVED_AT,
        selectors={"result_count": ".missing-result-count"},
        max_result_cards=2,
        count_is_complete=True,
    )
    validation = validate_capture(
        capture,
        _plan(),
        sample_size=5,
        max_result_cards=2,
    )

    assert capture.result_count == 5
    assert capture.count_is_complete is True
    assert len(capture.cards) == 3
    assert len(parser_inputs) == 1
    assert parser_inputs[0].count(b'class="listing-card"') == 5
    assert len(projection_calls) == 3
    assert validation.status is DiscoveryStatus.RESULT_SET_TOO_LARGE


def test_filtered_html_treats_detail_url_as_data_without_following_it(monkeypatch):
    parser_calls: list[tuple[bytes, str, str, datetime]] = []

    def recording_parser(content, requested_url, source_name, observed_at):
        parser_calls.append((content, requested_url, source_name, observed_at))
        return [
            {
                "address": "808 Card Street",
                "source_url": "https://listings.example/detail/808",
                "description": "drop me",
            }
        ]

    monkeypatch.setattr(discovery_sources_module, "parse_public_html", recording_parser)

    capture = parse_filtered_html(
        b"<div class='result-count'>1 result</div>",
        source_name=SOURCE_NAME,
        source_url=SOURCE_URL,
        observed_at=OBSERVED_AT,
        selectors={"result_count": ".result-count"},
    )

    assert parser_calls == [
        (
            b"<div class='result-count'>1 result</div>",
            SOURCE_URL,
            SOURCE_NAME,
            OBSERVED_AT,
        )
    ]
    assert capture.detail_requests == 0
    assert capture.cards[0].source_url == "https://listings.example/detail/808"
    assert capture.cards[0].description == "drop me"


def test_filtered_html_keeps_valid_cards_and_reports_addressless_card_warning():
    html = b"""
    <article class="listing-card" data-address="909 Valid Street"></article>
    <article class="listing-card" data-price="$1,500,000"></article>
    """

    capture = parse_filtered_html(
        html,
        source_name=SOURCE_NAME,
        source_url=SOURCE_URL,
        observed_at=OBSERVED_AT,
        selectors={"result_count": ".result-count"},
    )

    assert [card.address for card in capture.cards] == ["909 Valid Street"]
    assert capture.parse_warnings == [
        "row 1: listing card has no explicit address"
    ]


def test_filtered_html_reports_all_addressless_cards_without_aborting_capture():
    capture = parse_filtered_html(
        b"<article class='listing-card' data-price='$1,500,000'></article>",
        source_name=SOURCE_NAME,
        source_url=SOURCE_URL,
        observed_at=OBSERVED_AT,
        selectors={"result_count": ".result-count"},
    )

    assert capture.cards == []
    assert capture.count_is_complete is False
    assert capture.parse_warnings == [
        "row 0: listing card has no explicit address"
    ]


def test_html_local_replay_adapter_passes_config_and_never_calls_transport(tmp_path):
    transport_calls: list[str] = []

    def forbidden_transport(url: str):
        transport_calls.append(url)
        raise AssertionError("local replay must not use a transport")

    capture_path = _write_html(
        tmp_path / "filtered.html",
        b"""
        <div class="summary">1 result</div>
        <span class="selected-filter">Detached</span>
        <article class="listing-card" data-address="1001 Adapter Avenue"></article>
        """,
    )
    source = HtmlFilteredCaptureSource(
        SOURCE_NAME,
        capture_path,
        {
            "source_url": SOURCE_URL,
            "selectors": {
                "result_count": ".summary",
                "applied_filter": ".selected-filter",
            },
            "status_code": 200,
            "max_result_cards": 100,
            "count_is_complete": True,
            "applied_filters": {"property_type": "Detached"},
            "transport": forbidden_transport,
            "raw_snapshot_path": "/untrusted/other.html",
        },
    )

    capture = source.execute(_plan(), observed_at=OBSERVED_AT)

    assert isinstance(capture, DiscoveryCapture)
    assert capture.source_name == SOURCE_NAME
    assert capture.preset_name == "detached-core"
    assert capture.raw_snapshot_path == str(capture_path)
    assert capture.result_count == 1
    assert capture.count_is_complete is True
    assert capture.applied_filters == {}
    assert [card.address for card in capture.cards] == ["1001 Adapter Avenue"]
    assert transport_calls == []


class _FakeResponse:
    def __init__(self, status: int = 200):
        self.status = status


class _FakePage:
    def __init__(self, html: str, *, status: int = 200):
        self.html = html
        self.status = status
        self.url = "https://example.test/search?filtered=1"
        self.calls: list[tuple[str, object]] = []
        self.closed = False

    def goto(self, url, **kwargs):
        self.calls.append(("goto", (url, kwargs)))
        self.url = url
        return _FakeResponse(self.status)

    def fill(self, selector, value, **kwargs):
        self.calls.append(("fill", (selector, value, kwargs)))

    def select_option(self, selector, value, **kwargs):
        self.calls.append(("select_option", (selector, value, kwargs)))

    def check(self, selector, **kwargs):
        self.calls.append(("check", (selector, kwargs)))

    def click(self, selector, **kwargs):
        self.calls.append(("click", (selector, kwargs)))

    def wait_for_selector(self, selector, **kwargs):
        self.calls.append(("wait_for_selector", (selector, kwargs)))

    def content(self):
        self.calls.append(("content", None))
        return self.html

    def close(self):
        self.calls.append(("close", None))
        self.closed = True


class _DetailFakePage:
    def __init__(self, pages: dict[str, str | Exception]):
        self.pages = pages
        self.url = "about:blank"
        self.html = ""
        self.calls: list[tuple[str, object]] = []
        self.closed = False

    def goto(self, url, **kwargs):
        self.calls.append(("goto", (url, kwargs)))
        value = self.pages[url]
        if isinstance(value, Exception):
            raise value
        self.url = url
        self.html = value
        return _FakeResponse(200)

    def content(self):
        self.calls.append(("content", None))
        return self.html

    def wait_for_timeout(self, milliseconds):
        self.calls.append(("wait_for_timeout", milliseconds))

    def close(self):
        self.calls.append(("close", None))
        self.closed = True


class _RateLimitDetailPage(_DetailFakePage):
    def goto(self, url, **kwargs):
        self.calls.append(("goto", (url, kwargs)))
        return _FakeResponse(429)


class _FakeLocator:
    def __init__(self, value: str):
        self.value = value

    def input_value(self, **kwargs):
        return self.value


class _FilterStatePage(_FakePage):
    def __init__(self, html: str, filter_values: dict[str, str]):
        super().__init__(html)
        self.filter_values = filter_values

    def locator(self, selector, **kwargs):
        self.calls.append(("locator", (selector, kwargs)))
        return _FakeLocator(self.filter_values[selector])


class _PaginationLocator:
    def __init__(self, page):
        self.page = page

    def count(self):
        return 1 if self.page.page_index < len(self.page.pages) - 1 else 0

    def is_enabled(self):
        return self.page.page_index < len(self.page.pages) - 1

    def click(self, **kwargs):
        self.page.calls.append(("paginate", (self.page.page_index, kwargs)))
        self.page.page_index += 1


class _PaginatedFakePage(_FakePage):
    def __init__(self, pages: list[str]):
        super().__init__(pages[0])
        self.pages = pages
        self.page_index = 0

    def content(self):
        self.calls.append(("content", self.page_index))
        return self.pages[self.page_index]

    def locator(self, selector, **kwargs):
        self.calls.append(("locator", (selector, kwargs)))
        if selector == ".next":
            return _PaginationLocator(self)
        raise AssertionError(f"unexpected locator: {selector}")


class _DelayedPaginatedFakePage(_PaginatedFakePage):
    def __init__(self, pages: list[str]):
        super().__init__(pages)
        self.pending_page = False

    def wait_for_timeout(self, milliseconds):
        self.calls.append(("wait_for_timeout", milliseconds))
        if self.pending_page:
            self.page_index += 1
            self.pending_page = False

    def content(self):
        self.calls.append(("content", self.page_index))
        if self.pending_page:
            return "<div class='result-count'>2 listings</div><article class='listing-card'></article>"
        return self.pages[self.page_index]

    def locator(self, selector, **kwargs):
        self.calls.append(("locator", (selector, kwargs)))
        if selector != ".next":
            raise AssertionError(f"unexpected locator: {selector}")
        page = self

        class DelayedLocator(_PaginationLocator):
            def click(self, **kwargs):
                page.calls.append(("paginate", (page.page_index, kwargs)))
                page.pending_page = True

        return DelayedLocator(self)


class _FlappingPaginatedFakePage(_PaginatedFakePage):
    def __init__(self, pages: list[str], transient_pages: list[str]):
        super().__init__(pages)
        self.transient_pages = transient_pages
        self.progress_reads = 0

    def content(self):
        self.calls.append(("content", self.page_index))
        if self.page_index == 0:
            return self.pages[0]
        index = min(self.progress_reads, len(self.transient_pages) - 1)
        self.progress_reads += 1
        return self.transient_pages[index]

    def locator(self, selector, **kwargs):
        self.calls.append(("locator", (selector, kwargs)))
        if selector != ".next":
            raise AssertionError(f"unexpected locator: {selector}")
        return _PaginationLocator(self)


def _browser_plan() -> DiscoveryFilterPlan:
    return DiscoveryFilterPlan(
        preset_name="detached-core",
        source_name="playwright-test",
        desired_filters={
            "property_type": "Detached",
            "minimum_bedrooms": 3,
            "price_max": 2_200_000,
        },
        native_filters={
            "property_type": "Detached",
            "minimum_bedrooms": 3,
            "price_max": 2_200_000,
        },
    )


def _browser_html(*, result_count: str = "1 result", challenge: str = "") -> str:
    return f"""
    <html><body>
      <div class='result-count'>{result_count}</div>
      <span class='applied-filter'>Detached</span>
      {challenge}
      <article class='listing-card' data-address='101 Browser Street'
          data-price='$1,500,000' data-property-type='Detached'
          data-bedrooms='3' data-url='https://example.test/listing/101'>
        <span class='beds'>3</span><span class='baths'>3</span>
        <span class='house-size'>1,800 sqft</span>
      </article>
    </body></html>
    """


def _browser_html_for(address: str, *, result_count: str = "2 listings") -> str:
    return f"""
    <html><body>
      <div class='result-count'>{result_count}</div>
      <span class='applied-filter'>Detached</span>
      <article class='listing-card' data-address='{address}'
          data-price='$1,500,000' data-property-type='Detached'
          data-bedrooms='2' data-url='https://example.test/listing/{address.split()[0]}'>
        <span class='beds'>2</span><span class='baths'>1</span>
        <span class='house-size'>1,250 sqft</span>
      </article>
    </body></html>
    """


def _browser_config(**overrides):
    config = {
        "entrypoint": "https://example.test/search",
        "actions": [
            {"action": "fill", "selector": "#min-beds", "value_from": "minimum_bedrooms"},
            {"action": "fill", "selector": "#max-price", "value_from": "price_max"},
            {"action": "select_option", "selector": "#type", "value_from": "property_type"},
            {"action": "check", "selector": "#detached"},
            {"action": "click_filter_submit", "selector": "#submit"},
            {"action": "wait_for_selector", "selector": ".listing-card"},
        ],
        "selectors": {
            "result_count": ".result-count",
            "applied_filter": ".applied-filter",
            "card": ".listing-card",
        },
        "max_result_cards": 100,
    }
    config.update(overrides)
    return config


def test_playwright_native_source_applies_only_configured_filter_actions_and_reads_cards():
    page = _FakePage(_browser_html())
    source = PlaywrightNativeDiscoverySource(
        name="playwright-test",
        config=_browser_config(),
        page_factory=lambda: page,
    )

    capture = source.execute(_browser_plan(), observed_at=OBSERVED_AT)

    names = [name for name, _ in page.calls]
    assert names == [
        "goto",
        "fill",
        "fill",
        "select_option",
        "check",
        "click",
        "wait_for_selector",
        "content",
        "close",
    ]
    assert capture.source_name == "playwright-test"
    assert capture.source_url == "https://example.test/search"
    assert capture.preset_name == "detached-core"
    assert capture.result_count == 1
    assert capture.detail_requests == 0
    assert capture.cards[0].address == "101 Browser Street"
    assert capture.applied_filter_evidence == [
        "global: applied filter marker found: Detached"
    ]
    assert page.closed is True


def test_playwright_native_source_enriches_all_detail_pages_and_isolates_one_failure(
    tmp_path,
):
    search_html = """
    <html><body>
      <div class='result-count'>2 listings</div>
      <span class='applied-filter'>Detached</span>
      <article class='listing-card' data-address='101 Detail Street'
          data-price='$1,600,000' data-property-type='Detached'
          data-bedrooms='3' data-url='https://example.test/listing/101'>
        <span class='beds'>3</span><span class='baths'>2</span>
        <span class='house-size'>1,800 sqft</span>
      </article>
      <article class='listing-card' data-address='102 Failed Street'
          data-price='$1,550,000' data-property-type='Detached'
          data-bedrooms='3' data-url='https://example.test/listing/102'>
        <span class='beds'>3</span><span class='baths'>2</span>
        <span class='house-size'>1,700 sqft</span>
      </article>
    </body></html>
    """
    detail_page = _DetailFakePage(
        {
            "https://example.test/listing/101": """
                <html><body><h1>101 Detail Street</h1>
                <div class='city'>Vancouver</div>
                <div class='lot-area'>4,026 sqft</div>
                <div class='lot-dimensions'>33 x 122 ft</div>
                <div class='year-built'>1950</div></body></html>
            """,
            "https://example.test/listing/102": RuntimeError("detail unavailable"),
        }
    )
    source = PlaywrightNativeDiscoverySource(
        name="playwright-test",
        config=_browser_config(
            detail_enrichment={
                "enabled": True,
                "max_requests": 100,
                "continue_on_error": True,
                "raw_snapshot_dir": str(tmp_path / "details"),
            }
        ),
        page_factory=lambda: _FakePage(search_html),
        detail_page_factory=lambda: detail_page,
    )

    capture = source.execute(_browser_plan(), observed_at=OBSERVED_AT)

    assert capture.detail_requests == 2
    assert capture.detail_successes == 1
    assert capture.detail_failures == 1
    assert len(capture.cards) == 2
    assert capture.cards[0].lot_area_sqft == 4_026
    assert capture.cards[0].frontage_ft == 33
    assert capture.cards[0].depth_ft == 122
    assert capture.cards[0].year_built == 1950
    assert "DETAIL_FETCH_FAILED" in capture.cards[1].reason_codes
    assert any("102 Failed Street" in warning for warning in capture.parse_warnings)
    assert len(capture.detail_snapshot_paths) == 1
    assert Path(capture.detail_snapshot_paths[0]).is_file()
    assert detail_page.closed is True


def test_detail_identity_accepts_detail_address_with_municipality_suffix():
    card = DiscoveryCard(
        address="5755 DUMFRIES STREET",
        municipality="Vancouver",
        mls_number="R3161299",
    )

    enriched = _merge_detail_card(
        card,
        {
            "address": "5755 DUMFRIES STREET Vancouver",
            "municipality": "Vancouver",
            "mls_number": "R3161299",
        },
    )

    assert enriched.address == "5755 DUMFRIES STREET"


def test_replay_cached_realtor_details_is_local_and_keeps_missing_history_unknown(tmp_path):
    card = DiscoveryCard(
        address="101 Replay Street",
        municipality="Vancouver",
        mls_number="R3160001",
        source_url="https://www.realtor.ca/real-estate/3160001/101-replay-street-vancouver",
    )
    (tmp_path / "r3160001.html").write_text(
        """
        <html><body>
          <h1 id="listingAddress">101 Replay Street<br>Vancouver, British Columbia</h1>
          <div class="lot-dimensions">33 x 122 ft</div>
          <table id="tableHistoryDetail"><tr><td>Apr 22, 2013</td><td>Sale Closed</td><td>$646,000</td></tr></table>
        </body></html>
        """,
        encoding="utf-8",
    )

    replayed, warnings = replay_cached_realtor_details([card], tmp_path, observed_at=OBSERVED_AT)

    assert warnings == []
    assert replayed[0].frontage_ft == 33
    assert replayed[0].geometry_source == "LISTING_OBSERVED"
    assert replayed[0].realtor_history_status == "RECORDED"
    assert replayed[0].realtor_price_sale_history[0]["price"] == 646_000
    assert replayed[0].raw["realtor_history_status"] == "RECORDED"


def test_playwright_native_source_stops_detail_requests_after_rate_limit():
    search_html = """
    <html><body>
      <div class='result-count'>2 listings</div>
      <span class='applied-filter'>Detached</span>
      <article class='listing-card' data-address='101 Rate Street'
          data-price='$1,600,000' data-property-type='Detached'
          data-bedrooms='3' data-url='https://example.test/listing/101'>
        <span class='beds'>3</span><span class='baths'>2</span>
        <span class='house-size'>1,800 sqft</span>
      </article>
      <article class='listing-card' data-address='102 Rate Street'
          data-price='$1,550,000' data-property-type='Detached'
          data-bedrooms='3' data-url='https://example.test/listing/102'>
        <span class='beds'>3</span><span class='baths'>2</span>
        <span class='house-size'>1,700 sqft</span>
      </article>
    </body></html>
    """
    detail_page = _RateLimitDetailPage({})
    source = PlaywrightNativeDiscoverySource(
        name="playwright-test",
        config=_browser_config(
            detail_enrichment={
                "enabled": True,
                "stop_on_rate_limit": True,
            }
        ),
        page_factory=lambda: _FakePage(search_html),
        detail_page_factory=lambda: detail_page,
    )

    capture = source.execute(_browser_plan(), observed_at=OBSERVED_AT)

    assert capture.detail_requests == 1
    assert capture.detail_failures == 1
    assert len(capture.cards) == 2
    assert "DETAIL_FETCH_FAILED" in capture.cards[0].reason_codes
    assert "DETAIL_NOT_ATTEMPTED_RATE_LIMITED" in capture.cards[1].reason_codes
    assert [name for name, _ in detail_page.calls].count("goto") == 1


def test_playwright_native_source_does_not_open_details_when_native_filter_validation_fails():
    search_page = _FakePage(
        _browser_html().replace("data-property-type='Detached'", "data-property-type='Townhouse'")
    )

    def fail_if_detail_opened():
        raise AssertionError("detail pages must not open after a failed native filter")

    source = PlaywrightNativeDiscoverySource(
        name="playwright-test",
        config=_browser_config(
            detail_enrichment={"enabled": True},
        ),
        page_factory=lambda: search_page,
        detail_page_factory=fail_if_detail_opened,
    )

    capture = source.execute(_browser_plan(), observed_at=OBSERVED_AT)

    assert capture.detail_requests == 0
    assert capture.detail_successes == 0
    assert capture.cards[0].reason_codes == []


def test_playwright_native_source_forwards_configured_result_wait_timeout():
    page = _FakePage(_browser_html())
    source = PlaywrightNativeDiscoverySource(
        name="playwright-test",
        config=_browser_config(),
        page_factory=lambda: page,
    )

    source._run_action(
        page,
        {
            "action": "wait_for_selector",
            "selector": ".listing-card",
            "timeout_ms": 90_000,
        },
        _browser_plan(),
        None,
    )

    wait_calls = [value for name, value in page.calls if name == "wait_for_selector"]
    assert wait_calls == [(".listing-card", {"timeout": 90_000})]


def test_playwright_native_source_supports_clicking_a_view_toggle():
    page = _FakePage(_browser_html())
    source = PlaywrightNativeDiscoverySource(
        name="playwright-test",
        config=_browser_config(),
        page_factory=lambda: page,
    )

    source._run_action(
        page,
        {"action": "click", "selector": "#view-list"},
        _browser_plan(),
        None,
    )

    assert [value for name, value in page.calls if name == "click"] == [
        ("#view-list", {}),
    ]


def test_playwright_native_source_can_launch_visible_google_chrome():
    source = PlaywrightNativeDiscoverySource(
        name="realtor-ca-chrome",
        config={
            "browser_engine": "chromium",
            "browser_channel": "chrome",
            "headless": False,
        },
    )

    assert source._launch_options() == {
        "channel": "chrome",
        "headless": False,
    }


def test_cdp_page_selection_ignores_chrome_internal_targets():
    internal = type("Target", (), {"url": "chrome://newtab/"})()
    listing = type("Target", (), {"url": "https://www.realtor.ca/map#filtered"})()

    assert _select_cdp_page([internal, listing]) is listing


def test_cdp_discovery_can_open_a_fresh_tab_instead_of_reusing_listing_state():
    existing = type("Page", (), {"url": "https://www.realtor.ca/map#old-state"})()
    fresh = type("Page", (), {"url": "about:blank"})()

    class Context:
        def new_page(self):
            return fresh

    page, owns_page = _prepare_cdp_page(
        Context(),
        [existing],
        open_new_page=True,
    )

    assert page is fresh
    assert owns_page is True


def test_playwright_native_source_builds_realtor_map_url_before_navigation():
    page = _FakePage(_browser_html())
    source_url = (
        "https://www.realtor.ca/map#ZoomLevel=15&Center=49.252003%2C-123.110789"
        "&PGeoIds=g30_c2b2nw3h&GeoName=Vancouver%2C%20BC"
        "&TransactionTypeId=2&PropertyTypeGroupID=1&BuildingTypeId=1"
        "&OwnershipTypeGroupId=1&PriceMax=1700000&BedRange=2-0"
        "&BathRange=1-0&SQFTRange=1250-0&Currency=CAD"
    )
    config = _browser_config(
        entrypoint=source_url,
        url_builder="REALTOR_CA_MAP",
    )
    source = PlaywrightNativeDiscoverySource(
        name="realtor-ca-native",
        config=config,
        page_factory=lambda: page,
    )

    source.execute(_browser_plan(), observed_at=OBSERVED_AT)

    goto_calls = [value for name, value in page.calls if name == "goto"]
    assert goto_calls[0][0] == build_realtor_ca_map_url(_browser_plan(), source_url)


def test_playwright_native_source_waits_for_ajax_results_after_navigation():
    page = _FakePage(_browser_html())
    page.wait_for_timeout = lambda milliseconds: page.calls.append(
        ("wait_for_timeout", milliseconds)
    )
    source = PlaywrightNativeDiscoverySource(
        name="playwright-test",
        config=_browser_config(navigation_settle_seconds=2),
        page_factory=lambda: page,
    )

    source.execute(_browser_plan(), observed_at=OBSERVED_AT)

    assert ("wait_for_timeout", 2000) in page.calls


def test_playwright_native_source_collects_all_filtered_result_pages_before_shortlist():
    page = _PaginatedFakePage(
        [
            _browser_html_for("101 Browser Street"),
            _browser_html_for("102 Browser Street"),
        ]
    )
    source = PlaywrightNativeDiscoverySource(
        name="playwright-test",
        config=_browser_config(
            pagination={
                "enabled": True,
                "next_selector": ".next",
                "max_pages": 5,
                "wait_after_click_ms": 0,
            }
        ),
        page_factory=lambda: page,
    )

    capture = source.execute(_browser_plan(), observed_at=OBSERVED_AT)

    assert capture.result_count == 2
    assert capture.count_is_complete is True
    assert [card.address for card in capture.cards] == [
        "101 Browser Street",
        "102 Browser Street",
    ]
    assert [name for name, _ in page.calls].count("paginate") == 1
    assert [name for name, _ in page.calls].count("goto") == 1


def test_playwright_native_source_accepts_configured_small_result_card_variance():
    page = _PaginatedFakePage(
        [
            _browser_html_for("101 Browser Street", result_count="3 listings"),
            _browser_html_for("102 Browser Street", result_count="3 listings"),
        ]
    )
    source = PlaywrightNativeDiscoverySource(
        name="playwright-test",
        config=_browser_config(
            result_count_variance_tolerance=1,
            pagination={
                "enabled": True,
                "next_selector": ".next",
                "max_pages": 5,
                "wait_after_click_ms": 0,
            },
        ),
        page_factory=lambda: page,
    )

    capture = source.execute(_browser_plan(), observed_at=OBSERVED_AT)

    assert capture.count_is_complete is True
    assert [card.address for card in capture.cards] == [
        "101 Browser Street",
        "102 Browser Street",
    ]
    assert capture.parse_warnings == [
        "RESULT_COUNT_CARD_VARIANCE: reported=3 parsed=2 gap=1"
    ]


def test_playwright_native_source_accepts_tolerated_gap_after_repeated_page():
    page_html = _browser_html_for("101 Browser Street", result_count="2 listings")
    page = _PaginatedFakePage([page_html, page_html])
    source = PlaywrightNativeDiscoverySource(
        name="playwright-test",
        config=_browser_config(
            result_count_variance_tolerance=1,
            pagination={
                "enabled": True,
                "next_selector": ".next",
                "max_pages": 5,
                "wait_after_click_ms": 0,
            },
        ),
        page_factory=lambda: page,
    )

    capture = source.execute(_browser_plan(), observed_at=OBSERVED_AT)

    assert capture.count_is_complete is True
    assert capture.parse_warnings == [
        "RESULT_COUNT_CARD_VARIANCE: reported=2 parsed=1 gap=1"
    ]


def test_playwright_native_source_polls_for_ajax_page_progress_after_next_click():
    page = _DelayedPaginatedFakePage(
        [
            _browser_html_for("101 Browser Street"),
            _browser_html_for("102 Browser Street"),
        ]
    )
    source = PlaywrightNativeDiscoverySource(
        name="playwright-test",
        config=_browser_config(
            pagination={
                "enabled": True,
                "next_selector": ".next",
                "max_pages": 5,
                "wait_after_click_ms": 0,
                "progress_poll_interval_ms": 1,
                "progress_timeout_ms": 10,
            }
        ),
        page_factory=lambda: page,
    )

    capture = source.execute(_browser_plan(), observed_at=OBSERVED_AT)

    assert capture.count_is_complete is True
    assert [card.address for card in capture.cards] == [
        "101 Browser Street",
        "102 Browser Street",
    ]
    assert capture.parse_warnings == [
        "row 0: listing card has no explicit address"
    ]


def test_playwright_native_source_waits_for_stable_page_after_transient_ajax_content():
    page = _FlappingPaginatedFakePage(
        [
            _browser_html_for("101 Browser Street"),
            _browser_html_for("102 Browser Street"),
        ],
        [
            _browser_html_for("999 Transient Street"),
            _browser_html_for("101 Browser Street"),
            _browser_html_for("102 Browser Street"),
            _browser_html_for("102 Browser Street"),
        ],
    )
    source = PlaywrightNativeDiscoverySource(
        name="playwright-test",
        config=_browser_config(
            pagination={
                "enabled": True,
                "next_selector": ".next",
                "max_pages": 2,
                "wait_after_click_ms": 0,
                "progress_poll_interval_ms": 1,
                "progress_timeout_ms": 20,
                "progress_stable_polls": 2,
            }
        ),
        page_factory=lambda: page,
    )

    capture = source.execute(_browser_plan(), observed_at=OBSERVED_AT)

    assert [card.address for card in capture.cards] == [
        "101 Browser Street",
        "102 Browser Street",
    ]
    assert page.progress_reads >= 4


def test_playwright_native_source_ignores_transient_page_with_only_seen_cards():
    initial = _browser_html_for(
        "101 Browser Street", result_count="3 listings"
    ) + _browser_html_for("999 Persistent Street", result_count="3 listings")
    complete = _browser_html_for("102 Browser Street", result_count="3 listings")
    page = _FlappingPaginatedFakePage(
        [initial, complete],
        [
            _browser_html_for("999 Persistent Street", result_count="3 listings"),
            _browser_html_for("999 Persistent Street", result_count="3 listings"),
            complete,
            complete,
        ],
    )
    source = PlaywrightNativeDiscoverySource(
        name="playwright-test",
        config=_browser_config(
            pagination={
                "enabled": True,
                "next_selector": ".next",
                "max_pages": 2,
                "wait_after_click_ms": 0,
                "progress_poll_interval_ms": 1,
                "progress_timeout_ms": 20,
                "progress_stable_polls": 2,
            }
        ),
        page_factory=lambda: page,
    )

    capture = source.execute(_browser_plan(), observed_at=OBSERVED_AT)

    assert capture.count_is_complete is True
    assert [card.address for card in capture.cards] == [
        "101 Browser Street",
        "999 Persistent Street",
        "102 Browser Street",
    ]
    assert page.progress_reads >= 4


def test_playwright_native_source_keeps_pagination_complete_when_bad_card_is_skipped():
    page = _PaginatedFakePage(
        [
            _browser_html_for("101 Browser Street", result_count="3 listings")
            + "<article class='listing-card' data-price='$1,500,000'></article>",
            _browser_html_for("102 Browser Street", result_count="3 listings"),
        ]
    )
    source = PlaywrightNativeDiscoverySource(
        name="playwright-test",
        config=_browser_config(
            pagination={
                "enabled": True,
                "next_selector": ".next",
                "max_pages": 5,
                "wait_after_click_ms": 0,
            }
        ),
        page_factory=lambda: page,
    )

    capture = source.execute(_browser_plan(), observed_at=OBSERVED_AT)

    assert capture.count_is_complete is True
    assert [card.address for card in capture.cards] == [
        "101 Browser Street",
        "102 Browser Street",
    ]
    assert capture.parse_warnings == [
        "row 1: listing card has no explicit address"
    ]


def test_playwright_native_source_accepts_final_page_with_only_bad_card():
    page = _PaginatedFakePage(
        [
            _browser_html_for("101 Browser Street", result_count="2 listings"),
            "<div class='result-count'>2 listings</div>"
            "<article class='listing-card' data-price='$1,500,000'></article>",
        ]
    )
    source = PlaywrightNativeDiscoverySource(
        name="playwright-test",
        config=_browser_config(
            pagination={
                "enabled": True,
                "next_selector": ".next",
                "max_pages": 5,
                "wait_after_click_ms": 0,
            }
        ),
        page_factory=lambda: page,
    )

    capture = source.execute(_browser_plan(), observed_at=OBSERVED_AT)

    assert capture.count_is_complete is True
    assert [card.address for card in capture.cards] == ["101 Browser Street"]
    assert capture.parse_warnings == [
        "row 0: listing card has no explicit address"
    ]


def test_playwright_native_source_skips_bad_page_and_continues_to_next_page():
    page = _PaginatedFakePage(
        [
            _browser_html_for("101 Browser Street", result_count="3 listings"),
            "<div class='result-count'>3 listings</div>"
            "<article class='listing-card' data-price='$1,500,000'></article>",
            _browser_html_for("102 Browser Street", result_count="3 listings"),
        ]
    )
    source = PlaywrightNativeDiscoverySource(
        name="playwright-test",
        config=_browser_config(
            pagination={
                "enabled": True,
                "next_selector": ".next",
                "max_pages": 5,
                "wait_after_click_ms": 0,
            }
        ),
        page_factory=lambda: page,
    )

    capture = source.execute(_browser_plan(), observed_at=OBSERVED_AT)

    assert capture.count_is_complete is True
    assert [card.address for card in capture.cards] == [
        "101 Browser Street",
        "102 Browser Street",
    ]
    assert capture.parse_warnings == [
        "row 0: listing card has no explicit address"
    ]


def test_playwright_native_source_stops_on_repeated_page_fingerprint():
    page = _PaginatedFakePage(
        [
            _browser_html_for("101 Browser Street", result_count="3 listings"),
            _browser_html_for("102 Browser Street", result_count="3 listings"),
            _browser_html_for("101 Browser Street", result_count="3 listings"),
        ]
    )
    source = PlaywrightNativeDiscoverySource(
        name="playwright-test",
        config=_browser_config(
            pagination={
                "enabled": True,
                "next_selector": ".next",
                "max_pages": 5,
                "wait_after_click_ms": 0,
            }
        ),
        page_factory=lambda: page,
    )

    capture = source.execute(_browser_plan(), observed_at=OBSERVED_AT)

    assert capture.count_is_complete is False
    assert [card.address for card in capture.cards] == [
        "101 Browser Street",
        "102 Browser Street",
    ]


def test_filtered_html_parser_extracts_live_realtor_small_listing_card_fields():
    content = """
    <div id="mapResultsNumVal">1 Listings</div>
    <div class="smallListingCard" data-value="30228326_49.215237_-123.074276_true">
      <a class="listingDetailsLink" href="/real-estate/30228326/7625-borden-street-vancouver"></a>
      <div class="smallListingCardAddress">7625 BORDEN STREET, Vancouver</div>
      <div class="listingCardPrice" data-value-cad="$1,699,000">$1,699,000</div>
      <div class="smallListingCardIconCon"><div class="smallListingCardIconNum">5</div><div class="smallListingCardIconText">Bedrooms</div></div>
      <div class="smallListingCardIconCon"><div class="smallListingCardIconNum">3</div><div class="smallListingCardIconText">Bathrooms</div></div>
      <div class="smallListingCardIconCon"><div class="smallListingCardIconNum">2889 sqft</div><div class="smallListingCardIconText">sqft</div></div>
    </div>
    """

    capture = parse_filtered_html(
        content,
        source_name="realtor-ca-native",
        source_url="https://www.realtor.ca/map#filtered",
        observed_at=OBSERVED_AT,
        selectors={
            "result_count": "#mapResultsNumVal",
            "card": ".smallListingCard",
        },
    )

    assert capture.result_count == 1
    assert len(capture.cards) == 1
    card = capture.cards[0]
    assert card.address == "7625 BORDEN STREET"
    assert card.municipality == "Vancouver"
    assert card.asking == 1_699_000
    assert card.bedrooms == 5
    assert card.bathrooms == 3
    assert card.finished_sqft == 2889
    assert card.latitude == 49.215237
    assert card.longitude == -123.074276
    assert card.source_url.endswith("/real-estate/30228326/7625-borden-street-vancouver")


def test_playwright_native_source_captures_realtor_rendered_filter_values():
    page = _FilterStatePage(
        _browser_html(),
        {
            "#status": "2",
            "#type": "1",
            "#ownership": "1",
            "#min-price": "1500000",
            "#max-price": "2000000",
            "#beds": "3-0",
            "#baths": "2-0",
            "#sqft": "1500",
        },
    )
    source_url = (
        "https://www.realtor.ca/map#ZoomLevel=15&Center=49.252003%2C-123.110789"
        "&TransactionTypeId=2&PropertyTypeGroupID=1&BuildingTypeId=1"
        "&OwnershipTypeGroupId=1&Currency=CAD"
    )
    plan = DiscoveryFilterPlan(
        preset_name="vancouver-west",
        native_filters={
            "status": "ACTIVE",
            "property_type": "DETACHED_HOUSE",
            "ownership": "FREEHOLD",
            "minimum_bedrooms": 3,
            "minimum_bathrooms": 2,
            "minimum_finished_sqft": 1500,
            "price_min": 1_500_000,
            "price_max": 2_000_000,
        },
    )
    source = PlaywrightNativeDiscoverySource(
        name="realtor-ca-native",
        config=_browser_config(
            entrypoint=source_url,
            url_builder="REALTOR_CA_MAP",
            applied_filter_selectors={
                "status": {"selector": "#status", "value_mapping": {"2": "ACTIVE"}},
                "property_type": {
                    "selector": "#type",
                    "value_mapping": {"1": "DETACHED_HOUSE"},
                },
                "ownership": {"selector": "#ownership", "value_mapping": {"1": "FREEHOLD"}},
                "price_min": "#min-price",
                "price_max": "#max-price",
                "minimum_bedrooms": {"selector": "#beds", "value_mapping": {"3-0": 3}},
                "minimum_bathrooms": {"selector": "#baths", "value_mapping": {"2-0": 2}},
                "minimum_finished_sqft": "#sqft",
            },
        ),
        page_factory=lambda: page,
    )

    capture = source.execute(plan, observed_at=OBSERVED_AT)

    assert capture.applied_filters == plan.native_filters


def test_playwright_native_source_can_wait_for_network_idle_during_navigation():
    page = _FakePage(_browser_html())
    source = PlaywrightNativeDiscoverySource(
        name="realtor-ca-native",
        config=_browser_config(navigation_wait_until="networkidle"),
        page_factory=lambda: page,
    )

    source.execute(_browser_plan(), observed_at=OBSERVED_AT)

    goto_calls = [value for name, value in page.calls if name == "goto"]
    assert goto_calls[0][1] == {"wait_until": "networkidle"}


def test_playwright_native_source_never_opens_detail_links_or_paginates():
    page = _FakePage(_browser_html())
    source = PlaywrightNativeDiscoverySource(
        name="playwright-test",
        config=_browser_config(),
        page_factory=lambda: page,
    )

    source.execute(_browser_plan(), observed_at=OBSERVED_AT)

    assert not any(name in {"follow_detail", "paginate"} for name, _ in page.calls)
    assert [name for name, _ in page.calls].count("goto") == 1


def test_playwright_native_source_can_reuse_an_already_loaded_filtered_page():
    page = _FakePage(_browser_html())
    source = PlaywrightNativeDiscoverySource(
        name="playwright-test",
        config=_browser_config(reuse_current_page=True),
        page_factory=lambda: page,
    )

    capture = source.execute(_browser_plan(), observed_at=OBSERVED_AT)

    assert capture.result_count == 1
    assert [card.address for card in capture.cards] == ["101 Browser Street"]
    assert not any(name == "goto" for name, _ in page.calls)


def test_playwright_native_source_maps_403_to_blocked_and_still_closes_page():
    page = _FakePage(_browser_html(), status=403)
    source = PlaywrightNativeDiscoverySource(
        name="playwright-test",
        config=_browser_config(),
        page_factory=lambda: page,
    )

    capture = source.execute(_browser_plan(), observed_at=OBSERVED_AT)

    assert capture.blocked is True
    assert capture.status_code == 403
    assert capture.cards == []
    assert page.closed is True


def test_playwright_native_source_does_not_read_filter_controls_on_block_page():
    page = _FilterStatePage(
        _browser_html(challenge="<h1>Sorry, you have been blocked</h1>"),
        {"#status": "2"},
    )
    source = PlaywrightNativeDiscoverySource(
        name="realtor-ca-native",
        config=_browser_config(applied_filter_selectors={"status": "#status"}),
        page_factory=lambda: page,
    )

    capture = source.execute(_browser_plan(), observed_at=OBSERVED_AT)

    assert capture.blocked is True
    assert not any(name == "locator" for name, _ in page.calls)


def test_playwright_native_source_fails_closed_on_challenge_and_does_not_bypass():
    page = _FakePage(_browser_html(challenge="<h1>Verify you are human</h1>"))
    source = PlaywrightNativeDiscoverySource(
        name="playwright-test",
        config=_browser_config(),
        page_factory=lambda: page,
    )

    capture = source.execute(_browser_plan(), observed_at=OBSERVED_AT)

    assert capture.blocked is True
    assert capture.cards == []
    assert not any(name in {"solve_captcha", "rotate_proxy", "stealth"} for name, _ in page.calls)


def test_playwright_native_source_rejects_unknown_action_before_opening_page():
    page_factory_calls: list[str] = []
    config = _browser_config(actions=[{"action": "open_detail", "selector": ".listing-card"}])
    source = PlaywrightNativeDiscoverySource(
        name="playwright-test",
        config=config,
        page_factory=lambda: page_factory_calls.append("opened") or _FakePage(_browser_html()),
    )

    with pytest.raises(ValueError, match="unsupported discovery browser action"):
        source.execute(_browser_plan(), observed_at=OBSERVED_AT)
    assert page_factory_calls == []


def test_playwright_native_source_closes_page_when_action_fails():
    page = _FakePage(_browser_html())
    config = _browser_config(actions=[{"action": "fill", "selector": "#missing", "value_from": "not_in_plan"}])
    source = PlaywrightNativeDiscoverySource(
        name="playwright-test",
        config=config,
        page_factory=lambda: page,
    )

    with pytest.raises(ValueError, match="value_from"):
        source.execute(_browser_plan(), observed_at=OBSERVED_AT)
    assert page.closed is True


def test_playwright_native_source_respects_result_cap_before_any_pagination():
    page = _FakePage(_browser_html(result_count="101 listings"))
    source = PlaywrightNativeDiscoverySource(
        name="playwright-test",
        config=_browser_config(max_result_cards=100),
        page_factory=lambda: page,
    )

    capture = source.execute(_browser_plan(), observed_at=OBSERVED_AT)

    assert capture.result_count == 101
    assert capture.cards == []
    assert page.closed is True
