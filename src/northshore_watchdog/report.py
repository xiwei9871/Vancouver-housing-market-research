from __future__ import annotations

from collections import Counter
from typing import Any

from .models import ScoredListing
from .utils.dates import now_iso
from .utils.money import format_money, ratio


def summarize(scored: list[ScoredListing], delta: Any | None = None) -> dict:
    delta_data = _as_dict(delta)
    grades = Counter(item.grade for item in scored)
    cities = Counter(item.listing.city for item in scored if not item.excluded)
    return {
        "generated_at": now_iso(),
        "total": len(scored),
        "a_count": grades.get("A", 0),
        "b_count": grades.get("B", 0),
        "review_count": grades.get("Review", 0),
        "c_count": grades.get("C", 0),
        "excluded_count": grades.get("Excluded", 0),
        "west_vancouver_count": cities.get("West Vancouver", 0),
        "north_vancouver_count": cities.get("North Vancouver", 0),
        "new_count": _delta_count(delta_data, "new_count", "new"),
        "price_reduced_count": (
            _delta_count(delta_data, "price_reduced_count", "price_reduced")
            if delta_data is not None
            else sum(1 for item in scored if item.listing.price_reduced)
        ),
        "price_increased_count": _delta_count(delta_data, "price_increased_count", "price_increased"),
        "not_seen_count": _delta_count(delta_data, "not_seen_count", "not_seen"),
        "baseline": bool(delta_data.get("baseline")) if delta_data is not None else None,
        "urgent_count": sum(1 for item in scored if item.alert_level == "urgent"),
    }


def build_markdown_report(
    scored: list[ScoredListing],
    config: dict,
    data_status: Any | None = None,
    delta: Any | None = None,
) -> str:
    ordered = sorted(scored, key=lambda item: (item.excluded, -item.score_total, item.listing.address))
    summary = summarize(ordered, delta=delta)
    lines = [
        "# North Shore Property Watchdog - Weekly Report",
        "",
        "## Summary",
        f"- Generated at: {summary['generated_at']}",
        f"- Total listings: {summary['total']}",
        f"- A/B/Review/C/Excluded: {summary['a_count']} / {summary['b_count']} / {summary['review_count']} / {summary['c_count']} / {summary['excluded_count']}",
        f"- West Vancouver candidates: {summary['west_vancouver_count']}",
        f"- North Vancouver candidates: {summary['north_vancouver_count']}",
        f"- New listings: {summary['new_count']}",
        f"- Price reductions: {summary['price_reduced_count']}",
        f"- Price increases: {summary['price_increased_count']}",
        f"- Not seen in current run: {summary['not_seen_count']}",
        f"- Urgent alerts: {summary['urgent_count']}",
        "",
    ]
    lines.extend(_data_status_lines(data_status))
    lines.extend(_weekly_change_lines(delta))
    sections = [
        ("Top A Candidates", [item for item in ordered if item.grade == "A"]),
        ("B Candidates", [item for item in ordered if item.grade == "B"]),
        ("Review Candidates", [item for item in ordered if item.grade == "Review"]),
        ("Price Reduction Watchlist", [item for item in ordered if item.listing.price_reduced and not item.excluded]),
        ("Deep Discount Watchlist", [item for item in ordered if _assessment_ratio(item) is not None and _assessment_ratio(item) <= 0.70 and not item.excluded]),
        ("Current Snapshot Listings", [item for item in ordered if not item.excluded]),
        ("Risk / Excluded Listings", [item for item in ordered if item.excluded or item.risks]),
    ]
    for title, items in sections:
        lines.extend([f"## {title}", ""])
        if not items:
            lines.extend(["No listings.", ""])
            continue
        for item in items[:20]:
            lines.extend(_listing_markdown(item, config))
    lines.extend(_weekly_new_listing_lines(delta))
    lines.extend([
        "## Manual Review Checklist",
        "",
        "- 查 BC Assessment",
        "- 查 zoning",
        "- 查 title / easement / covenant",
        "- 查是否 strata / leasehold",
        "- 查是否 creek / slope / ravine",
        "- 查 oil tank / asbestos / electrical / plumbing",
        "- 联系 realtor 要 property disclosure statement",
        "- 如果仍符合条件，再安排看房",
        "",
    ])
    return "\n".join(lines)


def _listing_markdown(item: ScoredListing, config: dict) -> list[str]:
    listing = item.listing
    assessment_ratio = _assessment_ratio(item)
    land_ratio = ratio(listing.assessment_land, listing.assessment_total)
    lines = [
        f"### {listing.address}, {listing.city}",
        f"- Link: {listing.url or 'unknown'}",
        f"- MLS® Num: {listing.mls_number or 'unknown'}",
        f"- Price: {format_money(listing.list_price)} ({_price_level(item, config)})",
        f"- Score / grade / confidence: {item.score_total} / {item.grade} / {item.confidence}",
        f"- Lot / frontage: {listing.lot_size_sqft or 'unknown'} sqft / {listing.frontage_ft or 'unknown'} ft",
        f"- Year / house / bed-bath: {listing.year_built or 'unknown'} / {listing.house_size_sqft or 'unknown'} sqft / {listing.bedrooms or 'unknown'} bed {listing.bathrooms or 'unknown'} bath",
        f"- Assessment total / land / building: {format_money(listing.assessment_total)} / {format_money(listing.assessment_land)} / {format_money(listing.assessment_building)}",
        f"- List / assessment: {_fmt_ratio(assessment_ratio)}; land value ratio: {_fmt_ratio(land_ratio)}",
        f"- Reasons: {', '.join(item.reasons[:3]) or 'none recorded'}",
        f"- Risks: {', '.join((item.risks + item.exclusion_reasons)[:4]) or 'none recorded'}",
        f"- Missing fields: {', '.join(item.missing_fields) or 'none'}",
        f"- Next actions: {'; '.join(item.next_actions[:4])}",
    ]
    snapshot_observed_at = listing.raw_data.get("snapshot_observed_at")
    if snapshot_observed_at:
        lines.append(f"- Snapshot observed: {snapshot_observed_at}")
    lines.append("")
    return lines


def _as_dict(value: Any | None) -> dict | None:
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    to_dict = getattr(value, "to_dict", None)
    return to_dict() if callable(to_dict) else None


def _delta_count(delta: dict | None, count_key: str, list_key: str) -> int:
    if delta is None:
        return 0
    if count_key in delta:
        return int(delta[count_key])
    return len(delta.get(list_key, []))


def _data_status_lines(data_status: Any | None) -> list[str]:
    data = _as_dict(data_status)
    if data is None:
        return []
    newest = data.get("newest") or {}
    source_files = data.get("source_files")
    if source_files is None:
        source_files = [
            item
            for items in (data.get("selected_files") or {}).values()
            for item in items
        ]
    paths = [item.get("path", "unknown") if isinstance(item, dict) else str(item) for item in source_files]
    lines = [
        "## Data Status",
        "",
        f"- Data status: {data.get('status', data.get('data_status', 'unknown'))}",
        f"- Newest source: {newest.get('path', 'none')}",
        f"- Newest observation: {newest.get('observed_at', 'none')}",
        f"- Newest age: {newest.get('age_days', 'unknown')} days",
        f"- Source files: {', '.join(paths) or 'none'}",
    ]
    warnings = data.get("warnings") or []
    if warnings:
        lines.append(f"- Warnings: {'; '.join(str(item) for item in warnings)}")
    lines.append("")
    return lines


def _weekly_change_lines(delta: Any | None) -> list[str]:
    data = _as_dict(delta)
    if data is None:
        return []
    lines = [
        "## Weekly Changes",
        "",
        f"- Baseline run: {'yes' if data.get('baseline') else 'no'}",
        f"- New listings: {_delta_count(data, 'new_count', 'new')}",
        f"- Price reductions: {_delta_count(data, 'price_reduced_count', 'price_reduced')}",
        f"- Price increases: {_delta_count(data, 'price_increased_count', 'price_increased')}",
        f"- Not seen in current run: {_delta_count(data, 'not_seen_count', 'not_seen')}",
    ]
    for title, key, formatter in (
        ("Price reductions", "price_reduced", _format_price_change),
        ("Price increases", "price_increased", _format_price_change),
        ("Not seen", "not_seen", _format_history_entry),
    ):
        entries = data.get(key) or []
        if entries:
            lines.append(f"- {title} details:")
            lines.extend(f"  - {formatter(entry)}" for entry in entries[:20])
    lines.append("")
    return lines


def _weekly_new_listing_lines(delta: Any | None) -> list[str]:
    data = _as_dict(delta)
    if data is None:
        return []
    entries = data.get("new") or []
    lines = ["## Weekly New Listings", ""]
    if not entries:
        lines.extend(["No listings.", ""])
        return lines
    lines.extend(f"- {_format_history_entry(entry)}" for entry in entries[:20])
    lines.append("")
    return lines


def _format_history_entry(entry: dict) -> str:
    address = entry.get("address", "unknown")
    city = entry.get("city", "unknown")
    price = entry.get("price")
    price_text = format_money(price) if price is not None else "unknown"
    return f"{address}, {city} — {price_text} ({entry.get('identity_key', 'unknown')})"


def _format_price_change(entry: dict) -> str:
    return (
        f"{entry.get('address', 'unknown')}, {entry.get('city', 'unknown')}: "
        f"{format_money(entry.get('previous_price'))} → {format_money(entry.get('current_price'))}"
    )


def _assessment_ratio(item: ScoredListing) -> float | None:
    return ratio(item.listing.list_price, item.listing.assessment_total)


def _fmt_ratio(value: float | None) -> str:
    return "unknown" if value is None else f"{value:.2f}"


def _price_level(item: ScoredListing, config: dict) -> str:
    city = item.listing.city.lower().replace(" ", "_")
    configured = config.get("budgets", {}).get(city, {})
    budget = (configured.get("official_max"), configured.get("watch_max")) if configured else {
        "west_vancouver": (1_700_000, 1_850_000),
        "north_vancouver": (1_500_000, 1_650_000),
    }.get(city)
    if not budget or not item.listing.list_price:
        return "unknown"
    official, watch = budget
    if item.listing.list_price <= official:
        return "正式候选"
    if item.listing.list_price <= watch:
        return "观察候选"
    return "超预算"
