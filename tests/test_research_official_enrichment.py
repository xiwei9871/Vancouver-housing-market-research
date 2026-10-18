from datetime import datetime, timezone
from pathlib import Path

from northshore_watchdog.research.models import PropertyRecord, SourceType
from northshore_watchdog.research.enrichment import JsonMappingProvider, enrich_property
from northshore_watchdog.research.official_enrichment import (
    BCAssessmentAdapter,
    MunicipalRoadAdapter,
    MunicipalZoningAdapter,
    OfficialLookupRow,
    OfficialQueryStatus,
    SchoolCatchmentAdapter,
    SchoolPerformanceAdapter,
    SoldHistoryAdapter,
    match_official_identity,
    parse_bc_assessment_payload,
    parse_zoning_payload,
    route_zoning_source,
    parse_school_payload,
    parse_sold_history_payload,
    route_school_source,
    _addresses_match,
    _address_core,
    _address_query_variants,
    _municipality_from_address,
    _parse_bca_search_candidates,
)


def test_street_component_normalizes_address_street_type_variants():
    from northshore_watchdog.research.official_enrichment import _street_component

    assert _street_component("3910 W 10TH AVENUE") == _street_component("3900 W 10TH AV")
from northshore_watchdog.research.provenance import SourceRecord
from northshore_watchdog.research.official_transport import OfficialHttpResponse


OBSERVED_AT = datetime(2026, 9, 1, tzinfo=timezone.utc)


def test_official_lookup_row_records_identity_scope_and_snapshot():
    row = OfficialLookupRow(
        property_id="property:1",
        status=OfficialQueryStatus.SUCCESS,
        fields={"assessment_total": 1_700_000},
        source_url="https://official.example/property/1",
        match_method="PID",
        query_scope="last_three_full_calendar_years",
        raw_snapshot_path="data/research/raw/official-enrichment/a.json",
    )

    assert row.status is OfficialQueryStatus.SUCCESS
    assert row.match_method == "PID"
    assert row.query_scope == "last_three_full_calendar_years"


def test_source_record_exposes_direct_query_metadata():
    source = SourceRecord(
        property_id="property:1",
        field_name="zoning",
        source_url="https://city.example/zoning/1",
        source_type=SourceType.OFFICIAL_GOV,
        retrieved_at=OBSERVED_AT,
        match_method="address",
        query_scope="current_bylaw",
        raw_snapshot_path="raw/zoning.json",
    )

    assert source.raw_snapshot_path == "raw/zoning.json"
    assert source.match_method == "address"


def test_property_record_exposes_official_enrichment_scope():
    record = PropertyRecord(
        property_id="property:1",
        address="1 Example Street",
        municipality="City of Vancouver",
        school_district="SD39",
        sold_history_scope="last_three_full_calendar_years",
        official_enrichment_status={"bca-assessment": "SUCCESS"},
    )

    assert record.school_district == "SD39"
    assert record.sold_history_scope == "last_three_full_calendar_years"
    assert record.official_enrichment_status["bca-assessment"] == "SUCCESS"


def test_school_performance_adapter_joins_official_catchment_to_databc_fsa(tmp_path):
    csv_body = """SCHOOL_YEAR,DATA_LEVEL,PUBLIC_OR_INDEPENDENT,SCHOOL_NAME,SUB_POPULATION,GRADE,FSA_SKILL_CODE,NUMBER_EXPECTED_WRITERS,NUMBER_ONTRACK,NUMBER_EXTENDING
2025/2026,School Level,Public School,Tecumseh Elementary,All Students,04,LITERACY,100,70,10
2025/2026,School Level,Public School,Tecumseh Elementary,All Students,04,NUMERACY,100,60,20
2025/2026,School Level,Public School,David Thompson Secondary,All Students,07,LITERACY,100,80,10
2025/2026,School Level,Public School,David Thompson Secondary,All Students,07,NUMERACY,100,70,20
2025/2026,School Level,Public School,École Exemple,All Students,04,LITERACY,100,70,10
""".encode("cp1252")

    def responder(request):
        return OfficialHttpResponse(
            url=request["url"],
            status_code=200,
            headers={"content-type": "text/csv"},
            content=csv_body,
        )

    adapter = SchoolPerformanceAdapter(
        name="school-performance",
        source_registry_id="source:school-performance",
        source_url="https://catalogue.data.gov.bc.ca/fsa.csv",
        raw_snapshot_dir=tmp_path / "raw",
        config={
            "enabled": True,
            "dataset_url": "https://catalogue.data.gov.bc.ca/fsa.csv",
            "latest_school_year": "2025/2026",
            "source_version": "test-fsa-v1",
            "allowed_domains": ["catalogue.data.gov.bc.ca"],
            "max_requests_per_run": 1,
        },
        transport=responder,
    )
    record = _property(
        official_elementary_catchment="Tecumseh",
        official_secondary_catchment="David Thompson",
    )

    batch = adapter.collect_for_records([record], OBSERVED_AT)

    assert batch.rows[0].status is OfficialQueryStatus.SUCCESS
    assert batch.rows[0].fields["official_elementary_score"] == 5.6
    assert batch.rows[0].fields["official_secondary_score"] == 2.7
    assert batch.rows[0].match_method == "OFFICIAL_SCHOOL_NAME+FSA_DATASET"


def test_school_adapters_keep_their_own_public_collection_methods():
    assert SchoolCatchmentAdapter.collect_for_records.__qualname__ == (
        "SchoolCatchmentAdapter.collect_for_records"
    )
    assert SchoolPerformanceAdapter.collect_for_records.__qualname__ == (
        "SchoolPerformanceAdapter.collect_for_records"
    )


def test_vancouver_school_address_lookup_includes_official_av_variant(tmp_path):
    adapter = SchoolCatchmentAdapter(
        name="school-catchment",
        source_registry_id="source:school-catchment",
        source_url="https://vsb.example/catchment",
        raw_snapshot_dir=tmp_path / "raw",
        config={
            "lookup_mode": "vsb_boundary_zip",
            "address_search_url": "https://maps.vancouver.ca/address/query",
        },
    )

    requests = adapter._address_requests(
        _property(address="123 E 64TH AVENUE")
    )
    where_values = [request["params"]["where"] for request in requests]

    assert any("123 E 64TH AV" in where for where in where_values)
    drive_requests = adapter._address_requests(
        _property(address="200 WORTHINGTON DRIVE")
    )
    drive_where_values = [request["params"]["where"] for request in drive_requests]
    assert any("200 WORTHINGTON DRIVE" in where for where in drive_where_values)
    ordinal_requests = adapter._address_requests(
        _property(address="2232 E 45 AVENUE")
    )
    ordinal_where_values = [request["params"]["where"] for request in ordinal_requests]
    assert any("2232 E 45TH AV" in where for where in ordinal_where_values)
    undirected_requests = adapter._address_requests(
        _property(address="6944 E MAIN STREET")
    )
    undirected_where_values = [request["params"]["where"] for request in undirected_requests]
    assert any("6944 MAIN ST" in where for where in undirected_where_values)
    punctuation_requests = adapter._address_requests(
        _property(address="5743 ST. MARGARETS STREET")
    )
    punctuation_where_values = [request["params"]["where"] for request in punctuation_requests]
    assert any("5743 ST. MARGARETS ST" in where for where in punctuation_where_values)


def test_vancouver_school_address_batch_uses_one_official_query_for_records(tmp_path):
    adapter = SchoolCatchmentAdapter(
        name="school-catchment",
        source_registry_id="source:school-catchment",
        source_url="https://vsb.example/catchment",
        raw_snapshot_dir=tmp_path / "raw",
        config={
            "lookup_mode": "vsb_boundary_zip",
            "address_search_url": "https://maps.vancouver.ca/address/query",
        },
    )

    request = adapter._address_batch_request([
        _property(address="123 E 64TH AVENUE"),
        _property(property_id="property:2", address="200 W 10TH AVENUE"),
    ])

    assert request["query_step"] == "address_parcels"
    assert request["property_id"] == "__address_parcels__"
    assert "address IN" in request["params"]["where"]
    assert "123 E 64TH AV" in request["params"]["where"]


def test_vancouver_school_boundary_batch_returns_no_match_without_success_rows(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(
        "northshore_watchdog.research.official_enrichment.parse_vsb_boundary_zip",
        lambda content, source_url, observed_at: [],
    )

    def responder(request):
        body = b'{"features": []}' if request["query_step"] == "address_parcels" else b"zip"
        return OfficialHttpResponse(
            url=request["url"],
            status_code=200,
            headers={"content-type": "application/octet-stream"},
            content=body,
        )

    adapter = SchoolCatchmentAdapter(
        name="school-catchment",
        source_registry_id="source:school-catchment",
        source_url="https://vsb.example/catchment",
        raw_snapshot_dir=tmp_path / "raw",
        config={
            "lookup_mode": "vsb_boundary_zip",
            "address_search_url": "https://maps.vancouver.ca/address/query",
            "elementary_boundary_url": "https://vsb.example/elementary.zip",
            "secondary_boundary_url": "https://vsb.example/secondary.zip",
            "allowed_domains": ["maps.vancouver.ca", "vsb.example"],
        },
        transport=responder,
    )

    batch = adapter.collect_for_records([_property()], OBSERVED_AT)

    assert batch.rows[0].status is OfficialQueryStatus.NO_MATCH
    assert batch.source_run.records[0]["status"] == "NO_MATCH"


def test_vancouver_school_boundary_uses_listing_coordinate_when_address_is_unresolved(
    tmp_path,
    monkeypatch,
):
    latitude = 49.263683
    longitude = -123.053982
    origin = 20037508.34
    x = longitude * origin / 180.0
    y = latitude * 3.141592653589793 / 180.0
    y = origin * __import__("math").log(__import__("math").tan(__import__("math").pi / 4.0 + y / 2.0)) / __import__("math").pi

    def fake_parse(content, source_url, observed_at):
        del content, observed_at
        school = "Maquinna" if "elementary" in source_url else "Vancouver Technical Secondary"
        return [{
            "school_name": school,
            "rings": [[[x - 100, y - 100], [x + 100, y - 100], [x + 100, y + 100], [x - 100, y - 100]]],
        }]

    monkeypatch.setattr(
        "northshore_watchdog.research.official_enrichment.parse_vsb_boundary_zip",
        fake_parse,
    )

    def responder(request):
        body = b'{"features": []}' if request["query_step"] == "address_parcels" else b"zip"
        return OfficialHttpResponse(
            url=request["url"],
            status_code=200,
            headers={"content-type": "application/octet-stream"},
            content=body,
        )

    adapter = SchoolCatchmentAdapter(
        name="school-catchment",
        source_registry_id="source:school-catchment",
        source_url="https://vsb.example/catchment",
        raw_snapshot_dir=tmp_path / "raw",
        config={
            "lookup_mode": "vsb_boundary_zip",
            "address_search_url": "https://maps.vancouver.ca/address/query",
            "elementary_boundary_url": "https://vsb.example/elementary.zip",
            "secondary_boundary_url": "https://vsb.example/secondary.zip",
            "boundary_cache_dir": str(tmp_path / "school-boundaries"),
            "allowed_domains": ["maps.vancouver.ca", "vsb.example"],
        },
        transport=responder,
    )

    batch = adapter.collect_for_records([
        _property(
            address="2530 7TH AVENUE",
            latitude=latitude,
            longitude=longitude,
        )
    ], OBSERVED_AT)

    row = batch.rows[0]
    assert row.status is OfficialQueryStatus.SUCCESS
    assert row.fields["official_elementary_catchment"] == "Maquinna"
    assert row.fields["official_secondary_catchment"] == "Vancouver Technical Secondary"
    assert row.match_method == "LISTING_COORDINATE+VSB_BOUNDARY_POLYGON"
    assert any("listing coordinates" in warning for warning in row.warnings)


def test_vancouver_school_boundary_files_are_prefetched_and_reused(tmp_path, monkeypatch):
    calls = []

    def fake_parse(content, source_url, observed_at):
        del content, observed_at
        return [{"school_name": source_url.rsplit("/", 1)[-1], "rings": [[[0, 0], [1, 0], [1, 1], [0, 0]]], "source_url": source_url}]

    monkeypatch.setattr(
        "northshore_watchdog.research.official_enrichment.parse_vsb_boundary_zip",
        fake_parse,
    )

    def responder(request):
        calls.append(request["query_step"])
        return OfficialHttpResponse(
            url=request["url"],
            status_code=200,
            headers={"content-type": "application/octet-stream"},
            content=b"zip",
        )

    config = {
        "lookup_mode": "vsb_boundary_zip",
        "elementary_boundary_url": "https://vsb.example/elementary.zip",
        "secondary_boundary_url": "https://vsb.example/secondary.zip",
        "boundary_cache_dir": str(tmp_path / "school-boundaries"),
        "source_version": "test-v1",
        "allowed_domains": ["vsb.example"],
    }
    first = SchoolCatchmentAdapter(
        name="school-catchment",
        source_registry_id="source:school-catchment",
        source_url="https://vsb.example/catchment",
        raw_snapshot_dir=tmp_path / "raw",
        config=config,
        transport=responder,
    )

    refreshed = first.prefetch_boundary_cache(OBSERVED_AT)

    assert refreshed["status"] == "REFRESHED"
    assert refreshed["cache_hit"] is False
    assert refreshed["boundary_counts"] == {"elementary": 1, "secondary": 1}
    assert Path(refreshed["cache_path"]).exists()
    assert calls == ["elementary_boundary", "secondary_boundary"]

    def fail_if_called(request):
        raise AssertionError(f"cache hit should not fetch {request['query_step']}")

    second = SchoolCatchmentAdapter(
        name="school-catchment",
        source_registry_id="source:school-catchment",
        source_url="https://vsb.example/catchment",
        raw_snapshot_dir=tmp_path / "raw",
        config=config,
        transport=fail_if_called,
    )

    cached = second.prefetch_boundary_cache(OBSERVED_AT)

    assert cached["status"] == "CACHE_HIT"
    assert cached["cache_hit"] is True
    assert cached["boundary_counts"] == {"elementary": 1, "secondary": 1}


def test_fsa_rate_uses_ontrack_when_extending_is_suppressed():
    rate = SchoolPerformanceAdapter._rate(
        {
            "NUMBER_EXPECTED_WRITERS": "80",
            "NUMBER_ONTRACK": "40",
            "NUMBER_EXTENDING": "Msk",
        }
    )

    assert rate == 0.5


def _property(**updates):
    values = {
        "property_id": "property:1",
        "address": "100 Example Street",
        "municipality": "City of Vancouver",
        "pid": "012-345-678",
    }
    values.update(updates)
    return PropertyRecord(**values)


def test_identity_matching_prefers_pid_over_address():
    match = match_official_identity(
        _property(),
        [
            {"pid": "999-999-999", "address": "100 Example Street", "municipality": "City of Vancouver"},
            {"pid": "012-345-678", "address": "100 Example Street", "municipality": "City of Vancouver"},
        ],
    )

    assert match.status is OfficialQueryStatus.SUCCESS
    assert match.match_method == "PID"
    assert match.candidate["pid"] == "012-345-678"


def test_identity_matching_uses_exact_address_when_pid_is_missing():
    match = match_official_identity(
        _property(pid=None),
        [{"address": "100 Example Street", "municipality": "City of Vancouver"}],
    )

    assert match.status is OfficialQueryStatus.SUCCESS
    assert match.match_method == "ADDRESS"


def test_identity_matching_normalizes_direction_order_and_ordinal_street_number():
    record = _property(pid=None, address="648 E 65TH AVENUE")
    match = match_official_identity(
        record,
        [{"address": "648 65TH AVE E VANCOUVER", "municipality": "City of Vancouver"}],
    )

    assert match.status is OfficialQueryStatus.SUCCESS
    assert match.match_method == "ADDRESS"


def test_address_core_does_not_treat_six_letter_street_as_postal_code():
    assert _address_core("2834 MURRAY DR VICTORIA") == "2834 murray dr"
    assert _addresses_match(
        "2834 VICTORIA DRIVE Vancouver, British Columbia V5N4L6",
        "2834 MURRAY DR VICTORIA",
    ) is False


def test_identity_matching_resolves_true_candidate_after_address_normalization():
    record = _property(
        pid=None,
        address="2834 VICTORIA DRIVE",
        municipality="City of Vancouver",
    )
    match = match_official_identity(
        record,
        [
            {"address": "2834 MURRAY DR VICTORIA", "municipality": "City of Vancouver"},
            {"address": "2834 VICTORIA DR VANCOUVER", "municipality": "City of Vancouver"},
        ],
    )

    assert match.status is OfficialQueryStatus.SUCCESS
    assert match.candidate["address"] == "2834 VICTORIA DR VANCOUVER"


def test_identity_matching_does_not_choose_between_multiple_official_results():
    match = match_official_identity(
        _property(pid=None),
        [
            {"address": "100 Example Street", "municipality": "City of Vancouver", "pid": "1"},
            {"address": "100 Example Street", "municipality": "City of Vancouver", "pid": "2"},
        ],
    )

    assert match.status is OfficialQueryStatus.AMBIGUOUS
    assert match.candidate is None


def test_identity_matching_excludes_other_municipality_candidate():
    # Province-wide BCA search may return the same civic address in another
    # city; the municipality check must exclude it rather than go ambiguous.
    record = _property(pid=None, address="4343 11TH AVENUE")
    match = match_official_identity(
        record,
        [
            {"address": "4343 11TH AVE W VANCOUVER", "municipality": "City of Vancouver", "municipality_inferred": True},
            {"address": "4343 11TH AVE PORT ALBERNI", "municipality": "City of Port Alberni", "municipality_inferred": True},
        ],
    )

    assert match.status is OfficialQueryStatus.SUCCESS
    assert match.candidate["address"] == "4343 11TH AVE W VANCOUVER"


def test_identity_matching_prefers_inferred_municipality_candidate():
    # A locality not in the table stays un-inferred; the confirmed candidate
    # wins rather than leaving the lookup ambiguous.
    record = _property(pid=None, address="4343 11TH AVENUE")
    match = match_official_identity(
        record,
        [
            {"address": "4343 11TH AVE W VANCOUVER", "municipality": "City of Vancouver", "municipality_inferred": True},
            {"address": "4343 11TH AVE CHETWYND", "municipality": "City of Vancouver", "municipality_inferred": False},
        ],
    )

    assert match.status is OfficialQueryStatus.SUCCESS
    assert match.match_method == "ADDRESS_MUNICIPALITY_CONFIRMED"
    assert match.candidate["address"] == "4343 11TH AVE W VANCOUVER"


def test_identity_matching_resolves_direction_twin_by_longitude():
    record = _property(pid=None, address="4343 11TH AVENUE", longitude=-123.198)
    match = match_official_identity(
        record,
        [
            {"address": "4343 11TH AVE W VANCOUVER", "municipality": "City of Vancouver"},
            {"address": "4343 11TH AVE E VANCOUVER", "municipality": "City of Vancouver"},
        ],
    )

    assert match.status is OfficialQueryStatus.SUCCESS
    assert match.match_method == "ADDRESS_DIRECTION_GEO"
    assert match.candidate["address"] == "4343 11TH AVE W VANCOUVER"


def test_identity_matching_direction_twin_stays_ambiguous_without_coordinates():
    record = _property(pid=None, address="4343 11TH AVENUE")
    match = match_official_identity(
        record,
        [
            {"address": "4343 11TH AVE W VANCOUVER", "municipality": "City of Vancouver"},
            {"address": "4343 11TH AVE E VANCOUVER", "municipality": "City of Vancouver"},
        ],
    )

    assert match.status is OfficialQueryStatus.AMBIGUOUS


def test_identity_matching_direction_twin_stays_ambiguous_near_split():
    # Inside the dead zone the longitude cannot safely choose a side.
    record = _property(pid=None, address="4343 11TH AVENUE", longitude=-123.1066)
    match = match_official_identity(
        record,
        [
            {"address": "4343 11TH AVE W VANCOUVER", "municipality": "City of Vancouver"},
            {"address": "4343 11TH AVE E VANCOUVER", "municipality": "City of Vancouver"},
        ],
    )

    assert match.status is OfficialQueryStatus.AMBIGUOUS


def test_identity_matching_reports_pid_mismatch_as_conflict():
    match = match_official_identity(
        _property(),
        [{"address": "100 Example Street", "municipality": "City of Vancouver", "pid": "999-999-999"}],
    )

    assert match.status is OfficialQueryStatus.AMBIGUOUS
    assert "PID_MISMATCH" in match.warnings


def test_bc_assessment_parser_preserves_assessment_and_recent_sales_scope():
    payload = {
        "results": [
            {
                "pid": "012-345-678",
                "address": "100 Example Street",
                "municipality": "City of Vancouver",
                "legal_description": "LOT 1 BLOCK 2 PLAN 3",
                "assessment_year": 2026,
                "assessment_total": "$1,700,000",
                "assessment_land": "$1,500,000",
                "assessment_improvement": "$200,000",
                "previous_year_total": 1_650_000,
                "previous_year_land": 1_450_000,
                "sales_history": [{"date": "2025-04-02", "price": "$1,250,000"}],
            }
        ]
    }

    rows = parse_bc_assessment_payload(
        __import__("json").dumps(payload).encode("utf-8"),
        "https://assessment.example/property/012-345-678",
        OBSERVED_AT,
    )

    assert rows[0]["assessment_land"] == 1_500_000
    assert rows[0]["previous_sale_prices"] == [1_250_000]
    assert rows[0]["sold_history_scope"] == "last_three_full_calendar_years"


def test_bc_assessment_parser_reads_public_portal_detail_html():
    content = b"""
    <h1 id="mainaddresstitle">1379 HAYWOOD AVE WEST VANCOUVER V7T 1V4</h1>
    <div id="lblLastAssessmentDate">2026 assessment as of July 1, 2025</div>
    <span id="lblTotalAssessedValue">$2,599,000</span>
    <p id="lblTotalAssessedLand">$2,488,000</p>
    <p id="lblTotalAssessedBuilding">$111,000</p>
    <p id="lblPreviousAssessedValue">$2,620,000</p>
    <p id="lblPreviousAssessedLand">$2,502,000</p>
    <p id="lblPreviousAssessedBuilding">$118,000</p>
    <td id="lblYearBuilt">1981</td>
    <td id="lblBedrooms">2</td>
    <td id="lblBathRooms">3</td>
    <td id="lblLandSize">7500 Sq Ft</td>
    <div id="lblLegalDescription"><p>LOT 9, PLAN VAP3604</p><p>PID: 005-086-019</p></div>
    <div id="lblSalesHistory"><table><tr class="salesrow"><td>Oct 23, 2025</td><td>$1,700,000</td></tr></table></div>
    """

    rows = parse_bc_assessment_payload(
        content,
        "https://www.bcassessment.ca/Property/PropertyInformation",
        OBSERVED_AT,
    )

    assert rows[0]["assessment_total"] == 2_599_000
    assert rows[0]["assessment_land"] == 2_488_000
    assert rows[0]["assessment_improvement"] == 111_000
    assert rows[0]["previous_year_total"] == 2_620_000
    assert rows[0]["pid"] == "005-086-019"
    assert rows[0]["year_built"] == 1981
    assert rows[0]["previous_sale_prices"] == [1_700_000]


def test_bc_assessment_parser_does_not_treat_dimension_string_as_lot_area():
    content = b"""
    <h1 id="mainaddresstitle">5322 CULLODEN ST VANCOUVER V5W 3R6</h1>
    <span id="lblTotalAssessedValue">$1,742,800</span>
    <p id="lblTotalAssessedLand">$1,705,000</p>
    <p id="lblTotalAssessedBuilding">$37,800</p>
    <td id="lblLandSize">33 x 125.3 Ft</td>
    <div id="lblLegalDescription"><p>LOT 20, BLOCK 3, PLAN VAP1383</p><p>PID: 014-801-850</p></div>
    """

    rows = parse_bc_assessment_payload(
        content,
        "https://www.bcassessment.ca/Property/PropertyInformation",
        OBSERVED_AT,
    )

    assert rows[0]["frontage_ft"] == 33
    assert rows[0]["depth_ft"] == 125.3
    assert rows[0].get("lot_area_sqft") != 33


def test_bc_assessment_adapter_public_portal_resolves_search_then_detail(tmp_path):
    search_body = __import__("json").dumps([
        {"label": "1379 HAYWOOD AVE WEST VANCOUVER", "value": "OID-TOKEN"}
    ]).encode()
    detail_body = b"""
    <h1 id="mainaddresstitle">1379 HAYWOOD AVE WEST VANCOUVER V7T 1V4</h1>
    <div id="lblLastAssessmentDate">2026 assessment as of July 1, 2025</div>
    <span id="lblTotalAssessedValue">$2,599,000</span>
    <p id="lblTotalAssessedLand">$2,488,000</p>
    <p id="lblTotalAssessedBuilding">$111,000</p>
    <div id="lblLegalDescription"><p>LOT 9, PLAN VAP3604</p><p>PID: 005-086-019</p></div>
    <div id="lblSalesHistory"><table><tr class="salesrow"><td>Oct 23, 2025</td><td>$1,700,000</td></tr></table></div>
    """
    calls = []

    def responder(request):
        calls.append(request)
        if "/GetByAddress" in request["url"]:
            return OfficialHttpResponse(
                url=request["url"],
                status_code=200,
                headers={"content-type": "application/json"},
                content=search_body,
            )
        return OfficialHttpResponse(
            url=request["url"],
            status_code=200,
            headers={"content-type": "text/html"},
            content=detail_body,
        )

    adapter = BCAssessmentAdapter(
        name="bca-assessment",
        source_registry_id="source:bca-assessment",
        source_url="https://www.bcassessment.ca/",
        raw_snapshot_dir=tmp_path / "raw",
        config={
            "enabled": True,
            "allowed_domains": ["www.bcassessment.ca"],
            "lookup_mode": "public_portal",
            "address_search_url": "https://www.bcassessment.ca/Property/Search/GetByAddress?addr={address}",
            "property_information_url": "https://www.bcassessment.ca/Property/PropertyInformation",
            "max_requests_per_run": 4,
        },
        transport=responder,
    )

    batch = adapter.collect_for_records([_property(pid=None, address="1379 Haywood Avenue", municipality="District of West Vancouver")], OBSERVED_AT)

    assert len(calls) == 2
    assert calls[0]["method"] == "GET"
    assert calls[1]["method"] == "POST"
    assert calls[1]["data"]["oid"] == "OID-TOKEN"
    assert batch.rows[0].status is OfficialQueryStatus.SUCCESS
    assert batch.rows[0].fields["assessment_land"] == 2_488_000
    assert batch.rows[0].fields["pid"] == "005-086-019"
    assert batch.rows[0].query_scope == "last_three_full_calendar_years"


def test_bc_assessment_adapter_retries_address_variant_after_no_results(tmp_path):
    valid_search = __import__("json").dumps([
        {"label": "2232 45TH AVE E VANCOUVER", "value": "OID-VARIANT"}
    ]).encode()
    detail_body = b"""
    <h1 id="mainaddresstitle">2232 45TH AVE E VANCOUVER V5P 1N8</h1>
    <span id="lblTotalAssessedValue">$1,700,000</span>
    <p id="lblTotalAssessedLand">$1,500,000</p>
    <p id="lblTotalAssessedBuilding">$200,000</p>
    <div id="lblLegalDescription"><p>LOT 1, PLAN VAP1</p><p>PID: 005-000-001</p></div>
    """
    calls = []

    def responder(request):
        calls.append(request)
        if "/GetByAddress" in request["url"]:
            if "45TH" in request["url"]:
                return OfficialHttpResponse(
                    url=request["url"],
                    status_code=200,
                    headers={"content-type": "application/json"},
                    content=valid_search,
                )
            return OfficialHttpResponse(
                url=request["url"],
                status_code=200,
                headers={"content-type": "application/json"},
                content=b'[{"label":"No results","value":0,"gid":0}]',
            )
        return OfficialHttpResponse(
            url=request["url"],
            status_code=200,
            headers={"content-type": "text/html"},
            content=detail_body,
        )

    adapter = BCAssessmentAdapter(
        name="bca-assessment",
        source_registry_id="source:bca-assessment",
        source_url="https://www.bcassessment.ca/",
        raw_snapshot_dir=tmp_path / "raw",
        config={
            "enabled": True,
            "allowed_domains": ["www.bcassessment.ca"],
            "lookup_mode": "public_portal",
            "address_search_url": "https://www.bcassessment.ca/Property/Search/GetByAddress?addr={address}",
            "property_information_url": "https://www.bcassessment.ca/Property/PropertyInformation",
            "max_requests_per_run": 6,
        },
        transport=responder,
    )

    batch = adapter.collect_for_records(
        [_property(pid=None, address="2232 E 45 AVENUE")],
        OBSERVED_AT,
    )

    search_calls = [call for call in calls if call.get("phase") == "search"]
    assert len(search_calls) >= 2
    assert search_calls[0]["query_variant"] == "original"
    assert any("45TH" in call["url"] for call in search_calls)
    assert calls[-1]["phase"] == "detail"
    assert batch.rows[0].status is OfficialQueryStatus.SUCCESS


def test_bc_assessment_adapter_continues_fuzzy_variants_after_transport_failure(tmp_path):
    valid_search = __import__("json").dumps([
        {"label": "3910 10TH AVE W VANCOUVER", "value": "OID-TRANSPORT-RETRY"}
    ]).encode()
    detail_body = b"""
    <h1 id="mainaddresstitle">3910 10TH AVE W VANCOUVER V6R 2H1</h1>
    <span id="lblTotalAssessedValue">$2,242,200</span>
    <p id="lblTotalAssessedLand">$2,212,000</p>
    <p id="lblTotalAssessedBuilding">$30,200</p>
    <div id="lblLegalDescription"><p>LOT 1, PLAN VAP1</p><p>PID: 005-000-002</p></div>
    """
    calls = []
    failures = {"count": 0}

    def responder(request):
        calls.append(request)
        if "/GetByAddress" in request["url"]:
            if failures["count"] == 0:
                failures["count"] += 1
                raise OSError("temporary TLS failure")
            if "10TH%20AVE%20W" in request["url"]:
                return OfficialHttpResponse(
                    url=request["url"],
                    status_code=200,
                    headers={"content-type": "application/json"},
                    content=valid_search,
                )
            return OfficialHttpResponse(
                url=request["url"],
                status_code=200,
                headers={"content-type": "application/json"},
                content=b'[{"label":"No results","value":0,"gid":0}]',
            )
        return OfficialHttpResponse(
            url=request["url"],
            status_code=200,
            headers={"content-type": "text/html"},
            content=detail_body,
        )

    adapter = BCAssessmentAdapter(
        name="bca-assessment",
        source_registry_id="source:bca-assessment",
        source_url="https://www.bcassessment.ca/",
        raw_snapshot_dir=tmp_path / "raw",
        config={
            "enabled": True,
            "allowed_domains": ["www.bcassessment.ca"],
            "lookup_mode": "public_portal",
            "address_search_url": "https://www.bcassessment.ca/Property/Search/GetByAddress?addr={address}",
            "property_information_url": "https://www.bcassessment.ca/Property/PropertyInformation",
            "max_requests_per_run": 12,
            "retry_attempts": 0,
        },
        transport=responder,
    )

    batch = adapter.collect_for_records(
        [_property(pid=None, address="3910 W 10TH AVENUE")],
        OBSERVED_AT,
    )

    search_calls = [call for call in calls if call.get("phase") == "search"]
    assert len(search_calls) >= 2
    assert batch.rows[0].status is OfficialQueryStatus.SUCCESS
    assert batch.rows[0].match_method == "ADDRESS"
    assert any("10TH%20AVE%20W" in call["url"] for call in search_calls)


def test_bc_assessment_matcher_disambiguates_same_address_by_municipality():
    record = _property(pid=None, address="120 KOOTENAY STREET")
    record = record.model_copy(update={"municipality": "City of Vancouver"})
    candidates = _parse_bca_search_candidates(
        b'[{"label":"120 KOOTENAY ST KITIMAT","value":"kitimat-oid"},'
        b'{"label":"120 KOOTENAY ST VANCOUVER","value":"vancouver-oid"}]'
    )
    for candidate in candidates:
        candidate["municipality"] = _municipality_from_address(candidate["address"])

    identity = match_official_identity(record, candidates)

    assert identity.status is OfficialQueryStatus.SUCCESS
    assert identity.candidate is not None
    assert identity.candidate["oid"] == "vancouver-oid"


def test_bc_assessment_adapter_returns_snapshot_linked_success_row(tmp_path):
    body = __import__("json").dumps({
        "results": [{
            "pid": "012-345-678",
            "address": "100 Example Street",
            "municipality": "City of Vancouver",
            "assessment_year": 2026,
            "assessment_total": 1_700_000,
            "assessment_land": 1_500_000,
            "assessment_improvement": 200_000,
        }]
    }).encode("utf-8")

    def responder(request):
        return OfficialHttpResponse(
            url=request["url"],
            status_code=200,
            headers={"content-type": "application/json"},
            content=body,
        )

    adapter = BCAssessmentAdapter(
        name="bca-assessment",
        source_registry_id="source:bca-assessment",
        source_url="https://assessment.example",
        raw_snapshot_dir=tmp_path / "raw",
        config={
            "enabled": True,
            "allowed_domains": ["assessment.example"],
            "search_url": "https://assessment.example/property/{pid}",
            "source_version": "bca-test-v1",
        },
        transport=responder,
    )

    batch = adapter.collect_for_records([_property()], OBSERVED_AT)

    assert batch.rows[0].status is OfficialQueryStatus.SUCCESS
    assert batch.rows[0].fields["assessment_total"] == 1_700_000
    assert batch.rows[0].match_method == "PID"
    assert batch.rows[0].raw_snapshot_path
    assert batch.source_run.snapshots


def test_zoning_routes_city_and_district_north_vancouver_separately():
    assert route_zoning_source("City of North Vancouver").name == "municipal-zoning-cnv"
    assert route_zoning_source("District of North Vancouver").name == "municipal-zoning-dnv"
    assert route_zoning_source("District of West Vancouver").name == "municipal-zoning-wv"


def test_zoning_parser_does_not_infer_units_without_matching_bylaw_rule():
    payload = {
        "results": [{
            "address": "100 Example Street",
            "municipality": "City of Vancouver",
            "zoning": "RS-1",
            "bylaw_version": "3210",
            "rules": {"zoning": "RM-1", "maximum_units": 6, "maximum_fsr": 1.2},
        }]
    }

    rows = parse_zoning_payload(
        __import__("json").dumps(payload).encode("utf-8"),
        "https://city.example/zoning/100",
        OBSERVED_AT,
    )

    assert rows[0]["zoning"] == "RS-1"
    assert rows[0]["bylaw_version"] == "3210"
    assert "maximum_units" not in rows[0]
    assert "maximum_fsr" not in rows[0]


def test_zoning_adapter_returns_official_rule_fields_when_codes_match(tmp_path):
    body = __import__("json").dumps({
        "results": [{
            "pid": "012-345-678",
            "address": "100 Example Street",
            "municipality": "City of Vancouver",
            "zoning": "RS-1",
            "bylaw_version": "3210",
            "rules": {"zoning": "RS-1", "maximum_units": 2, "maximum_fsr": 0.75},
        }]
    }).encode("utf-8")

    def responder(request):
        return OfficialHttpResponse(
            url=request["url"],
            status_code=200,
            headers={"content-type": "application/json"},
            content=body,
        )

    adapter = MunicipalZoningAdapter(
        name="municipal-zoning-cnv",
        source_registry_id="source:municipal-zoning-cnv",
        source_url="https://city.example/zoning",
        raw_snapshot_dir=tmp_path / "raw",
        config={
            "enabled": True,
            "allowed_domains": ["city.example"],
            "search_url": "https://city.example/zoning/{pid}",
            "municipality": "City of Vancouver",
            "source_version": "zoning-test-v1",
        },
        transport=responder,
    )

    batch = adapter.collect_for_records([_property()], OBSERVED_AT)

    assert batch.rows[0].status is OfficialQueryStatus.SUCCESS
    assert batch.rows[0].fields["maximum_units"] == 2
    assert batch.rows[0].fields["maximum_fsr"] == 0.75
    assert batch.rows[0].query_scope == "current_bylaw"


def test_vancouver_arcgis_zoning_adapter_resolves_address_parcel_then_zoning(tmp_path):
    parcel_body = __import__("json").dumps({
        "features": [{
            "attributes": {"address": "5322 CULLODEN ST"},
            "geometry": {"rings": [[[1, 1], [1, 2], [2, 2], [2, 1], [1, 1]]]},
        }]
    }).encode()
    zoning_body = __import__("json").dumps({
        "features": [{
            "attributes": {
                "zoning": "R1-1",
                "zonename": "R1-1",
                "zonedesc": "Residential Inclusive",
            }
        }]
    }).encode()
    calls = []

    def responder(request):
        calls.append(request)
        body = parcel_body if request.get("query_step") == "address_parcel" else zoning_body
        return OfficialHttpResponse(
            url=request["url"],
            status_code=200,
            headers={"content-type": "application/json"},
            content=body,
        )

    adapter = MunicipalZoningAdapter(
        name="municipal-zoning-vancouver",
        source_registry_id="source:municipal-zoning",
        source_url="https://city.example/zoning",
        raw_snapshot_dir=tmp_path / "raw",
        config={
            "enabled": True,
            "lookup_mode": "arcgis_address_spatial",
            "allowed_domains": ["city.example"],
            "parcel_search_url": "https://city.example/parcel/query",
            "zoning_query_url": "https://city.example/zoning/query",
            "source_version": "zoning-arcgis-test-v1",
        },
        transport=responder,
    )

    record = _property(address="5322 Culloden Street", municipality="City of Vancouver")
    batch = adapter.collect_for_records([record], OBSERVED_AT)

    assert [request["query_step"] for request in calls] == ["address_parcel", "zoning_polygon"]
    assert batch.rows[0].status is OfficialQueryStatus.SUCCESS
    assert batch.rows[0].fields["zoning"] == "R1-1"
    assert batch.rows[0].fields["current_zoning"] == "R1-1"
    assert batch.rows[0].match_method == "ADDRESS_VARIANT+PARCEL_CENTROID"


def test_address_query_variants_cover_city_suffix_and_direction_forms():
    variants = [value for value, _ in _address_query_variants("3910 W 10TH AVENUE")]

    assert "3910 W 10TH AV" in variants
    assert "3910 10TH AV W" in variants
    assert "3910 10TH AV" in variants


def test_vancouver_arcgis_zoning_uses_constrained_address_variant_for_avenue(tmp_path):
    parcel_body = __import__("json").dumps({
        "features": [{
            "attributes": {"address": "3910 W 10TH AV", "objectid": 61035686},
            "geometry": {"rings": [[[1, 1], [1, 2], [2, 2], [2, 1], [1, 1]]]},
        }]
    }).encode()
    zoning_body = __import__("json").dumps({
        "features": [{
            "attributes": {"zoning": "R1-1", "zonename": "R1-1"},
        }]
    }).encode()
    calls = []

    def responder(request):
        calls.append(request)
        if request.get("query_step") == "address_parcel":
            assert "3910 W 10TH AV" in request["params"]["where"]
            assert "LIKE '3910 W 10TH AV%'" in request["params"]["where"]
            body = parcel_body
        else:
            body = zoning_body
        return OfficialHttpResponse(
            url=request["url"],
            status_code=200,
            headers={"content-type": "application/json"},
            content=body,
        )

    adapter = MunicipalZoningAdapter(
        name="municipal-zoning-vancouver",
        source_registry_id="source:municipal-zoning",
        source_url="https://city.example/zoning",
        raw_snapshot_dir=tmp_path / "raw",
        config={
            "enabled": True,
            "lookup_mode": "arcgis_address_spatial",
            "allowed_domains": ["city.example"],
            "parcel_search_url": "https://city.example/parcel/query",
            "zoning_query_url": "https://city.example/zoning/query",
            "source_version": "zoning-arcgis-test-v1",
        },
        transport=responder,
    )

    batch = adapter.collect_for_records(
        [_property(address="3910 W 10TH AVENUE", municipality="City of Vancouver")],
        OBSERVED_AT,
    )

    assert [request["query_step"] for request in calls] == ["address_parcel", "zoning_polygon"]
    assert batch.rows[0].status is OfficialQueryStatus.SUCCESS
    assert batch.rows[0].fields["zoning"] == "R1-1"
    assert batch.rows[0].match_method == "ADDRESS_VARIANT+PARCEL_CENTROID"


def test_vancouver_arcgis_zoning_falls_back_to_listing_coordinate_for_missing_address(tmp_path):
    parcel_body = __import__("json").dumps({
        "features": [{
            "attributes": {"address": "2530 E 7TH AV", "objectid": 61056881},
            "geometry": {"rings": [[[1, 1], [1, 2], [2, 2], [2, 1], [1, 1]]]},
        }]
    }).encode()
    zoning_body = __import__("json").dumps({
        "features": [{"attributes": {"zoning": "R1-1", "zonename": "R1-1"}}]
    }).encode()
    calls = []

    def responder(request):
        calls.append(request)
        if request.get("query_step") == "address_parcel":
            body = b'{"features": []}'
        elif request.get("query_step") == "address_parcel_coordinate_fallback":
            assert request["params"]["geometry"] == "-123.053982,49.263683"
            body = parcel_body
        else:
            assert request["params"]["geometryType"] == "esriGeometryPoint"
            body = zoning_body
        return OfficialHttpResponse(
            url=request["url"],
            status_code=200,
            headers={"content-type": "application/json"},
            content=body,
        )

    adapter = MunicipalZoningAdapter(
        name="municipal-zoning-vancouver",
        source_registry_id="source:municipal-zoning",
        source_url="https://city.example/zoning",
        raw_snapshot_dir=tmp_path / "raw",
        config={
            "enabled": True,
            "lookup_mode": "arcgis_address_spatial",
            "allowed_domains": ["city.example"],
            "parcel_search_url": "https://city.example/parcel/query",
            "zoning_query_url": "https://city.example/zoning/query",
            "source_version": "zoning-coordinate-fallback-test-v1",
        },
        transport=responder,
    )

    batch = adapter.collect_for_records([
        _property(address="2530 7TH AVENUE", latitude=49.263683, longitude=-123.053982),
    ], OBSERVED_AT)

    assert [request["query_step"] for request in calls] == [
        "address_parcel",
        "address_parcel_coordinate_fallback",
        "zoning_polygon",
    ]
    assert batch.rows[0].status is OfficialQueryStatus.SUCCESS
    assert batch.rows[0].fields["zoning"] == "R1-1"


def test_city_road_adapter_resolves_nearest_arterial_from_street_geometry(tmp_path):
    parcel_body = __import__("json").dumps({
        "features": [{
            "attributes": {"address": "5322 CULLODEN ST"},
            "geometry": {"rings": [[[0, 0], [0, 0.001], [0.001, 0.001], [0.001, 0], [0, 0]]]},
        }]
    }).encode()
    street_body = __import__("json").dumps({
        "total_count": 1,
        "results": [{
            "hblock": "5300-5400 KNIGHT ST",
            "streetuse": "Arterial",
            "geom": {"type": "Feature", "geometry": {"type": "LineString", "coordinates": [[0, 0], [0, 0.001]]}},
        }],
    }).encode()
    calls = []

    def responder(request):
        calls.append(request)
        body = parcel_body if request.get("query_step") == "address_parcel" else street_body
        return OfficialHttpResponse(
            url=request["url"],
            status_code=200,
            headers={"content-type": "application/json"},
            content=body,
        )

    adapter = MunicipalRoadAdapter(
        name="municipal-road",
        source_registry_id="source:municipal-road",
        source_url="https://city.example/public-streets",
        raw_snapshot_dir=tmp_path / "raw",
        config={
            "enabled": True,
            "allowed_domains": ["city.example"],
            "address_search_url": "https://city.example/parcel/query",
            "street_records_url": "https://city.example/streets/records",
            "search_radius_m": 500,
            "source_version": "road-test-v1",
        },
        transport=responder,
    )

    record = _property(address="5322 Culloden Street", municipality="City of Vancouver")
    batch = adapter.collect_for_records([record], OBSERVED_AT)

    assert [request["query_step"] for request in calls] == ["address_parcel", "street_proximity"]
    assert batch.rows[0].status is OfficialQueryStatus.SUCCESS
    assert batch.rows[0].fields["nearest_major_road"] == "5300-5400 KNIGHT ST"
    assert batch.rows[0].fields["road_class"] == "B"
    assert batch.rows[0].fields["distance_to_major_road_m"] == 0.0


def test_city_road_adapter_falls_back_to_listing_coordinates(tmp_path):
    street_body = __import__("json").dumps({
        "total_count": 1,
        "results": [{
            "hblock": "5300-5400 KNIGHT ST",
            "streetuse": "Arterial",
            "geom": {"type": "Feature", "geometry": {"type": "LineString", "coordinates": [[-123.0, 49.0], [-123.0, 49.001]]}},
        }],
    }).encode()
    calls = []

    def responder(request):
        calls.append(request)
        if request.get("query_step") == "address_parcel":
            body = b'{"features": []}'
        else:
            body = street_body
        return OfficialHttpResponse(
            url=request["url"],
            status_code=200,
            headers={"content-type": "application/json"},
            content=body,
        )

    adapter = MunicipalRoadAdapter(
        name="municipal-road",
        source_registry_id="source:municipal-road",
        source_url="https://city.example/public-streets",
        raw_snapshot_dir=tmp_path / "raw",
        config={
            "enabled": True,
            "allowed_domains": ["city.example"],
            "address_search_url": "https://city.example/parcel/query",
            "street_records_url": "https://city.example/streets/records",
            "search_radius_m": 500,
            "source_version": "road-coordinate-fallback-test-v1",
        },
        transport=responder,
    )

    batch = adapter.collect_for_records(
        [_property(address="100 Coordinate Street", latitude=49.0, longitude=-123.0)],
        OBSERVED_AT,
    )

    assert [request["query_step"] for request in calls] == [
        "address_parcel",
        "coordinate_street_proximity",
    ]
    assert batch.rows[0].status is OfficialQueryStatus.SUCCESS
    assert batch.rows[0].match_method == "LISTING_COORDINATE+STREET_GEOMETRY"
    assert batch.rows[0].fields["road_class"] == "B"


def test_school_routes_municipalities_to_their_official_districts():
    assert route_school_source("City of Vancouver").district == "SD39"
    assert route_school_source("City of Burnaby").district == "SD41"
    assert route_school_source("District of North Vancouver").district == "SD44"
    assert route_school_source("District of West Vancouver").district == "SD45"


def test_school_parser_returns_elementary_and_secondary_catchments():
    payload = {
        "results": [{
            "address": "100 Example Street",
            "municipality": "City of Vancouver",
            "elementary": "Example Elementary",
            "secondary": "Example Secondary",
            "district": "SD39",
            "current_score": 82,
            "future_score": 78,
            "overflow": "Monitor capacity",
            "special_programs": ["French Immersion"],
        }]
    }

    rows = parse_school_payload(
        __import__("json").dumps(payload).encode("utf-8"),
        "https://school.example/locator",
        OBSERVED_AT,
    )

    assert rows[0]["official_elementary_catchment"] == "Example Elementary"
    assert rows[0]["official_secondary_catchment"] == "Example Secondary"
    assert rows[0]["school_district"] == "SD39"
    assert rows[0]["current_school_score"] == 82


def test_school_adapter_returns_official_catchments(tmp_path):
    body = __import__("json").dumps({
        "results": [{
            "pid": "012-345-678",
            "address": "100 Example Street",
            "municipality": "City of Vancouver",
            "elementary": "Example Elementary",
            "secondary": "Example Secondary",
            "district": "SD39",
        }]
    }).encode("utf-8")

    def responder(request):
        return OfficialHttpResponse(
            url=request["url"],
            status_code=200,
            headers={"content-type": "application/json"},
            content=body,
        )

    adapter = SchoolCatchmentAdapter(
        name="school-catchment-sd39",
        source_registry_id="source:school-catchment-sd39",
        source_url="https://school.example/locator",
        raw_snapshot_dir=tmp_path / "raw",
        config={
            "enabled": True,
            "allowed_domains": ["school.example"],
            "search_url": "https://school.example/locator/{pid}",
            "municipality": "City of Vancouver",
            "school_district": "SD39",
            "source_version": "school-test-v1",
        },
        transport=responder,
    )

    batch = adapter.collect_for_records([_property()], OBSERVED_AT)

    assert batch.rows[0].status is OfficialQueryStatus.SUCCESS
    assert batch.rows[0].fields["official_elementary_catchment"] == "Example Elementary"
    assert batch.rows[0].fields["school_district"] == "SD39"
    assert batch.rows[0].query_scope == "official_catchment_locator"


def test_sold_history_parser_keeps_official_sales_separate_from_listing_price():
    payload = {
        "results": [{
            "pid": "012-345-678",
            "address": "100 Example Street",
            "municipality": "City of Vancouver",
            "sales_history": [{"date": "2025-04-02", "price": "$1,250,000"}],
            "asking": 1_500_000,
            "listing_date": "2026-08-01",
        }]
    }

    rows = parse_sold_history_payload(
        __import__("json").dumps(payload).encode("utf-8"),
        "https://assessment.example/property/012-345-678",
        OBSERVED_AT,
    )

    assert rows[0]["previous_sale_prices"] == [1_250_000]
    assert rows[0]["sold_history_scope"] == "last_three_full_calendar_years"
    assert "asking" not in rows[0]
    assert "listing_date" not in rows[0]


def test_sold_history_adapter_returns_only_sales_fields(tmp_path):
    body = __import__("json").dumps({
        "results": [{
            "pid": "012-345-678",
            "address": "100 Example Street",
            "municipality": "City of Vancouver",
            "sales_history": [{"date": "2025-04-02", "price": 1_250_000}],
        }]
    }).encode("utf-8")

    adapter = SoldHistoryAdapter(
        name="sold-history-bca",
        source_registry_id="source:sold-history-bca",
        source_url="https://assessment.example",
        raw_snapshot_dir=tmp_path / "raw",
        config={
            "enabled": True,
            "allowed_domains": ["assessment.example"],
            "search_url": "https://assessment.example/property/{pid}",
            "source_version": "sold-test-v1",
        },
        transport=lambda request: OfficialHttpResponse(
            url=request["url"],
            status_code=200,
            headers={"content-type": "application/json"},
            content=body,
        ),
    )

    batch = adapter.collect_for_records([_property(asking=1_500_000)], OBSERVED_AT)

    assert batch.rows[0].status is OfficialQueryStatus.SUCCESS
    assert batch.rows[0].fields["previous_sale_prices"] == [1_250_000]
    assert "asking" not in batch.rows[0].fields


def test_sold_history_adapter_marks_checked_window_when_no_sale_is_displayed(tmp_path):
    body = __import__("json").dumps({
        "results": [{
            "pid": "012-345-678",
            "address": "100 Example Street",
            "municipality": "City of Vancouver",
        }]
    }).encode()

    adapter = SoldHistoryAdapter(
        name="sold-history-bca-empty",
        source_registry_id="source:sold-history-bca-empty",
        source_url="https://assessment.example",
        raw_snapshot_dir=tmp_path / "raw",
        config={
            "enabled": True,
            "allowed_domains": ["assessment.example"],
            "search_url": "https://assessment.example/property/{pid}",
            "source_version": "sold-test-v1",
        },
        transport=lambda request: OfficialHttpResponse(
            url=request["url"],
            status_code=200,
            headers={"content-type": "application/json"},
            content=body,
        ),
    )

    batch = adapter.collect_for_records([_property()], OBSERVED_AT)

    assert batch.rows[0].status is OfficialQueryStatus.SUCCESS
    assert batch.rows[0].fields["sold_history_scope"] == "last_three_full_calendar_years"


def test_adapter_selects_the_matching_snapshot_when_endpoint_url_is_shared(tmp_path):
    bodies = {
        "property:1": __import__("json").dumps({"results": [{
            "pid": "012-345-678", "address": "100 Example Street",
            "municipality": "City of Vancouver", "assessment_total": 1_700_000,
        }]}).encode(),
        "property:2": __import__("json").dumps({"results": [{
            "pid": "987-654-321", "address": "101 Example Street",
            "municipality": "City of Vancouver", "assessment_total": 1_800_000,
        }]}).encode(),
    }

    adapter = BCAssessmentAdapter(
        name="bca-assessment",
        source_registry_id="source:bca-assessment",
        source_url="https://assessment.example",
        raw_snapshot_dir=tmp_path / "raw",
        config={
            "enabled": True,
            "allowed_domains": ["assessment.example"],
            "search_url": "https://assessment.example/property",
        },
        transport=lambda request: OfficialHttpResponse(
            url=request["url"],
            status_code=200,
            headers={"content-type": "application/json"},
            content=bodies[request["property_id"]],
        ),
    )

    batch = adapter.collect_for_records(
        [_property(), _property(property_id="property:2", pid="987-654-321", address="101 Example Street")],
        OBSERVED_AT,
    )

    assert [row.status for row in batch.rows] == [OfficialQueryStatus.SUCCESS, OfficialQueryStatus.SUCCESS]
    assert batch.rows[0].raw_snapshot_path != batch.rows[1].raw_snapshot_path


def test_sold_source_run_preserves_query_scope_and_snapshot_metadata(tmp_path):
    body = __import__("json").dumps({"results": [{
        "pid": "012-345-678", "address": "100 Example Street",
        "municipality": "City of Vancouver",
        "sales_history": [{"date": "2025-04-02", "price": 1_250_000}],
    }]}).encode()
    adapter = SoldHistoryAdapter(
        name="sold-history-bca",
        source_registry_id="source:sold-history-bca",
        source_url="https://assessment.example",
        raw_snapshot_dir=tmp_path / "raw",
        config={
            "enabled": True,
            "allowed_domains": ["assessment.example"],
            "search_url": "https://assessment.example/property/{pid}",
        },
        transport=lambda request: OfficialHttpResponse(
            url=request["url"], status_code=200,
            headers={"content-type": "application/json"}, content=body,
        ),
    )

    batch = adapter.collect_for_records([_property()], OBSERVED_AT)
    source_row = batch.source_run.records[0]

    assert source_row["query_scope"] == "last_three_full_calendar_years"
    assert source_row["raw_snapshot_path"] == batch.rows[0].raw_snapshot_path


def test_json_mapping_provider_preserves_direct_query_metadata():
    provider = JsonMappingProvider(
        name="direct-zoning",
        source_type=SourceType.OFFICIAL_GOV,
        source_registry_id="source:direct-zoning",
        data=[{
            "property_id": "property:1",
            "zoning": "RS-1",
            "source_url": "https://city.example/zoning/1",
            "retrieved_at": OBSERVED_AT.isoformat(),
            "match_method": "PID",
            "query_scope": "current_bylaw",
            "raw_snapshot_path": "raw/zoning.bin",
        }],
    )
    registry = {
        "source:direct-zoning": __import__("northshore_watchdog.research.provenance", fromlist=["SourceRegistryEntry"]).SourceRegistryEntry(
            source_registry_id="source:direct-zoning",
            name="direct zoning",
            source_type=SourceType.OFFICIAL_GOV,
            authorization_status="NOT_APPLICABLE",
            evidence_status="CONFIRMED",
        )
    }

    result = enrich_property(_property(), providers=[provider], observed_at=OBSERVED_AT, source_registry=registry)

    source = next(item for item in result.provenance if item.field_name == "zoning")
    assert source.match_method == "PID"
    assert source.query_scope == "current_bylaw"
    assert source.raw_snapshot_path == "raw/zoning.bin"
