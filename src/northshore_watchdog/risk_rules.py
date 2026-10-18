from __future__ import annotations

from urllib.parse import urlparse

from .models import Listing
from .utils.text import slug_text


EXCLUDED_PROPERTY_TYPES = [
    "strata",
    "townhouse",
    "duplex",
    "manufactured",
    "mobile",
    "bare land",
    "co-op",
    "coop",
    "apartment",
    "condo",
]

EXCLUDED_TENURES = ["strata", "leasehold", "leased", "co-op", "coop"]
EXCLUDED_LISTING_STATUSES = ["sold", "withdrawn", "expired", "terminated", "cancelled", "closed"]


def _url_domain(url: str | None) -> str:
    if not url:
        return ""
    return (urlparse(url).hostname or "").removeprefix("www.").lower()


def _domain_allowed(domain: str, allowed_domains: list[str]) -> bool:
    normalized_allowed = [item.removeprefix("www.").lower() for item in allowed_domains]
    return any(domain == allowed or domain.endswith(f".{allowed}") for allowed in normalized_allowed)


def find_exclusion_reasons(listing: Listing, config: dict | None = None) -> list[str]:
    reasons: list[str] = []
    city = slug_text(listing.city)
    if city not in {"west vancouver", "north vancouver"}:
        reasons.append(f"city_not_target:{listing.city}")

    property_type = slug_text(listing.property_type)
    tenure = slug_text(listing.tenure)
    listing_status = slug_text(listing.listing_status or listing.raw_data.get("listing_status") or listing.raw_data.get("status"))
    description = slug_text(listing.description)
    combined_tenure = " ".join([tenure, description])

    if listing_status in EXCLUDED_LISTING_STATUSES:
        reasons.append(f"listing_status_excluded:{listing_status}")

    if any(word in property_type for word in EXCLUDED_PROPERTY_TYPES):
        reasons.append(f"property_type_excluded:{listing.property_type or 'unknown'}")
    if any(word in combined_tenure for word in EXCLUDED_TENURES):
        reasons.append(f"tenure_excluded:{listing.tenure or 'unknown'}")

    manual_review = "manual review" in description or "人工复核" in description
    exclude_pre_1965 = True if config is None else config.get("exclusions", {}).get("pre_1965_hard_exclude", True)
    if listing.year_built and listing.year_built < 1965 and exclude_pre_1965 and not manual_review:
        reasons.append(f"year_built_before_1965:{listing.year_built}")
    exclude_post_2010 = True if config is None else config.get("exclusions", {}).get("post_2010_hard_exclude", True)
    if listing.year_built and listing.year_built > 2010 and exclude_post_2010:
        reasons.append(f"year_built_after_2010:{listing.year_built}")

    if config and listing.list_price:
        city_key = "west_vancouver" if city == "west vancouver" else "north_vancouver"
        budget = config.get("budgets", {}).get(city_key, {})
        watch_max = budget.get("watch_max")
        near_miss_review_max = budget.get("near_miss_review_max")
        if watch_max and listing.list_price > watch_max:
            if near_miss_review_max and listing.list_price <= near_miss_review_max:
                pass
            elif near_miss_review_max:
                reasons.append(f"price_above_near_miss_review_max:{listing.list_price}")
            else:
                reasons.append(f"price_above_watch_max:{listing.list_price}")

    if config:
        allowed_domains = config.get("source_domains", {}).get("allowed_listing_domains", [])
        domain = _url_domain(listing.url)
        if allowed_domains and domain and not _domain_allowed(domain, allowed_domains):
            reasons.append(f"source_domain_not_allowed:{domain}")

    return reasons
