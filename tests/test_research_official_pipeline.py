from datetime import datetime, timezone
import json
from pathlib import Path

from northshore_watchdog.config import load_config
from northshore_watchdog.research.models import SourceType
from northshore_watchdog.research.official_transport import OfficialHttpResponse
from northshore_watchdog.research.pipeline import run_research_pipeline
from northshore_watchdog.research.storage import ResearchStorage


OBSERVED_AT = datetime(2026, 9, 1, tzinfo=timezone.utc)


def _config(tmp_path: Path):
    listing_path = tmp_path / "listings.json"
    listing_path.write_text(json.dumps([{
        "property_id": "listing:official-1",
        "pid": "012-345-678",
        "address": "100 Example Street",
        "municipality": "City of Vancouver",
        "neighbourhood": "Knight",
        "property_type": "Detached House",
        "ownership_type": "Freehold Non-Strata",
        "asking": 1_500_000,
        "listing_status": "Active",
        "lot_area_sqft": 5000,
        "frontage_ft": 40,
        "depth_ft": 125,
        "finished_sqft": 2200,
        "source_url": "https://listing.example/1",
    }]), encoding="utf-8")
    config = load_config("config/research.yaml")
    config["storage"]["sqlite_path"] = str(tmp_path / "research.sqlite")
    config["reports"]["weekly_dir"] = str(tmp_path / "weekly")
    config["reports"]["latest_path"] = str(tmp_path / "latest.md")
    config["market_buckets"] = {
        "vancouver_east": {
            "municipality": "City of Vancouver",
            "neighbourhoods": ["Knight"],
            "core_range": [1_000_000, 2_000_000],
            "extended_range": [1_000_000, 2_200_000],
        }
    }
    config["acquisition"] = {"sources": [{
        "name": "official-listings",
        "type": "LOCAL_JSON",
        "path": str(listing_path),
        "source_type": "MLS",
        "source_registry_id": "source:official-listings",
        "enabled": True,
    }]}
    config["official_enrichment"] = {
        "enabled": True,
        "stage2_only": True,
        "max_properties_per_run": 30,
        "raw_snapshot_dir": str(tmp_path / "official-raw"),
        "sources": [
            {
                "name": "bca-assessment",
                "adapter": "bca_assessment",
                "source_type": "BC_ASSESSMENT",
                "source_registry_id": "source:bca-assessment",
                "source_url": "https://official.example/bca",
                "allowed_domains": ["official.example"],
                "search_url": "https://official.example/bca/{pid}",
                "source_version": "test-bca-v1",
                "max_requests_per_run": 30,
            },
            {
                "name": "municipal-zoning",
                "adapter": "municipal_zoning",
                "source_type": "OFFICIAL_GOV",
                "source_registry_id": "source:municipal-zoning",
                "source_url": "https://official.example/zoning",
                "allowed_domains": ["official.example"],
                "search_url": "https://official.example/zoning/{pid}",
                "municipality": "City of Vancouver",
                "source_version": "test-zoning-v1",
                "max_requests_per_run": 30,
            },
            {
                "name": "school-catchment",
                "adapter": "school_catchment",
                "source_type": "OFFICIAL_GOV",
                "source_registry_id": "source:school-catchment",
                "source_url": "https://official.example/school",
                "allowed_domains": ["official.example"],
                "search_url": "https://official.example/school/{pid}",
                "municipality": "City of Vancouver",
                "school_district": "SD39",
                "source_version": "test-school-v1",
                "max_requests_per_run": 30,
            },
            {
                "name": "sold-history",
                "adapter": "sold_history",
                "source_type": "BC_ASSESSMENT",
                "source_registry_id": "source:sold-history",
                "source_url": "https://official.example/sold",
                "allowed_domains": ["official.example"],
                "search_url": "https://official.example/sold/{pid}",
                "source_version": "test-sold-v1",
                "max_requests_per_run": 30,
            },
        ],
    }
    registry = [
        {
            "source_registry_id": "source:official-listings",
            "name": "official test listings",
            "source_type": "MLS",
            "authorization_status": "NOT_APPLICABLE",
            "evidence_status": "CONFIRMED",
        },
    ]
    for source in config["official_enrichment"]["sources"]:
        registry.append({
            "source_registry_id": source["source_registry_id"],
            "name": source["name"],
            "source_type": source["source_type"],
            "authorization_status": "NOT_APPLICABLE",
            "evidence_status": "CONFIRMED",
            "enabled": True,
        })
    config["source_registry"] = registry
    return config


def test_official_enrichment_runs_only_stage2_and_persists_source_lineage(tmp_path):
    config = _config(tmp_path)
    bodies = {
        "/bca/": json.dumps({"results": [{
            "pid": "012-345-678", "address": "100 Example Street", "municipality": "City of Vancouver",
            "assessment_year": 2026, "assessment_total": 1_700_000, "assessment_land": 1_500_000,
            "assessment_improvement": 200_000, "sales_history": [{"date": "2025-04-02", "price": 1_250_000}],
        }]}).encode(),
        "/zoning/": json.dumps({"results": [{
            "pid": "012-345-678", "address": "100 Example Street", "municipality": "City of Vancouver",
            "zoning": "RS-1", "bylaw_version": "3210",
            "rules": {"zoning": "RS-1", "maximum_units": 2, "maximum_fsr": 0.75},
        }]}).encode(),
        "/sold/": json.dumps({"results": [{
            "pid": "012-345-678", "address": "100 Example Street", "municipality": "City of Vancouver",
            "sales_history": [{"date": "2025-04-02", "price": 1_250_000}],
        }]}).encode(),
    }
    calls = []

    def responder(request):
        calls.append(request["url"])
        if "/school/" in request["url"]:
            return OfficialHttpResponse(
                url=request["url"], status_code=403, headers={"content-type": "text/html"}, content=b"access denied"
            )
        body = next(value for prefix, value in bodies.items() if prefix in request["url"])
        return OfficialHttpResponse(
            url=request["url"], status_code=200, headers={"content-type": "application/json"}, content=body
        )

    config["_official_transports"] = {
        source["name"]: responder for source in config["official_enrichment"]["sources"]
    }

    result = run_research_pipeline(config, stages={"acquire", "enrich"}, observed_at=OBSERVED_AT)

    assert len(calls) == 4
    assert result.records[0].assessment_total == 1_700_000
    assert result.records[0].official_pid_confirmed is True
    assert result.records[0].zoning == "RS-1"
    assert result.records[0].maximum_units == 2
    assert result.records[0].previous_sale_prices == [1_250_000]
    assert result.records[0].official_enrichment_status["school-catchment"] == "BLOCKED"
    assert result.stage_status["enrich"] == "PARTIAL"
    assert not result.conflicts
    zoning_source = next(item for item in result.provenance[result.records[0].property_id] if item.field_name == "zoning")
    assert zoning_source.source_run_id is not None
    with ResearchStorage(tmp_path / "research.sqlite") as storage:
        assert storage.conn.execute("SELECT COUNT(*) FROM source_runs").fetchone()[0] == 5
        assert storage.conn.execute("SELECT COUNT(*) FROM source_snapshots").fetchone()[0] == 4
        assert storage.conn.execute("SELECT COUNT(*) FROM field_provenance WHERE field_name = 'zoning'").fetchone()[0] == 1


def test_blocked_official_queries_append_verification_items_and_report_queue(tmp_path):
    config = _config(tmp_path)

    def blocked(request):
        return OfficialHttpResponse(
            url=request["url"],
            status_code=403,
            headers={"content-type": "text/html"},
            content=b"access denied",
        )

    config["_official_transports"] = {
        source["name"]: blocked for source in config["official_enrichment"]["sources"]
    }

    first = run_research_pipeline(
        config,
        stages={"acquire", "enrich", "report"},
        observed_at=OBSERVED_AT,
    )

    with ResearchStorage(tmp_path / "research.sqlite") as storage:
        first_items = storage.list_open_verification_items()
        assert len(first_items) == 4
        assert {item.status for item in first_items} == {"BLOCKED"}
        assert all(item.raw_snapshot_path for item in first_items)
    assert "## Official Verification Queue" in first.report_path.read_text(encoding="utf-8")

    second = run_research_pipeline(
        config,
        stages={"acquire", "enrich", "report"},
        observed_at=OBSERVED_AT.replace(day=8),
    )

    assert second.records[0].official_enrichment_status["bca-assessment"] == "BLOCKED"
    with ResearchStorage(tmp_path / "research.sqlite") as storage:
        assert len(storage.list_verification_items()) == 8


def test_official_enrichment_respects_stage2_property_cap(tmp_path):
    config = _config(tmp_path)
    listing_path = Path(config["acquisition"]["sources"][0]["path"])
    listings = json.loads(listing_path.read_text(encoding="utf-8"))
    second = dict(listings[0])
    second.update({
        "property_id": "listing:official-2",
        "pid": "012-345-679",
        "address": "101 Example Street",
        "source_url": "https://listing.example/2",
    })
    listings.append(second)
    listing_path.write_text(json.dumps(listings), encoding="utf-8")
    config["official_enrichment"]["max_properties_per_run"] = 1
    calls = []

    def responder(request):
        calls.append(request["url"])
        return OfficialHttpResponse(
            url=request["url"],
            status_code=403,
            headers={"content-type": "text/html"},
            content=b"access denied",
        )

    config["_official_transports"] = {
        source["name"]: responder for source in config["official_enrichment"]["sources"]
    }

    result = run_research_pipeline(config, stages={"acquire", "enrich"}, observed_at=OBSERVED_AT)

    assert len(result.records) == 2
    assert len(calls) == 4
    assert result.records[0].official_enrichment_status
    assert result.records[1].official_enrichment_status == {}


def test_official_verification_queries_title_unknown_in_scope_candidates(tmp_path):
    config = _config(tmp_path)
    listing_path = Path(config["acquisition"]["sources"][0]["path"])
    listing = json.loads(listing_path.read_text(encoding="utf-8"))[0]
    listing.pop("ownership_type")
    listing_path.write_text(json.dumps([listing]), encoding="utf-8")
    calls = []

    def responder(request):
        calls.append(request["url"])
        return OfficialHttpResponse(
            url=request["url"], status_code=403,
            headers={"content-type": "text/html"}, content=b"access denied",
        )

    config["_official_transports"] = {
        source["name"]: responder for source in config["official_enrichment"]["sources"]
    }

    result = run_research_pipeline(config, stages={"acquire", "enrich"}, observed_at=OBSERVED_AT)

    assert len(calls) == 4
    assert result.stage1.included_count == 0
    assert result.stage1.verification_queue_count == 1
    assert result.records[0].official_enrichment_status
    assert result.stage_status["enrich"] == "PARTIAL"


def test_official_pid_mismatch_creates_open_conflict_and_marks_property(tmp_path):
    config = _config(tmp_path)

    def responder(request):
        if "/bca/" in request["url"]:
            body = json.dumps({"results": [{
                "pid": "999-999-999",
                "address": "100 Example Street",
                "municipality": "City of Vancouver",
                "assessment_total": 1_700_000,
            }]}).encode()
        elif "/sold/" in request["url"]:
            body = json.dumps({"results": [{
                "pid": "012-345-678",
                "address": "100 Example Street",
                "municipality": "City of Vancouver",
                "sales_history": [{"date": "2025-04-02", "price": 1_250_000}],
            }]}).encode()
        elif "/school/" in request["url"]:
            return OfficialHttpResponse(
                url=request["url"], status_code=403,
                headers={"content-type": "text/html"}, content=b"access denied",
            )
        else:
            body = json.dumps({"results": [{
                "pid": "012-345-678",
                "address": "100 Example Street",
                "municipality": "City of Vancouver",
                "zoning": "RS-1",
            }]}).encode()
        return OfficialHttpResponse(
            url=request["url"], status_code=200,
            headers={"content-type": "application/json"}, content=body,
        )

    config["_official_transports"] = {
        source["name"]: responder for source in config["official_enrichment"]["sources"]
    }

    result = run_research_pipeline(config, stages={"acquire", "enrich"}, observed_at=OBSERVED_AT)

    record = result.records[0]
    conflicts = [item for item in result.conflicts if item.property_id == record.property_id]
    assert record.data_conflict is True
    assert record.major_data_conflict is True
    assert any(item.field_name == "pid" and item.conflict_type == "OFFICIAL_IDENTITY_MISMATCH" for item in conflicts)
    conflict = next(item for item in conflicts if item.conflict_type == "OFFICIAL_IDENTITY_MISMATCH")
    assert "source:bca-assessment" in conflict.conflicting_sources
    assert "012-345-678" in conflict.source_values.values()
