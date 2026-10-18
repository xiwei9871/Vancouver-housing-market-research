"""Pure helpers for constructing native REALTOR.ca map-search URLs."""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .boundaries import load_boundary_registry, region_by_id
from .discovery import DiscoveryFilterPlan


_MISSING = object()


def build_realtor_ca_map_url(
    plan: DiscoveryFilterPlan,
    source_url: str,
) -> str:
    """Rewrite the native filters in a REALTOR.ca map URL.

    REALTOR.ca stores the map search state in the URL fragment rather than in
    the query string.  The supplied URL therefore acts as a geography and UI
    template: viewport, ``PGeoIds``, sorting, currency, and any future fields
    are preserved while the project filter plan rewrites the known controls.
    """

    parts = urlsplit(source_url)
    if not parts.fragment.strip():
        raise ValueError("REALTOR.ca map source URL requires a search fragment")

    pairs = parse_qsl(parts.fragment, keep_blank_values=True)
    for key, value in realtor_ca_native_parameters(plan).items():
        _set_param(pairs, key, value)
    geography = plan.geography if isinstance(plan.geography, dict) else {}
    map_params = geography.get("map_params", geography)
    if isinstance(map_params, dict):
        for key in (
            "Center",
            "LatitudeMax",
            "LongitudeMax",
            "LatitudeMin",
            "LongitudeMin",
            "ZoomLevel",
            "PGeoIds",
            "GeoName",
        ):
            value = map_params.get(key)
            if value is not None:
                _set_param(pairs, key, str(value))
        boundary_id = map_params.get("boundary_id")
    else:
        boundary_id = None
    if boundary_id:
        registry_path = map_params.get("boundary_registry_path") if isinstance(map_params, dict) else None
        if not isinstance(registry_path, str) or not registry_path.strip():
            raise ValueError("map boundary_id requires boundary_registry_path")
        registry = load_boundary_registry(registry_path)
        region = region_by_id(registry, str(boundary_id))
        polygon_wkt = region.get("polygon_wkt")
        if not isinstance(polygon_wkt, str) or not polygon_wkt.strip():
            raise ValueError(f"boundary {boundary_id!r} has no polygon_wkt")
        # The native page uses this value when its custom boundary state is
        # active.  The Playwright executor also applies the same WKT through
        # the page's native map object because a fresh page may otherwise
        # discard custom polygon state during initial URL normalization.
        _set_param(pairs, "PolygonWKT", polygon_wkt)
    # The map UI keeps the last pagination page in its hash.  Always start a
    # fresh bounded discovery run at page 1; otherwise a reused Chrome tab can
    # silently reopen on the last page while retaining only that page's cards.
    _set_param(pairs, "CurrentPage", "1")

    return urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, urlencode(pairs)))


def realtor_ca_native_parameters(plan: DiscoveryFilterPlan) -> dict[str, str]:
    """Return the native control values represented by a filter plan."""

    result: dict[str, str] = {}
    _set_mapping(result, "TransactionTypeId", _transaction_type(plan))
    _set_mapping(result, "PropertyTypeGroupID", _property_type_group(plan))
    _set_mapping(result, "BuildingTypeId", _building_type(plan))
    _set_mapping(result, "OwnershipTypeGroupId", _ownership_type(plan))
    _set_mapping(result, "PriceMin", _optional_number(plan, "price_min"))
    _set_mapping(result, "PriceMax", _optional_number(plan, "price_max"))
    _set_mapping(result, "BedRange", _minimum_range(plan, "minimum_bedrooms"))
    _set_mapping(result, "BathRange", _minimum_range(plan, "minimum_bathrooms"))
    _set_mapping(result, "SQFTRange", _minimum_range(plan, "minimum_finished_sqft"))
    return result


def _set_param(pairs: list[tuple[str, str]], key: str, value: str | None) -> None:
    if value is None:
        return
    for index, (current_key, _) in enumerate(pairs):
        if current_key == key:
            pairs[index] = (key, value)
            return
    pairs.append((key, value))


def _set_mapping(mapping: dict[str, str], key: str, value: str | None) -> None:
    if value is not None:
        mapping[key] = value


def _value(plan: DiscoveryFilterPlan, field: str) -> Any:
    value = plan.native_filters.get(field, _MISSING)
    if value is _MISSING:
        value = plan.desired_filters.get(field, _MISSING)
    return value


def _transaction_type(plan: DiscoveryFilterPlan) -> str | None:
    value = _value(plan, "status")
    if value is _MISSING:
        return None
    normalized = str(value).strip().upper()
    if normalized in {"ACTIVE", "FOR_SALE", "SALE"}:
        return "2"
    raise ValueError(f"unsupported REALTOR.ca transaction/status value: {value!r}")


def _property_type_group(plan: DiscoveryFilterPlan) -> str | None:
    value = _value(plan, "property_type")
    if value is _MISSING:
        return None
    normalized = str(value).strip().upper().replace("-", "_")
    if normalized in {"DETACHED_HOUSE", "DETACHED", "SINGLE_FAMILY", "HOUSE"}:
        return "1"
    raise ValueError(f"unsupported REALTOR.ca property type value: {value!r}")


def _building_type(plan: DiscoveryFilterPlan) -> str | None:
    value = _value(plan, "property_type")
    if value is _MISSING:
        return None
    normalized = str(value).strip().upper().replace("-", "_")
    if normalized in {"DETACHED_HOUSE", "DETACHED", "SINGLE_FAMILY", "HOUSE"}:
        return "1"
    raise ValueError(f"unsupported REALTOR.ca building type value: {value!r}")


def _ownership_type(plan: DiscoveryFilterPlan) -> str | None:
    value = _value(plan, "ownership")
    if value is _MISSING:
        return None
    normalized = str(value).strip().upper().replace("-", "_")
    if normalized in {"FREEHOLD", "FREEHOLD_NON_STRATA"}:
        return "1"
    raise ValueError(f"unsupported REALTOR.ca ownership value: {value!r}")


def _optional_number(plan: DiscoveryFilterPlan, field: str) -> str | None:
    value = _value(plan, field)
    if value is _MISSING:
        return None
    return _integer_string(value, field)


def _minimum_range(plan: DiscoveryFilterPlan, field: str) -> str | None:
    value = _value(plan, field)
    if value is _MISSING:
        return None
    # REALTOR.ca exposes the lower bound as an integer.  A desired 2.5 bath
    # minimum is intentionally narrowed natively to 2+ and remains in the
    # plan's post_filters for exact card-level checking.
    minimum = _integer_string(value, field)
    return f"{minimum}-0"


def _integer_string(value: Any, field: str) -> str:
    if isinstance(value, bool):
        raise ValueError(f"REALTOR.ca {field} value must be numeric")
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"REALTOR.ca {field} value must be numeric: {value!r}") from exc
    if not numeric.is_integer() and field not in {"minimum_bathrooms"}:
        raise ValueError(f"REALTOR.ca {field} value must be an integer: {value!r}")
    if numeric < 0:
        raise ValueError(f"REALTOR.ca {field} value must not be negative")
    return str(int(numeric))
