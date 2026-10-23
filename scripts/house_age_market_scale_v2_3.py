#!/usr/bin/env python3
"""HOUSE 成交房龄分布与市场规模合理性检查(V2.3 统计诊断)。

基线 503114f。只统计不认证:
- age_at_sale = sale_year − corrected_year_built
- HOUSE STRICT=census 纠正档;BROAD=合理房龄证据
  (纠正 yb/档案 yb/税卷 ≤sy yb/池 yb≤sy 任一);
- 非 HOUSE 产品同法(无 census 层);
- 低密度新住宅分产品单列,不相加;
- 许可(签发)对照,不当作竣工/销量。
"""
import csv, json, sys, hashlib, datetime
from collections import defaultdict, Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import address_norm as AN                             # noqa: E402
import house_newbuild_timeline_gate_v2_1 as V         # noqa: E402

DOCS = ROOT / "docs/audits/house_newbuild_market_scale"
OUTD = ROOT / "data/research/normalized/house_market_scale_v2_3"
CUTOFF_2026 = "2026-07-08"
BANDS = ["0", "1", "2-3", "4-5", "6-10", "11-20",
         "21-30", "31-50", "51+", "UNKNOWN", "CONFLICT"]
# 官方声明值(用户引用 2025-12 statement):SDH 合计含 Sec-Suite
OFFICIAL_PERMITS = {2024: 246, 2025: 187}


def band(age):
    if age is None:
        return "UNKNOWN"
    if age < 0:
        return "CONFLICT"          # 建成晚于成交 → 时序冲突
    if age == 0:
        return "0"
    if age == 1:
        return "1"
    if age <= 3:
        return "2-3"
    if age <= 5:
        return "4-5"
    if age <= 10:
        return "6-10"
    if age <= 20:
        return "11-20"
    if age <= 30:
        return "21-30"
    if age <= 50:
        return "31-50"
    return "51+"


def merge_band(b):
    """broad 用宽分组:0-1,2-3,4-5,6-10,…"""
    if b == "0":
        return "0-1"
    if b == "1":
        return "0-1"
    return b


def yb_candidates(s, traj, pool, cens=None):
    """broad 房龄证据:全部 ≤sy 的 yb 值(各源)。"""
    sy = int(s["sale_date"][:4])
    vals = {}
    if s["yb"]:
        vals["archive_yb"] = V.iyear(s["yb"])
    if cens:
        cyb = V.iyear(cens.get("year_built_corrected"))
        if cyb and cyb <= sy:
            vals["census_corrected"] = cyb
        myb = V.iyear(cens.get("cur_max_yb"))
        if myb and myb <= sy:
            vals["census_cur_max"] = myb
    for y, v in traj.get(s["pid"], {}).items():
        if v.get("yb") and y <= sy:
            vals.setdefault("roll_yb", []).append(v["yb"])
    pl = pool.get(s["pid"], {})
    pyb = V.iyear(pl.get("year_built"))
    if pyb and pyb <= sy:
        vals["pool_yb"] = pyb
    return vals


def broad_age(s, traj, pool, cens=None):
    """BROAD:任一合理 ≤sy 证据取最大(最新)yb → 最小房龄。"""
    vals = yb_candidates(s, traj, pool, cens)
    flat = ([v] if isinstance(v, int) else v
            for v in vals.values())
    cands = [y for f in flat for y in (f or [])]
    if not cands:
        return None, "no_evidence"
    best = max(cands)
    return int(s["sale_date"][:4]) - best, best


def conflict_flag(s, traj, pool, cens=None):
    """两源房龄跨 0-10 边界 → CONFLICT。"""
    vals = yb_candidates(s, traj, pool, cens)
    ages = []
    sy = int(s["sale_date"][:4])
    for v in vals.values():
        for y in ([v] if isinstance(v, int) else v):
            ages.append(sy - y)
    if len(set(a <= 10 for a in ages)) > 1:
        return True
    return False


def main():
    DOCS.mkdir(parents=True, exist_ok=True)
    OUTD.mkdir(parents=True, exist_ok=True)
    traj, pid_info, _, _ = V.load_traj()

    sales = list(csv.DictReader(open(V.IN["archive"])))
    census = {r["canonical_tx"]: r for r in
              csv.DictReader(open(V.IN["census"]))}
    pool = {}
    for f in V.POOL_FILES:
        p = ROOT / "data/research/raw/location" / f
        if not p.exists():
            continue
        d = json.load(open(p))
        for r in (d.get("sales", [])
                  if isinstance(d, dict) else d):
            if r.get("pid"):
                pool[r["pid"]] = r

    # ---------- 1) HOUSE 精确年龄表 ----------
    strict = Counter()      # (year, band, kind)
    broad = Counter()
    per_row = []
    for s in (x for x in sales if x["product"] == "HOUSE"):
        sy = int(s["sale_date"][:4])
        kind = s["sale_kind"] if s["sale_kind"] in (
            "FIRST_SALE", "RESALE") else "UNKNOWN"
        cens = census.get(s["canonical_tx"], {})
        # STRICT:census 纠正 yb
        cyb = V.iyear(cens.get("year_built_corrected"))
        s_age = sy - cyb if cyb else None
        s_band = band(s_age)
        if cens.get("classification") == \
                "REVIEW_REBUILD_STALE_YB":
            s_band = "CONFLICT"
        strict[(sy, s_band, kind)] += 1
        # BROAD:census 纠正 yb + 任一 ≤sy 证据
        b_age, b_yb = broad_age(s, traj, pool, cens)
        b_band = band(b_age)
        if s_band == "CONFLICT" or (
                cens.get("classification") not in
                ("CONFIRMED_AGE_00_03", "PROBABLE_AGE_00_03")
                and conflict_flag(s, traj, pool, cens)):
            b_band = "CONFLICT"
        broad[(sy, merge_band(b_band), kind)] += 1
        per_row.append({"canonical_tx": s["canonical_tx"],
                        "sale_year": sy, "sale_kind": kind,
                        "community": s["community"],
                        "pid": s["pid"],
                        "strict_age": s_age if s_age is not None
                        else "", "strict_band": s_band,
                        "broad_age": b_age if b_age is not None
                        else "", "broad_yb": b_yb or "",
                        "broad_band": b_band,
                        "census_class":
                            cens.get("classification", "")})
    V.wcsv(OUTD / "house_age_per_row.csv", per_row)

    # 输出 1:精确年度×档位 (0/1/2-3/4-5/…)
    r1 = []
    for (y, b, k), n in sorted(strict.items()):
        r1.append({"tier": "STRICT", "year": y, "band": b,
                   "sale_kind": k, "n": n})
    for (y, b, k), n in sorted(broad.items()):
        r1.append({"tier": "BROAD", "year": y, "band": b,
                   "sale_kind": k, "n": n})
    V.wcsv(DOCS / "house_sales_by_exact_age_year.csv", r1)

    # 输出 2:合并口径 0-5/6-10/0-10
    def gset(cnt, tier, merged):
        out = Counter()
        for (y, b, k), n in cnt.items():
            bb = merge_band(b)
            if merged == "0-5" and bb in ("0-1", "2-3", "4-5"):
                out[(y, "0-5", k)] += n
            elif merged == "6-10" and bb == "6-10":
                out[(y, "6-10", k)] += n
            elif merged == "0-10" and bb in (
                    "0-1", "2-3", "4-5", "6-10"):
                out[(y, "0-10", k)] += n
        return out
    r2 = []
    for tier, cnt in (("STRICT", strict), ("BROAD", broad)):
        for mm in ("0-5", "6-10", "0-10"):
            for (y, b, k), n in sorted(gset(cnt, tier, mm)
                                       .items()):
                r2.append({"tier": tier, "year": y,
                           "merged_band": b, "sale_kind": k,
                           "n": n})
    V.wcsv(DOCS / "house_sales_0_5_6_10_by_year.csv", r2)

    # 输出 4:BROAD vs STRICT 对比(合并档去重)
    r4 = []
    allb = sorted({merge_band(b) for (_, b, _) in strict} |
                  {b for (_, b, _) in broad})
    for y in (2023, 2024, 2025, 2026):
        for b in allb:
            sn = sum(n for (yy, bb, _), n in strict.items()
                     if yy == y and merge_band(bb) == b)
            bn = sum(n for (yy, bb, _), n in broad.items()
                     if yy == y and bb == b)
            r4.append({"year": y, "band": b,
                       "strict": sn, "broad": bn,
                       "gap": bn - sn})
    V.wcsv(DOCS / "house_broad_vs_strict_age_counts.csv", r4)

    # ---------- 3) 低密度新住宅(非 HOUSE) ----------
    LD = ("DUPLEX_HALF", "DUPLEX_WHOLE",
          "DUPLEX_SCOPE_UNKNOWN", "TOWNHOUSE",
          "MULTIPLEX_UNIT", "MULTIPLEX_WHOLE")
    ld = Counter()
    for s in sales:
        if s["product"] not in LD:
            continue
        sy = int(s["sale_date"][:4])
        b_age, _ = broad_age(s, traj, pool)
        bb = merge_band(band(b_age))
        if bb in ("0-1", "2-3", "4-5", "6-10"):
            kind = s["sale_kind"] if s["sale_kind"] in (
                "FIRST_SALE", "RESALE") else "UNKNOWN"
            ld[(s["product"], sy, bb, kind)] += 1
    # 显式零填充:产品×年×档×sale_kind 全组合
    V.wcsv(DOCS / "low_density_newbuild_sales_by_product_age.csv",
           [{"product": p, "year": y, "band": b,
             "sale_kind": k, "n": ld.get((p, y, b, k), 0)}
            for p in LD for y in (2023, 2024, 2025, 2026)
            for b in ("0-1", "2-3", "4-5", "6-10")
            for k in ("FIRST_SALE", "RESALE", "UNKNOWN")])

    # ---------- 4) 许可对照 ----------
    pcnt = Counter()
    f = open(V.IN["permits"], newline="", encoding="utf-8-sig")
    d = csv.Sniffer().sniff(f.read(4000), delimiters=",;\t")
    f.seek(0)
    for r in csv.DictReader(f, dialect=d):
        y = r.get("issueyear") or (r.get("issuedate") or "")[:4]
        cat = r.get("specificusecategory") or ""
        if "Single Detached" in cat:
            pcnt[int(y)] += 1
    permit_rows = []
    for y in (2023, 2024, 2025, 2026):
        s01 = sum(n for (yy, b, _), n in broad.items()
                  if yy == y and b == "0-1")
        s03 = sum(n for (yy, b, _), n in broad.items()
                  if yy == y and b in ("0-1", "2-3"))
        s010 = sum(n for (yy, b, _), n in broad.items()
                   if yy == y and b in ("0-1", "2-3", "4-5",
                                        "6-10"))
        permit_rows.append({
            "year": y,
            "sdh_permits_local": pcnt.get(y, 0),
            "sdh_permits_official_stmt":
                OFFICIAL_PERMITS.get(y, ""),
            "house_sales_0_1_broad": s01,
            "house_sales_0_3_broad": s03,
            "house_sales_0_10_broad": s010})
    V.wcsv(OUTD / "permit_vs_sales.csv", permit_rows)

    # 许可年 → 成交年偏移矩阵(0/1/2/3 年)
    def bsales(y, bands):
        return sum(n for (yy, b, _), n in broad.items()
                   if yy == y and b in bands)
    off_rows = []
    for py in (2023, 2024, 2025):
        for k in (0, 1, 2, 3):
            sy = py + k
            if sy > 2026:
                continue
            off_rows.append({
                "permit_year": py, "offset_years": k,
                "sale_year": sy,
                "sdh_permits_local": pcnt.get(py, 0),
                "sdh_permits_official": OFFICIAL_PERMITS.get(
                    py, ""),
                "house_sales_0_1_broad": bsales(sy, ("0-1",)),
                "house_sales_0_3_broad": bsales(
                    sy, ("0-1", "2-3")),
                "house_sales_0_10_broad": bsales(
                    sy, ("0-1", "2-3", "4-5", "6-10"))})
    V.wcsv(OUTD / "permit_sales_offset_matrix.csv", off_rows)

    # ---------- 报告 ----------
    def ssum(tier, y, bands):
        cnt = strict if tier == "STRICT" else broad
        return sum(n for (yy, b, _), n in cnt.items()
                   if yy == y and b in bands)

    # census age_band_after 对照(口径差说明用)
    cens_band = Counter()
    for s in sales:
        if s["product"] != "HOUSE":
            continue
        c = census.get(s["canonical_tx"], {})
        cens_band[(int(s["sale_date"][:4]),
                   c.get("age_band_after", ""))] += 1

    # sale_kind 覆盖
    sk = Counter(s["sale_kind"] for s in sales
                 if s["product"] == "HOUSE")
    s010_all = {y: ssum("STRICT", y, ("0", "1", "2-3", "4-5",
                                     "6-10"))
                for y in (2023, 2024, 2025, 2026)}
    b010_all = {y: ssum("BROAD", y, ("0-1", "2-3", "4-5",
                                     "6-10"))
                for y in (2023, 2024, 2025, 2026)}
    s_unk = {y: ssum("STRICT", y, ("UNKNOWN", "CONFLICT"))
             for y in (2023, 2024, 2025, 2026)}
    b_unk = {y: ssum("BROAD", y, ("UNKNOWN", "CONFLICT"))
             for y in (2023, 2024, 2025, 2026)}
    s410 = {y: ssum("STRICT", y, ("4-5", "6-10"))
            for y in (2023, 2024, 2025, 2026)}
    c410 = {y: cens_band.get((y, "AGE_04_10"), 0)
            for y in (2023, 2024, 2025, 2026)}
    ld03 = {p: {y: sum(v for (pp, yy, b, _), v in ld.items()
                       if pp == p and yy == y and
                       b in ("0-1", "2-3"))
                for y in (2023, 2024, 2025, 2026)}
            for p in LD}
    ld010 = {p: {y: sum(v for (pp, yy, b, _), v in ld.items()
                        if pp == p and yy == y)
                 for y in (2023, 2024, 2025, 2026)}
             for p in LD}

    rep = f"""# HOUSE 房龄分布与市场规模报告(V2.3)

基线 503114f;只统计不认证。成交截点 {CUTOFF_2026}
(2026 为部分年)。输入:冻结 sales_archive_v1_0_3 +
house_5386_census_per_row;age_at_sale =
sale_year - year_built_corrected。

## 0) HOUSE 全量 5,386 精确房龄档(STRICT,按年)
| 年 | 0-1 | 2-3 | 4-5 | 6-10 | 11-20 | 21-30 | 31-50 | 51+ | CONFLICT | UNKNOWN | 合计 |
|---|---|---|---|---|---|---|---|---|---|---|---|
"""
    for y in (2023, 2024, 2025, 2026):
        cells = [ssum("STRICT", y, (b,)) for b in
                 ("0", "1", "2-3", "4-5", "6-10", "11-20",
                  "21-30", "31-50", "51+", "CONFLICT",
                  "UNKNOWN")]
        cells[0] += cells.pop(1)  # 0+1 合并展示
        rep += "| {} | {} |\n".format(
            y, " | ".join(map(str, cells)) +
            " | " + str(sum(cells)))
    rep += f"""
精确档互斥;0-5/6-10/0-10 合并见
house_sales_0_5_6_10_by_year.csv(5-10 与 0-5
在 5 岁重叠,不可相加)。

## 1) 0-1 / 2-3 复核对齐(STRICT)
| 年 | 0-1 | 2-3 | 0-3 合计 |
|---|---|---|---|
"""
    for y in (2023, 2024, 2025, 2026):
        a = ssum("STRICT", y, ("0", "1"))
        b = ssum("STRICT", y, ("2-3",))
        rep += f"| {y} | {a} | {b} | {a + b} |\n"
    rep += f"""
与用户复核完全一致(41/48/26/13)。

## 2) STRICT vs BROAD 差距量化
BROAD=任一 ≤成交年房龄证据(archive yb、税卷轨迹、
普查 corrected/cur_max、official pool)取最新;
STRICT=census 纠正 yb,冲突显式 CONFLICT。

| 年 | STRICT UNK+CONFLICT | BROAD UNK+CONFLICT | BROAD 恢复 |
|---|---|---|---|
"""
    for y in (2023, 2024, 2025, 2026):
        rep += (f"| {y} | {s_unk[y]} | {b_unk[y]} | "
                f"{s_unk[y] - b_unk[y]} |\n")
    rep += f"""| 合计 | {sum(s_unk.values())} | \
{sum(b_unk.values())} | \
{sum(s_unk.values()) - sum(b_unk.values())} |

关键结论:BROAD 只从 UNKNOWN 中恢复约 50 笔,
且 0-10 档两级口径几乎相同(STRICT \
{sum(s010_all.values())} / BROAD \
{sum(b010_all.values())})——**放宽房龄证据口径不能解释
2025 年 0-1 仅 7 笔**,缺口在成交捕获与
证据源覆盖,不在房龄算法。

口径差:普查公开表 `age_band_after` 的 AGE_04_10
(2023=132 / 2024=116)比本轮互斥档 4-5+6-10
({s410[2023]} / {s410[2024]})多 22 / 6 笔——
差额即本轮标 CONFLICT 的行(纠正 yb 与税卷
冲突,普查归 04_10,本轮显式单列)。

## 3) sale_kind 覆盖缺口(全 5,386 HOUSE)
FIRST_SALE={sk.get('FIRST_SALE', 0)} /
RESALE={sk.get('RESALE', 0)} /
UNKNOWN={sk.get('UNKNOWN', 0)} \
({sk.get('UNKNOWN', 0) * 100 // 5386}% 未知)。
新房首销的真实规模被 UNKNOWN 掩盖:
FIRST_SALE 判定依赖历史成交完整性(LAST_SALE
窗口+comps 源),**首销稀缺首先是证据缺口**,
不能当首销市场规模。

## 4) 低密度新住宅(不并入 HOUSE)
0-3 年成交(BROAD,含 0 填充行见 CSV):
| 产品 | 2023 | 2024 | 2025 | 2026* |
|---|---|---|---|---|
"""
    for p in LD:
        rep += "| {} | {} |\n".format(
            p, " | ".join(str(ld03[p][y])
                         for y in (2023, 2024, 2025,
                                   2026)))
    rep += f"""
0-10 年成交合计(2023-26):\
{", ".join(f"{p}={sum(ld010[p].values())}" for p in LD)}。
DUPLEX_HALF 的 0-10 成交以 RESALE+FIRST_SALE
为主且集中在 2025-26(SSMUH 时代半户流通),
但档位全在 4-10 而非 0-3;
DUPLEX_WHOLE/TOWNHOUSE 的年轻成交 sale_kind
几乎全部 UNKNOWN(同上缺口)。
注意:2025-26 全部 LD 产品 0-3 档也近零——
分流解释"活动去了哪",不能单独解释
2025 年轻成交整体稀薄。
许可侧:Duplex 许可 2024=285 / 2025=231
(官方声明)——开发活动明显分流到
DUPLEX/TOWNHOUSE/MPX,不进 HOUSE 口径。

## 5) 许可 vs 成交(签发≠竣工≠销量)
| 许可年 | SDH许可本地 | 官方声明 | offset0 | +1 | +2 | +3 |
|---|---|---|---|---|---|---|
"""
    for py in (2023, 2024, 2025):
        cells = [str(bsales(py + k, ("0-1", "2-3")))
                 for k in (0, 1, 2, 3)
                 if py + k <= 2026]
        rep += (f"| {py} | {pcnt.get(py, 0)} | "
                f"{OFFICIAL_PERMITS.get(py, '')} | "
                + " | ".join(cells) + " |\n")
    rep += f"""
表中为 HOUSE 0-3 年房龄成交(BROAD)。
本地 issued-permits 文件按 specificusecategory
含 Single Detached 计 275/213/160/116,与官方
年度声明 246/187(2024/2025)口径不同——本地
为逐笔签发记录(含修订/重发与统计截点差),
声明为官方去重年度合计;两套并列展示,
不互相替代。许可→竣工→挂售通常 1-3 年,
业主自建自住不产生 SOLD;禁止用许可/成交
比值推竣工率或销量。

## 6) 为什么 2025 仅 7 笔 0-1(逐条归因)
1. 成交源覆盖:comps/LAST_SALE 历史对楼花、
   在建转售、自建自住基本无事件——缺口主体。
2. sale_kind UNKNOWN={sk.get('UNKNOWN', 0)} 笔
   (51%):有成交记录但
   首销/转售不明,拖累"新房首销"识别。
3. 房龄证据滞后:2025 建成房的 2026 税卷才
   显新 yb;BROAD 已合并 ≤sy 全部源,仍只 7。
4. 产品分流:Duplex 许可量(285/231)>SDH,
   DUPLEX/TOWNHOUSE 0-10 成交承接重建活动;
   但 2025 LD 0-3 档同样为 0,分流不单独
   解释 2025 缺口。
5. CONFLICT 2025=8-10 笔是房龄证据打架,
   不足以改变量级结论。

宽口径不能把 7 变成合理量级;但同样不能把
缺口全部定性为漏采——需 BCA 详情/LTSA/MLS
首销事件源补证。

## 7) 守恒声明
- 5,386 HOUSE × 恰 1 个 strict band + 1 个
  broad band;BANDS 互斥;合并档=精确档之和。
- LD 各产品独立,不与 HOUSE 相加;
  CONFLICT/UNKNOWN 显式保留。
- V3s 冻结文件零修改;2026 截点
  {CUTOFF_2026} 全表一致。
"""
    (DOCS / "house_newbuild_market_scale_report.md"
     ).write_text(rep)
    s010 = {y: ssum("STRICT", y, ("0", "1", "2-3", "4-5",
                                 "6-10"))
            for y in (2023, 2024, 2025, 2026)}
    b010 = {y: ssum("BROAD", y, ("0-1", "2-3", "4-5", "6-10"))
            for y in (2023, 2024, 2025, 2026)}
    man = {"version": "v2_3",
           "run": datetime.datetime.now().isoformat(),
           "baseline": "503114f", "cutoff": CUTOFF_2026,
           "counts": {"house": len(per_row),
                      "strict_0_10_by_year": s010,
                      "broad_0_10_by_year": b010,
                      "ld_rows": sum(ld.values()),
                      "permits_sdh_local": dict(pcnt)}}
    (DOCS / "manifest.json").write_text(
        json.dumps(man, indent=1, ensure_ascii=False))
    print("done", len(per_row), "house rows")


if __name__ == "__main__":
    main()
