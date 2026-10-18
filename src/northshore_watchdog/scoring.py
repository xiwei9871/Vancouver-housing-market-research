from __future__ import annotations

from .enrichers.keyword_extractor import extract_keywords
from .enrichers.region_classifier import classify_region
from .models import Listing, ScoredListing
from .risk_rules import find_exclusion_reasons
from .utils.money import ratio
from .utils.text import contains_any, slug_text


DEFAULT_NEXT_ACTIONS = [
    "查 BC Assessment",
    "查 zoning",
    "查 title / easement / covenant",
    "查是否 strata / leasehold",
    "查是否 creek / slope / ravine",
    "查 oil tank / asbestos / electrical / plumbing",
    "联系 realtor 要 property disclosure statement",
    "如果仍符合条件，再安排看房",
]

GRADE_ORDER = {"C": 1, "Review": 2, "B": 3, "A": 4}


def _city_key(listing: Listing) -> str:
    return "west_vancouver" if slug_text(listing.city) == "west vancouver" else "north_vancouver"


def _target_lot_range(listing: Listing, config: dict) -> tuple[int, int]:
    return tuple(config.get("lot_size", {}).get(_city_key(listing), {}).get("target", [0, 99_999]))  # type: ignore[return-value]


def _frontage_threshold(listing: Listing, config: dict) -> int:
    return int(config.get("frontage", {}).get(_city_key(listing), 0))


def _missing_fields(listing: Listing) -> list[str]:
    required = [
        "property_type",
        "tenure",
        "list_price",
        "bedrooms",
        "bathrooms",
        "house_size_sqft",
        "lot_size_sqft",
        "year_built",
        "assessment_total",
    ]
    return [field for field in required if getattr(listing, field) in (None, "")]


def _confidence(missing: list[str], listing: Listing) -> str:
    critical = {"assessment_total", "lot_size_sqft", "year_built", "tenure"}
    if critical.issubset(set(missing)):
        return "low"
    if len(missing) >= 4 or not listing.assessment_total:
        return "medium"
    return "high"


def _assessment_score(listing: Listing, reasons: list[str], risks: list[str]) -> int:
    assessment_ratio = ratio(listing.list_price, listing.assessment_total)
    if assessment_ratio is None:
        risks.append("assessment_missing")
        return 10
    if assessment_ratio <= 0.65:
        reasons.append("极强 assessment discount: list/assessment <= 0.65")
        return 25
    if assessment_ratio <= 0.75:
        reasons.append("强 assessment discount: list/assessment <= 0.75")
        return 21
    if assessment_ratio <= 0.85:
        return 14
    if assessment_ratio <= 0.95:
        return 8
    risks.append("list_price_above_or_near_assessment")
    return 0


def _region_score(listing: Listing, config: dict, reasons: list[str], risks: list[str]) -> int:
    classification = classify_region(listing, config)
    if classification.category == "preferred_region":
        reasons.append(f"优先区域: {classification.matched_keyword or listing.neighbourhood}")
        return 18
    if classification.category == "acceptable_region":
        return 12
    if classification.category == "downgraded_region":
        risks.append(f"downgraded_region:{classification.matched_keyword}")
        return 5
    risks.append(f"lifestyle_or_slope_region:{classification.matched_keyword}")
    return 0


def _livability_score(listing: Listing, reasons: list[str], risks: list[str]) -> int:
    score = 0
    if (listing.bedrooms or 0) >= 3 and (listing.bathrooms or 0) >= 2:
        score += 5
    city_min = 1800 if slug_text(listing.city) == "west vancouver" else 1500
    if (listing.house_size_sqft or 0) >= city_min:
        score += 5
    if listing.year_built and 1970 <= listing.year_built <= 2000:
        score += 5
    opportunity = contains_any(listing.description or "", ["original condition", "needs TLC", "renovate", "vacant"])
    if opportunity:
        reasons.append(f"可通过中等装修改善: {', '.join(opportunity[:2])}")
        score += 5
    major = contains_any(listing.description or "", ["structural issue", "foundation issue", "water ingress"])
    if major:
        risks.extend(major)
        score = max(0, score - 12)
    return min(score, 20)


def _land_score(listing: Listing, config: dict, reasons: list[str], risks: list[str]) -> int:
    score = 0
    lot_min, lot_max = _target_lot_range(listing, config)
    if listing.lot_size_sqft and lot_min <= listing.lot_size_sqft <= lot_max:
        reasons.append("土地面积处于目标范围")
        score += 5
    elif listing.lot_size_sqft:
        risks.append("lot_size_outside_target_range")
    if listing.frontage_ft is None:
        risks.append("frontage_unknown")
        if not config.get("scoring_options", {}).get("penalize_missing_frontage", True):
            score += 2
    elif listing.frontage_ft >= _frontage_threshold(listing, config):
        score += 4
    description = _risk_scan_text(listing.description or "", config)
    risk_text = contains_any(description, ["flat", "regular lot", "level lot", "no slope"])
    slope_risk = contains_any(description, ["creek", "ravine", "steep", "slope"])
    if risk_text and not slope_risk:
        score += 4
    if not slope_risk and not (listing.lot_size_sqft and listing.lot_size_sqft > 12_000):
        score += 2
    else:
        risks.extend(slope_risk)
    return min(score, 15)


def _risk_scan_text(text: str, config: dict) -> str:
    for phrase in config.get("risk_keyword_ignore_phrases", []):
        text = text.replace(phrase, " ")
    return text


def _age_score(listing: Listing, config: dict) -> int:
    if not listing.year_built:
        return 0
    years = config.get("year_built", {})
    if listing.year_built < years.get("backup_min", 1965):
        return 0
    if years.get("core_min", 1970) <= listing.year_built <= years.get("core_max", 2000):
        return 10
    if years.get("backup_min", 1965) <= listing.year_built <= years.get("backup_max", 2005):
        return 5
    return 0


def _optionality_score(listing: Listing, reasons: list[str], risks: list[str]) -> int:
    text = slug_text(" ".join([listing.zoning or "", listing.description or "", listing.tenure or "", listing.property_type or ""]))
    score = 0
    if "rs" in text or "residential" in text:
        score += 1
    if "basement" in text or "separate entrance" in text:
        score += 1
    if "suite potential" in text or "suite" in text:
        score += 1
    if listing.lot_size_sqft and listing.frontage_ft:
        score += 1
    if not any(word in text for word in [" cd ", "strata", "leasehold"]):
        score += 1
    if score >= 3:
        reasons.append("保留未来出租/rebuild 选择权")
    return score


def _motivation_score(listing: Listing, reasons: list[str]) -> int:
    score = 0
    if listing.price_reduced or (listing.previous_price and listing.list_price and listing.list_price < listing.previous_price):
        score += 2
        reasons.append("有降价信号")
    if listing.days_on_market and listing.days_on_market > 30:
        score += 1
    text = slug_text(listing.description)
    if any(word in text for word in ["vacant", "estate", "probate", "motivated seller"]):
        score += 1
    if "relisted" in text or "stale" in text:
        score += 1
    return min(score, 5)


def _risk_penalty(risks: list[str], strong_risk_count: int) -> int:
    if strong_risk_count >= 3:
        return 30
    if strong_risk_count == 2:
        return 20
    if strong_risk_count == 1:
        return 10
    return 0


def _alert_level(listing: Listing, score: int, grade: str, risks: list[str], config: dict) -> str:
    if grade == "A":
        city_key = _city_key(listing)
        official_max = config.get("budgets", {}).get(city_key, {}).get("official_max", 0)
        assessment_ratio = ratio(listing.list_price, listing.assessment_total)
        preferred = classify_region(listing, config).category == "preferred_region"
        no_major = not any("foundation" in risk or "structural" in risk or "water ingress" in risk for risk in risks)
        if (
            listing.list_price
            and listing.list_price <= official_max
            and assessment_ratio is not None
            and assessment_ratio <= 0.70
            and listing.year_built
            and 1970 <= listing.year_built <= 2000
            and preferred
            and no_major
        ):
            return "urgent"
        return "normal"
    if score >= config.get("alert_thresholds", {}).get("normal_score", 70):
        return "normal"
    return "none"


def _is_near_miss_price(listing: Listing, config: dict) -> bool:
    if not listing.list_price:
        return False
    budget = config.get("budgets", {}).get(_city_key(listing), {})
    watch_max = budget.get("watch_max")
    near_miss_review_max = budget.get("near_miss_review_max")
    return bool(watch_max and near_miss_review_max and watch_max < listing.list_price <= near_miss_review_max)


def _cap_grade(grade: str, cap: str) -> str:
    if grade not in GRADE_ORDER or cap not in GRADE_ORDER:
        return grade
    if GRADE_ORDER[grade] > GRADE_ORDER[cap]:
        return cap
    return grade


def _apply_grade_caps(listing: Listing, config: dict, grade: str, risks: list[str]) -> str:
    caps = config.get("grade_caps", {})
    capped = grade
    if listing.assessment_total is None:
        capped = _cap_grade(capped, caps.get("missing_assessment", "B"))
    if _is_near_miss_price(listing, config):
        capped = _cap_grade(capped, caps.get("near_miss_price", "B"))
    if listing.year_built and listing.year_built < 1965:
        capped = _cap_grade(capped, caps.get("older_home_pre_1965", "Review"))
    elif listing.year_built and listing.year_built < config.get("year_built", {}).get("core_min", 1970):
        capped = _cap_grade(capped, caps.get("older_home_1950s_1960s", "B"))
    if any(risk.startswith("lifestyle_or_slope_region") for risk in risks):
        capped = _cap_grade(capped, caps.get("lifestyle_or_slope_region", "Review"))
    if slug_text(listing.city) == "west vancouver" and any(risk.startswith("downgraded_region") for risk in risks):
        capped = _cap_grade(capped, caps.get("west_vancouver_downgraded_region", "Review"))
    if capped != grade:
        risks.append(f"grade_capped:{grade}->{capped}")
    return capped


def _alert_for_grade(listing: Listing, score: int, grade: str, risks: list[str], config: dict) -> str:
    if grade in {"A", "B"}:
        return _alert_level(listing, score, grade, risks, config)
    if grade == "Review" and score >= config.get("alert_thresholds", {}).get("normal_score", 70):
        return "normal"
    return "none"


def score_listing(listing: Listing, config: dict) -> ScoredListing:
    reasons: list[str] = []
    risks: list[str] = []
    exclusion_reasons = find_exclusion_reasons(listing, config)
    keyword_risks, opportunities = extract_keywords(listing, config)
    risks.extend(keyword_risks)
    older_home_review = bool(listing.year_built and listing.year_built < config.get("year_built", {}).get("core_min", 1970))
    if older_home_review:
        risks.append("review_older_home")
    if _is_near_miss_price(listing, config):
        risks.append("near_miss_price_review")
    for keyword in opportunities[:3]:
        reasons.append(f"机会关键词: {keyword}")

    breakdown = {
        "assessment_discount": _assessment_score(listing, reasons, risks),
        "location_liquidity": _region_score(listing, config, reasons, risks),
        "livability": _livability_score(listing, reasons, risks),
        "land_regularness": _land_score(listing, config, reasons, risks),
        "age_fit": _age_score(listing, config),
        "optionality": _optionality_score(listing, reasons, risks),
        "seller_motivation": _motivation_score(listing, reasons),
    }
    score = sum(breakdown.values()) - _risk_penalty(risks, len(keyword_risks))
    score = max(0, min(100, int(score)))
    missing = _missing_fields(listing)
    confidence = _confidence(missing, listing)

    excluded = bool(exclusion_reasons)
    has_assessment = listing.assessment_total is not None
    major_region_risk = any(risk.startswith("lifestyle_or_slope_region") for risk in risks)
    if excluded:
        grade = "Excluded"
        score = 0
        alert = "none"
    elif score >= 78 and has_assessment and not major_region_risk and not older_home_review:
        grade = "A"
    elif score >= 65 or (not has_assessment and score >= config.get("grade_thresholds", {}).get("b_without_assessment_min", 58)):
        grade = "B"
    elif score >= config.get("grade_thresholds", {}).get("review_min", 45):
        grade = "Review"
    else:
        grade = "C"

    if not excluded:
        grade = _apply_grade_caps(listing, config, grade, risks)
        alert = _alert_for_grade(listing, score, grade, risks, config)

    return ScoredListing(
        listing=listing,
        score_total=score,
        score_breakdown=breakdown,
        grade=grade,
        confidence=confidence,
        reasons=dedupe_preserve(reasons),
        risks=dedupe_preserve(risks),
        missing_fields=missing,
        excluded=excluded,
        exclusion_reasons=exclusion_reasons,
        next_actions=DEFAULT_NEXT_ACTIONS.copy(),
        alert_level=alert,
    )


def dedupe_preserve(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result
