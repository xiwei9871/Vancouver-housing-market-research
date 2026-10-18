#!/usr/bin/env python3
"""Analyze a saved polygon-bounded REALTOR.ca discovery scan.

The output is intentionally a market-scan report, not an underwriting report.
It does not assign investment scores to cards that have not gone through
detail and official-data enrichment.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil

from northshore_watchdog.research.boundaries import load_boundary_registry
from northshore_watchdog.config import load_config
from northshore_watchdog.research.regional_analysis import analyze_regional_boundary_scan


ROOT = Path(__file__).resolve().parents[1]


def _money(value: object) -> str:
    if value is None:
        return "UNKNOWN"
    return f"${float(value):,.0f}"


def _pct(value: int, total: int) -> str:
    return f"{(value / total * 100):.1f}%" if total else "0.0%"


def _render(summary: dict) -> str:
    lines = [
        "# Regional Boundary Scan Analysis",
        "",
        f"- Observed at: `{summary.get('observed_at') or 'UNKNOWN'}`",
        f"- Boundary version: `{summary.get('boundary_version') or 'UNKNOWN'}`",
        f"- Boundary registry: `{summary.get('boundary_schema_version') or 'UNKNOWN'}`",
        f"- Official boundary source: {summary.get('boundary_registry_source_url') or 'UNKNOWN'}",
        f"- Partition positive-area overlap check: `{summary.get('partition_overlap_check', {}).get('status', 'UNKNOWN')}`",
        "",
        "## Scope and interpretation",
        "",
        "本报告分析的是已经由 REALTOR.ca 原生筛选和保存的区域扫描结果。五个区域使用持久化的 City of Vancouver 官方 local-area geometry；同时保存了按固定顺序切除微小 GIS 重叠后的 partition geometry。搜索端使用 REALTOR.ca 可接受的 exterior-only polygon，结果坐标使用 partition geometry 回验；未经修改的官方 geometry 仍保留在 registry 中审计。",
        "",
        "本轮 Westside、Central、Oakridge、South 使用 05:30Z 扫描；East 因分页控件状态修复于 05:50Z 重跑，均保留各自 raw snapshot。",
        "",
        "本轮只验证 boundary + price + native search result。没有打开 166 个详情页，因此 lot dimensions、BCA、official zoning、学区、房龄维护和 sold comps 不应被解释为已确认，也没有据此生成投资排名。",
        "",
        "## Acquisition result",
        "",
        f"- Reported result cards: **{summary.get('total_reported_results', 0)}**",
        f"- Parsed cards: **{summary.get('total_parsed_cards', 0)}**",
        f"- Validated cards with address: **{summary.get('total_validated_cards', 0)}**",
        f"- Missing-address observations: **{summary.get('total_missing_address_observations', 0)}**",
        f"- Cards outside official region: **{summary.get('total_boundary_outside_points', 0)}**",
        "",
        "| Region | Configured ask range | Official local areas | Reported | Parsed/valid | Ask min | Ask median | Ask max | 3+ bed / 2+ bath / 1250+ sqft proxy | Boundary |",
        "|---|---:|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for item in summary["regions"]:
        price = item["price"]
        points = item["boundary_point_counts"]
        total = item["parsed_cards"] or 0
        boundary = f"{points['inside']} inside"
        if points["outside"] or points["unknown"]:
            boundary += f", {points['outside']} outside, {points['unknown']} unknown"
        lines.append(
            f"| {item['label']} | {_money(item.get('configured_price_min'))}–{_money(item.get('configured_price_max'))} | {', '.join(item['source_local_areas'])} | "
            f"{item['reported_results']} | {item['parsed_cards']}/{item['validated_cards']} | "
            f"{_money(price['min'])} | {_money(price['median'])} | {_money(price['max'])} | "
            f"{item['family_proxy_count']} ({_pct(item['family_proxy_count'], total)}) | {boundary} |"
        )

    lines.extend([
        "",
        "### Asking-price bands",
        "",
        "| Region | < $1.30M | $1.30M–$1.50M | $1.50M–$1.60M | $1.60M+ |",
        "|---|---:|---:|---:|---:|",
    ])
    for item in summary["regions"]:
        bands = item["price_bands"]
        lines.append(
            f"| {item['label']} | {bands['< $1.30M']} | {bands['$1.30M–$1.50M']} | "
            f"{bands['$1.50M–$1.60M']} | {bands['$1.60M+']} |"
        )

    lines.extend([
        "",
        "## Boundary registry",
        "",
        "| Boundary ID | Region | Persisted source units |",
        "|---|---|---|",
    ])
    for item in summary["regions"]:
        lines.append(
            f"| `{item['boundary_id']}` | {item['label']} | {', '.join(item['source_local_areas'])} |"
        )

    lines.extend([
        "",
        "## What the scan says",
        "",
        "1. **Vancouver East is the largest result pool under the current preset.** It contributes 108 validated cards and has a median asking price of $1.4545M. This is an acquisition-volume result, not a conclusion that East properties are better investments.",
        "2. **Westside has the smallest and most expensive pool.** It produced 8 validated cards with a $1.947M median asking price. The current price range is therefore not comparable across regions without area-relative comps.",
        "3. **Central Core and South have intermediate pools.** Central Core produced 15 cards at a $1.725M median; South produced 35 cards at a $1.490M median.",
        "4. **Oakridge returned zero cards under its own polygon and price range.** This is a valid zero-result region, not a reason to borrow cards from adjacent Central or South regions.",
        "5. **The current upstream filter is intentionally broad.** The 2+ bed / 1+ bath native filter leaves many 5–10 bedroom cards in East and Central. Those are not current-use advantages; they require detail review to distinguish a family house from a suite-heavy income layout.",
        "6. **No boundary leakage was observed.** Every parsed card with coordinates was inside its official region. East had one no-address card; it was retained as a warning and did not stop the run.",
        "",
        "## Lowest asking cards in each region",
        "",
        "这些只是价格排序后的 discovery cards，不是推荐名单。没有 lot、维护、BCA、zoning 和学区验证，不能称为 shortlist。",
        "",
    ])
    for item in summary["regions"]:
        lines.extend([
            f"### {item['label']}",
            "",
            "| Address | MLS | Ask | Beds/Baths | Sqft | Link |",
            "|---|---|---:|---:|---:|---|",
        ])
        if not item["lowest_price_cards"]:
            lines.append("| — | — | — | — | — | No result |")
        for card in item["lowest_price_cards"]:
            link = f"[REALTOR.ca]({card['url']})" if card.get("url") else "UNKNOWN"
            lines.append(
                f"| {card.get('address') or 'UNKNOWN'} | `{card.get('mls') or 'UNKNOWN'}` | "
                f"{_money(card.get('asking'))} | {card.get('beds') or 'UNKNOWN'}/{card.get('baths') or 'UNKNOWN'} | "
                f"{card.get('sqft') or 'UNKNOWN'} | {link} |"
            )
        lines.append("")

    lines.extend([
        "## Data completeness and next gate",
        "",
        "| Field | Current scan status | Meaning |",
        "|---|---:|---|",
        "| MLS / asking / beds / baths / sqft / URL | Parsed for all cards with an address | Suitable for discovery-level review |",
        "| Coordinates | Parsed and checked for all cards with an address | Suitable for boundary validation |",
        "| Lot dimensions / lot area | Not acquired in this run | Must be read from detail pages before lot screening |",
        "| BCA assessment | Not acquired in this run | No assessment discount signal yet |",
        "| Official zoning / approved use | Not acquired in this run | No redevelopment score yet |",
        "| Sold comps / price dislocation | Not acquired in this run | No fair-value or MOS conclusion |",
        "",
        "当前正确的下一道门是：对每个区域的 discovery cards 做 bounded detail enrichment，再把有地址的结果按 lot、房龄/维护、主路和房屋使用面积/房间数做 Stage B 筛选；只有留下的少量候选才进入 BCA、official zoning、school 和 redevelopment underwriting。",
        "",
        "## Audit artifacts",
        "",
        "- Boundary registry: `data/research/boundaries/vancouver-market-boundaries-2026-09-05.json`",
        "- Scan artifact: `data/research/normalized/regional-boundary-scan-2026-09-05.json`",
        "- Raw snapshots: `data/research/raw/discovery/realtor-ca-native-*-boundary-2026-09-05.html`",
        "- Ranking status: `NOT_RUN_DISCOVERY_ONLY`",
        "",
    ])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scan", default="data/research/normalized/regional-boundary-scan-2026-09-05.json")
    parser.add_argument("--registry", default="data/research/boundaries/vancouver-market-boundaries-2026-09-05.json")
    parser.add_argument("--config", default="config/research-discovery.yaml")
    parser.add_argument("--output", default="reports/discovery/regional-boundary-analysis-2026-09-05.md")
    args = parser.parse_args()

    scan_path = ROOT / args.scan
    registry = load_boundary_registry(ROOT / args.registry)
    config = load_config(ROOT / args.config)
    scan = json.loads(scan_path.read_text(encoding="utf-8"))
    configured_presets = {
        str(item.get("name")): item
        for item in config.get("discovery", {}).get("presets", [])
        if isinstance(item, dict) and item.get("name")
    }
    for preset in scan.get("presets", []):
        if not isinstance(preset, dict):
            continue
        configured = configured_presets.get(str(preset.get("preset")), {})
        geography = configured.get("geography", {})
        map_params = geography.get("map_params", {}) if isinstance(geography, dict) else {}
        filters = configured.get("desired_filters", {})
        if isinstance(map_params, dict):
            preset["boundary_id"] = map_params.get("boundary_id")
        if isinstance(filters, dict):
            preset["configured_price_min"] = filters.get("price_min")
            preset["configured_price_max"] = filters.get("price_max")
    summary = analyze_regional_boundary_scan(scan, registry)
    output = ROOT / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(_render(summary), encoding="utf-8")
    latest = output.parent / "regional-boundary-analysis-latest.md"
    shutil.copyfile(output, latest)
    json_path = output.with_suffix(".json")
    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"report": str(output), "latest": str(latest), "json": str(json_path)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
