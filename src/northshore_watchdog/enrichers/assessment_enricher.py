from __future__ import annotations

import json
from difflib import SequenceMatcher
from pathlib import Path

import pandas as pd

from northshore_watchdog.config import resolve_path
from northshore_watchdog.models import Listing
from northshore_watchdog.normalize import normalized_address
from northshore_watchdog.utils.money import parse_money


def load_assessment_rows(path: str | Path) -> list[dict]:
    resolved = resolve_path(path)
    if not resolved.exists():
        return []
    if resolved.suffix.lower() == ".json":
        payload = json.loads(resolved.read_text(encoding="utf-8"))
        return payload.get("assessments", payload) if isinstance(payload, dict) else payload
    frame = pd.read_csv(resolved)
    return frame.to_dict(orient="records")


def enrich_assessment(listing: Listing, rows: list[dict], min_score: float = 0.86) -> Listing:
    listing_key = f"{normalized_address(listing.address)} {listing.city.lower()}"
    best_row = None
    best_score = 0.0
    for row in rows:
        row_key = f"{normalized_address(str(row.get('address', '')))} {str(row.get('city', '')).lower()}"
        score = SequenceMatcher(None, listing_key, row_key).ratio()
        if score > best_score:
            best_score = score
            best_row = row
    if not best_row or best_score < min_score:
        return listing
    return listing.model_copy(update={
        "assessment_total": parse_money(best_row.get("assessment_total")),
        "assessment_land": parse_money(best_row.get("assessment_land")),
        "assessment_building": parse_money(best_row.get("assessment_building")),
        "assessment_year": int(best_row["assessment_year"]) if best_row.get("assessment_year") else None,
    })
