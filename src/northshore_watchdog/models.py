from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator


class Listing(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    source: str
    source_id: str | None = None
    mls_number: str | None = None
    url: str | None = None
    address: str
    city: str
    neighbourhood: str | None = None
    property_type: str | None = None
    tenure: str | None = None
    listing_status: str | None = None
    list_price: int | None = None
    previous_price: int | None = None
    price_reduced: bool = False
    bedrooms: float | None = None
    bathrooms: float | None = None
    house_size_sqft: int | None = None
    lot_size_sqft: int | None = None
    frontage_ft: float | None = None
    depth_ft: float | None = None
    year_built: int | None = None
    zoning: str | None = None
    description: str | None = None
    days_on_market: int | None = None
    listed_date: datetime | None = None
    last_seen_date: datetime | None = None
    assessment_total: int | None = None
    assessment_land: int | None = None
    assessment_building: int | None = None
    assessment_year: int | None = None
    latitude: float | None = None
    longitude: float | None = None
    raw_data: dict[str, Any] = Field(default_factory=dict)

    @field_validator("city")
    @classmethod
    def normalize_city(cls, value: str) -> str:
        value = " ".join(value.strip().split())
        aliases = {
            "west vancouver": "West Vancouver",
            "north vancouver": "North Vancouver",
        }
        return aliases.get(value.lower(), value)


class ScoredListing(BaseModel):
    listing: Listing
    score_total: int
    score_breakdown: dict[str, int]
    grade: Literal["A", "B", "Review", "C", "Excluded"]
    confidence: Literal["high", "medium", "low"]
    reasons: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    excluded: bool = False
    exclusion_reasons: list[str] = Field(default_factory=list)
    next_actions: list[str] = Field(default_factory=list)
    alert_level: Literal["none", "normal", "urgent"] = "none"


class NotificationEvent(BaseModel):
    event_type: Literal[
        "weekly_report",
        "daily_digest",
        "urgent_listing",
        "price_drop",
        "score_upgrade",
    ]
    listing_ids: list[str] = Field(default_factory=list)
    title: str
    summary: str
    markdown: str | None = None
    card_payload: dict[str, Any] | None = None
    created_at: datetime = Field(default_factory=datetime.now)
    sent_at: datetime | None = None
    status: str = "pending"
