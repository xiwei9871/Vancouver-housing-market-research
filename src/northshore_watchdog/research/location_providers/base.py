from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


ProviderStatus = Literal[
    "OK",
    "NOT_CONFIGURED",
    "NO_DATA",
    "FAILED",
    "INVALID_REQUEST",
]


def _now() -> datetime:
    return datetime.now(timezone.utc)


class _LocationModel(BaseModel):
    model_config = ConfigDict(extra="allow")


class ProviderResult(_LocationModel):
    provider: str
    status: ProviderStatus = "NOT_CONFIGURED"
    scores: dict[str, float] = Field(default_factory=dict)
    cross_checks: dict[str, float] = Field(default_factory=dict)
    raw: dict[str, Any] = Field(default_factory=dict)
    queried_at: datetime = Field(default_factory=_now)
    latitude: float | None = None
    longitude: float | None = None
    source_url: str | None = None
    provider_version: str | None = None
    http_status: int | None = None
    error: str | None = None


class LocationComposite(_LocationModel):
    model_name: str = "FAMILY_DETACHED_WALK_SCORE"
    weights_version: str = "FAMILY_DETACHED_WALK_SCORE_V1"
    status: Literal["COMPLETE", "UNKNOWN"] = "UNKNOWN"
    external_location_score: float | None = None
    urban_access_score: float | None = None
    residential_environment_score: float | None = None
    residential_environment_status: Literal["NOT_AVAILABLE", "UNKNOWN"] = "NOT_AVAILABLE"
    missing_scores: list[str] = Field(default_factory=list)
    normalized_scores: dict[str, float] = Field(default_factory=dict)


class LocationSnapshot(_LocationModel):
    property_id: str
    address: str
    mls: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    queried_at: datetime = Field(default_factory=_now)
    model_name: str = "FAMILY_DETACHED_WALK_SCORE"
    model_version: str = "1.0"
    walk_score: ProviderResult = Field(
        default_factory=lambda: ProviderResult(provider="walk_score")
    )
    composite: LocationComposite = Field(default_factory=LocationComposite)
