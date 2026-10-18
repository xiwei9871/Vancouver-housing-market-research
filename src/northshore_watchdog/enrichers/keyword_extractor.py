from __future__ import annotations

from northshore_watchdog.models import Listing
from northshore_watchdog.utils.text import contains_any


def extract_keywords(listing: Listing, config: dict) -> tuple[list[str], list[str]]:
    text = " ".join(
        [
            listing.description or "",
            str(listing.raw_data.get("remarks", "")),
            str(listing.raw_data.get("features", "")),
        ]
    )
    for phrase in config.get("risk_keyword_ignore_phrases", []):
        text = text.replace(phrase, " ")
    return (
        contains_any(text, config.get("risk_keywords", [])),
        contains_any(text, config.get("opportunity_keywords", [])),
    )
