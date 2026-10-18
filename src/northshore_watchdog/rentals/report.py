from __future__ import annotations

from collections import Counter
from typing import Any

from northshore_watchdog.utils.dates import now_iso
from northshore_watchdog.utils.money import format_money

from .models import ScoredRentalListing


def summarize_rentals(scored: list[ScoredRentalListing], delta: Any | None = None) -> dict:
    delta_data = _as_dict(delta)
    grades = Counter(item.grade for item in scored)
    cities = Counter(item.listing.city for item in scored if not item.excluded)
    return {
        "generated_at": now_iso(),
        "total": len(scored),
        "a_count": grades.get("A", 0),
        "b_count": grades.get("B", 0),
        "c_count": grades.get("C", 0),
        "excluded_count": grades.get("Excluded", 0),
        "richmond_count": cities.get("Richmond", 0),
        "vancouver_count": cities.get("Vancouver", 0),
        "burnaby_count": cities.get("Burnaby", 0),
        "new_count": _delta_count(delta_data, "new_count", "new"),
        "price_reduced_count": _delta_count(delta_data, "price_reduced_count", "price_reduced"),
        "price_increased_count": _delta_count(delta_data, "price_increased_count", "price_increased"),
        "not_seen_count": _delta_count(delta_data, "not_seen_count", "not_seen"),
        "baseline": bool(delta_data.get("baseline")) if delta_data is not None else None,
        "urgent_count": sum(1 for item in scored if item.alert_level == "urgent"),
    }


def build_rental_markdown_report(
    scored: list[ScoredRentalListing],
    config: dict,
    data_status: Any | None = None,
    delta: Any | None = None,
) -> str:
    ordered = sorted(scored, key=lambda item: (item.excluded, -item.score_total, item.listing.address))
    summary = summarize_rentals(ordered, delta=delta)
    lines = [
        "# 大温租房雷达 - 周报",
        "",
        "## 总结",
        f"- 生成时间: {summary['generated_at']}",
        f"- 总房源数: {summary['total']}",
        f"- A/B/C/Excluded: {summary['a_count']} / {summary['b_count']} / {summary['c_count']} / {summary['excluded_count']}",
        f"- Richmond / Vancouver / Burnaby: {summary['richmond_count']} / {summary['vancouver_count']} / {summary['burnaby_count']}",
        f"- 本周新房源: {summary['new_count']}",
        f"- 本周降租: {summary['price_reduced_count']}",
        f"- 本周涨租: {summary['price_increased_count']}",
        f"- 本周未再看到: {summary['not_seen_count']}",
        f"- 紧急提醒: {summary['urgent_count']}",
        "",
    ]
    lines.extend(_data_status_lines(data_status))
    lines.extend(_weekly_change_lines(delta))
    sections = [
        ("A类候选", [item for item in ordered if item.grade == "A"]),
        ("B类观察", [item for item in ordered if item.grade == "B"]),
        ("C类参考", [item for item in ordered if item.grade == "C"]),
        ("风险/排除", [item for item in ordered if _belongs_in_risk_section(item)]),
    ]
    for title, items in sections:
        lines.extend([f"## {title}", ""])
        if not items:
            lines.extend(["暂无。", ""])
            continue
        for item in items[:20]:
            lines.extend(_rental_markdown(item))
    lines.extend(_weekly_new_listing_lines(delta))
    lines.extend([
        "## 人工复核清单",
        "",
        "- 确认正式租约、押金、入住日期和租期",
        "- 确认是否包含水电网、停车、locker、move-in fee",
        "- 确认父母步行到超市、药房、餐饮、天车是否方便",
        "- 看房时重点检查噪音、采光、电梯、洗衣、暖气/空调",
        "- 警惕无正式租约、现金-only、异常转账、短租或转租",
        "",
    ])
    return "\n".join(lines)


def _rental_markdown(item: ScoredRentalListing) -> list[str]:
    listing = item.listing
    lines = [
        f"### {listing.address}, {listing.city}",
        f"- 链接: {listing.url or 'unknown'}",
        f"- 区域: {listing.neighbourhood or 'unknown'}",
        f"- 月租: {format_money(listing.monthly_rent)}",
        f"- Score / grade / confidence: {item.score_total} / {item.grade} / {item.confidence}",
        f"- 房型: {listing.bedrooms or 'unknown'} bed {listing.bathrooms or 'unknown'} bath; 类型: {listing.property_type or 'unknown'}",
        f"- 面积/交通: {listing.size_sqft or 'unknown'} sqft / 到天车或主要公交 {listing.transit_minutes if listing.transit_minutes is not None else 'unknown'} 分钟",
        f"- 配套: {', '.join(listing.amenities) or 'unknown'}",
        f"- 推荐理由: {', '.join(item.reasons[:8]) or 'none'}",
        f"- 风险: {', '.join((item.risks + item.exclusion_reasons)[:5]) or 'none'}",
        f"- 缺失字段: {', '.join(item.missing_fields) or 'none'}",
        f"- 下一步: {'; '.join(item.next_actions[:4])}",
    ]
    snapshot_observed_at = listing.raw_data.get("snapshot_observed_at")
    if snapshot_observed_at:
        lines.append(f"- 快照观察时间: {snapshot_observed_at}")
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
        "## 数据状态",
        "",
        f"- 数据状态: {data.get('status', data.get('data_status', 'unknown'))}",
        f"- 最新来源: {newest.get('path', 'none')}",
        f"- 最新观察时间: {newest.get('observed_at', 'none')}",
        f"- 最新数据年龄: {newest.get('age_days', 'unknown')} 天",
        f"- 来源文件: {', '.join(paths) or 'none'}",
    ]
    warnings = data.get("warnings") or []
    if warnings:
        lines.append(f"- 警告: {'; '.join(str(item) for item in warnings)}")
    lines.append("")
    return lines


def _weekly_change_lines(delta: Any | None) -> list[str]:
    data = _as_dict(delta)
    if data is None:
        return []
    lines = [
        "## 每周变化",
        "",
        f"- 基线运行: {'是' if data.get('baseline') else '否'}",
        f"- 新房源: {_delta_count(data, 'new_count', 'new')}",
        f"- 降租: {_delta_count(data, 'price_reduced_count', 'price_reduced')}",
        f"- 涨租: {_delta_count(data, 'price_increased_count', 'price_increased')}",
        f"- 本周未再看到: {_delta_count(data, 'not_seen_count', 'not_seen')}",
    ]
    for title, key, formatter in (
        ("降租详情", "price_reduced", _format_price_change),
        ("涨租详情", "price_increased", _format_price_change),
        ("未再看到详情", "not_seen", _format_history_entry),
    ):
        entries = data.get(key) or []
        if entries:
            lines.append(f"- {title}:")
            lines.extend(f"  - {formatter(entry)}" for entry in entries[:20])
    lines.append("")
    return lines


def _weekly_new_listing_lines(delta: Any | None) -> list[str]:
    data = _as_dict(delta)
    if data is None:
        return []
    entries = data.get("new") or []
    lines = ["## 本周新房源", ""]
    if not entries:
        lines.extend(["暂无。", ""])
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


def _belongs_in_risk_section(item: ScoredRentalListing) -> bool:
    strong_risks = {
        "over_budget",
        "cash_only",
        "wire_transfer",
        "no_lease",
        "no_contract",
        "short_term",
        "shared_accommodation",
        "basement",
        "sublet",
    }
    return item.excluded or bool(strong_risks & set(item.risks + item.exclusion_reasons))
