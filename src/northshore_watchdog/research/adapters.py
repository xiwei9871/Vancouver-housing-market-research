from __future__ import annotations

from typing import Any

from ..models import Listing


def adapt_legacy_listing(listing: Listing) -> dict[str, Any]:
    data = listing.model_dump(mode="python")
    return {
        "property_id": None,
        "address": listing.address,
        "municipality": listing.city,
        "neighbourhood": listing.neighbourhood,
        "property_type": listing.property_type,
        "listing_id": listing.source_id,
        "mls_number": listing.mls_number,
        "source_url": listing.url,
        "listing_status": listing.listing_status,
        "asking": listing.list_price,
        "original_asking": listing.previous_price or listing.list_price,
        "listing_date": listing.listed_date,
        "dom": listing.days_on_market,
        "bedrooms": listing.bedrooms,
        "bathrooms": listing.bathrooms,
        "finished_sqft": listing.house_size_sqft,
        "lot_area_sqft": listing.lot_size_sqft,
        "frontage_ft": listing.frontage_ft,
        "year_built": listing.year_built,
        "zoning": listing.zoning,
        "assessment_total": listing.assessment_total,
        "assessment_land": listing.assessment_land,
        "assessment_improvement": listing.assessment_building,
        "ownership_type": listing.tenure,
        "description": listing.description,
        "raw_legacy_data": data,
    }
