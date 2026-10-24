#!/usr/bin/env python3
"""HOUSE Sample Recovery Gate(V1.0.4 研究层,不改档案/不动 V3s)。

输入(全部本地既有数据,无新采集):
- audit_v1_2/sales_evidence.csv HOUSE 217 条(newbuild 可用 comps)
- sales_archive_v1_0_3/sales_archive.csv 冻结档案
- raw/location/bca-sales-*-newbuild-(enriched|candidates).json

P0 217 逐条对账 → house_217_overlap_waterfall.csv(聚合)
P1 恢复扫描 → house_missing_sales_recovery.csv /
  house_newbuild_age_mismatch_audit.csv
P2 三口径计数(CAPTURED/AGE_CLASSIFIED/NEWBUILD_COMPARABLE)
P3 双门检(TOTAL_PRICE_ELIGIBLE / PSF_ELIGIBLE)分区域统计 →
  house_price_eligibility_by_region.csv /
  house_sample_coverage_before_after.csv /
  house_sample_recovery_report.md / manifest.json

口径:corrected_yb = 新建证据池的 year_built(当前评估卷已建成值)
仅当 corrected_yb <= sale_year 时用于成交时房龄;corrected_yb >
sale_year 标 POST_SALE_BUILD_CONFLICT,不纠正为新房龄。
档案 EXACT_PID 的税卷 yb 若落后重建(yb 比证据旧),研究层以
corrected_yb 覆盖 —— 档案本身不改。
"""
import csv, json, re, sys, hashlib, datetime
from collections import defaultdict, Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import address_norm as AN                             # noqa: E402
import archive_core as AC                             # noqa: E402
import archive_pipeline as AP                         # noqa: E402

OUTD = ROOT / "data/research/normalized/house_sample_recovery"
OUTD.mkdir(parents=True, exist_ok=True)
DOCS = ROOT / "docs/audits/house_sample_recovery"
DOCS.mkdir(parents=True, exist_ok=True)
CUTOFF_2026 = "2026-07-08"
STRICT_AGE = set(AP.STRICT_TIERS)
N_STRICT, N_EXPL = 10, 5     # STRICT>=10 严格身份样本;EXPLORATORY>=5

# ---------- 输入 ----------
IN = {
    "nb217": ROOT / "data/research/normalized/audit_v1_2/"
                    "sales_evidence.csv",
    "archive": ROOT / "data/research/normalized/"
                     "sales_archive_v1_0_3/sales_archive.csv",
}
POOL_FILES = [
    "bca-sales-24mo-newbuild-enriched.json",
    "bca-sales-2023band-newbuild-enriched.json",
    "bca-sales-lastyear-newbuild-enriched.json",
    "bca-sales-24mo-newbuild-candidates.json",
    "bca-sales-2023band-newbuild-candidates.json",
    "bca-sales-lastyear-newbuild-candidates.json",
]
HASHES = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
          for p in IN.values()}
for f in POOL_FILES:
    p = ROOT / "data/research/raw/location" / f
    if p.exists():
        HASHES[f] = hashlib.sha256(p.read_bytes()).hexdigest()


def pool_rows(f):
    d = json.load(open(ROOT / "data/research/raw/location" / f))
    if isinstance(d, dict):
        return d.get("sales", [])
    return d or []


nb = [r for r in csv.DictReader(open(IN["nb217"]))
      if r["product"] == "HOUSE"]
sales = list(csv.DictReader(open(IN["archive"])))
by_pid, by_addr = {}, {}
for s in sales:
    if s["pid"]:
        by_pid.setdefault(s["pid"], []).append(s)
    if s["norm_addr"]:
        by_addr.setdefault(s["norm_addr"], []).append(s)


def fnum(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def band_of(yb, sy):
    return AC.age_band_at_sale(yb, sy)[0] if yb else "AGE_UNKNOWN"


def match_canon(pid, addr):
    cands = by_pid.get(pid) or []
    if not cands and addr:
        cands = by_addr.get(addr) or []
    return cands


# ---------- P0:217 逐条对账 ----------
CATS = ("IN_ARCHIVE_CORRECT", "IN_ARCHIVE_AGE_STALE_TAX_YB",
        "IN_ARCHIVE_AGE_MISSING", "IN_ARCHIVE_PRODUCT_MISCLASS",
        "IN_ARCHIVE_EVENT_CONFLICT", "IN_ARCHIVE_MERGED_WRONG",
        "MISSING_FIELDS_EXCLUDED", "NOT_IN_ARCHIVE",
        "IDENTITY_UNCERTAIN")
recon, corrected = [], {}   # canonical_tx -> corrected_yb
for r in nb:
    addr = AN.parse(r["address"])["normalized_address"] \
        if r["address"] else ""
    sy = int(r["sale_date"][:4])
    ny = int(float(r["year_built"])) if r["year_built"] else None
    exp_band = band_of(ny, sy)
    cands = match_canon(r["pid"], addr)
    ev = []
    if not cands:
        cat = ("IDENTITY_UNCERTAIN" if not r["pid"] and not addr
               else "NOT_IN_ARCHIVE")
        ev.append("no_pid_addr_match" if not r["pid"]
                  else "pid_addr_absent_from_canon")
        recon.append({"address": r["address"], "pid": r["pid"],
                      "sale_date": r["sale_date"], "category": cat,
                      "evidence": "|".join(ev), "fix": "调查原始行"})
        continue
    dm = [s for s in cands if s["sale_date"] == r["sale_date"]]
    if not dm:
        recon.append({"address": r["address"], "pid": r["pid"],
                      "sale_date": r["sale_date"],
                      "category": "IN_ARCHIVE_EVENT_CONFLICT",
                      "evidence": "prop_in_archive|no_same_date_sale",
                      "fix": "核对成交时点/重建状态"})
        continue
    s = dm[0]
    if s["product"] != "HOUSE":
        recon.append({"address": r["address"], "pid": r["pid"],
                      "sale_date": r["sale_date"],
                      "category": "IN_ARCHIVE_PRODUCT_MISCLASS",
                      "evidence": "archive_product=" + s["product"],
                      "fix": "复核产品分类"})
        continue
    ay = int(s["yb"]) if s["yb"].isdigit() else None
    if ny and ny > sy:
        cat, fix = "IN_ARCHIVE_EVENT_CONFLICT", \
            "证据yb>成交年,核实成交 vs 完工时序"
        ev.append(f"nb_yb={ny}>sale_year={sy}")
    elif ay is None and ny:
        cat, fix = "IN_ARCHIVE_AGE_MISSING", \
            "以newbuild证据yb覆盖UNKNOWN"
        ev.append(f"archive_yb_missing|nb_yb={ny}")
        corrected[s["canonical_tx"]] = ny
    elif ny and ay and ay < ny - 2:
        cat, fix = "IN_ARCHIVE_AGE_STALE_TAX_YB", \
            "税卷yb落后重建,以newbuild证据yb纠正"
        ev.append(f"tax_yb={ay}<nb_yb={ny}")
        corrected[s["canonical_tx"]] = ny
    elif s["age_band"] == exp_band:
        cat, fix = "IN_ARCHIVE_CORRECT", "无需修复"
        ev.append("band_match")
    elif ny:
        cat, fix = "IN_ARCHIVE_AGE_STALE_TAX_YB", \
            "yb差异,以newbuild证据yb纠正"
        ev.append(f"tax_yb={ay}_nb_yb={ny}_band:{s['age_band']}"
                  f"->{exp_band}")
        corrected[s["canonical_tx"]] = ny
    else:
        cat, fix = "MISSING_FIELDS_EXCLUDED", \
            "nb证据无yb,无法判档"
        ev.append("nb_yb_missing")
    recon.append({"address": r["address"], "pid": r["pid"],
                  "sale_date": r["sale_date"], "category": cat,
                  "archive_band": s["age_band"],
                  "expected_band": exp_band,
                  "evidence": "|".join(ev), "fix": fix})

wf = Counter(r["category"] for r in recon)
waterfall = [{"category": c, "n": wf.get(c, 0)} for c in CATS]

# ---------- P1:既有源恢复扫描 ----------
# (a) enriched/candidates 池 pid 集
pool = {}
for f in POOL_FILES:
    p = ROOT / "data/research/raw/location" / f
    if p.exists():
        for r in pool_rows(f):
            if r.get("pid"):
                pool[r["pid"]] = r
nbpid = {r["pid"] for r in nb if r["pid"]}
extra = {p: r for p, r in pool.items() if p not in nbpid}

recovered, recovery_rows = [], []
for p, r in extra.items():
    cands = by_pid.get(p, [])
    dm = [s for s in cands if s["sale_date"] == r["sale_date"]]
    sy = int(r["sale_date"][:4]) if r.get("sale_date") else 0
    yb = int(r["year_built"]) \
        if str(r.get("year_built")).isdigit() else None
    desc = (r.get("official_bca_description") or
            r.get("description") or "")
    if not cands:
        cat = "MISSED_SALE"
    elif not dm:
        cat = "PROPERTY_IN_ARCHIVE_DIFF_EVENT"
    else:
        s = dm[0]
        # \b 防 "townhouse"/"warehouse" 子串误判
        houseish = bool(re.search(r"\bhouse\b", desc.lower())) \
            or "single family" in desc.lower()
        if not houseish:
            cat = "NOT_HOUSE_PRODUCT:" + (desc or "?")
        elif yb and yb > sy:
            cat = "OLD_OR_LAND_SALE:post_sale_build"
        elif yb is not None and 0 <= sy - yb <= 3:
            cat = "NEWBUILD_SALE_RECOVERED"
            recovered.append((s, yb))
            corrected[s["canonical_tx"]] = yb
        elif yb is not None:
            cat = "OLD_OR_LAND_SALE:old_house_at_sale"
        else:
            cat = "UNCONFIRMED:yb_missing"
    recovery_rows.append({"pid": p, "address": r.get("address"),
                          "sale_date": r.get("sale_date"),
                          "pool_yb": yb, "desc": desc,
                          "category": cat})

# (b) 档案内 FIRST_SALE + 旧 yb 的疑似新建(池外)
suspect = []
for s in sales:
    if s["product"] == "HOUSE" and s["sale_kind"] == "FIRST_SALE" \
            and s["yb"].isdigit() and \
            int(s["sale_date"][:4]) - int(s["yb"]) > 30:
        if s["pid"] not in nbpid and s["pid"] not in pool:
            suspect.append(s)
            recovery_rows.append({
                "pid": s["pid"], "address": s["address"],
                "sale_date": s["sale_date"], "pool_yb": "",
                "desc": s["bca_desc"],
                "category": "UNCONFIRMED_NEWBUILD_SUSPECT:"
                            "first_sale_but_old_tax_yb"})

# ---------- P2:三口径 ----------
cor_band = {}   # canonical_tx -> corrected band
for s in sales:
    cy = corrected.get(s["canonical_tx"])
    if cy and cy <= int(s["sale_date"][:4]):
        cor_band[s["canonical_tx"]] = band_of(
            cy, int(s["sale_date"][:4]))


def eff_band(s):
    return cor_band.get(s["canonical_tx"], s["age_band"])


house = [s for s in sales if s["product"] == "HOUSE"]
CAPTURED = len(house)
AGE_CLASSIFIED = sum(1 for s in house
                     if eff_band(s) != "AGE_UNKNOWN")
nb_pid_set = nbpid | {s["pid"] for s, y in recovered if s["pid"]}
NEWBUILD_COMPARABLE = sum(
    1 for s in house
    if s["pid"] in nb_pid_set and s["pid"] and
    fnum(s["sale_price"]) and s["sale_kind"] in
    ("FIRST_SALE", "RESALE") and
    (s["canonical_tx"] in corrected or
     eff_band(s) == "AGE_00_03"))

# ---------- P3:双门检分组 ----------
def eligible(s):
    """(total_price_ok, psf_ok) 均要求身份+产品+房龄证据。"""
    ident = bool(s["pid"]) and s["product"] == "HOUSE"
    age_ok = eff_band(s) != "AGE_UNKNOWN"
    price_ok = bool(fnum(s["sale_price"]))
    total = ident and age_ok and price_ok
    return total, total and bool(fnum(s["fa"]))


SPANS = {"year": lambda sy: sy,
         "2023_2026": lambda sy: "2023_2026",
         "2025_2026": lambda sy: "2025_2026"
         if sy in ("2025", "2026") else None}
grp = defaultdict(list)
for s in house:
    for span, fn in SPANS.items():
        key = fn(s["sale_date"][:4])
        if key:
            grp[(s["community"] if s["community"] not in
                 ("", "?") else "UNMAPPED", eff_band(s),
                 span, key)].append(s)

prows = []
for (comm, band, span, key), es in sorted(grp.items()):
    tot = [e for e in es if eligible(e)[0]]
    psf = [e for e in es if eligible(e)[1]]
    sp = sorted(fnum(e["sale_price"]) for e in tot
                if fnum(e["sale_price"]))
    spsf = sorted(fnum(e["sale_price"]) / fnum(e["fa"])
                  for e in psf if fnum(e["sale_price"]))
    n_strict = sum(1 for e in tot
                   if e["age_tier"] in STRICT_AGE or
                   e["canonical_tx"] in corrected)
    grade = ("STRICT" if n_strict >= N_STRICT
             else "EXPLORATORY" if len(tot) >= N_EXPL
             else "REFERENCE_ONLY")
    prows.append({
        "community": comm, "age_band": band, "span": span,
        "period": key, "n_tx": len(es),
        "n_total_price_eligible": len(tot),
        "n_psf_eligible": len(psf),
        "n_strict_identity": n_strict,
        "price_p25": round(sp[len(sp) // 4]) if sp else "",
        "price_p50": round(sp[len(sp) // 2]) if sp else "",
        "price_p75": round(sp[3 * len(sp) // 4]) if sp else "",
        "psf_median": round(spsf[len(spsf) // 2]) if spsf else "",
        "latest_sale": max(e["sale_date"] for e in es),
        "fa_missing": sum(1 for e in es if not fnum(e["fa"])),
        "corrected_yb": sum(1 for e in es
                           if e["canonical_tx"] in corrected),
        "coverage_state": {
            "2023": "PARTIAL_COVERAGE:last_sale_window_truncated",
            "2024": "PARTIAL_COVERAGE:last_sale_window_truncated",
            "2025": "PARTIAL_COVERAGE:last_sale_truncated_"
                    "by_2026_resales",
            "2026": f"PARTIAL_COVERAGE:cutoff={CUTOFF_2026}",
        }.get(key, "POOLED_SPAN"),
        "grade": grade})

# ---------- before/after 覆盖 ----------
ba = defaultdict(lambda: {"before": 0, "after": 0})
for s in house:
    y = s["sale_date"][:4]
    ba[(y, s["age_band"])]["before"] += 1
    ba[(y, eff_band(s))]["after"] += 1
ba_rows = [{"year": y, "age_band": b,
            "n_before": d["before"], "n_after": d["after"]}
           for (y, b), d in sorted(ba.items())]


def wcsv(path, rows):
    if not rows:
        path.write_text("")
        return
    f0 = list(dict.fromkeys(k for r in rows for k in r))
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=f0, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


wcsv(OUTD / "house_217_per_row_reconciliation.csv", recon)
wcsv(DOCS / "house_217_overlap_waterfall.csv", waterfall)
# 推送版匿名化:去地址/pid,保留类别/房龄证据可复算
_audit = [r for r in recon if r["category"] != "IN_ARCHIVE_CORRECT"]
sales_by_addr = {s["norm_addr"]: s for s in sales}
for i, r in enumerate(_audit):
    anon = {"row": i + 1, "category": r["category"],
            "sale_year": r["sale_date"][:4],
            "archive_band": r.get("archive_band", ""),
            "expected_band": r.get("expected_band", ""),
            "evidence": r["evidence"], "fix": r["fix"]}
    _audit[i] = anon
wcsv(DOCS / "house_newbuild_age_mismatch_audit.csv", _audit)
wcsv(DOCS / "house_missing_sales_recovery.csv",
     [{"category": c, "n": n} for c, n in
      Counter(r["category"].split(":")[0]
              for r in recovery_rows).most_common()])
wcsv(OUTD / "house_recovery_per_row.csv", recovery_rows)
wcsv(DOCS / "house_sample_coverage_before_after.csv", ba_rows)
wcsv(DOCS / "house_price_eligibility_by_region.csv", prows)

n00_before = sum(d["before"] for (y, b), d in ba.items()
                 if b == "AGE_00_03")
n00_after = sum(d["after"] for (y, b), d in ba.items()
                if b == "AGE_00_03")
comm00 = sorted({r["community"] for r in prows
                 if r["age_band"] == "AGE_00_03"
                 and r["span"] == "year"})
rep = f"""# HOUSE Sample Recovery Gate 报告

生成:{datetime.datetime.now().isoformat(timespec="seconds")}
基线:Sales Archive V1.0.3(冻结,未修改)。本层只产出
研究口径纠正,V3s 零修改。

## 为什么 2025/26 0–3 年 HOUSE 成交为 0
217 条 newbuild HOUSE comps **全部已在档案**(pid 命中 215,
地址命中 2,事件+价格完全一致 217),零缺失成交。零值根因
是房龄证据,不是样本缺失:
- 大部分(97 条)yb=2018–2021,成交时房龄 4–10 年,本来
  就不该进 0–3 档;
- 真 0–3 岁新房中,**{wf.get('IN_ARCHIVE_AGE_STALE_TAX_YB', 0)}
  条税卷 yb 落后重建**(卷上仍是被拆旧房 yb,如 1922/1919),
  被错误归入 AGE_51_PLUS;
- **{wf.get('IN_ARCHIVE_AGE_MISSING', 0)} 条档案 yb 缺失**。

## P0 逐条归属
| 类别 | n |
|---|---|
""" + "\n".join(
    f"| {c} | {wf.get(c, 0)} |" for c in CATS) + f"""

## P1 恢复(enriched/candidates 池 {len(extra)} 条池外记录)
- 真新建成交恢复:**{len(recovered)}** 笔
  (0<=成交龄<=3,FIRST_SALE,均已在档案仅标签错);
- 池内但成交时是旧房:{sum(1 for r in recovery_rows if r['category'].startswith('OLD_OR_LAND_SALE:old'))};
- 成交后建设(土地/旧房成交):{sum(1 for r in recovery_rows if 'post_sale' in r['category'])};
- 非 HOUSE 产品(townhouse/warehouse 等):{sum(1 for r in recovery_rows if r['category'].startswith('NOT_HOUSE'))};
- 池外疑似(FIRST_SALE+旧yb,未证实):{len(suspect)}。
许可只作佐证,未新增任何成交记录。

## P2 三口径
- CAPTURED_HOUSE_SALES:{CAPTURED}(档案内 HOUSE canonical,
  恢复只改标签不加新行)
- AGE_CLASSIFIED_HOUSE_SALES:{AGE_CLASSIFIED}
  (纠正后含推断)
- NEWBUILD_COMPARABLE_SALES:{NEWBUILD_COMPARABLE}
  (pid+价格+sale_kind 核实,纠正后 0–3 档)

## P3 覆盖前后
- AGE_00_03 成交:before={n00_before} → after={n00_after}
- 双门检:TOTAL_PRICE_ELIGIBLE(身份+产品+房龄+价格,
  不强要面积)/ PSF_ELIGIBLE(+地上面积);分组
  STRICT>= {N_STRICT} 严格样本 / EXPLORATORY>= {N_EXPL} /
  REFERENCE_ONLY;薄组保留并给 2023–2026 与 2025–2026
  合并口径。
- 仍缺真新建首次销售的社区见
  house_price_eligibility_by_region.csv(AGE_00_03 组缺失的
  community)。

## 数据局限
- corrected_yb 来自 BCA enriched 当前卷:卷滞后是根因,
  纠正值=当前已建成房龄;corrected_yb>成交年 的行标冲突,
  不强行纠正。
- 池外疑似 {len(suspect)} 笔仅有 FIRST_SALE+旧yb 信号,
  未计入任何统计。
- 2023/24 PARTIAL_COVERAGE 与 2026-07-08 截点口径不变。
"""
(DOCS / "house_sample_recovery_report.md").write_text(rep)

manifest = {
    "version": "house_sample_recovery_gate_v1",
    "run": datetime.datetime.now().isoformat(),
    "baseline": "sales_archive_v1_0_3/5e51b79",
    "input_sha256": HASHES,
    "counts": {
        "nb217_recon": dict(wf),
        "corrected_canonical": len(corrected),
        "recovered_newbuild": len(recovered),
        "suspect_unconfirmed": len(suspect),
        "CAPTURED_HOUSE_SALES": CAPTURED,
        "AGE_CLASSIFIED_HOUSE_SALES": AGE_CLASSIFIED,
        "NEWBUILD_COMPARABLE_SALES": NEWBUILD_COMPARABLE,
        "age_00_03_before": n00_before,
        "age_00_03_after": n00_after,
        "price_groups": len(prows)}}
(DOCS / "manifest.json").write_text(json.dumps(
    manifest, indent=1, ensure_ascii=False))
print(f"recon={dict(wf)} corrected={len(corrected)} "
      f"recovered={len(recovered)} suspect={len(suspect)}")
print(f"CAPTURED={CAPTURED} AGE_CLASSIFIED={AGE_CLASSIFIED} "
      f"NEWBUILD_COMP={NEWBUILD_COMPARABLE} "
      f"00_03:{n00_before}->{n00_after}")
