"""Cheap, track-aware routing before expensive Strategy V2 photo analysis.

The router deliberately separates three decisions:

* whether a property remains eligible for a full or representative visual
  budget;
* whether the property was selected by the existing high-recall shortlist;
* which visual route is finally executed.

It never rejects a property from the structured research universe and never
turns a missing field into a negative property-quality score.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


VisualEligibility = Literal[
    "FULL_POOL",
    "REPRESENTATIVE_POOL",
    "STRUCTURED_ONLY",
    "SPECIAL_REVIEW",
]
AnalysisRoute = Literal[
    "FULL_VISION",
    "REPRESENTATIVE_VISION",
    "STRUCTURED_ONLY",
    "SPECIAL_REVIEW",
]
HomeFit = Literal["PASS", "FAIL", "UNKNOWN"]
DevelopmentTextSignal = Literal["NONE", "GENERAL", "LAND_DOMINANT"]


DEFAULT_ANALYSIS_ROUTING: dict[str, Any] = {
    "min_home_bedrooms": 3,
    "min_home_bathrooms": 2,
    "min_home_lot_area_sqft": 3000,
    "min_visual_photo_count": 3,
    "min_full_visual_photo_count": 6,
    "representative_photo_budget": 3,
    "special_tracks": ["SPECIAL_SITUATION"],
    "land_dominant_phrases": [
        "land value only",
        "being sold for land value",
        "sold for land value",
        "offered at land value",
        "value of the land",
        "building lot",
        "tear down",
        "teardown",
        "no value in the house",
    ],
    "development_signal_phrases": [
        "rebuild",
        "re-develop",
        "redevelop",
        "development site",
        "development opportunity",
        "development potential",
        "future development",
        "developer",
        "builder alert",
        "building lot",
        "land value",
    ],
}


class AnalysisRoutingDecision(BaseModel):
    """Auditable routing result for one property."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    schema_version: str = "strategy-v2-analysis-routing-v1"
    property_key: str
    primary_track: str = "UNCLASSIFIED_REVIEW"
    visual_eligibility: VisualEligibility = "STRUCTURED_ONLY"
    analysis_route: AnalysisRoute = "STRUCTURED_ONLY"
    photo_count: int | None = Field(default=None, ge=0)
    unique_photo_count: int | None = Field(default=None, ge=0)
    lot_area_sqft: float | None = Field(default=None, ge=0)
    bedrooms: float | None = Field(default=None, ge=0)
    bathrooms: float | None = Field(default=None, ge=0)
    home_fit: HomeFit = "UNKNOWN"
    development_text_signal: DevelopmentTextSignal = "NONE"
    selected_for_visual: bool = False
    visual_budget_images: int = Field(default=0, ge=0)
    flags: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    selection_reasons: list[str] = Field(default_factory=list)


def _value(item: Any, name: str, default: Any = None) -> Any:
    if isinstance(item, Mapping):
        return item.get(name, default)
    return getattr(item, name, default)


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        result = float(value)
        return result if math.isfinite(result) else None
    text = str(value).strip()
    if not text or text.upper() in {"UNKNOWN", "N/A", "NA", "NULL", "NONE", "UNAVAILABLE"}:
        return None
    match = re.search(r"[-+]?\d[\d,]*(?:\.\d+)?", text)
    if not match:
        return None
    try:
        result = float(match.group(0).replace(",", ""))
    except ValueError:
        return None
    return result if math.isfinite(result) else None


def _text(value: Any) -> str:
    return " ".join(str(value or "").lower().replace("&amp;", "and").split())


def _routing_config(config: Mapping[str, Any] | None) -> dict[str, Any]:
    result = dict(DEFAULT_ANALYSIS_ROUTING)
    if not isinstance(config, Mapping):
        return result
    supplied = config.get("analysis_routing") if "analysis_routing" in config else config
    if not isinstance(supplied, Mapping):
        return result
    for key, value in supplied.items():
        if value is not None:
            result[key] = value
    return result


def _phrases(config: Mapping[str, Any], key: str) -> tuple[str, ...]:
    raw = config.get(key, DEFAULT_ANALYSIS_ROUTING[key])
    if isinstance(raw, str):
        raw_values: Sequence[Any] = [raw]
    elif isinstance(raw, Sequence) and not isinstance(raw, (bytes, bytearray)):
        raw_values = raw
    else:
        raw_values = DEFAULT_ANALYSIS_ROUTING[key]
    return tuple(_text(item) for item in raw_values if _text(item))


def _contains_any(text: str, phrases: Sequence[str]) -> bool:
    return any(phrase in text for phrase in phrases)


def _property_key(candidate: Any) -> str:
    value = _value(candidate, "property_key")
    if value is None or not str(value).strip():
        value = _value(candidate, "address")
    return str(value).strip() if value is not None and str(value).strip() else "unknown-property"


def _track(candidate: Any) -> str:
    return str(_value(candidate, "primary_track", "UNCLASSIFIED_REVIEW") or "UNCLASSIFIED_REVIEW").strip().upper()


def _home_fit(
    *,
    lot_area: float | None,
    bedrooms: float | None,
    bathrooms: float | None,
    config: Mapping[str, Any],
) -> HomeFit:
    minimums = (
        (lot_area, _number(config.get("min_home_lot_area_sqft"))),
        (bedrooms, _number(config.get("min_home_bedrooms"))),
        (bathrooms, _number(config.get("min_home_bathrooms"))),
    )
    if any(value is not None and minimum is not None and value < minimum for value, minimum in minimums):
        return "FAIL"
    if any(value is None or minimum is None for value, minimum in minimums):
        return "UNKNOWN"
    return "PASS"


def _development_text_signal(text: str, config: Mapping[str, Any]) -> DevelopmentTextSignal:
    if _contains_any(text, _phrases(config, "land_dominant_phrases")):
        return "LAND_DOMINANT"
    if _contains_any(text, _phrases(config, "development_signal_phrases")):
        return "GENERAL"
    return "NONE"


def _is_special(track: str, config: Mapping[str, Any]) -> bool:
    special = config.get("special_tracks", DEFAULT_ANALYSIS_ROUTING["special_tracks"])
    if isinstance(special, str):
        special = [special]
    if not isinstance(special, Sequence):
        special = DEFAULT_ANALYSIS_ROUTING["special_tracks"]
    return track in {str(item).strip().upper() for item in special}


def _exception_route(candidate: Any, *, home_fit: HomeFit, text_signal: DevelopmentTextSignal) -> bool:
    track = _track(candidate)
    if track in {"PREMIUM_SMALL_LOT", "LAND_DEVELOPMENT"}:
        return True
    return home_fit != "PASS" and text_signal == "GENERAL"


def _decision(
    base: Mapping[str, Any],
    *,
    flags: Sequence[str] | None = None,
    reasons: Sequence[str] | None = None,
    **updates: Any,
) -> AnalysisRoutingDecision:
    """Build a decision while safely replacing accumulated list fields."""

    payload = dict(base)
    if flags is not None:
        payload["flags"] = list(flags)
    if reasons is not None:
        payload["reasons"] = list(reasons)
    payload.update(updates)
    return AnalysisRoutingDecision(**payload)


def classify_visual_eligibility(
    candidate: Any,
    *,
    config: Mapping[str, Any] | None = None,
) -> AnalysisRoutingDecision:
    """Classify the visual-analysis eligibility without selecting a slot."""

    rules = _routing_config(config)
    track = _track(candidate)
    photo_count_value = _number(_value(candidate, "photo_count"))
    unique_photo_value = _number(_value(candidate, "unique_photo_count"))
    photo_count = int(photo_count_value) if photo_count_value is not None else None
    unique_photo_count = int(unique_photo_value) if unique_photo_value is not None else None
    lot_area = _number(_value(candidate, "lot_area_sqft"))
    bedrooms = _number(_value(candidate, "bedrooms"))
    bathrooms = _number(_value(candidate, "bathrooms"))
    home_fit = _home_fit(
        lot_area=lot_area,
        bedrooms=bedrooms,
        bathrooms=bathrooms,
        config=rules,
    )
    description = _text(_value(candidate, "description", _value(candidate, "listing_description", "")))
    text_signal = _development_text_signal(description, rules)
    flags: list[str] = []
    reasons: list[str] = []

    if home_fit == "PASS":
        flags.append("HOME_BASELINE_PASS")
        reasons.append("meets normal 3-bedroom/2-bathroom/3,000-sqft Home baseline")
    elif home_fit == "FAIL":
        flags.append("HOME_BASELINE_FAIL")
        reasons.append("does not meet the normal Home baseline; retain only through an exception route")
    else:
        flags.append("HOME_BASELINE_UNKNOWN")
        reasons.append("one or more Home baseline fields are unknown")

    if text_signal == "LAND_DOMINANT":
        flags.append("LAND_DOMINANT_TEXT")
        reasons.append("listing language is primarily land/development-oriented")
    elif text_signal == "GENERAL":
        flags.append("GENERAL_DEVELOPMENT_TEXT")
        reasons.append("listing mentions development but does not by itself eliminate holding value")

    base = {
        "property_key": _property_key(candidate),
        "primary_track": track,
        "photo_count": photo_count,
        "unique_photo_count": unique_photo_count,
        "lot_area_sqft": lot_area,
        "bedrooms": bedrooms,
        "bathrooms": bathrooms,
        "home_fit": home_fit,
        "development_text_signal": text_signal,
        "flags": flags,
        "reasons": reasons,
    }

    if _is_special(track, rules):
        return _decision(
            base,
            visual_eligibility="SPECIAL_REVIEW",
            analysis_route="SPECIAL_REVIEW",
            reasons=[*reasons, "special transaction/property track is kept outside the normal visual budget"],
            flags=[*flags, "SPECIAL_TRACK"],
        )

    minimum_photos = int(_number(rules.get("min_visual_photo_count")) or 3)
    full_photos = int(_number(rules.get("min_full_visual_photo_count")) or 6)
    if photo_count is None:
        return _decision(
            base,
            flags=[*flags, "PHOTO_COUNT_UNKNOWN"],
            reasons=[*reasons, "photo count is unknown; do not spend visual budget automatically"],
        )
    if photo_count < minimum_photos:
        return _decision(
            base,
            flags=[*flags, "INSUFFICIENT_PHOTOS"],
            reasons=[*reasons, f"only {photo_count} usable listing photo(s); structured analysis continues"],
        )

    if text_signal == "LAND_DOMINANT":
        if home_fit == "PASS":
            return _decision(
                base,
                visual_eligibility="REPRESENTATIVE_POOL",
                flags=[*flags, "LAND_TEXT_LIMITS_TO_REPRESENTATIVE"],
                reasons=[*reasons, "qualifying Home is retained, but land-dominant wording limits visual work to representative evidence"],
            )
        return _decision(
            base,
            flags=[*flags, "LAND_TEXT_SKIPS_INTERIOR_BUDGET"],
            reasons=[*reasons, "weak Home fit plus land-dominant wording routes to structured development analysis"],
        )

    if home_fit == "PASS":
        if photo_count >= full_photos:
            return _decision(
                base,
                visual_eligibility="FULL_POOL",
                reasons=[*reasons, "qualifying Home with enough photos for full visual eligibility"],
            )
        return _decision(
            base,
            visual_eligibility="REPRESENTATIVE_POOL",
            flags=[*flags, "PHOTO_COUNT_BELOW_FULL_THRESHOLD"],
            reasons=[*reasons, "qualifying Home has photos but below the full-visual threshold"],
        )

    if _exception_route(candidate, home_fit=home_fit, text_signal=text_signal):
        return _decision(
            base,
            visual_eligibility="REPRESENTATIVE_POOL",
            flags=[*flags, "EXCEPTION_ROUTE"],
            reasons=[*reasons, "development or premium-small-lot signal justifies limited representative visual review"],
        )

    return _decision(
        base,
        reasons=[*reasons, "no Home fit or approved exception; retain structured evidence without visual calls"],
    )


def apply_visual_selection(
    decision: AnalysisRoutingDecision,
    *,
    selected: bool,
    unique_photo_count: int | None = None,
    selection_reasons: Sequence[str] = (),
    representative_photo_budget: int = 3,
) -> AnalysisRoutingDecision:
    """Apply the separate visual-budget selection to an eligibility result."""

    resolved_unique_photo_count = (
        unique_photo_count
        if unique_photo_count is not None
        else decision.unique_photo_count
    )
    unique_photo_update = (
        {"unique_photo_count": int(resolved_unique_photo_count)}
        if resolved_unique_photo_count is not None
        else {}
    )

    if not selected or decision.visual_eligibility == "SPECIAL_REVIEW":
        route: AnalysisRoute = "SPECIAL_REVIEW" if decision.visual_eligibility == "SPECIAL_REVIEW" else "STRUCTURED_ONLY"
        return decision.model_copy(
            update={
                **unique_photo_update,
                "analysis_route": route,
                "selected_for_visual": False,
                "visual_budget_images": 0,
                "selection_reasons": [],
            }
        )

    photo_value = resolved_unique_photo_count
    if photo_value is None or int(photo_value) <= 0:
        return decision.model_copy(
            update={
                **unique_photo_update,
                "analysis_route": "STRUCTURED_ONLY",
                "selected_for_visual": False,
                "visual_budget_images": 0,
                "flags": [*decision.flags, "NO_USABLE_PHOTOS"],
                "reasons": [*decision.reasons, "visual slot selected but no usable local image is available"],
                "selection_reasons": list(selection_reasons),
            }
        )

    if decision.visual_eligibility == "FULL_POOL":
        route: AnalysisRoute = "FULL_VISION"
        budget = int(photo_value)
    elif decision.visual_eligibility == "REPRESENTATIVE_POOL":
        route = "REPRESENTATIVE_VISION"
        budget = min(int(photo_value), max(1, int(representative_photo_budget)))
    else:
        return decision.model_copy(
            update={
                **unique_photo_update,
                "analysis_route": "STRUCTURED_ONLY",
                "selected_for_visual": False,
                "visual_budget_images": 0,
                "selection_reasons": list(selection_reasons),
            }
        )

    return decision.model_copy(
        update={
            **unique_photo_update,
            "analysis_route": route,
            "selected_for_visual": True,
            "visual_budget_images": budget,
            "selection_reasons": list(selection_reasons),
        }
    )


__all__ = [
    "AnalysisRoutingDecision",
    "apply_visual_selection",
    "classify_visual_eligibility",
]
