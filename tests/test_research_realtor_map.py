from urllib.parse import parse_qsl, urlsplit

from northshore_watchdog.research.discovery import DiscoveryFilterPlan
from northshore_watchdog.research.realtor_map import build_realtor_ca_map_url


EXAMPLE_REALTOR_MAP_URL = (
    "https://www.realtor.ca/map#ZoomLevel=15&Center=49.252003%2C-123.110789"
    "&LatitudeMax=49.26055&LongitudeMax=-123.08272"
    "&LatitudeMin=49.24346&LongitudeMin=-123.13886"
    "&Sort=6-D&PGeoIds=g30_c2b2nw3h&GeoName=Vancouver%2C%20BC"
    "&PropertyTypeGroupID=1&TransactionTypeId=2&PropertySearchTypeId=0"
    "&PriceMax=1700000&BedRange=2-0&BathRange=1-0&SQFTRange=1250-0"
    "&BuildingTypeId=1&OwnershipTypeGroupId=1&Currency=CAD"
)


def test_realtor_map_builder_preserves_map_state_and_rewrites_native_filters():
    plan = DiscoveryFilterPlan(
        preset_name="vancouver-east",
        native_filters={
            "status": "ACTIVE",
            "property_type": "DETACHED_HOUSE",
            "ownership": "FREEHOLD",
            "minimum_bedrooms": 3,
            "minimum_bathrooms": 2,
            "minimum_finished_sqft": 1500,
            "price_min": 1_000_000,
            "price_max": 1_600_000,
        },
    )

    result = build_realtor_ca_map_url(plan, EXAMPLE_REALTOR_MAP_URL)
    params = dict(parse_qsl(urlsplit(result).fragment, keep_blank_values=True))

    assert urlsplit(result).path == "/map"
    assert params["Center"] == "49.252003,-123.110789"
    assert params["PGeoIds"] == "g30_c2b2nw3h"
    assert params["GeoName"] == "Vancouver, BC"
    assert params["TransactionTypeId"] == "2"
    assert params["PropertyTypeGroupID"] == "1"
    assert params["BuildingTypeId"] == "1"
    assert params["OwnershipTypeGroupId"] == "1"
    assert params["PriceMin"] == "1000000"
    assert params["PriceMax"] == "1600000"
    assert params["BedRange"] == "3-0"
    assert params["BathRange"] == "2-0"
    assert params["SQFTRange"] == "1500-0"
    assert params["CurrentPage"] == "1"


def test_realtor_map_builder_applies_preset_region_map_bounds():
    plan = DiscoveryFilterPlan(
        preset_name="vancouver-west",
        geography={
            "map_params": {
                "Center": "49.255000,-123.185000",
                "LatitudeMax": "49.29200",
                "LongitudeMax": "-123.08000",
                "LatitudeMin": "49.20500",
                "LongitudeMin": "-123.26500",
            }
        },
    )

    result = build_realtor_ca_map_url(plan, EXAMPLE_REALTOR_MAP_URL)
    params = dict(parse_qsl(urlsplit(result).fragment, keep_blank_values=True))

    assert params["Center"] == "49.255000,-123.185000"
    assert params["LatitudeMax"] == "49.29200"
    assert params["LongitudeMax"] == "-123.08000"
    assert params["LatitudeMin"] == "49.20500"
    assert params["LongitudeMin"] == "-123.26500"


def test_realtor_map_builder_applies_versioned_polygon_boundary(tmp_path):
    registry = tmp_path / "boundaries.json"
    registry.write_text(
        '{"schema_version":"market-boundaries-v1","source_local_areas":["A"],'
        '"regions":[{"boundary_id":"west","source_local_areas":["A"],'
        '"geometry":{"type":"Polygon","coordinates":[[[0,0],[1,0],[1,1],[0,0]]]},'
        '"polygon_wkt":"POLYGON ((0 0, 1 0, 1 1, 0 0))"}]}',
        encoding="utf-8",
    )
    plan = DiscoveryFilterPlan(
        preset_name="vancouver-west",
        geography={
            "map_params": {
                "boundary_id": "west",
                "boundary_registry_path": str(registry),
            }
        },
    )

    result = build_realtor_ca_map_url(plan, EXAMPLE_REALTOR_MAP_URL)
    params = dict(parse_qsl(urlsplit(result).fragment, keep_blank_values=True))

    assert params["PolygonWKT"] == "POLYGON ((0 0, 1 0, 1 1, 0 0))"


def test_realtor_map_builder_requires_a_map_fragment():
    plan = DiscoveryFilterPlan(preset_name="vancouver-east", native_filters={})

    try:
        build_realtor_ca_map_url(plan, "https://www.realtor.ca/map")
    except ValueError as exc:
        assert "fragment" in str(exc)
    else:
        raise AssertionError("a REALTOR.ca map URL without search state must fail")
