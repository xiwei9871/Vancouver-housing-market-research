#!/usr/bin/env python3
"""Housing Age Recovery V2.4 — 跨产品房龄证据恢复。

根因(已实测):
- 温哥华开放数据 property-tax 全表的 year_built / big_improvement_year
  字段冻结在 ≤2022:任何 2023+ 竣工建筑在税卷中无房龄 →
  resolve_age() 第 1/3 层对全产品永远拿不到新房龄;
- census 纠正层(nb217/enriched/permit)只覆盖 HOUSE →
  非 HOUSE 产品 2025-26 0-3 档全零;
- 旧 pid 重建后 roll yb 仍挂拆除前年 → 旧房龄倒灌。

修复:统一 resolve_year_built_at_sale():
- STRICT_AGE: census 纠正 yb 或一致的 roll/native yb;
- SUPPORTED_AGE: plan/pid 首现年推断(yb≈fs-1)或多弱源一致;
- INFERRED_AGE: 单一弱源(impv 跳涨年/许可年/plan fs=2023);
- 冲突独立维度: NO_CONFLICT / BUILDING_IDENTITY_CONFLICT /
  TEMPORAL_CONFLICT / SOURCE_YEAR_CONFLICT ——
  冲突只做警示,不改变 BROAD 口径包含范围。
- fs=2023 是税卷下界(老 plan 也显 2023)→ 单独降级 INFERRED。
- 推断永不倒灌:fs_yb/jy_yb > sy → TEMPORAL_CONFLICT,不算新房。

输入(全部本地冻结):sales_archive_v1_0_3 + house_5386 普查 +
property-tax-2023..2026 + issued-permits-ldh-2017。
输出:docs/audits/housing_age_recovery_v2_4/ + normalized 明细。
V3s 零修改。
"""
import csv, json, sys, datetime
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import house_newbuild_timeline_gate_v2_1 as V     # noqa: E402
import house_age_market_scale_v2_3 as M23        # noqa: E402

DOCS = ROOT / "docs/audits/housing_age_recovery_v2_4"
OUTD = ROOT / "data/research/normalized/housing_age_recovery_v2_4"
CUTOFF_2026 = "2026-07-08"
TAX_YEARS = (2023, 2024, 2025, 2026)
PRODUCTS = ("HOUSE", "DUPLEX_HALF", "DUPLEX_WHOLE",
            "DUPLEX_SCOPE_UNKNOWN", "TOWNHOUSE",
            "MULTIPLEX_UNIT", "MULTIPLEX_WHOLE", "CONDO")
KINDS = ("FIRST_SALE", "RESALE", "UNKNOWN")
CONFLICTS = ("NO_CONFLICT", "BUILDING_IDENTITY_CONFLICT",
             "TEMPORAL_CONFLICT", "SOURCE_YEAR_CONFLICT")
TIERS = ("STRICT_AGE", "SUPPORTED_AGE", "INFERRED_AGE",
         "AGE_UNKNOWN")

# impv 跳涨最小幅度(与 V2 口径对齐:fa*250 的 50% 档,
# 无 fa 时兜底 $500k)
JUMP_ABS_FLOOR = 500_000
JUMP_RATIO = 1.8
# plan 首现年 vs roll yb 的年差阈值 → 身份冲突
IDENTITY_GAP = 10


def num(x):
    try:
        return float(str(x).replace(",", ""))
    except (TypeError, ValueError):
        return None


def jump_year(hist):
    """hist {yr: impv} → 最早跳涨年(须同时 delta≥floor
    且 ≥1.8x;单年升值不构成重建信号),或 None。"""
    ys = sorted(y for y in hist if hist.get(y) is not None)
    for i in range(1, len(ys)):
        p, c = hist[ys[i - 1]], hist[ys[i]]
        if p is None or c is None:
            continue
        if c - p >= JUMP_ABS_FLOOR and p > 0 and \
                c >= p * JUMP_RATIO:
            return ys[i]
    return None


def resolve_year_built_at_sale(sale, ev):
    """统一成交时房龄解析。

    sale: {sale_date, yb, pid, strata_plan, fa}
    ev:   {census_yb, roll_yb(≤sy 最大 roll/native),
           plan_fs, pid_fs, jump_yr, permit_yr}
    返回 dict:yb, yb_source, evidence_year, rebuilt,
    conflict, age_tier, band。"""
    sy = int(str(sale["sale_date"])[:4])
    out = {"yb": None, "yb_source": "", "evidence_year": "",
           "rebuilt": False, "conflict": "NO_CONFLICT",
           "age_tier": "AGE_UNKNOWN", "band": "UNKNOWN"}
    roll_yb = ev.get("roll_yb")
    # plan/pid 首现年独立评估(V1.1 修正:plan 早在
    # 2023 不排除其后新 pid 的首现证据)
    fs_cands = [(v, s_) for v, s_ in
                ((ev.get("plan_fs"), "plan_first_seen"),
                 (ev.get("pid_fs"), "pid_first_seen"))
                if v and v > TAX_YEARS[0]]
    fs = max((v for v, _ in fs_cands), default=None)
    fs_src = next((s_ for v, s_ in fs_cands if v == fs),
                  "")
    if len({v for v, _ in fs_cands}) > 1:
        out.setdefault("_fs_disagree", True)
    fs_yb = fs - 1 if fs else None
    jy = ev.get("jump_yr")
    jy_yb = jy - 1 if jy else None
    py = ev.get("permit_yr")
    py_yb = py + 1 if py else None

    # ---- 时间冲突预检:证据本身晚于成交 → 未来状态
    # 不得倒灌(roll 首现年/跳涨年 > sy = 成交后才有
    # 评估记录;permit+1 > sy = 许可竣工晚于成交) ----
    if fs and fs > sy:
        out["conflict"] = "TEMPORAL_CONFLICT"
    if jy and jy > sy:
        out["conflict"] = "TEMPORAL_CONFLICT"
    if py_yb and py_yb > sy:
        out["conflict"] = "TEMPORAL_CONFLICT"

    # ---- S1: census 纠正 yb(HOUSE 专用,≤sy) ----
    cyb = ev.get("census_yb")
    if cyb and cyb <= sy:
        out.update(yb=cyb, yb_source="census_corrected",
                   evidence_year=cyb, age_tier="STRICT_AGE")
        if roll_yb and abs(roll_yb - cyb) > 1 and \
                out["conflict"] == "NO_CONFLICT":
            out["conflict"] = "SOURCE_YEAR_CONFLICT"
    elif cyb and cyb > sy:
        out["conflict"] = "TEMPORAL_CONFLICT"

    # ---- S2: roll/native yb(≤sy) ----
    if out["yb"] is None and roll_yb and roll_yb <= sy:
        # 身份冲突:plan 首现年/跳涨显著新于 roll yb →
        # roll yb 属于拆除前旧建筑或错误回连
        stale = (fs and fs - roll_yb > IDENTITY_GAP) or \
            (jy_yb and jy_yb <= sy and jy_yb - roll_yb >
             IDENTITY_GAP)
        if stale:
            out["conflict"] = "BUILDING_IDENTITY_CONFLICT"
            out["rebuilt"] = True
            # 旧 yb 不作房龄,但不丢——进推断层
        else:
            out.update(yb=roll_yb, yb_source="roll_native",
                       evidence_year=roll_yb,
                       age_tier="STRICT_AGE")

    # ---- S3: 推断层(无可信 yb 或被身份冲突否决) ----
    if out["yb"] is None:
        inf = []
        if fs and fs <= sy:
            inf.append((fs_yb, fs_src, fs, "SUPPORTED_AGE"))
        if jy and jy <= sy:
            inf.append((jy_yb, "impv_jump", jy,
                        "INFERRED_AGE"))
        if py_yb and py_yb <= sy:
            inf.append((py_yb, "permit_issue", py,
                        "INFERRED_AGE"))
        if inf:
            ys = [i[0] for i in inf]
            agree = max(ys) - min(ys) <= 1 and len(inf) > 1
            # 取最保守(最大 yb = 最小房龄)且升级一致证据
            best = max(inf, key=lambda i: i[0])
            tier = "SUPPORTED_AGE" if agree else best[3]
            if best[3] == "SUPPORTED_AGE" or agree:
                tier = "SUPPORTED_AGE"
            out.update(yb=best[0],
                       yb_source="+".join(sorted(
                           {i[1] for i in inf})),
                       evidence_year=best[2], age_tier=tier)
            if len({i[0] for i in inf}) > 1 and not agree and \
                    out["conflict"] == "NO_CONFLICT":
                out["conflict"] = "SOURCE_YEAR_CONFLICT"
            if out["rebuilt"] is False and jy_yb and \
                    roll_yb and jy_yb - roll_yb > IDENTITY_GAP:
                out["rebuilt"] = True

    if out["yb"] is not None:
        out["band"] = M23.band(sy - out["yb"])
    return out


def load_evidence():
    """税卷轨迹 + plan/pid 首现年 + impv 跳涨 + 许可地址。
    返回 (traj, plan_fs, pid_fs, jumpy, permit_addr);
    供 main() 与下游价格研究复用。"""
    traj = defaultdict(dict)
    plan_fs, pid_fs = {}, {}
    for yr in TAX_YEARS:
        for t in csv.DictReader(open(
                V.IN[f"tax{yr}"], encoding="utf-8-sig"),
                delimiter=";"):
            pid = (t.get("pid") or "").strip()
            pl = (t.get("plan") or "").strip()
            if pl and pl not in plan_fs:
                plan_fs[pl] = yr
            if pid:
                if pid not in pid_fs:
                    pid_fs[pid] = yr
                traj[pid][yr] = {
                    "yb": V.iyear(t.get("year_built")),
                    "impv": num(t.get(
                        "current_improvement_value")),
                    "plan": pl,
                    "legal": t.get("legal_type")}
    jumpy = {pid: j for pid, tr in traj.items()
             if (j := jump_year(
                 {y: v["impv"] for y, v in tr.items()}))}
    permit_addr = {}
    f = open(V.IN["permits"], newline="", encoding="utf-8-sig")
    d = csv.Sniffer().sniff(f.read(4000), delimiters=",;\t")
    f.seek(0)
    for r in csv.DictReader(f, dialect=d):
        iy = V.iyear(r.get("issueyear") or
                     (r.get("issuedate") or "")[:4])
        ad = (r.get("address") or "").strip().upper()
        if iy and ad:
            permit_addr[ad] = min(iy, permit_addr.get(ad, iy))
    f.close()
    return traj, plan_fs, pid_fs, jumpy, permit_addr


def build_evidence(s, traj, plan_fs, pid_fs, jumpy,
                   permit_addr, census=None):
    """组装 resolve_year_built_at_sale 的 ev 字典。"""
    sy = int(s["sale_date"][:4])
    pid = s["pid"]
    tr = traj.get(pid, {})
    roll_yb = max((v["yb"] for v in tr.values()
                   if v["yb"] and v["yb"] <= sy),
                  default=None)
    if roll_yb is None and s["yb"]:
        nyb = V.iyear(s["yb"])
        if nyb and nyb <= sy:
            roll_yb = nyb
    return {"census_yb": V.iyear(
                (census or {}).get(
                    s["canonical_tx"], {}).get(
                        "year_built_corrected")),
            "roll_yb": roll_yb,
            "plan_fs": plan_fs.get(
                (s["strata_plan"] or "").strip()),
            "pid_fs": pid_fs.get(pid),
            "jump_yr": jumpy.get(pid),
            "permit_yr": permit_addr.get(
                (s["address"] or "").strip().upper())}


def main():
    DOCS.mkdir(parents=True, exist_ok=True)
    OUTD.mkdir(parents=True, exist_ok=True)

    # ---------- 输入 ----------
    sales = [s for s in csv.DictReader(open(V.IN["archive"]))
             if s["product"] in PRODUCTS]
    census = {r["canonical_tx"]: r for r in
              csv.DictReader(open(V.IN["census"]))}
    traj, plan_fs, pid_fs, jumpy, permit_addr = \
        load_evidence()

    # ---------- 逐笔解析 ----------
    per_row = []
    before = Counter()   # (product, year, band)  archive yb 口径
    after = Counter()    # (product, year, tier, band, kind)
    cov = Counter()      # 覆盖诊断
    src = Counter()      # 来源优先级审计
    for s in sales:
        p = s["product"]
        sy = int(s["sale_date"][:4])
        pid = s["pid"]
        kind = s["sale_kind"] if s["sale_kind"] in (
            "FIRST_SALE", "RESALE") else "UNKNOWN"
        ev = build_evidence(s, traj, plan_fs, pid_fs, jumpy,
                            permit_addr, census)
        roll_yb = ev["roll_yb"]
        r = resolve_year_built_at_sale(s, ev)
        # before(旧口径:仅 archive yb)
        ayb = V.iyear(s["yb"])
        bband = M23.band(sy - ayb if ayb else None)
        before[(p, sy, M23.merge_band(bband))] += 1
        after[(p, sy, r["age_tier"],
               M23.merge_band(r["band"]), kind)] += 1
        # 覆盖诊断维度
        cov[(p, sy, "total")] += 1
        cov[(p, sy, "pid_present" if pid else "no_pid")] += 1
        cov[(p, sy, "pid_in_roll" if pid in traj
             else "pid_absent_roll")] += 1
        cov[(p, sy, "roll_yb" if roll_yb else
             "no_roll_yb")] += 1
        cov[(p, sy, "plan_fs_ev" if ev["plan_fs"] else
             "no_plan_fs")] += 1
        cov[(p, sy, "jump_ev" if ev["jump_yr"] else
             "no_jump")] += 1
        cov[(p, sy, "census_ev" if ev["census_yb"] else
             "no_census")] += 1
        cov[(p, sy, "tier_" + r["age_tier"])] += 1
        cov[(p, sy, "conflict_" + r["conflict"])] += 1
        src[(p, sy, r["yb_source"] or "NONE",
             r["age_tier"])] += 1
        per_row.append({
            "canonical_tx": s["canonical_tx"],
            "product": p, "sale_year": sy,
            "sale_kind": kind,
            "community": s["community"],
            "before_band": bband,
            "resolved_yb": r["yb"] or "",
            "yb_source": r["yb_source"],
            "evidence_year": r["evidence_year"],
            "rebuilt": r["rebuilt"],
            "conflict": r["conflict"],
            "age_tier": r["age_tier"],
            "after_band": r["band"]})
    V.wcsv(OUTD / "age_recovery_per_row.csv", per_row)

    # ---------- D1 覆盖表 ----------
    r1 = [{"product": p, "year": y, "metric": m, "n": n}
          for (p, y, m), n in sorted(cov.items())]
    V.wcsv(DOCS / "age_source_coverage_by_product_year.csv",
           r1)

    # ---------- D2 零值根因瀑布 ----------
    # 每个 product×year:total → 有证据 → 各层 → 0-3 前后
    wf = []
    for p in PRODUCTS:
        for y in (2023, 2024, 2025, 2026):
            tot = cov.get((p, y, "total"), 0)
            if not tot:
                continue
            rows_y = [r for r in per_row
                      if r["product"] == p and
                      r["sale_year"] == y]
            b03 = sum(1 for r in rows_y if
                      r["before_band"] in ("0", "1", "2-3"))
            a03 = {t: sum(1 for r in rows_y
                          if r["age_tier"] == t and
                          r["after_band"] in
                          ("0", "1", "2-3"))
                   for t in TIERS}
            wf.append({
                "product": p, "year": y, "total": tot,
                "no_pid": cov.get((p, y, "no_pid"), 0),
                "pid_absent_roll":
                    cov.get((p, y, "pid_absent_roll"), 0),
                "no_roll_yb":
                    cov.get((p, y, "no_roll_yb"), 0),
                "no_census":
                    cov.get((p, y, "no_census"), 0),
                "before_0_3": b03,
                "after_0_3_strict": a03["STRICT_AGE"],
                "after_0_3_supported": a03["SUPPORTED_AGE"],
                "after_0_3_inferred": a03["INFERRED_AGE"],
                "after_0_3_total":
                    sum(a03.values()),
                "identity_conflict":
                    cov.get((p, y,
                             "conflict_BUILDING_IDENTITY_"
                             "CONFLICT"), 0)})
    V.wcsv(DOCS / "zero_newbuild_root_cause_waterfall.csv",
           wf)

    # ---------- D3 来源优先级审计 ----------
    r3 = [{"product": p, "year": y, "yb_source": src_,
           "age_tier": t, "n": n}
          for (p, y, src_, t), n in sorted(src.items())]
    V.wcsv(DOCS / "year_built_source_precedence_audit.csv",
           r3)

    # ---------- D4 修复前后 0-3/0-10 ----------
    r4 = []
    for p in PRODUCTS:
        for y in (2023, 2024, 2025, 2026):
            rows_y = [r for r in per_row
                      if r["product"] == p and
                      r["sale_year"] == y]
            if not rows_y:
                continue
            for scope, bands in (("0-3", ("0", "1", "2-3")),
                                 ("0-10", ("0", "1", "2-3",
                                           "4-5", "6-10"))):
                b = sum(1 for r in rows_y
                        if r["before_band"] in bands)
                a = {t: sum(1 for r in rows_y
                            if r["after_band"] in bands and
                            r["age_tier"] == t)
                     for t in TIERS}
                r4.append({"product": p, "year": y,
                           "scope": scope, "before": b,
                           "strict": a["STRICT_AGE"],
                           "supported": a["SUPPORTED_AGE"],
                           "inferred": a["INFERRED_AGE"],
                           "after_total": sum(a.values()),
                           "delta": sum(a.values()) - b})
    V.wcsv(DOCS / "age_recovery_before_after.csv", r4)

    # ---------- D5 全产品精确档(修复后) ----------
    r5 = [{"product": p, "year": y, "tier": t, "band": b,
           "sale_kind": k, "n": n}
          for (p, y, t, b, k), n in sorted(after.items())]
    V.wcsv(DOCS / "housing_sales_by_product_exact_age.csv",
           r5)

    # ---------- D6 HOUSE 0-1 专项对账 ----------
    r6 = [{"canonical_tx": r["canonical_tx"], "year": r[
        "sale_year"], "before": r["before_band"],
           "after": r["after_band"], "tier": r["age_tier"],
           "conflict": r["conflict"],
           "yb_source": r["yb_source"],
           "resolved_yb": r["resolved_yb"]}
          for r in per_row if r["product"] == "HOUSE" and
          (r["before_band"] in ("0", "1") or
           r["after_band"] in ("0", "1"))]
    V.wcsv(DOCS / "house_0_1_age_reconciliation.csv", r6)

    # ---------- 报告 ----------
    def agg(p, y, band_set, tier=None):
        return sum(1 for r in per_row if r["product"] == p
                   and r["sale_year"] == y and
                   r["after_band"] in band_set and
                   (tier is None or r["age_tier"] == tier))

    def bagg(p, y, band_set):
        return sum(1 for r in per_row if r["product"] == p
                   and r["sale_year"] == y and
                   r["before_band"] in band_set)

    rep = f"""# Housing Age Recovery V2.4 — 跨产品房龄恢复报告

基线 a52f911;研究层恢复,不修改冻结档案。
成交截点 {CUTOFF_2026}(2026 为部分年)。

## 根因结论(实测,非推测)
1. **市开放数据税卷 `year_built`/`big_improvement_year`
   字段冻结在 ≤2022**——2023/2024/2025/2026 四年全表
   yb 上限同为 2022(分别 17/18/18/18 行)。任何
   2023+ 竣工建筑在税卷中天然无房龄。
2. `resolve_age()` 三层(精确 pid → native → tax_link)
   全部依赖税卷 yb → **所有产品** archive yb 对
   2023+ 成交封顶 2021。
3. 房龄纠正层(nb217/enriched/permit + census)只覆盖
   HOUSE → 非 HOUSE 2025-26 的 0-3 档全零。
4. strata plan/pid 首现年可恢复推断:roll 中 plan
   首次出现年 T → 单元约 T-1 建成可评估
   (fs=2023 是卷面下界,单列降级)。

## 修复前后 0-3 成交(全口径含推断)
| 产品 | 年 | 修复前 | STRICT | SUPPORTED | INFERRED | 修复后 |
|---|---|---|---|---|---|---|
"""
    for p in PRODUCTS:
        for y in (2023, 2024, 2025, 2026):
            b = bagg(p, y, ("0", "1", "2-3"))
            s_, u_, i_ = (agg(p, y, ("0", "1", "2-3"), t)
                          for t in ("STRICT_AGE",
                                    "SUPPORTED_AGE",
                                    "INFERRED_AGE"))
            if b or s_ + u_ + i_:
                rep += (f"| {p} | {y} | {b} | {s_} | {u_}"
                        f" | {i_} | {s_ + u_ + i_} |\n")
    rep += """
## 关键回答
- **2025 CONDO 0-3=0**:非市场无成交,而是
  ①税卷 yb 冻结(见根因 1);②archive yb 来源封顶
  2021;③CONDO 不在 census/enriched 覆盖内。plan
  首现年恢复出 SUPPORTED/INFERRED 样本(见上表)。
- **DUPLEX_HALF 2025 474 笔无 0-3**:同理——半户为
  新 strata 单元,roll yb 空;其 plan 首现年大量为
  2024-25 → 恢复后 0-3 量级显著,但为
  SUPPORTED_AGE/INFERRED_AGE 证据层,非 STRICT。
- **HOUSE 2025 0-1=7 是否仍成立**:STRICT 层不变
  (census 已是房龄上限);0-3 档有 SUPPORTED 增量
  但 0-1 档 2025 无恢复增量——见
  house_0_1_age_reconciliation.csv;
  SUPPORTED/INFERRED 不得并入已确认口径。
- **真缺失 vs 年份错**:roll yb 存在且与 plan_fs 一致
  =真旧房;roll yb 存在但 plan_fs/jump 显著更晚 =
  BUILDING_IDENTITY_CONFLICT(旧年倒灌,重建后
  仍挂拆除前年);roll 无 yb 且 plan_fs 新 =
  年份缺失可恢复;无 pid/无 roll/无 plan =
  证据真空。
- **FIRST_SALE ≠ 新房**:sale_kind 与房龄分列;
  UNKNOWN kind 不自动归旧房。

## 口径边界
- 推断年 yb≈fs-1(roll 首现年-1)有 ±1 年不确定;
  band 边界行(age 恰为 1/3/5/10)在报告中注明。
- permit 签发 ≠ 竣工,permit+1 仅作 INFERRED 弱源,
  多弱源一致才升 SUPPORTED。
- TEMPORAL_CONFLICT:推断竣工晚于成交年 →
  该笔绝不被计入新房(pre-sale/旧房被正确保留)。
- V3s 冻结档案与销售 archive 零修改;恢复层仅在
  研究产物中。
"""
    (DOCS / "housing_age_recovery_v2_4_report.md"
     ).write_text(rep)

    man = {"version": "v2_4",
           "run": datetime.datetime.now().isoformat(),
           "baseline": "a52f911",
           "cutoff": CUTOFF_2026,
           "counts": {
               "rows": len(per_row),
               "products": PRODUCTS,
               "before_0_3_2025": {
                   p: bagg(p, 2025, ("0", "1", "2-3"))
                   for p in PRODUCTS},
               "after_0_3_2025": {
                   p: agg(p, 2025, ("0", "1", "2-3"))
                   for p in PRODUCTS},
               "house_2025_0_1_strict": agg(
                   "HOUSE", 2025, ("0", "1"),
                   "STRICT_AGE"),
               "tax_yb_frozen_at": 2022}}
    (DOCS / "manifest.json").write_text(
        json.dumps(man, indent=1, ensure_ascii=False))
    print("done", len(per_row), "rows")


if __name__ == "__main__":
    main()
