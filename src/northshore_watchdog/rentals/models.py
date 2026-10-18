from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class RentalListing(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    source: str
    source_id: str | None = None
    url: str | None = None
    address: str
    city: str
    neighbourhood: str | None = None
    property_type: str | None = None
    monthly_rent: int | None = None
    previous_rent: int | None = None
    rent_reduced: bool = False
    bedrooms: float | None = None
    bathrooms: float | None = None
    size_sqft: int | None = None
    furnished: bool | None = None
    parking: bool | None = None
    pets_allowed: bool | None = None
    available_date: datetime | None = None
    transit_minutes: int | None = None
    amenities: list[str] = Field(default_factory=list)
    description: str | None = None
    last_seen_date: datetime | None = None
    raw_data: dict[str, Any] = Field(default_factory=dict)

    @field_validator("city")
    @classmethod
    def normalize_city(cls, value: str) -> str:
        aliases = {
            "richmond": "Richmond",
            "vancouver": "Vancouver",
            "burnaby": "Burnaby",
        }
        return aliases.get(" ".join(value.strip().split()).lower(), value.strip())

    @field_validator("amenities")
    @classmethod
    def normalize_amenities(cls, value: list[str]) -> list[str]:
        return [item.strip().lower() for item in value if item and item.strip()]


class ScoredRentalListing(BaseModel):
    listing: RentalListing
    score_total: int
    score_breakdown: dict[str, int]
    grade: Literal["A", "B", "C", "Excluded"]
    confidence: Literal["high", "medium", "low"]
    reasons: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    excluded: bool = False
    exclusion_reasons: list[str] = Field(default_factory=list)
    next_actions: list[str] = Field(default_factory=list)
    alert_level: Literal["none", "normal", "urgent"] = "none"
