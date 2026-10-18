from __future__ import annotations

import re

from northshore_watchdog.models import Listing
from northshore_watchdog.utils.money import parse_money


def enrich_details(listing: Listing) -> Listing:
    text = " ".join([listing.description or "", str(listing.raw_data.get("text", "")), str(listing.raw_data.get("raw_text", ""))])
    updates = {}
    patterns = {
        "bedrooms": r"(\d+(?:\.\d+)?)\s*(?:bed|bedroom)",
        "bathrooms": r"(\d+(?:\.\d+)?)\s*(?:bath|bathroom)",
        "house_size_sqft": r"(?:house|home|interior|living|floor area)\s*(?:size)?\s*[:\-]?\s*([0-9,]+)\s*(?:sq\.?\s*ft|sqft)",
        "lot_size_sqft": r"lot\s*(?:size)?\s*[:\-]?\s*([0-9,]+)\s*(?:sq\.?\s*ft|sqft)",
        "frontage_ft": r"frontage\s*[:\-]?\s*([0-9.]+)",
        "depth_ft": r"depth\s*[:\-]?\s*([0-9.]+)",
        "year_built": r"(?:built|year built)\s*[:\-]?\s*(19\d{2}|20\d{2})",
    }
    for field, pattern in patterns.items():
        if getattr(listing, field) is not None:
            continue
        match = re.search(pattern, text, re.I)
        if not match:
            continue
        raw = match.group(1)
        updates[field] = float(raw.replace(",", "")) if "." in raw else int(raw.replace(",", ""))

    for field in ["property_type", "tenure", "zoning"]:
        if getattr(listing, field) is None and listing.raw_data.get(field):
            updates[field] = listing.raw_data[field]

    if listing.previous_price is None and listing.raw_data.get("previous_price"):
        updates["previous_price"] = parse_money(listing.raw_data["previous_price"])
    if updates.get("previous_price") and listing.list_price and listing.list_price < updates["previous_price"]:
        updates["price_reduced"] = True
    return listing.model_copy(update=updates)
