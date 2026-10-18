from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from .base import LocationComposite


REQUIRED_EXTERNAL_SCORES = ("walk", "transit", "bike")
WALK_SCORE_WEIGHTS = {"walk": 0.50, "transit": 0.375, "bike": 0.125}


def _numeric(value: Any) -> float:
    if isinstance(value, bool):
        raise ValueError("location score must be numeric")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("location score must be numeric") from exc
    if not math.isfinite(number):
        raise ValueError("location score must be finite")
    return number


def normalize_walk_score(value: Any) -> float:
    score = _numeric(value)
    if not 0.0 <= score <= 100.0:
        raise ValueError("Walk Score must be between 0 and 100")
    return round(score, 2)


def _complete_scores(scores: Mapping[str, Any]) -> tuple[dict[str, float], list[str]]:
    normalized: dict[str, float] = {}
    missing: list[str] = []
    for name in REQUIRED_EXTERNAL_SCORES:
        value = scores.get(name)
        if value is None:
            missing.append(name)
            continue
        normalized[name] = normalize_walk_score(value)
    return normalized, missing


def build_location_composite(
    scores: Mapping[str, Any],
    *,
    model_name: str = "FAMILY_DETACHED_WALK_SCORE",
    weights_version: str = "FAMILY_DETACHED_WALK_SCORE_V1",
) -> LocationComposite:
    """Build the Walk Score-only urban-access composite without imputation."""

    normalized, missing = _complete_scores(scores)
    if missing:
        return LocationComposite(
            model_name=model_name,
            weights_version=weights_version,
            status="UNKNOWN",
            residential_environment_status="NOT_AVAILABLE",
            missing_scores=missing,
            normalized_scores=normalized,
        )

    urban_access = sum(normalized[name] * weight for name, weight in WALK_SCORE_WEIGHTS.items())
    return LocationComposite(
        model_name=model_name,
        weights_version=weights_version,
        status="COMPLETE",
        external_location_score=round(urban_access, 2),
        urban_access_score=round(urban_access, 2),
        residential_environment_score=None,
        residential_environment_status="NOT_AVAILABLE",
        missing_scores=[],
        normalized_scores=normalized,
    )
