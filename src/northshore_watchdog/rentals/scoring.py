from __future__ import annotations

from urllib.parse import urlparse

from .models import RentalListing, ScoredRentalListing


EXCLUDE_TERMS = {
    "basement": "basement",
    "shared": "shared_accommodation",
    "room rental": "shared_accommodation",
    "short term": "short_term",
    "short-term": "short_term",
    "airbnb": "short_term",
    "sublet": "sublet",
}

GOOD_AMENITIES = {"pool", "gym", "fitness", "concierge", "amenity room", "elevator"}
PARENT_FRIENDLY_TERMS = {"t&t", "price smart", "pricesmart", "skytrain", "pharmacy", "clinic", "restaurants"}


def score_rental_listing(listing: RentalListing, config: dict) -> ScoredRentalListing:
    exclusion_reasons = _exclusion_reasons(listing, config)
    missing_fields = [
        field
        for field in ("monthly_rent", "bedrooms", "bathrooms", "property_type")
        if getattr(listing, field) in (None, "")
    ]
    if exclusion_reasons:
        return ScoredRentalListing(
            listing=listing,
            score_total=0,
            score_breakdown={},
            grade="Excluded",
            confidence="low",
            risks=[],
            missing_fields=missing_fields,
            excluded=True,
            exclusion_reasons=exclusion_reasons,
            next_actions=[],
            alert_level="none",
        )

    breakdown: dict[str, int] = {}
    reasons: list[str] = []
    risks: list[str] = []

    breakdown["budget"] = _budget_score(listing, config, reasons, risks)
    breakdown["layout"] = _layout_score(listing, reasons, risks)
    breakdown["chinese_community"] = _community_score(listing, config, reasons)
    breakdown["transit"] = _transit_score(listing, config, reasons, risks)
    breakdown["building"] = _building_score(listing, reasons, risks)
    breakdown["amenities"] = _amenity_score(listing, reasons)
    breakdown["risk"] = _risk_adjustment(listing, risks)

    total = max(0, min(100, sum(breakdown.values())))
    grade = _grade(total, risks)
    confidence = "high" if len(missing_fields) <= 1 and listing.url else "medium"
    if len(missing_fields) >= 3:
        confidence = "low"
    alert_level = "urgent" if grade == "A" and listing.monthly_rent and listing.monthly_rent <= config.get("budget", {}).get("target_max", 3000) else "none"
    if grade == "B":
        alert_level = "normal"

    return ScoredRentalListing(
        listing=listing,
        score_total=total,
        score_breakdown=breakdown,
        grade=grade,
        confidence=confidence,
        reasons=reasons,
        risks=risks,
        missing_fields=missing_fields,
        excluded=False,
        exclusion_reasons=[],
        next_actions=_next_actions(listing),
        alert_level=alert_level,
    )


def _exclusion_reasons(listing: RentalListing, config: dict) -> list[str]:
    reasons: list[str] = []
    allowed_domains = config.get("allowed_domains", [])
    if allowed_domains and listing.url:
        host = urlparse(listing.url).netloc.lower()
        if not any(host == domain.lower() or host.endswith(f".{domain.lower()}") for domain in allowed_domains):
            reasons.append(f"source_domain_not_allowed:{host}")
    elif allowed_domains:
        reasons.append("source_url_missing")

    text = " ".join(
        str(part or "")
        for part in (listing.property_type, listing.description, " ".join(listing.amenities))
    ).lower()
    for term, reason in EXCLUDE_TERMS.items():
        if term in text and reason not in reasons:
            reasons.append(reason)
    if listing.bedrooms is not None and listing.bedrooms < 2:
        reasons.append("less_than_2_bedrooms")
    return reasons


def _budget_score(listing: RentalListing, config: dict, reasons: list[str], risks: list[str]) -> int:
    rent = listing.monthly_rent
    budget = config.get("budget", {})
    target_min = int(budget.get("target_min", 2500))
    target_max = int(budget.get("target_max", 3000))
    stretch_max = int(budget.get("stretch_max", 3300))
    if rent is None:
        risks.append("rent_missing")
        return 8
    if target_min <= rent <= target_max:
        reasons.append("预算内")
        return 25
    if rent < target_min:
        reasons.append("低于预算上限")
        return 22
    if rent <= stretch_max:
        risks.append("stretch_budget")
        return 15
    risks.append("over_budget")
    return 5


def _layout_score(listing: RentalListing, reasons: list[str], risks: list[str]) -> int:
    if listing.bedrooms and listing.bedrooms >= 2 and listing.bathrooms and listing.bathrooms >= 2:
        reasons.append("2房2卫")
        return 20
    if listing.bedrooms and listing.bedrooms >= 2 and listing.bathrooms and listing.bathrooms >= 1:
        reasons.append("2房1卫可接受")
        return 14
    risks.append("layout_weak_or_missing")
    return 5


def _community_score(listing: RentalListing, config: dict, reasons: list[str]) -> int:
    area = f"{listing.city} {listing.neighbourhood or ''} {listing.address}".lower()
    regions = config.get("regions", {})
    if _contains_any(area, regions.get("preferred", [])):
        reasons.append("中文生活便利强")
        return 18
    if _contains_any(area, regions.get("acceptable", [])):
        reasons.append("生活便利度可接受")
        return 12
    if _contains_any(area, regions.get("opportunity", [])):
        reasons.append("机会区域")
        return 8
    if listing.city == "Richmond":
        reasons.append("Richmond中文环境友好")
        return 14
    return 6


def _transit_score(listing: RentalListing, config: dict, reasons: list[str], risks: list[str]) -> int:
    minutes = listing.transit_minutes
    text = (listing.description or "").lower()
    strong = int(config.get("transit", {}).get("strong_minutes", 10))
    acceptable = int(config.get("transit", {}).get("acceptable_minutes", 15))
    if minutes is not None and minutes <= strong:
        reasons.append("近SkyTrain")
        return 12
    if minutes is not None and minutes <= acceptable:
        reasons.append("公交/天车距离可接受")
        return 8
    if "skytrain" in text or "transit" in text:
        reasons.append("描述提到公共交通")
        return 7
    risks.append("transit_unknown_or_far")
    return 3


def _building_score(listing: RentalListing, reasons: list[str], risks: list[str]) -> int:
    text = f"{listing.property_type or ''} {listing.description or ''} {' '.join(listing.amenities)}".lower()
    score = 8
    if any(term in text for term in ("condo", "apartment")):
        score += 4
        reasons.append("公寓/condo类型匹配")
    if "elevator" in text:
        score += 3
        reasons.append("有电梯")
    else:
        risks.append("elevator_unknown")
    if "in-suite laundry" in text or "insuite laundry" in text:
        score += 3
        reasons.append("室内洗衣")
    return min(score, 15)


def _amenity_score(listing: RentalListing, reasons: list[str]) -> int:
    amenities = set(listing.amenities)
    text = (listing.description or "").lower()
    matched = {item for item in GOOD_AMENITIES if item in amenities or item in text}
    score = min(6, len(matched) * 2)
    if "pool" in matched or "gym" in matched or "fitness" in matched:
        reasons.append("pool/gym等楼宇配套")
    return score


def _risk_adjustment(listing: RentalListing, risks: list[str]) -> int:
    text = (listing.description or "").lower()
    adjustment = 0
    for term in ("scam", "cash only", "wire transfer", "no lease", "no contract"):
        if term in text:
            risks.append(term.replace(" ", "_"))
            adjustment -= 20
    return adjustment


def _grade(total: int, risks: list[str]) -> str:
    if total >= 78 and "over_budget" not in risks and "stretch_budget" not in risks:
        return "A"
    if total >= 62:
        return "B"
    return "C"


def _next_actions(listing: RentalListing) -> list[str]:
    actions = [
        "确认正式租约、押金金额和入住日期",
        "确认是否包含水电网、停车、locker和move-in fee",
        "视频或现场看房，重点看噪音、采光、电梯和洗衣",
        "确认父母步行到超市、药房、餐饮和天车是否可接受",
    ]
    if listing.monthly_rent and listing.monthly_rent > 3000:
        actions.append("尝试议价或等待降价")
    return actions


def _contains_any(text: str, needles: list[str]) -> bool:
    return any(needle.lower() in text for needle in needles)
