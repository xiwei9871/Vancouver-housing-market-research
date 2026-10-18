"""Pure, transport-independent listing discovery domain logic.

The functions in this module deliberately know nothing about HTTP clients,
browsers, feeds, persistence, or reporting.  They operate on a small capture
envelope and make the filter-validation and candidate-selection decisions
that are safe to make from listing cards alone.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from copy import deepcopy
from datetime import datetime, timezone
from enum import Enum
import json
import math
import re
import sys
import unicodedata
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, field_validator, model_validator

from .boundaries import geometry_to_multipolygon, load_boundary_registry, point_in_multipolygon, region_by_id
from .normalization import normalize_address


UTC = timezone.utc


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _as_utc(value: Any) -> Any:
    """Return datetimes as aware UTC values while leaving other values alone."""

    if value is None or isinstance(value, datetime) is False:
        if isinstance(value, str):
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                return value
            return _as_utc(parsed)
        return value
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


_UNKNOWN_TEXT = {
    "",
    "-",
    "--",
    "na",
    "n a",
    "n/a",
    "not available",
    "not disclosed",
    "not provided",
    "no data",
    "not applicable",
    "nan",
    "nil",
    "infinity",
    "+ infinity",
    "- infinity",
    "none",
    "null",
    "tbd",
    "unknown",
    "unavailable",
}


def _normal_text(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).strip().lower()
    text = text.replace("&", " and ")
    text = text.replace("/", " ")
    text = re.sub(r"[_\-]+", " ", text)
    text = re.sub(r"[^\w\s]+", " ", text, flags=re.UNICODE)
    return " ".join(text.split())


def _is_unknown(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return _normal_text(value) in {_normal_text(item) for item in _UNKNOWN_TEXT}
    return False


def _is_missing(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _is_missing_or_unknown(value: Any) -> bool:
    return _is_missing(value) or _is_unknown(value)


_NUMBER_PATTERN = re.compile(
    r"""
    ^\s*
    (?:(?:CAD|USD)\s*)?
    \$?\s*
    (?P<number>[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)
    \s*
    (?P<suffix>
        thousand|million|billion|[kmb]
        |bedrooms?|beds?
        |bathrooms?|baths?
        |sqft|sq\.?\s*ft\.?|square\s+(?:feet|foot)
        |ft\.?|feet|foot
        |days?(?:\s+on\s+market)?|dom
    )?
    \s*(?:CAD|USD)?\s*$
    """,
    re.IGNORECASE | re.VERBOSE,
)
_RANGE_PATTERN = re.compile(r"\d\s*(?:-|–|—|to)\s*\d", re.IGNORECASE)
_SCIENTIFIC_NOTATION_PATTERN = re.compile(
    r"[-+]?(?:\d[\d,]*)(?:\.\d+)?\s*[eE]\s*[-+]?\d"
)
_INTEGER_TEXT_PATTERN = re.compile(r"[+-]?\d+")
_MAX_PUBLIC_LIMIT = sys.maxsize


def _parse_public_limit(value: Any) -> int | None:
    """Parse a bounded positive integer without lossy coercion."""

    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, float):
        if not math.isfinite(value) or not value.is_integer():
            return None
        try:
            parsed = int(value)
        except (OverflowError, ValueError):
            return None
    elif isinstance(value, str):
        text = value.strip()
        if not _INTEGER_TEXT_PATTERN.fullmatch(text):
            return None
        digits = text.lstrip("+-")
        if len(digits) > len(str(_MAX_PUBLIC_LIMIT)):
            return None
        try:
            parsed = int(text)
        except (OverflowError, ValueError):
            return None
    else:
        return None

    if parsed < 1 or parsed > _MAX_PUBLIC_LIMIT:
        return None
    return parsed


def _coerce_number(value: Any, *, integer: bool = False) -> Any:
    """Coerce common listing number formats, retaining undisclosed text."""

    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, Mapping) or isinstance(value, (list, tuple, set, frozenset)):
        # A container is not a scalar listing measurement.  Never stringify it
        # and extract an arbitrary number from its representation.
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else value
    if not isinstance(value, str):
        return value

    text = str(value).strip()
    if (
        not text
        or _is_unknown(text)
        or _RANGE_PATTERN.search(text)
        or _SCIENTIFIC_NOTATION_PATTERN.search(text)
    ):
        return value
    match = _NUMBER_PATTERN.fullmatch(text)
    if match is None:
        return value

    number_text = match.group("number").replace(",", "")
    try:
        number = float(number_text)
    except (ValueError, OverflowError):
        return value

    suffix = (match.group("suffix") or "").strip().lower()
    if suffix in {"k", "thousand"}:
        number *= 1_000
    elif suffix in {"m", "million"}:
        number *= 1_000_000
    elif suffix in {"b", "billion"}:
        number *= 1_000_000_000

    if not math.isfinite(number):
        return value
    if integer and number.is_integer():
        return int(number)
    return int(number) if number.is_integer() else number


def _number_for_compare(value: Any) -> float | None:
    if (
        _is_unknown(value)
        or isinstance(value, bool)
        or isinstance(value, Mapping)
        or isinstance(value, (list, tuple, set, frozenset))
    ):
        return None
    coerced = _coerce_number(value)
    if isinstance(coerced, bool):
        return None
    if isinstance(coerced, (int, float)):
        try:
            numeric = float(coerced)
        except (OverflowError, TypeError, ValueError):
            return None
        if math.isfinite(numeric):
            return numeric
    return None


class _DiscoveryModel(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)


class DiscoveryPreset(_DiscoveryModel):
    name: str
    market_bucket: str
    desired_filters: dict[str, Any] = Field(default_factory=dict)
    geography: dict[str, Any] = Field(default_factory=dict)
    max_result_cards: int = Field(default=100, ge=1)
    sample_size: int = Field(default=5, ge=1)
    shortlist_limit: int | None = Field(default=None, ge=1)

    @field_validator("max_result_cards", "sample_size", mode="before")
    @classmethod
    def _validate_public_limits(cls, value: Any) -> int:
        parsed = _parse_public_limit(value)
        if parsed is None:
            raise ValueError("public limits must be positive bounded integers")
        return parsed

    @field_validator("shortlist_limit", mode="before")
    @classmethod
    def _validate_optional_shortlist_limit(cls, value: Any) -> int | None:
        if value is None:
            return None
        parsed = _parse_public_limit(value)
        if parsed is None:
            raise ValueError("shortlist_limit must be None or a positive bounded integer")
        return parsed


class SiteCapabilities(_DiscoveryModel):
    native_filter_fields: set[str] = Field(default_factory=set)
    card_fields: set[str] = Field(default_factory=set)
    filter_value_mapping: dict[str, Any] = Field(default_factory=dict)


DiscoveryAction = str | dict[str, Any]


class DiscoveryFilterPlan(_DiscoveryModel):
    preset_name: str
    source_name: str | None = None
    desired_filters: dict[str, Any] = Field(default_factory=dict)
    geography: dict[str, Any] = Field(default_factory=dict)
    native_filters: dict[str, Any] = Field(default_factory=dict)
    post_filters: dict[str, Any] = Field(default_factory=dict)
    unsupported_filters: dict[str, Any] = Field(default_factory=dict)
    actions: list[DiscoveryAction] = Field(default_factory=list)


class DiscoveryStatus(str, Enum):
    PASS = "PASS"
    FILTER_NOT_APPLIED = "FILTER_NOT_APPLIED"
    FILTER_VALIDATION_INCONCLUSIVE = "FILTER_VALIDATION_INCONCLUSIVE"
    RESULT_SET_TOO_LARGE = "RESULT_SET_TOO_LARGE"
    SOURCE_BLOCKED = "SOURCE_BLOCKED"
    UNSUPPORTED_FILTER = "UNSUPPORTED_FILTER"
    PARSER_ERROR = "PARSER_ERROR"


_CARD_ALIASES = {
    "street_address": "address",
    "full_address": "address",
    "city": "municipality",
    "list_price": "asking",
    "listPrice": "asking",
    "price": "asking",
    "house_size_sqft": "finished_sqft",
    "house_sqft": "finished_sqft",
    "living_area_sqft": "finished_sqft",
    "lot_size_sqft": "lot_area_sqft",
    "lot_sqft": "lot_area_sqft",
    "days_on_market": "dom",
    "daysOnMarket": "dom",
    "tenure": "ownership_type",
    "ownership": "ownership_type",
    "type": "property_type",
    "listing_type": "property_type",
    "status": "listing_status",
    "listing_url": "source_url",
    "url": "source_url",
    "mls": "mls_number",
    "mls_id": "mls_number",
}

_NUMERIC_CARD_FIELDS = {
    "asking",
    "bedrooms",
    "bathrooms",
    "finished_sqft",
    "lot_area_sqft",
    "frontage_ft",
    "depth_ft",
    "dom",
}


def _alias_values_equivalent(field: str, left: Any, right: Any) -> bool:
    if field in _NUMERIC_CARD_FIELDS:
        left_number = _number_for_compare(left)
        right_number = _number_for_compare(right)
        if left_number is not None and right_number is not None:
            return left_number == right_number
    if isinstance(left, str) and isinstance(right, str):
        return _normal_text(left) == _normal_text(right)
    return left == right


class DiscoveryCard(_DiscoveryModel):
    source_name: str | None = None
    mls_number: str | None = None
    municipality: str | None = None
    asking: Any | None = None
    bedrooms: Any | None = None
    bathrooms: Any | None = None
    finished_sqft: Any | None = None
    lot_area_sqft: Any | None = None
    lot_dimensions: Any | None = None
    frontage_ft: Any | None = None
    depth_ft: Any | None = None
    property_type: str | None = None
    ownership_type: str | None = None
    listing_status: str | None = None
    dom: Any | None = None
    year_built: Any | None = None
    taxes: Any | None = None
    description: str | None = None
    neighbourhood: str | None = None
    elementary_school: str | None = None
    secondary_school: str | None = None
    lot_area_source: str | None = None
    source_url: str | None = None
    feed_url: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    geometry_source: str | None = None
    # Persist the detail history in ``raw`` as a source observation.  Exclude
    # the convenience projection from the lightweight card serialization so
    # search-result captures remain backward-compatible and small.
    realtor_price_sale_history: list[dict[str, Any]] = Field(default_factory=list, exclude=True)
    realtor_history_status: str | None = None
    observed_at: datetime | None = None
    address: str
    reason_codes: list[str] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _map_common_card_aliases(cls, value: Any) -> Any:
        if not isinstance(value, Mapping):
            return value
        data = dict(value)
        reason_codes = data.get("reason_codes")
        if isinstance(reason_codes, list):
            normalized_reasons = list(reason_codes)
        elif reason_codes is None:
            normalized_reasons = []
        else:
            normalized_reasons = [str(reason_codes)]
        for alias, canonical in _CARD_ALIASES.items():
            if alias not in data:
                continue
            alias_value = data[alias]
            canonical_value = data.get(canonical)
            if _is_missing_or_unknown(canonical_value):
                if not _is_missing_or_unknown(alias_value):
                    data[canonical] = alias_value
            elif not _is_missing_or_unknown(alias_value) and not _alias_values_equivalent(
                canonical,
                canonical_value,
                alias_value,
            ):
                if "ALIAS_CONFLICT" not in normalized_reasons:
                    normalized_reasons.append("ALIAS_CONFLICT")
        if normalized_reasons:
            data["reason_codes"] = normalized_reasons
        return data

    @field_validator("asking", "dom", mode="before")
    @classmethod
    def _coerce_integerish_values(cls, value: Any) -> Any:
        return _coerce_number(value, integer=True)

    @field_validator(
        "bedrooms",
        "bathrooms",
        "finished_sqft",
        "lot_area_sqft",
        "frontage_ft",
        "depth_ft",
        mode="before",
    )
    @classmethod
    def _coerce_measurements(cls, value: Any) -> Any:
        return _coerce_number(value)

    @field_validator("observed_at", mode="before")
    @classmethod
    def _normalise_observed_at(cls, value: Any) -> Any:
        return _as_utc(value)


class DiscoveryDuplicateObservation(_DiscoveryModel):
    """A discarded card retained as an auditable duplicate observation."""

    reason_code: str
    canonical_index: int
    duplicate_index: int
    identity_key: str
    card: DiscoveryCard


class DiscoveryCapture(_DiscoveryModel):
    source_name: str
    source_url: str
    preset_name: str | None = None
    observed_at: datetime = Field(default_factory=_utc_now)
    status_code: int = 200
    result_count: int | None = None
    count_is_complete: bool = False
    applied_filters: dict[str, Any] = Field(default_factory=dict)
    applied_filter_evidence: list[str] | dict[str, Any] = Field(default_factory=list)
    parse_warnings: list[str] = Field(default_factory=list)
    cards: list[DiscoveryCard] = Field(default_factory=list)
    detail_requests: int = Field(default=0, ge=0)
    detail_successes: int = Field(default=0, ge=0)
    detail_failures: int = Field(default=0, ge=0)
    detail_snapshot_paths: list[str] = Field(default_factory=list)
    blocked: bool = False
    raw_snapshot_path: str | None = None

    @field_validator("observed_at", mode="before")
    @classmethod
    def _normalise_capture_observed_at(cls, value: Any) -> Any:
        return _as_utc(value)

    @field_validator("result_count", mode="before")
    @classmethod
    def _validate_result_count_scalar(cls, value: Any) -> Any:
        if value is None:
            return None
        if (
            isinstance(value, bool)
            or isinstance(value, Mapping)
            or isinstance(value, (list, tuple, set, frozenset))
            or (isinstance(value, float) and not math.isfinite(value))
        ):
            raise ValueError("result_count must be a finite scalar integer")
        return value


class DiscoveryValidation(_DiscoveryModel):
    status: DiscoveryStatus
    reason_codes: list[str] = Field(default_factory=list)
    violations: list[dict[str, Any]] = Field(default_factory=list)
    sampled_card_count: int = 0
    candidate_cards: list[DiscoveryCard] = Field(default_factory=list)


class DiscoveryDeduplicationResult(_DiscoveryModel):
    cards: list[DiscoveryCard] = Field(default_factory=list)
    duplicate_flags: list[str] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=list)
    duplicate_observations: list[DiscoveryCard] = Field(default_factory=list)
    _duplicate_observation_metadata: list[dict[str, Any]] = PrivateAttr(default_factory=list)

    @property
    def duplicate_observation_records(self) -> list[DiscoveryDuplicateObservation]:
        """Derived compatibility records backed by one observation list."""

        records: list[DiscoveryDuplicateObservation] = []
        for index, observation in enumerate(self.duplicate_observations):
            metadata = (
                self._duplicate_observation_metadata[index]
                if index < len(self._duplicate_observation_metadata)
                else {}
            )
            records.append(
                DiscoveryDuplicateObservation(
                    reason_code=str(metadata.get("reason_code", "DUPLICATE")),
                    canonical_index=int(metadata.get("canonical_index", -1)),
                    duplicate_index=int(metadata.get("duplicate_index", -1)),
                    identity_key=str(metadata.get("identity_key", "")),
                    card=observation.model_copy(deep=True),
                )
            )
        return records


# Short aliases keep the result easy to discover for callers that use either
# “dedupe” or “deduplication” terminology.
DiscoveryDedupeResult = DiscoveryDeduplicationResult
DeduplicationResult = DiscoveryDeduplicationResult


# Canonical desired-filter names map to the card field needed for local
# validation.  The desired names remain the keys in every filter-plan bucket.
FILTER_CARD_FIELD_MAPPING: dict[str, str] = {
    "price_min": "asking",
    "price_max": "asking",
    "minimum_bedrooms": "bedrooms",
    "minimum_bathrooms": "bathrooms",
    "minimum_finished_sqft": "finished_sqft",
    "ownership": "ownership_type",
    "property_type": "property_type",
    "status": "listing_status",
    "municipality": "municipality",
}

_FILTER_ALIASES = {
    "min_price": "price_min",
    "minimum_price": "price_min",
    "max_price": "price_max",
    "maximum_price": "price_max",
    "min_bedrooms": "minimum_bedrooms",
    "min_bathrooms": "minimum_bathrooms",
    "min_finished_sqft": "minimum_finished_sqft",
    "minimum_house_sqft": "minimum_finished_sqft",
}

_CATEGORICAL_EQUIVALENCE: dict[str, dict[str, str]] = {
    "property_type": {
        "detached": "detached_house",
        "detached house": "detached_house",
        "detached home": "detached_house",
        "single detached": "detached_house",
        "single family": "detached_house",
        "single family house": "detached_house",
        "single family home": "detached_house",
        "house": "house",
        "home": "house",
        "townhouse": "townhouse",
        "townhome": "townhouse",
        "town house": "townhouse",
        "town home": "townhouse",
        "attached": "attached",
        "attached house": "attached",
        "semi detached": "semi_detached",
        "semi detached house": "semi_detached",
        "condo": "condo",
        "condominium": "condo",
        "apartment": "apartment",
    },
    "status": {
        "active": "active",
        "active listing": "active",
        "for sale": "active",
        "listed": "active",
        "new listing": "active",
        "current": "active",
        "pending": "pending",
        "under offer": "pending",
        "conditional": "pending",
        "sold": "sold",
        "closed": "sold",
        "completed": "sold",
        "inactive": "inactive",
        "expired": "inactive",
        "terminated": "inactive",
        "withdrawn": "inactive",
        "cancelled": "inactive",
    },
    "ownership": {
        "freehold": "freehold",
        "freehold non strata": "freehold",
        "fee simple": "freehold",
        "leasehold": "leasehold",
        "lease": "leasehold",
        "leased": "leasehold",
        "strata": "strata",
        "freehold strata": "strata",
        "cooperative": "cooperative",
        "co operative": "cooperative",
        "corporate share": "cooperative",
    },
}

# A native category is safe for a desired category when it is equal to, or an
# explicitly known subset of, that desired category.  Keep this as a small
# partial order rather than inferring relationships from text: a native
# ``house`` control is broader than ``detached_house``, while a native
# ``detached_house`` control safely narrows a desired ``house`` constraint.
_CATEGORICAL_CONTAINMENT: dict[str, set[tuple[str, str]]] = {
    "property_type": {
        ("house", "detached_house"),
        ("house", "townhouse"),
        ("house", "semi_detached"),
        ("house", "attached"),
    },
}


def _canonical_filter_name(field: Any) -> str:
    name = str(field).strip().lower()
    return _FILTER_ALIASES.get(name, name)


def _card_field_for_filter(field: Any) -> str:
    normalized = _canonical_filter_name(field)
    return FILTER_CARD_FIELD_MAPPING.get(normalized, str(field))


def _categorical_category(field: str, value: Any) -> str | None:
    categories = _CATEGORICAL_EQUIVALENCE.get(_canonical_filter_name(field))
    if categories is None:
        return None
    return categories.get(_normal_text(value))


def _categorical_category_contains(
    field: str,
    desired_category: str,
    native_category: str,
) -> bool:
    """Return whether a native category is no broader than the desired one."""

    canonical = _canonical_filter_name(field)
    return desired_category == native_category or (
        desired_category,
        native_category,
    ) in _CATEGORICAL_CONTAINMENT.get(canonical, set())


_CATEGORICAL_NEGATION_WORDS = {
    "not",
    "no",
    "without",
    "non",
    "false",
    "absent",
    "missing",
    "failed",
    "exclude",
    "excluded",
}


def _token_sequence_positions(tokens: list[str], phrase: list[str]) -> Iterable[int]:
    if not phrase or len(phrase) > len(tokens):
        return ()
    width = len(phrase)
    return (
        index
        for index in range(len(tokens) - width + 1)
        if tokens[index : index + width] == phrase
    )


def _categorical_label_is_negated(field: str, expected: Any, observed: Any) -> bool:
    """Detect an explicit negation of the desired category before parsing it."""

    expected_category = _categorical_category(field, expected)
    aliases = {_normal_text(expected)}
    category_table = _CATEGORICAL_EQUIVALENCE.get(_canonical_filter_name(field), {})
    if expected_category is not None:
        aliases.update(
            alias
            for alias, category in category_table.items()
            if category == expected_category
        )

    observed_tokens = _normal_text(observed).split()
    for alias in aliases:
        alias_tokens = alias.split()
        for start in _token_sequence_positions(observed_tokens, alias_tokens):
            end = start + len(alias_tokens)
            preceding = observed_tokens[max(0, start - 2) : start]
            following = observed_tokens[end : end + 1]
            if preceding and preceding[-1] in _CATEGORICAL_NEGATION_WORDS:
                return True
            if len(preceding) >= 2 and preceding[-2:] in (["not", "a"], ["not", "an"]):
                return True
            if following and following[0] in _CATEGORICAL_NEGATION_WORDS - {"non"}:
                return True
    return False


def _categorical_mapping_requires_post(field: str, desired: Any, effective: Any) -> bool:
    """Require a post-check when a native categorical mapping is unsafe."""

    canonical = _canonical_filter_name(field)
    if canonical not in _CATEGORICAL_EQUIVALENCE:
        return False
    if _normal_text(desired) == _normal_text(effective):
        return False
    desired_category = _categorical_category(field, desired)
    effective_category = _categorical_category(field, effective)
    if desired_category is None or effective_category is None:
        return True
    return not _categorical_category_contains(
        canonical,
        desired_category,
        effective_category,
    )


def _capability_contains(fields: Iterable[str], wanted: str) -> bool:
    wanted_text = str(wanted).strip().lower()
    return any(str(field).strip().lower() == wanted_text for field in fields)


_MISSING = object()
_GLOBAL_EVIDENCE_KEYS = {"global", "__global__", "global_marker", "*", "all", "__all__"}


def _mapping_value(mapping: Mapping[Any, Any], value: Any) -> Any:
    """Look up a value both literally and through common text normalisation."""

    try:
        if value in mapping:
            return mapping[value]
    except TypeError:
        pass
    wanted = _normal_text(value)
    for key, mapped in mapping.items():
        if _normal_text(key) == wanted:
            return mapped
    return _MISSING


def _apply_filter_value_mapping(field: str, value: Any, mapping: Mapping[str, Any]) -> Any:
    """Apply only a field-level dictionary of desired-to-native values."""

    spec = _MISSING
    for key, candidate in mapping.items():
        if _canonical_filter_name(key) == _canonical_filter_name(field):
            spec = candidate
            break

    if not isinstance(spec, Mapping):
        return deepcopy(value)

    mapped = _mapping_value(spec, value)
    return deepcopy(value) if mapped is _MISSING else deepcopy(mapped)


def _weaker_numeric_mapping(field: str, desired: Any, effective: Any) -> bool:
    """Whether a mapped numeric native bound is weaker than the desired one."""

    canonical = _canonical_filter_name(field)
    desired_number = _number_for_compare(desired)
    effective_number = _number_for_compare(effective)
    if desired_number is None or effective_number is None or desired_number == effective_number:
        return False
    if canonical in {"minimum_bedrooms", "minimum_bathrooms", "minimum_finished_sqft", "price_min"}:
        return effective_number < desired_number
    if canonical == "price_max":
        return effective_number > desired_number
    return False


def compile_filter_plan(
    preset: DiscoveryPreset | Mapping[str, Any],
    capabilities: SiteCapabilities | Mapping[str, Any],
    source_name: str | None = None,
    actions: Iterable[Any] | Any | None = None,
) -> DiscoveryFilterPlan:
    """Partition desired constraints into native, post, and unsupported sets."""

    preset_model = (
        preset if isinstance(preset, DiscoveryPreset) else DiscoveryPreset.model_validate(preset)
    )
    capabilities_model = (
        capabilities
        if isinstance(capabilities, SiteCapabilities)
        else SiteCapabilities.model_validate(capabilities)
    )

    desired_filters = deepcopy(preset_model.desired_filters)
    native_filters: dict[str, Any] = {}
    post_filters: dict[str, Any] = {}
    unsupported_filters: dict[str, Any] = {}

    for field, desired_value in desired_filters.items():
        field_name = str(field)
        normalized_field = _canonical_filter_name(field_name)
        card_field = _card_field_for_filter(field_name)
        native_available = _capability_contains(
            capabilities_model.native_filter_fields,
            field_name,
        )

        if native_available:
            effective_value = _apply_filter_value_mapping(
                field_name,
                desired_value,
                capabilities_model.filter_value_mapping,
            )
            native_filters[field_name] = effective_value
            requires_desired_check = _weaker_numeric_mapping(
                field_name,
                desired_value,
                effective_value,
            ) or _categorical_mapping_requires_post(
                field_name,
                desired_value,
                effective_value,
            )
            if requires_desired_check and card_field is not None and _capability_contains(
                capabilities_model.card_fields,
                card_field,
            ):
                post_filters[field_name] = deepcopy(desired_value)
            elif requires_desired_check:
                # The native control is usable for source narrowing, but the
                # stricter desired constraint cannot be checked without its
                # semantic card field.  Keep the condition explicitly
                # unsupported so validation cannot accept the broad result.
                unsupported_filters[field_name] = deepcopy(desired_value)
            continue

        post_available = normalized_field in FILTER_CARD_FIELD_MAPPING and _capability_contains(
            capabilities_model.card_fields,
            card_field,
        )

        if post_available:
            post_filters[field_name] = deepcopy(desired_value)
        else:
            unsupported_filters[field_name] = deepcopy(desired_value)

    if actions is None:
        action_list: list[Any] = []
    elif isinstance(actions, Mapping):
        action_list = [deepcopy(dict(actions))]
    elif isinstance(actions, (str, bytes)):
        action_list = [actions]
    else:
        action_list = deepcopy(list(actions))

    return DiscoveryFilterPlan(
        preset_name=preset_model.name,
        source_name=source_name,
        desired_filters=desired_filters,
        geography=deepcopy(preset_model.geography),
        native_filters=native_filters,
        post_filters=post_filters,
        unsupported_filters=unsupported_filters,
        actions=action_list,
    )


def _add_reason(reasons: list[str], reason: str) -> None:
    if reason not in reasons:
        reasons.append(reason)


def _failure(
    status: DiscoveryStatus,
    reason_codes: Iterable[str],
    *,
    violations: Iterable[dict[str, Any]] = (),
    sampled_card_count: int = 0,
) -> DiscoveryValidation:
    unique_reasons: list[str] = []
    for reason in reason_codes:
        _add_reason(unique_reasons, str(reason))
    return DiscoveryValidation(
        status=status,
        reason_codes=unique_reasons,
        violations=list(violations),
        sampled_card_count=sampled_card_count,
        candidate_cards=[],
    )


def _inherit_capture_metadata(capture: DiscoveryCapture) -> list[DiscoveryCard]:
    inherited: list[DiscoveryCard] = []
    for card in capture.cards:
        updates: dict[str, Any] = {}
        if _is_missing(card.source_name):
            updates["source_name"] = capture.source_name
        if _is_missing(card.source_url):
            updates["source_url"] = capture.source_url
        if card.observed_at is None:
            updates["observed_at"] = capture.observed_at
        inherited.append(card.model_copy(update=updates, deep=True))
    return inherited


_EVIDENCE_BOOLEAN_KEYS = {
    "present",
    "applied",
    "visible",
    "selected",
    "enabled",
    "matched",
    "verified",
    "value present",
}
_EVIDENCE_TEXT_KEYS = {
    "label",
    "text",
    "message",
    "evidence",
    "description",
    "reason",
}
_EVIDENCE_NEGATIVE_KEYS = {
    "error",
    "errors",
    "exception",
    "failed",
    "failure",
    "failures",
}
_EVIDENCE_NEGATIVE_INACTIVE_TEXT = {
    "",
    "no error",
    "no errors",
    "not failed",
    "ok",
    "okay",
    "success",
    "successful",
    "without error",
}
_EVIDENCE_FALSE_MARKER_TEXT = {"false", "no", "0", "none", "null"}
_EVIDENCE_NEGATIVE_PHRASES = (
    "not applied",
    "not selected",
    "not visible",
    "not present",
    "not found",
    "unapplied",
    "no filter",
    "without filter",
    "failed to apply",
    "filter failed",
    "disabled",
    "unavailable",
    "missing",
    "absent",
    "no evidence",
    "unable to apply",
    "could not apply",
    "not successful",
    "failure",
    "error",
)
_EVIDENCE_NEGATIVE_VALUE_PATTERN = re.compile(
    r"\b(?:applied|present|selected|visible|enabled|matched|verified|value present)\s+"
    r"(?:is\s+)?(?:false|no|none|null|absent|missing|failed|fail|off|disabled|0)\b"
)


def _evidence_text_is_explicitly_negative(value: Any) -> bool:
    text = _normal_text(value)
    if not text:
        return False
    if text in _EVIDENCE_NEGATIVE_INACTIVE_TEXT:
        return False
    return (
        text in _EVIDENCE_FALSE_MARKER_TEXT
        or
        any(phrase in text for phrase in _EVIDENCE_NEGATIVE_PHRASES)
        or _EVIDENCE_NEGATIVE_VALUE_PATTERN.search(text) is not None
        or (
            any(token in text.split() for token in ("false", "no", "absent", "missing", "failed"))
            and any(
                token in text.split()
                for token in ("applied", "present", "selected", "visible", "enabled", "filter", "control")
            )
        )
    )


def _evidence_text_is_positive(value: Any) -> bool:
    text = _normal_text(value)
    return bool(text) and not _evidence_text_is_explicitly_negative(value)


def _evidence_boolean_state(value: Any) -> bool | None:
    """Parse only bounded, explicit boolean evidence scalars."""

    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        if value == 0:
            return False
        if value == 1:
            return True
        return None
    if isinstance(value, float):
        try:
            if not math.isfinite(value):
                return None
        except (OverflowError, TypeError, ValueError):
            return None
        if value == 0:
            return False
        if value == 1:
            return True
    return None


def _negative_indicator_is_active(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    if isinstance(value, float):
        try:
            return not math.isfinite(value) or value != 0
        except (OverflowError, TypeError, ValueError):
            return True
    if isinstance(value, str):
        normalized = _normal_text(value)
        return normalized not in {
            *_EVIDENCE_NEGATIVE_INACTIVE_TEXT,
            *_EVIDENCE_FALSE_MARKER_TEXT,
        }
    if isinstance(value, Mapping):
        return bool(value)
    if isinstance(value, (list, tuple, set, frozenset)):
        return bool(value)
    return True


def _evidence_contains_explicit_negative(value: Any, *, key: Any = None) -> bool:
    """Detect nested negative diagnostics before aggregating positive markers."""

    key_text = _normal_text(key) if key is not None else ""
    if key_text in _EVIDENCE_NEGATIVE_KEYS:
        return _negative_indicator_is_active(value)
    if key_text in _EVIDENCE_BOOLEAN_KEYS:
        boolean_state = _evidence_boolean_state(value)
        if boolean_state is not None:
            return boolean_state is False
    if key is None:
        boolean_state = _evidence_boolean_state(value)
        if boolean_state is not None:
            return boolean_state is False

    if isinstance(value, str):
        return _evidence_text_is_explicitly_negative(value)
    if isinstance(value, Mapping):
        return any(
            _evidence_contains_explicit_negative(nested, key=nested_key)
            for nested_key, nested in value.items()
        )
    if isinstance(value, (list, tuple, set, frozenset)):
        return any(_evidence_contains_explicit_negative(item) for item in value)
    return False


def _evidence_is_positive(value: Any) -> bool:
    """Recognize only explicit positive evidence, failing closed on conflicts."""

    boolean_state = _evidence_boolean_state(value)
    if boolean_state is not None:
        return boolean_state
    if value is None or isinstance(value, (int, float)):
        return False
    if isinstance(value, str):
        return _evidence_text_is_positive(value)
    if isinstance(value, Mapping):
        if _evidence_contains_explicit_negative(value):
            return False
        states: list[bool] = []
        for key, nested in value.items():
            key_text = _normal_text(key)
            if key_text in _EVIDENCE_BOOLEAN_KEYS or key_text in _EVIDENCE_TEXT_KEYS:
                states.append(_evidence_is_positive(nested))
            elif _is_global_evidence_key(key):
                states.append(_evidence_is_positive(nested))
        return bool(states) and all(states) and any(states)
    if isinstance(value, (list, tuple, set, frozenset)):
        states = [_evidence_is_positive(item) for item in value]
        return bool(states) and all(states)
    return False


def _is_global_evidence_key(value: Any) -> bool:
    raw = str(value).strip().lower()
    normalized = _normal_text(value)
    return raw in _GLOBAL_EVIDENCE_KEYS or normalized in {
        "global",
        "all fields",
        "all native filters",
        "every filter",
    }


def _is_global_evidence_marker(value: Any) -> bool:
    raw = str(value).strip().lower()
    normalized = _normal_text(value)
    if _is_global_evidence_key(value):
        return True
    return (
        raw.startswith("global:")
        or raw.startswith("global ")
        or raw.startswith("*:")
        or normalized.startswith("all fields ")
        or normalized.startswith("all native filters ")
        or normalized.startswith("every filter ")
    )


def _filter_evidence_targets(field: str) -> set[str]:
    return {
        _normal_text(field),
        _normal_text(_canonical_filter_name(field)),
        _normal_text(_card_field_for_filter(field)),
    }


def _marker_mentions_target(marker: Any, targets: set[str]) -> bool:
    marker_tokens = _normal_text(marker).split()
    if not marker_tokens:
        return False
    for target in targets:
        target_tokens = target.split()
        if not target_tokens or len(target_tokens) > len(marker_tokens):
            continue
        width = len(target_tokens)
        if any(
            marker_tokens[index : index + width] == target_tokens
            for index in range(len(marker_tokens) - width + 1)
        ):
            return True
    return False


_EVIDENCE_STATE_POSITIVE = "positive"
_EVIDENCE_STATE_NEGATIVE = "negative"
_EVIDENCE_STATE_ABSENT = "absent"


def _field_evidence_items(capture: DiscoveryCapture, field: str) -> list[Any]:
    targets = _filter_evidence_targets(field)
    evidence_container = capture.applied_filter_evidence
    items: list[Any] = []

    if isinstance(evidence_container, Mapping):
        for key, evidence in evidence_container.items():
            if _is_global_evidence_key(key):
                items.append(evidence)
            elif _normal_text(key) in _EVIDENCE_BOOLEAN_KEYS:
                # A top-level present/applied marker is an explicitly generic
                # global assertion; arbitrary top-level prose is not.
                items.append(evidence)
            elif _marker_mentions_target(key, targets):
                items.append(evidence)
        return items

    if not isinstance(evidence_container, (list, tuple, set, frozenset)):
        return items
    for marker in evidence_container:
        if _is_global_evidence_marker(marker):
            items.append(marker)
        elif _marker_mentions_target(marker, targets):
            items.append(marker)
    return items


def _is_unbounded_filter_marker(value: Any) -> bool:
    """Recognize harmless page labels such as ``No min``/``No max``."""

    text = _normal_text(value)
    return " no min" in text or " no max" in text or text in {
        "no min",
        "no max",
    }


def _is_harmless_page_filter_label(value: Any) -> bool:
    """Recognize non-failure labels emitted by a native search control.

    REALTOR.ca exposes several control labels in the same evidence stream as
    applied values (for example ``Any``, ``0 selected``, ``No min`` and
    ``No max``).  These labels describe an unbounded or empty *secondary*
    control; they do not invalidate an explicit value captured for the target
    field.  A marker such as ``applied filter marker found: not applied`` is
    still treated as a failure.
    """

    if not isinstance(value, str):
        return False
    text = _normal_text(value)
    if _is_unbounded_filter_marker(text):
        return True
    marker = "applied filter marker found"
    if marker not in text:
        return False
    observed = text.split(marker, 1)[1].lstrip(" :")
    return bool(observed) and not _evidence_text_is_explicitly_negative(observed)


def _evidence_state_for_field_tristate(capture: DiscoveryCapture, field: str) -> str:
    """Classify scoped/global evidence without conflating absent and negative."""

    items = _field_evidence_items(capture, field)
    if not items:
        return _EVIDENCE_STATE_ABSENT
    if any(_evidence_contains_explicit_negative(item) for item in items):
        return _EVIDENCE_STATE_NEGATIVE
    if all(_evidence_is_positive(item) for item in items):
        return _EVIDENCE_STATE_POSITIVE
    return _EVIDENCE_STATE_ABSENT


def _evidence_state_for_field(capture: DiscoveryCapture, field: str) -> bool:
    """Return true only for positive evidence scoped to this native field."""

    return _evidence_state_for_field_tristate(capture, field) == _EVIDENCE_STATE_POSITIVE


def _evidence_has_explicit_negative_for_field(
    capture: DiscoveryCapture,
    field: str,
) -> bool:
    """Return whether scoped or global evidence explicitly rejects a field."""

    return _evidence_state_for_field_tristate(capture, field) == _EVIDENCE_STATE_NEGATIVE


def _applied_filter_value(value: Any) -> tuple[Any, bool | None]:
    """Extract an applied native value or an explicit applied marker."""

    if isinstance(value, Mapping):
        if _evidence_contains_explicit_negative(value):
            return _MISSING, False
        for key in ("applied", "present", "enabled"):
            if key in value and isinstance(value[key], bool) and not value[key]:
                return _MISSING, False
        for key in ("native_value", "effective_value", "selected_value", "filter_value", "value"):
            if key in value:
                return value[key], True
        state = _evidence_is_positive(value)
        return _MISSING, state
    if isinstance(value, bool):
        return _MISSING, value
    if value is None:
        return _MISSING, None
    if isinstance(value, str) and not _evidence_text_is_positive(value):
        return _MISSING, False
    return value, True


def _applied_values_match(field: str, expected: Any, observed: Any) -> bool:
    canonical = _canonical_filter_name(field)
    if isinstance(expected, (list, tuple, set, frozenset)):
        return any(_applied_values_match(field, item, observed) for item in expected)
    if isinstance(observed, (list, tuple, set, frozenset)):
        return any(_applied_values_match(field, expected, item) for item in observed)
    if canonical in _NUMERIC_CARD_FIELDS or canonical in {
        "price_min",
        "price_max",
        "minimum_bedrooms",
        "minimum_bathrooms",
        "minimum_finished_sqft",
    }:
        expected_number = _number_for_compare(expected)
        observed_number = _number_for_compare(observed)
        if observed_number is None and canonical in {
            "minimum_bedrooms",
            "minimum_bathrooms",
        }:
            # REALTOR.ca serializes an open-ended native range as e.g. ``2-0``
            # (minimum 2, no maximum).  The applied-filter evidence should be
            # compared with the lower bound, not treated as an unknown string.
            range_match = re.match(r"^\s*(\d+(?:\.\d+)?)\s*[-–—]\s*\d", str(observed))
            if range_match:
                observed_number = _number_for_compare(range_match.group(1))
        return (
            expected_number is not None
            and observed_number is not None
            and expected_number == observed_number
        )
    expected_category = _categorical_category(field, expected)
    observed_category = _categorical_category(field, observed)
    if expected_category is not None and observed_category is not None:
        return expected_category == observed_category
    return _normal_text(expected) == _normal_text(observed)


def _applied_filter_state(
    capture: DiscoveryCapture,
    field: str,
    native_value: Any,
) -> bool | None:
    states: list[bool] = []
    targets = _filter_evidence_targets(field)
    evidence_state = _evidence_state_for_field_tristate(capture, field)
    if not isinstance(capture.applied_filters, Mapping):
        return False if evidence_state == _EVIDENCE_STATE_NEGATIVE else None
    for key, value in capture.applied_filters.items():
        if _normal_text(key) not in targets:
            continue
        applied_value, marker_state = _applied_filter_value(value)
        if applied_value is not _MISSING:
            states.append(_applied_values_match(field, native_value, applied_value))
        elif marker_state is not None:
            states.append(marker_state)
    if states:
        if not all(states):
            return False
        if evidence_state == _EVIDENCE_STATE_NEGATIVE:
            evidence_items = _field_evidence_items(capture, field)
            if any(
                _evidence_contains_explicit_negative(item)
                and not _is_harmless_page_filter_label(item)
                for item in evidence_items
            ):
                return False
        return True
    return False if evidence_state == _EVIDENCE_STATE_NEGATIVE else None


def _has_filter_evidence(capture: DiscoveryCapture, field: str, native_value: Any) -> bool:
    applied_state = _applied_filter_state(capture, field, native_value)
    if applied_state is not None:
        return applied_state
    return _evidence_state_for_field_tristate(capture, field) == _EVIDENCE_STATE_POSITIVE


def _card_value(card: DiscoveryCard, field: str) -> Any:
    value = getattr(card, field, None)
    if value is not None:
        return value
    # Extra fields are accessible through model attributes, but model_dump is
    # a useful fallback for unusual aliases and remains transport-independent.
    data = card.model_dump(mode="python")
    if field in data:
        return data[field]
    wanted = field.strip().lower()
    for key, candidate in data.items():
        if str(key).strip().lower() == wanted:
            return candidate
    return None


def _iter_expected_values(value: Any) -> list[Any]:
    if isinstance(value, (list, tuple, set, frozenset)):
        return list(value)
    return [value]


def _text_match(expected: Any, observed: Any) -> bool | None:
    if _is_unknown(expected) or _is_unknown(observed):
        return None
    expected_text = _normal_text(expected)
    observed_text = _normal_text(observed)
    if not expected_text or not observed_text:
        return None
    return (
        expected_text == observed_text
        or expected_text in observed_text
        or observed_text in expected_text
    )


def _ownership_category(value: Any) -> str | None:
    text = _normal_text(value)
    if not text or _is_unknown(value):
        return None

    # Keep compound tenure labels aligned with the explicit equivalence table
    # before considering their individual words.  In particular, “freehold
    # strata” is strata, while “freehold non-strata” remains freehold.
    explicit_category = _categorical_category("ownership", value)
    if explicit_category is not None:
        return explicit_category

    tokens = set(text.split())
    if tokens & {"leasehold", "lease", "leased"}:
        return "leasehold"
    if (
        "cooperative" in tokens
        or {"co", "operative"}.issubset(tokens)
        or {"corporate", "share"}.issubset(tokens)
    ):
        return "cooperative"

    # Check the qualifier before the generic tenure word so a compound label
    # cannot be reduced to the broader freehold category.
    if {"non", "strata"}.issubset(tokens):
        return "freehold"
    if "strata" in tokens:
        return "strata"
    if "freehold" in tokens or {"fee", "simple"}.issubset(tokens):
        return "freehold"
    if "fractional" in tokens:
        return "fractional"
    if "undivided" in tokens:
        return "undivided"
    if {"partial", "interest"}.issubset(tokens):
        return "partial"
    if {"court", "sale"}.issubset(tokens) or "foreclosure" in tokens:
        return "court_sale"
    return None


def _property_type_category(value: Any) -> str | None:
    if _is_unknown(value):
        return None
    text = _normal_text(value)
    tokens = set(text.split())
    if not text:
        return None

    # Use explicit category labels and token combinations.  In particular,
    # “detached townhome” is still a townhome: the descriptive word must not
    # override the attached category.
    exact_category = _categorical_category("property_type", value)
    if exact_category is not None:
        return exact_category

    if (
        "townhouse" in tokens
        or "townhome" in tokens
        or {"town", "house"}.issubset(tokens)
        or {"town", "home"}.issubset(tokens)
    ):
        return "townhouse"
    if {"semi", "detached"}.issubset(tokens):
        return "semi_detached"
    if (
        tokens & {"condo", "condominium"}
        or "apartment" in tokens
    ):
        return "condo" if tokens & {"condo", "condominium"} else "apartment"
    if (
        tokens & {"attached", "duplex", "triplex", "fourplex", "rowhouse", "mobile"}
        or {"row", "house"}.issubset(tokens)
        or {"mobile", "home"}.issubset(tokens)
    ):
        return "attached"
    if "detached" in tokens or {"single", "family"}.issubset(tokens):
        return "detached_house"
    if tokens <= {"house"} or tokens <= {"home"}:
        # A generic native “house” value is intentionally broader than the
        # desired DETACHED_HOUSE category.  The matcher handles that explicit
        # containment relationship below.
        return "house"
    return None


def _property_type_match(expected: Any, observed: Any) -> bool | None:
    if _is_unknown(expected) or _is_unknown(observed):
        return None
    expected_category = _property_type_category(expected)
    observed_category = _property_type_category(observed)
    if expected_category is not None and observed_category is not None:
        if expected_category == "house":
            # Native “house” is a broad source category.  It may contain
            # detached, townhouse, and other attached-house cards, while
            # condos/apartments remain outside that category.
            return observed_category in {
                "house",
                "detached_house",
                "townhouse",
                "semi_detached",
                "attached",
            }
        if expected_category == "detached_house":
            # A generic “house” card does not establish the detached subtype.
            # Explicit attached categories, including “detached townhome”,
            # remain known violations rather than unknowns.
            if observed_category == "house":
                return None
            return observed_category == "detached_house"
        return expected_category == observed_category
    # An unmapped category is not evidence of a match.  In particular, two
    # unrelated labels must not collapse into a shared “other” bucket.
    return None


def _status_category(value: Any) -> str | None:
    text = _normal_text(value)
    if not text or _is_unknown(value):
        return None
    explicit_category = _categorical_category("status", value)
    if explicit_category is not None:
        return explicit_category
    tokens = set(text.split())
    if tokens & {"sold", "closed", "completed"}:
        return "sold"
    if "pending" in tokens or "conditional" in tokens or {"under", "offer"}.issubset(tokens):
        return "pending"
    if tokens & {"expired", "terminated", "withdrawn", "inactive", "cancelled"}:
        return "inactive"
    if (
        tokens & {"active", "listed", "current"}
        or {"for", "sale"}.issubset(tokens)
        or {"new", "listing"}.issubset(tokens)
    ):
        return "active"
    return None


def _semantic_match(field: str, expected: Any, observed: Any) -> bool | None:
    canonical = _canonical_filter_name(field)
    expected_values = _iter_expected_values(expected)
    outcomes: list[bool | None] = []
    for expected_item in expected_values:
        if canonical in {"ownership", "property_type", "status"} and _categorical_label_is_negated(
            canonical,
            expected_item,
            observed,
        ):
            outcomes.append(False)
            continue
        if canonical == "ownership":
            expected_category = _ownership_category(expected_item)
            observed_category = _ownership_category(observed)
            if expected_category is not None and observed_category is not None:
                outcome = expected_category == observed_category
            else:
                outcome = None
        elif canonical == "property_type":
            outcome = _property_type_match(expected_item, observed)
        elif canonical == "status":
            expected_category = _status_category(expected_item)
            observed_category = _status_category(observed)
            if expected_category is not None and observed_category is not None:
                outcome = expected_category == observed_category
            else:
                outcome = None
        else:
            outcome = _text_match(expected_item, observed)
        outcomes.append(outcome)

    if True in outcomes:
        return True
    if any(outcome is None for outcome in outcomes):
        return None
    return False


def _evaluate_filter(field: str, expected: Any, observed: Any) -> bool | None:
    """Return true/false/unknown for one canonical discovery constraint."""

    canonical = _canonical_filter_name(field)
    if _is_unknown(expected):
        return True
    if _is_missing(observed) or _is_unknown(observed):
        return None

    numeric_minimums = {
        "minimum_bedrooms",
        "minimum_bathrooms",
        "minimum_finished_sqft",
    }
    if canonical in {"price_min", "price_max", *numeric_minimums}:
        expected_number = _number_for_compare(expected)
        observed_number = _number_for_compare(observed)
        if expected_number is None or observed_number is None:
            return None
        if canonical == "price_min" or canonical in numeric_minimums:
            return observed_number >= expected_number
        return observed_number <= expected_number

    return _semantic_match(canonical, expected, observed)


def _filter_violation(
    *,
    stage: str,
    field: str,
    card_index: int,
    expected: Any,
    observed: Any,
    reason_code: str,
) -> dict[str, Any]:
    return {
        "stage": stage,
        "field": field,
        "card_index": card_index,
        "expected": expected,
        "observed": observed,
        "reason_code": reason_code,
    }


def _boundary_spec(plan: DiscoveryFilterPlan) -> tuple[str, str] | None:
    geography = plan.geography if isinstance(plan.geography, Mapping) else {}
    map_params = geography.get("map_params", geography)
    if not isinstance(map_params, Mapping):
        return None
    boundary_id = map_params.get("boundary_id")
    registry_path = map_params.get("boundary_registry_path")
    if not boundary_id and not registry_path:
        return None
    if not isinstance(boundary_id, str) or not boundary_id.strip():
        raise ValueError("boundary_registry_path requires boundary_id")
    if not isinstance(registry_path, str) or not registry_path.strip():
        raise ValueError("boundary_id requires boundary_registry_path")
    return boundary_id, registry_path


def _validate_boundary_points(
    cards: Sequence[DiscoveryCard],
    plan: DiscoveryFilterPlan,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Check map-card coordinates against the persisted region polygon."""

    specification = _boundary_spec(plan)
    if specification is None:
        return [], []
    boundary_id, registry_path = specification
    registry = load_boundary_registry(registry_path)
    region = region_by_id(registry, boundary_id)
    geometry = region.get("partition_geometry") or region.get("geometry")
    if not isinstance(geometry, Mapping):
        raise ValueError(f"boundary {boundary_id!r} has no geometry")
    polygons = geometry_to_multipolygon(geometry)
    violations: list[dict[str, Any]] = []
    unknowns: list[dict[str, Any]] = []
    for card_index, card in enumerate(cards):
        if card.latitude is None or card.longitude is None:
            unknowns.append(
                _filter_violation(
                    stage="boundary",
                    field="latitude/longitude",
                    card_index=card_index,
                    expected=boundary_id,
                    observed=None,
                    reason_code="BOUNDARY_POINT_UNKNOWN",
                )
            )
            continue
        if not point_in_multipolygon((float(card.longitude), float(card.latitude)), polygons):
            violations.append(
                _filter_violation(
                    stage="boundary",
                    field="boundary_id",
                    card_index=card_index,
                    expected=boundary_id,
                    observed={"latitude": card.latitude, "longitude": card.longitude},
                    reason_code="BOUNDARY_POINT_OUTSIDE",
                )
            )
    return violations, unknowns


def _requires_boundary_coordinates(
    capture: DiscoveryCapture,
    plan: DiscoveryFilterPlan,
) -> bool:
    """Require coordinates for native map sources, not local replay fixtures."""

    source_name = str(plan.source_name or capture.source_name or "").casefold()
    return "native" in source_name


def validate_capture(
    capture: DiscoveryCapture | Mapping[str, Any],
    plan: DiscoveryFilterPlan | Mapping[str, Any],
    sample_size: int,
    max_result_cards: int,
) -> DiscoveryValidation:
    """Fail-closed validation of a filtered discovery capture."""

    try:
        capture_payload = (
            capture.model_dump(mode="python")
            if isinstance(capture, DiscoveryCapture)
            else deepcopy(capture)
        )
        # Revalidate a fresh payload so neither envelope metadata inheritance
        # nor reason-code annotation can mutate the caller's model graph.
        capture_model = DiscoveryCapture.model_validate(capture_payload)
        plan_model = (
            plan if isinstance(plan, DiscoveryFilterPlan) else DiscoveryFilterPlan.model_validate(plan)
        )
    except Exception as exc:
        return _failure(
            DiscoveryStatus.PARSER_ERROR,
            ["CAPTURE_PARSE_ERROR"],
            violations=[{"stage": "envelope", "error": str(exc)}],
        )

    sample_limit = _parse_public_limit(sample_size)
    result_cap = _parse_public_limit(max_result_cards)
    if sample_limit is None or result_cap is None:
        return _failure(DiscoveryStatus.PARSER_ERROR, ["INVALID_VALIDATION_LIMIT"])

    cards = _inherit_capture_metadata(capture_model)

    try:
        boundary_violations, boundary_unknowns = _validate_boundary_points(cards, plan_model)
    except (OSError, TypeError, ValueError, KeyError, json.JSONDecodeError) as exc:
        return _failure(
            DiscoveryStatus.PARSER_ERROR,
            ["BOUNDARY_REGISTRY_ERROR"],
            violations=[{"stage": "boundary", "reason_code": "BOUNDARY_REGISTRY_ERROR", "error": str(exc)}],
        )
    if boundary_violations:
        return _failure(
            DiscoveryStatus.FILTER_NOT_APPLIED,
            ["BOUNDARY_POINT_OUTSIDE"],
            violations=boundary_violations,
            sampled_card_count=min(len(cards), sample_limit),
        )
    if boundary_unknowns and _requires_boundary_coordinates(capture_model, plan_model):
        return _failure(
            DiscoveryStatus.FILTER_VALIDATION_INCONCLUSIVE,
            ["BOUNDARY_POINT_UNKNOWN"],
            violations=boundary_unknowns,
            sampled_card_count=min(len(cards), sample_limit),
        )

    if capture_model.blocked:
        return _failure(
            DiscoveryStatus.SOURCE_BLOCKED,
            ["CAPTURE_BLOCKED"],
            violations=[{"stage": "envelope", "reason_code": "CAPTURE_BLOCKED"}],
        )
    if not 200 <= capture_model.status_code < 300:
        return _failure(
            DiscoveryStatus.SOURCE_BLOCKED,
            ["NON_2XX_STATUS"],
            violations=[
                {
                    "stage": "envelope",
                    "status_code": capture_model.status_code,
                    "reason_code": "NON_2XX_STATUS",
                }
            ],
        )
    if capture_model.preset_name is not None and capture_model.preset_name != plan_model.preset_name:
        return _failure(
            DiscoveryStatus.FILTER_VALIDATION_INCONCLUSIVE,
            ["PRESET_MISMATCH"],
            violations=[
                {
                    "stage": "envelope",
                    "capture_preset_name": capture_model.preset_name,
                    "plan_preset_name": plan_model.preset_name,
                    "reason_code": "PRESET_MISMATCH",
                }
            ],
        )
    if plan_model.unsupported_filters:
        return _failure(
            DiscoveryStatus.UNSUPPORTED_FILTER,
            ["UNSUPPORTED_FILTERS_PRESENT"],
            violations=[
                {
                    "stage": "plan",
                    "field": field,
                    "expected": value,
                    "reason_code": "UNSUPPORTED_FILTERS_PRESENT",
                }
                for field, value in plan_model.unsupported_filters.items()
            ],
        )

    # The materialized card list is always an independently bounded payload,
    # even when the source count is absent, provisional, or underreported.
    if len(cards) > result_cap:
        return _failure(
            DiscoveryStatus.RESULT_SET_TOO_LARGE,
            ["CARD_COUNT_EXCEEDS_CAP"],
            violations=[
                {
                    "stage": "envelope",
                    "card_count": len(cards),
                    "max_result_cards": result_cap,
                    "reason_code": "CARD_COUNT_EXCEEDS_CAP",
                }
            ],
        )

    if capture_model.result_count is not None and capture_model.result_count < 0:
        return _failure(
            DiscoveryStatus.PARSER_ERROR,
            ["NEGATIVE_RESULT_COUNT"],
            violations=[
                {
                    "stage": "envelope",
                    "result_count": capture_model.result_count,
                    "reason_code": "NEGATIVE_RESULT_COUNT",
                }
            ],
        )

    if (
        capture_model.count_is_complete
        and capture_model.result_count is not None
        and capture_model.result_count < len(cards)
    ):
        return _failure(
            DiscoveryStatus.PARSER_ERROR,
            ["RESULT_COUNT_UNDERREPORTS_CARDS"],
            violations=[
                {
                    "stage": "envelope",
                    "result_count": capture_model.result_count,
                    "card_count": len(cards),
                    "reason_code": "RESULT_COUNT_UNDERREPORTS_CARDS",
                }
            ],
        )

    # A count is authoritative for the cap only when the source marks it
    # complete.  Provisional values, including provisional values above the
    # cap, must remain inconclusive rather than being treated as oversized.
    if capture_model.result_count is None or not capture_model.count_is_complete:
        return _failure(
            DiscoveryStatus.FILTER_VALIDATION_INCONCLUSIVE,
            ["RESULT_COUNT_UNKNOWN"],
            violations=[
                {
                    "stage": "envelope",
                    "result_count": capture_model.result_count,
                    "count_is_complete": capture_model.count_is_complete,
                    "reason_code": "RESULT_COUNT_UNKNOWN",
                }
            ],
        )
    if capture_model.result_count > result_cap:
        return _failure(
            DiscoveryStatus.RESULT_SET_TOO_LARGE,
            ["RESULT_COUNT_EXCEEDS_CAP"],
            violations=[
                {
                    "stage": "envelope",
                    "result_count": capture_model.result_count,
                    "max_result_cards": result_cap,
                    "reason_code": "RESULT_COUNT_EXCEEDS_CAP",
                }
            ],
        )
    if capture_model.result_count > 0 and not cards:
        return _failure(
            DiscoveryStatus.FILTER_VALIDATION_INCONCLUSIVE,
            ["RESULT_CARDS_MISSING"],
            violations=[
                {
                    "stage": "envelope",
                    "result_count": capture_model.result_count,
                    "reason_code": "RESULT_CARDS_MISSING",
                }
            ],
        )

    sampled_cards = cards[:sample_limit]
    sampled_count = len(sampled_cards)
    native_violations: list[dict[str, Any]] = []
    native_unknowns: list[dict[str, Any]] = []

    applied_filter_violations: list[dict[str, Any]] = []
    for field, native_value in plan_model.native_filters.items():
        applied_state = _applied_filter_state(capture_model, field, native_value)
        if applied_state is False:
            applied_filter_violations.append(
                _filter_violation(
                    stage="envelope",
                    field=field,
                    card_index=-1,
                    expected=native_value,
                    observed=capture_model.applied_filters.get(field),
                    reason_code="APPLIED_FILTER_MISMATCH",
                )
            )
    if applied_filter_violations:
        return _failure(
            DiscoveryStatus.FILTER_NOT_APPLIED,
            ["APPLIED_FILTER_MISMATCH"],
            violations=applied_filter_violations,
            sampled_card_count=sampled_count,
        )

    if not sampled_cards and plan_model.native_filters:
        missing_evidence: list[dict[str, Any]] = []
        for field, native_value in plan_model.native_filters.items():
            if not _has_filter_evidence(capture_model, field, native_value):
                missing_evidence.append(
                    _filter_violation(
                        stage="native",
                        field=field,
                        card_index=-1,
                        expected=native_value,
                        observed=None,
                        reason_code="NATIVE_FILTER_EVIDENCE_MISSING",
                    )
                )
        if missing_evidence:
            return _failure(
                DiscoveryStatus.FILTER_VALIDATION_INCONCLUSIVE,
                ["NATIVE_FILTER_EVIDENCE_MISSING"],
                violations=missing_evidence,
                sampled_card_count=sampled_count,
            )

    for card_index, card in enumerate(sampled_cards):
        for field, native_value in plan_model.native_filters.items():
            card_field = _card_field_for_filter(field)
            observed = _card_value(card, card_field)
            if _is_missing(observed) or _is_unknown(observed):
                if _has_filter_evidence(capture_model, field, native_value):
                    continue
                native_unknowns.append(
                    _filter_violation(
                        stage="native",
                        field=field,
                        card_index=card_index,
                        expected=native_value,
                        observed=observed,
                        reason_code="NATIVE_FILTER_VALUE_UNKNOWN",
                    )
                )
                continue

            # Native validation answers whether the source-control value was
            # respected.  The desired value, when stricter, is checked later
            # by the post-filter retained in the compiled plan.
            expected = native_value
            outcome = _evaluate_filter(field, expected, observed)
            if outcome is False:
                native_violations.append(
                    _filter_violation(
                        stage="native",
                        field=field,
                        card_index=card_index,
                        expected=expected,
                        observed=observed,
                        reason_code="NATIVE_FILTER_VIOLATION",
                    )
                )
            elif outcome is None and not _has_filter_evidence(capture_model, field, native_value):
                native_unknowns.append(
                    _filter_violation(
                        stage="native",
                        field=field,
                        card_index=card_index,
                        expected=expected,
                        observed=observed,
                        reason_code="NATIVE_FILTER_VALUE_UNKNOWN",
                    )
                )

    if native_violations:
        return _failure(
            DiscoveryStatus.FILTER_NOT_APPLIED,
            ["NATIVE_FILTER_VIOLATION"],
            violations=native_violations + native_unknowns,
            sampled_card_count=sampled_count,
        )
    if native_unknowns:
        return _failure(
            DiscoveryStatus.FILTER_VALIDATION_INCONCLUSIVE,
            ["NATIVE_FILTER_VALUE_UNKNOWN"],
            violations=native_unknowns,
            sampled_card_count=sampled_count,
        )

    candidates: list[DiscoveryCard] = []
    reason_codes: list[str] = []
    post_violations: list[dict[str, Any]] = []
    for card_index, card in enumerate(cards):
        excluded = False
        for field, expected in plan_model.post_filters.items():
            card_field = _card_field_for_filter(field)
            observed = _card_value(card, card_field)
            outcome = _evaluate_filter(field, expected, observed)
            if outcome is False:
                excluded = True
                _add_reason(card.reason_codes, "POST_FILTER_EXCLUDED")
                _add_reason(reason_codes, "POST_FILTER_EXCLUDED")
                post_violations.append(
                    _filter_violation(
                        stage="post",
                        field=field,
                        card_index=card_index,
                        expected=expected,
                        observed=observed,
                        reason_code="POST_FILTER_EXCLUDED",
                    )
                )
                break
            if outcome is None:
                _add_reason(card.reason_codes, "POST_FILTER_UNKNOWN")
                _add_reason(reason_codes, "POST_FILTER_UNKNOWN")
                post_violations.append(
                    _filter_violation(
                        stage="post",
                        field=field,
                        card_index=card_index,
                        expected=expected,
                        observed=observed,
                        reason_code="POST_FILTER_UNKNOWN",
                    )
                )
        if not excluded:
            candidates.append(card)

    capture_model.cards = cards
    return DiscoveryValidation(
        status=DiscoveryStatus.PASS,
        reason_codes=reason_codes,
        violations=post_violations,
        sampled_card_count=sampled_count,
        candidate_cards=candidates,
    )


_IDENTITY_PLACEHOLDERS = {
    "",
    "0",
    "00",
    "000",
    "n a",
    "na",
    "nil",
    "none",
    "null",
    "unknown",
    "not applicable",
    "not available",
    "not provided",
    "no address",
    "no mls",
    "no mls number",
}


def _is_invalid_identity_value(value: Any) -> bool:
    if value is None or isinstance(value, bool):
        return True
    if isinstance(value, (int, float)):
        try:
            return value == 0 or not math.isfinite(float(value))
        except (OverflowError, TypeError, ValueError):
            return True
    if isinstance(value, Mapping) or isinstance(value, (list, tuple, set, frozenset)):
        return True
    if not isinstance(value, str):
        return True
    if _is_unknown(value):
        return True
    text = _normal_text(value)
    compact = text.replace(" ", "")
    if text in _IDENTITY_PLACEHOLDERS or re.fullmatch(r"0+(?:\.0+)?", compact or ""):
        return True
    return text.startswith(("unknown mls", "no mls", "not available"))


def _normalise_mls(value: Any) -> str | None:
    if _is_invalid_identity_value(value):
        return None
    text = unicodedata.normalize("NFKC", str(value)).upper()
    text = re.sub(r"\bMLS(?:#|\s*(?:NO|NUMBER))?\b", "", text)
    if _is_invalid_identity_value(text):
        return None
    key = re.sub(r"[^A-Z0-9]+", "", text)
    if not key or re.fullmatch(r"0+", key):
        return None
    return key


def _safe_address_key(card: DiscoveryCard) -> str:
    if _is_invalid_identity_value(card.address):
        return ""
    municipality = None if _is_invalid_identity_value(card.municipality) else card.municipality
    try:
        normalized = normalize_address(card.address, municipality)
    except Exception:
        text = _normal_text(card.address)
        if municipality is not None:
            text = f"{text} {_normal_text(municipality)}".strip()
        normalized = text
    return "" if _is_invalid_identity_value(normalized) else normalized


def deduplicate_cards(
    cards: Iterable[DiscoveryCard | Mapping[str, Any]],
) -> DiscoveryDeduplicationResult:
    """Deduplicate exact MLS identities, then no-MLS address identities.

    A retained card is never populated from another card.  This intentionally
    leaves conflicting observations visible instead of silently merging them.
    """

    source_cards: list[DiscoveryCard] = []
    try:
        for value in cards:
            card = value if isinstance(value, DiscoveryCard) else DiscoveryCard.model_validate(value)
            source_cards.append(card.model_copy(deep=True))
    except Exception as exc:
        return DiscoveryDeduplicationResult(
            cards=[],
            duplicate_flags=["PARSER_ERROR"],
            reason_codes=[f"PARSER_ERROR:{exc}"],
        )

    retained: list[DiscoveryCard] = []
    retained_mls: dict[str, int] = {}
    address_entries: dict[str, list[tuple[int, str | None]]] = {}
    no_mls_addresses: dict[str, int] = {}
    flags: list[str] = []
    duplicate_observations: list[DiscoveryCard] = []
    duplicate_observation_metadata: list[dict[str, Any]] = []

    def flag(code: str) -> None:
        _add_reason(flags, code)

    def retain_duplicate_observation(
        card: DiscoveryCard,
        *,
        reason_code: str,
        canonical_index: int,
        duplicate_index: int,
        identity_key: str,
    ) -> None:
        observation = card.model_copy(deep=True)
        _add_reason(observation.reason_codes, reason_code)
        duplicate_observations.append(observation)
        duplicate_observation_metadata.append(
            {
                "reason_code": reason_code,
                "canonical_index": canonical_index,
                "duplicate_index": duplicate_index,
                "identity_key": identity_key,
            }
        )

    for input_index, card in enumerate(source_cards):
        mls_key = _normalise_mls(card.mls_number)
        address_key = _safe_address_key(card)

        if mls_key is not None and mls_key in retained_mls:
            flag("EXACT_MLS_DUPLICATE")
            canonical_index = retained_mls[mls_key]
            retained_card = retained[canonical_index]
            _add_reason(retained_card.reason_codes, "EXACT_MLS_DUPLICATE")
            retain_duplicate_observation(
                card,
                reason_code="EXACT_MLS_DUPLICATE",
                canonical_index=canonical_index,
                duplicate_index=input_index,
                identity_key=f"mls:{mls_key}",
            )
            continue

        if mls_key is None and address_key and address_key in no_mls_addresses:
            flag("ADDRESS_FALLBACK_DUPLICATE")
            canonical_index = no_mls_addresses[address_key]
            _add_reason(retained[canonical_index].reason_codes, "ADDRESS_FALLBACK_DUPLICATE")
            retain_duplicate_observation(
                card,
                reason_code="ADDRESS_FALLBACK_DUPLICATE",
                canonical_index=canonical_index,
                duplicate_index=input_index,
                identity_key=f"address:{address_key}",
            )
            continue

        retained_index = len(retained)
        retained.append(card)
        if mls_key is not None:
            retained_mls[mls_key] = retained_index
        else:
            if address_key:
                no_mls_addresses[address_key] = retained_index

        if address_key:
            entries = address_entries.setdefault(address_key, [])
            for prior_index, prior_mls in entries:
                if prior_mls is not None and mls_key is not None and prior_mls != mls_key:
                    flag("POSSIBLE_DUPLICATE")
                    _add_reason(retained[prior_index].reason_codes, "POSSIBLE_DUPLICATE")
                    _add_reason(card.reason_codes, "POSSIBLE_DUPLICATE")
                elif (prior_mls is None) != (mls_key is None):
                    # A no-MLS card cannot establish identity against an MLS
                    # card, so retain it and make the uncertainty explicit.
                    flag("POSSIBLE_DUPLICATE")
                    _add_reason(retained[prior_index].reason_codes, "POSSIBLE_DUPLICATE")
                    _add_reason(card.reason_codes, "POSSIBLE_DUPLICATE")
            entries.append((retained_index, mls_key))

    result = DiscoveryDeduplicationResult(
        cards=retained,
        duplicate_flags=list(flags),
        reason_codes=list(flags),
        duplicate_observations=duplicate_observations,
    )
    result._duplicate_observation_metadata = duplicate_observation_metadata
    return result


def _raw_or_extra(card: DiscoveryCard, *names: str) -> Any:
    for name in names:
        extra = getattr(card, name, None)
        if extra is not None:
            return extra
        if name in card.raw:
            return card.raw[name]
    return None


def _core_range(card: DiscoveryCard) -> tuple[float, float] | None:
    value = _raw_or_extra(card, "core_range", "core_price_range", "core_asking_range")
    if isinstance(value, Mapping):
        value = value.get("range") or value.get("bounds") or [value.get("min"), value.get("max")]
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) != 2:
        return None
    low = _number_for_compare(value[0])
    high = _number_for_compare(value[1])
    if low is None or high is None:
        return None
    return (min(low, high), max(low, high))


def _core_signal(card: DiscoveryCard) -> int:
    classification = _raw_or_extra(card, "range_classification")
    if classification is not None:
        text = _normal_text(classification)
        negative_markers = (
            "out of core range",
            "outside core range",
            "out of range",
            "outside",
            "reject",
            "special",
            "not core",
        )
        if any(marker in text for marker in negative_markers):
            return 0
        if "core" in text:
            return 2

    bounds = _core_range(card)
    if bounds is None:
        return 1
    asking = _number_for_compare(card.asking)
    if asking is None:
        return 1
    return 2 if bounds[0] <= asking <= bounds[1] else 0


def _known(card: DiscoveryCard, field: str) -> bool:
    value = _card_value(card, field)
    if field in _NUMERIC_CARD_FIELDS:
        return _number_for_compare(value) is not None
    return not _is_missing(value) and not _is_unknown(value)


def _lot_signal(card: DiscoveryCard) -> tuple[int, int, float]:
    values = [
        card.lot_area_sqft,
        card.frontage_ft,
        card.depth_ft,
        card.lot_dimensions,
    ]
    visible = sum(not _is_missing(value) and not _is_unknown(value) for value in values)
    area = _number_for_compare(card.lot_area_sqft) or 0.0
    # The area bucket is deliberately modest: it provides a visible-lot
    # signal without treating a small lot as an exclusion.
    area_bucket = min(area / 1_000.0, 20.0) if area > 0 else 0.0
    return (1 if visible else 0, visible, area_bucket)


def _freshness_signal(card: DiscoveryCard) -> tuple[int, float, float]:
    dom = _number_for_compare(card.dom)
    observed = _as_utc(card.observed_at)
    timestamp = observed.timestamp() if isinstance(observed, datetime) else 0.0
    return (1 if dom is not None else 0, -dom if dom is not None else 0.0, timestamp)


def _completeness(card: DiscoveryCard) -> int:
    fields = (
        "source_name",
        "mls_number",
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
        "source_url",
        "feed_url",
        "observed_at",
    )
    return sum(_known(card, field) for field in fields)


def _ranking_key(card: DiscoveryCard) -> tuple[Any, ...]:
    current_use_known = sum(
        _known(card, field)
        for field in (
            "bedrooms",
            "bathrooms",
            "finished_sqft",
            "property_type",
            "ownership_type",
            "listing_status",
        )
    )
    lot_signal = _lot_signal(card)
    freshness = _freshness_signal(card)
    return (
        _core_signal(card),
        current_use_known,
        *lot_signal,
        *freshness,
        _completeness(card),
        *_stable_identity_key(card),
        _stable_serialized_card(card),
    )


def _stable_identity_key(card: DiscoveryCard) -> tuple[str, str, str]:
    """Provide an observable tie-breaker independent of input order."""

    mls_key = _normalise_mls(card.mls_number) or ""
    address_key = _safe_address_key(card)
    source_url_key = (
        unicodedata.normalize("NFKC", str(card.source_url or "")).strip().casefold()
    )
    return (mls_key, address_key, source_url_key)


def _stable_serialized_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        entries = sorted(
            ((str(key), _stable_serialized_value(item)) for key, item in value.items()),
            key=lambda entry: entry[0],
        )
        return {"__mapping__": [[key, item] for key, item in entries]}
    if isinstance(value, (list, tuple)):
        return ["__sequence__", [_stable_serialized_value(item) for item in value]]
    if isinstance(value, (set, frozenset)):
        values = [_stable_serialized_value(item) for item in value]
        values.sort(key=repr)
        return ["__set__", values]
    if isinstance(value, datetime):
        normalized = _as_utc(value)
        return normalized.isoformat() if isinstance(normalized, datetime) else repr(value)
    if isinstance(value, float) and not math.isfinite(value):
        return {"__float__": repr(value)}
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return {"__object__": f"{type(value).__module__}.{type(value).__qualname__}:{value!r}"}


def _stable_serialized_card(card: DiscoveryCard) -> str:
    try:
        payload = _stable_serialized_value(card.model_dump(mode="python"))
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError, OverflowError):
        return repr(card.model_dump(mode="python"))


def rank_shortlist(
    cards: Iterable[DiscoveryCard | Mapping[str, Any]],
    limit: int | None = None,
) -> list[DiscoveryCard]:
    """Return a transparent, optionally bounded discovery shortlist.

    This is intentionally a deterministic ordering of observable signals,
    not an investment score or a legal/redevelopment decision.
    """

    shortlist_limit = None if limit is None else _parse_public_limit(limit)
    if limit is not None and shortlist_limit is None:
        return []

    parsed_cards: list[DiscoveryCard] = []
    for value in cards:
        card = value if isinstance(value, DiscoveryCard) else DiscoveryCard.model_validate(value)
        copy = card.model_copy(deep=True)
        area = _number_for_compare(copy.lot_area_sqft)
        if area is not None and area < 3_000:
            _add_reason(copy.reason_codes, "SMALL_LOT")
        parsed_cards.append(copy)

    ranked = sorted(parsed_cards, key=_ranking_key, reverse=True)
    return ranked if shortlist_limit is None else ranked[:shortlist_limit]


__all__ = [
    "FILTER_CARD_FIELD_MAPPING",
    "DeduplicationResult",
    "DiscoveryCard",
    "DiscoveryCapture",
    "DiscoveryDedupeResult",
    "DiscoveryDeduplicationResult",
    "DiscoveryDuplicateObservation",
    "DiscoveryFilterPlan",
    "DiscoveryPreset",
    "DiscoveryStatus",
    "DiscoveryValidation",
    "SiteCapabilities",
    "compile_filter_plan",
    "deduplicate_cards",
    "rank_shortlist",
    "validate_capture",
]
