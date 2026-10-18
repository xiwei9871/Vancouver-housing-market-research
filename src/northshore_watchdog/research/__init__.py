"""Greater Vancouver detached-house research core."""

RESEARCH_SCHEMA_VERSION = "research-v1"

from .models import (
    ApprovedUse,
    CandidateStatus,
    CaseQuality,
    LotShape,
    OwnershipType,
    PropertyRecord,
    RedevelopmentScenario,
    RoadClass,
    ScenarioType,
    ScoreSnapshot,
    SourceType,
)

__all__ = [
    "RESEARCH_SCHEMA_VERSION",
    "ApprovedUse",
    "CandidateStatus",
    "CaseQuality",
    "LotShape",
    "OwnershipType",
    "PropertyRecord",
    "RedevelopmentScenario",
    "RoadClass",
    "ScenarioType",
    "ScoreSnapshot",
    "SourceType",
]
