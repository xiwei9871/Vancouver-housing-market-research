#!/usr/bin/env python3
"""Run bounded REALTOR.ca detail enrichment for the saved Vancouver regions."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
from typing import Any, Mapping

from northshore_watchdog.config import load_config
from northshore_watchdog.research.discovery_pipeline import run_discovery
from northshore_watchdog.research.discovery_report import (
    persist_discovery_result,
    render_discovery_report,
)
from northshore_watchdog.research.regional_detail import (
    REGIONAL_PRESETS,
    regional_detail_config,
)


ROOT = Path(__file__).resolve().parents[1]
def _detail_counts(result: Any) -> dict[str, int]:
    captures = [item.capture for item in result.preset_results if item.capture]
    return {
        "presets": len(result.preset_results),
        "reported_cards": sum(int(item.result_count or 0) for item in result.preset_results),
        "detail_requests": sum(int(capture.detail_requests or 0) for capture in captures),
        "detail_successes": sum(int(capture.detail_successes or 0) for capture in captures),
        "detail_failures": sum(int(capture.detail_failures or 0) for capture in captures),
        "detail_snapshot_files": sum(len(capture.detail_snapshot_paths or []) for capture in captures),
    }


def _field_coverage(result: Any) -> dict[str, int]:
    cards = [
        card
        for item in result.preset_results
        if item.capture
        for card in item.capture.cards
    ]
    return {
        "lot_area_sqft": sum(card.lot_area_sqft is not None for card in cards),
        "lot_dimensions": sum(bool(card.lot_dimensions) for card in cards),
        "frontage_and_depth": sum(
            card.frontage_ft is not None and card.depth_ft is not None
            for card in cards
        ),
        "year_built": sum(card.year_built is not None for card in cards),
        "taxes": sum(card.taxes is not None for card in cards),
        "dom": sum(card.dom is not None for card in cards),
        "description": sum(bool(card.description) for card in cards),
    }


def _render_report(result: Any, *, school_cache_path: str) -> str:
    counts = _detail_counts(result)
    coverage = _field_coverage(result)
    report_result = result.model_copy(update={"screening": None})
    lines = [
        "# Vancouver Regional Detail Enrichment Report",
        "",
        f"- Observed at: `{result.observed_at}`",
        f"- Scope: `{', '.join(REGIONAL_PRESETS)}`",
        "- Acquisition source: `REALTOR.ca native filtered search + Playwright detail pages`",
        "- Official enrichment during this run: `DISABLED` (this run is for detail-page retrieval)",
        "",
        "## Count reconciliation",
        "",
        "旧的 broad Vancouver run 使用全 Vancouver $1.0M–$1.8M，记录 235 套；本轮使用持久化非重叠区域 polygon，并按区域价格配置运行。两次运行日期、边界和价格口径不同，因此不是逐套差集，但价格上限差异是主要解释：旧结果中 108 套 asking > $1.6M，而当前 East/South 上限为 $1.6M；Central 下限为 $1.2M，Westside 则为 $1.5M–$2.0M。",
        "",
        "## Detail acquisition result",
        "",
        f"- Presets completed/attempted: `{counts['presets']}`",
        f"- Reported result cards: `{counts['reported_cards']}`",
        f"- Detail pages attempted: `{counts['detail_requests']}`",
        f"- Detail pages successful: `{counts['detail_successes']}`",
        f"- Detail pages failed: `{counts['detail_failures']}`",
        f"- Detail snapshot files: `{counts['detail_snapshot_files']}`",
        f"- School boundary cache: `{school_cache_path}`",
        "",
        "详情页失败不会伪造字段；失败卡片保留在结果中并带有 `DETAIL_FETCH_FAILED`，成功详情页才会合并 lot dimensions、year built、tax、DOM、description 等字段。",
        "",
        "## Detail field coverage",
        "",
        "| Field | Present | Total |",
        "|---|---:|---:|",
        *[
            f"| `{field}` | {present} | {counts['detail_successes']} |"
            for field, present in coverage.items()
        ],
        "",
        "## Per-region result",
        "",
        "| Preset | Status | Reported | Parsed candidates | Detail attempted | Detail successful | Detail failed | Lot coverage |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for item in result.preset_results:
        capture = item.capture
        cards = capture.cards if capture else []
        lot_count = sum(
            1
            for card in cards
            if card.lot_dimensions or card.lot_area_sqft is not None or card.frontage_ft is not None or card.depth_ft is not None
        )
        lines.append(
            f"| `{item.preset_name}` | `{item.status}` | {item.result_count or 0} | {len(item.candidates)} | "
            f"{capture.detail_requests if capture else 0} | {capture.detail_successes if capture else 0} | "
            f"{capture.detail_failures if capture else 0} | {lot_count}/{len(cards)} |"
        )
    lines.extend([
        "",
        "## Discovery report",
        "",
        render_discovery_report(report_result),
    ])
    return "\n".join(lines)


def main() -> int:
    config = load_config(ROOT / "config/research-discovery.yaml")
    run_config = regional_detail_config(config)
    observed_at = datetime.now(timezone.utc)
    result = run_discovery(
        run_config,
        source_name="realtor-ca-native",
        observed_at=observed_at,
        official_enrich=False,
    )
    persist_discovery_result(result, run_config)

    date = (result.observed_at or observed_at).date().isoformat()
    normalized = ROOT / "data/research/normalized" / f"regional-detail-enrichment-{date}.json"
    normalized.parent.mkdir(parents=True, exist_ok=True)
    normalized.write_text(
        json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    school_cache = ROOT / "data/research/normalized/school-boundaries"
    cache_paths = sorted(school_cache.glob("school-catchment-*.json"))
    cache_path = str(cache_paths[-1]) if cache_paths else "UNKNOWN"
    report = ROOT / "reports/discovery" / f"regional-detail-enrichment-{date}.md"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(_render_report(result, school_cache_path=cache_path), encoding="utf-8")
    latest = report.parent / "regional-detail-enrichment-latest.md"
    shutil.copyfile(report, latest)
    compact = {
        "status": result.status,
        "normalized": str(normalized),
        "report": str(report),
        "latest": str(latest),
        "detail_counts": _detail_counts(result),
    }
    print(json.dumps(compact, ensure_ascii=False, indent=2, default=str))
    return 0 if result.status in {"COMPLETED", "PARTIAL"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
