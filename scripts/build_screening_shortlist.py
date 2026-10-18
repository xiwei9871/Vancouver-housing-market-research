#!/usr/bin/env python3
"""Apply the calibrated screening rule and emit the blind-test shortlist.

Rule (agreed after the 117-label calibration pass):
    candidate = (best rank across current_use / redevelopment / dual views
                 <= 50) OR data_only_signal == STRONG
    minus rows the auto-prefill marked PASS or SPECIAL.

Outputs a JSON+markdown report under reports/calibration/screening-<date>/:
- calibration rows: measured keep/watch recall and PASS leakage
- blind rows: the candidate shortlist with model scores for human review
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

DEFAULT_MODEL = ROOT / "data/research/normalized/portfolio-analysis-2026-09-18.json"
DEFAULT_AUTO = ROOT / "data/research/normalized/strategy-v2-auto-annotations-2026-09-18.json"
DEFAULT_SPLITS = ROOT / "data/research/history/strategy-v2-shadow-splits.json"

VIEWS = ("current_use_score", "redevelopment_score", "dual_value_score")
TOP_N = 50
TIER_A = 30


def _rows(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = data.get("properties") if isinstance(data, dict) else data
    rows = rows if isinstance(rows, list) else list(rows.values())
    return [row for row in rows if isinstance(row, dict)]


def _mls(row: dict[str, Any]) -> str | None:
    return row.get("mls") or row.get("mls_number")


def _ranks(rows: list[dict[str, Any]], field: str) -> dict[str, int]:
    ranked = sorted(
        (row for row in rows if row.get(field) is not None),
        key=lambda row: -float(row[field]),
    )
    return {_mls(row): index + 1 for index, row in enumerate(ranked) if _mls(row)}


def _load_split(path: Path) -> dict[str, str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    out: dict[str, str] = {}
    for key in data.get("calibration_keys") or []:
        out[str(key).replace("mls:", "")] = "calibration"
    for key in data.get("blind_test_keys") or []:
        out[str(key).replace("mls:", "")] = "blind"
    if out:
        return out
    rows = data.get("assignments") or data.get("splits") or data.get("rows") or data
    if isinstance(rows, dict):
        for key, value in rows.items():
            mls = str(key).replace("mls:", "")
            split = value.get("split") if isinstance(value, dict) else value
            if split:
                out[mls] = str(split)
    elif isinstance(rows, list):
        for value in rows:
            if isinstance(value, dict):
                mls = str(value.get("property_key") or value.get("mls") or "").replace("mls:", "")
                if mls and value.get("split"):
                    out[mls] = str(value["split"])
    return out


def _load_auto(path: Path) -> tuple[set[str], dict[str, str]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    excluded: set[str] = set()
    signals: dict[str, str] = {}
    for key, row in (data.get("annotations") or {}).items():
        mls = str(key).replace("mls:", "")
        auto = row.get("auto") or {}
        if auto.get("human_decision") in ("PASS", "SPECIAL"):
            excluded.add(mls)
        if row.get("data_only_signal"):
            signals[mls] = row["data_only_signal"]
    return excluded, signals


def _load_human(path: Path | None) -> dict[str, str]:
    if path is None or not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = data.get("annotations") if isinstance(data, dict) else data
    rows = rows.values() if isinstance(rows, dict) else rows
    out: dict[str, str] = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        mls = str(row.get("property_key") or "").replace("mls:", "") or row.get("mls")
        decision = row.get("human_decision") or row.get("decision")
        if mls and decision:
            out[mls] = str(decision)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--auto", type=Path, default=DEFAULT_AUTO)
    parser.add_argument("--splits", type=Path, default=DEFAULT_SPLITS)
    parser.add_argument("--annotations", type=Path, default=None)
    parser.add_argument("--top-n", type=int, default=TOP_N)
    parser.add_argument("--output-date", default=datetime.now(timezone.utc).date().isoformat())
    args = parser.parse_args()

    rows = _rows(args.model)
    splits = _load_split(args.splits)
    auto_excluded, signals = _load_auto(args.auto)
    human = _load_human(args.annotations)

    rank_views = {field: _ranks(rows, field) for field in VIEWS}
    results: list[dict[str, Any]] = []
    for row in rows:
        mls = _mls(row)
        if not mls:
            continue
        per_view = {field: rank_views[field].get(mls) for field in VIEWS}
        best = min((value for value in per_view.values() if value is not None), default=None)
        strong = signals.get(mls) == "STRONG"
        # Unscored rows (rejected before Stage-B) can never be candidates,
        # regardless of the data-only signal.  A clearly overpriced package
        # (vfm <= 30) cannot be a candidate either: the calibration labels
        # show every human PASS in that zone.
        vfm = row.get("current_package_value_for_money_score")
        candidate = (
            best is not None
            and (best <= args.top_n or strong)
            and mls not in auto_excluded
            and (vfm is not None and vfm > 30)
        )
        results.append({
            "mls": mls,
            "address": row.get("address"),
            "asking": row.get("asking"),
            "split": splits.get(mls, "unassigned"),
            "candidate": candidate,
            "tier": "A" if candidate and best is not None and best <= TIER_A else "B" if candidate else None,
            "best_rank": best,
            "ranks": per_view,
            "current_use_score": row.get("current_use_score"),
            "redevelopment_score": row.get("redevelopment_score"),
            "dual_value_score": row.get("dual_value_score"),
            "data_signal": signals.get(mls),
            "auto_excluded": mls in auto_excluded,
            "human_decision": human.get(mls),
        })

    calib = [r for r in results if r["split"] == "calibration" and r["human_decision"]]
    keeps = [r for r in calib if r["human_decision"] in ("KEEP", "WATCH")]
    passes = [r for r in calib if r["human_decision"] == "PASS"]
    metrics = {
        "labeled_calibration": len(calib),
        "keep_watch": len(keeps),
        "keep": sum(1 for r in calib if r["human_decision"] == "KEEP"),
        "keep_recall": round(sum(1 for r in keeps if r["candidate"]) / len(keeps), 4) if keeps else None,
        "keep_only_recall": (
            round(
                sum(1 for r in calib if r["human_decision"] == "KEEP" and r["candidate"])
                / max(1, sum(1 for r in calib if r["human_decision"] == "KEEP")),
                4,
            )
        ),
        "pass_leak": [r["address"] for r in passes if r["candidate"]],
    }
    blind_candidates = [r for r in results if r["split"] == "blind" and r["candidate"]]
    blind_candidates.sort(key=lambda r: (r["best_rank"] or 9999, r["address"] or ""))

    out_dir = ROOT / "reports" / "calibration" / f"screening-{args.output_date}"
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "rule": {
            "views": list(VIEWS),
            "top_n": args.top_n,
            "tier_a": TIER_A,
            "include_strong_signal": True,
            "exclude_auto_decisions": ["PASS", "SPECIAL"],
        },
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "metrics": metrics,
        "blind_candidates": blind_candidates,
        "all_rows": results,
    }
    (out_dir / "screening-shortlist.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    lines = [
        "# Screening shortlist（校准后规则）",
        "",
        f"- 规则: 任一视图 top{args.top_n} ∪ 数据信号 STRONG − auto PASS/SPECIAL",
        f"- 校准组: KEEP+WATCH 召回 {metrics['keep_recall']} "
        f"(KEEP 单列 {metrics['keep_only_recall']}), PASS 泄漏 {metrics['pass_leak']}",
        "",
        f"## 盲测组候选（{len(blind_candidates)} 套，待人工复核）",
        "",
        "| 地址 | MLS | 层 | 最优名次 | CU | Redev | Dual | 信号 |",
        "|---|---|---|---|---:|---:|---:|---|",
    ]
    for r in blind_candidates:
        lines.append(
            f"| {r['address']} | {r['mls']} | {r['tier']} | {r['best_rank']} | "
            f"{r['current_use_score']} | {r['redevelopment_score']} | "
            f"{r['dual_value_score']} | {r['data_signal']} |"
        )

    all_rows_sorted = sorted(
        results, key=lambda r: (r["best_rank"] is None, r["best_rank"] or 9999)
    )
    n_in = sum(1 for r in results if r["candidate"])
    lines += [
        "",
        f"## 全宇宙 {len(results)} 套（候选 {n_in} / 排除 {len(results) - n_in}）",
        "",
        "| 地址 | MLS | 组 | 判定 | 最优名次 | CU | Redev | Dual | 人工 |",
        "|---|---|---|---|---:|---:|---:|---:|---|",
    ]
    view_names = {"current_use_score": "CU", "redevelopment_score": "Redev", "dual_value_score": "Dual"}
    for r in all_rows_sorted:
        if r["candidate"]:
            entries = [name for f, name in view_names.items()
                       if (r["ranks"].get(f) or 9999) <= args.top_n]
            if r["data_signal"] == "STRONG":
                entries.append("STRONG")
            verdict = f"候选{r['tier']}({'/'.join(entries)})"
        elif r["best_rank"] is None:
            verdict = "未评分"
        elif r["auto_excluded"]:
            verdict = "auto排除"
        elif r["best_rank"] <= args.top_n:
            verdict = "vfm≤30"
        else:
            verdict = f"名次>{args.top_n}"
        lines.append(
            f"| {r['address']} | {r['mls']} | {r['split']} | {verdict} | "
            f"{r['best_rank'] or '—'} | "
            f"{r['current_use_score']} | {r['redevelopment_score']} | "
            f"{r['dual_value_score']} | {r['human_decision'] or ''} |"
        )
    (out_dir / "shortlist.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(json.dumps({
        "metrics": metrics,
        "blind_candidate_count": len(blind_candidates),
        "output": str(out_dir),
    }, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
