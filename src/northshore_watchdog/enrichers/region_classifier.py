from __future__ import annotations

from dataclasses import dataclass

from northshore_watchdog.models import Listing
from northshore_watchdog.utils.text import slug_text


@dataclass(frozen=True)
class RegionClassification:
    category: str
    matched_keyword: str | None = None


def classify_region(listing: Listing, config: dict) -> RegionClassification:
    city_key = "west_vancouver" if slug_text(listing.city) == "west vancouver" else "north_vancouver"
    region_config = config.get("regions", {}).get(city_key, {})
    raw_text = " ".join([listing.address, listing.neighbourhood or "", listing.description or ""])
    for phrase in config.get("risk_keyword_ignore_phrases", []):
        raw_text = raw_text.replace(phrase, " ")
    text = slug_text(raw_text)

    for category in ["lifestyle_or_slope_region", "downgraded_region", "preferred_region", "acceptable_region"]:
        for keyword in region_config.get(category, []):
            if slug_text(keyword) in text:
                return RegionClassification(category=category, matched_keyword=keyword)
    return RegionClassification(category="acceptable_region", matched_keyword=None)
