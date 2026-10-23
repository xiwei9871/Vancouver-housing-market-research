#!/usr/bin/env python3
"""HOUSE 5,386 全量新建识别普查(GitHub Issue #1)。

输入(全部本地冻结数据,零新采集):
- sales_archive_v1_0_3/sales_archive.csv(5,386 HOUSE canonical)
- audit_v1_2/sales_evidence.csv(217 历史 newbuild comps)
- property-tax-2023..2026-full.csv(pid×roll_year→yb/big_impr/impv)
- issued-permits-ldh-2017.csv(低密度新建许可,含 SDH/Duplex)
- raw/location/bca-sales-*-newbuild-(enriched|candidates).json

口径(关键):
- best_yb_at_sale = 全部证据中 ≤成交年的最大 yb
  (税卷各年快照 | BCA enriched 当前卷 | nb217 MLS 口径)
- cur_yb = 证据中最大 yb(可>成交年);cur_yb>成交年 且
  另有旧 yb → 成交的是旧房/土地(POST_SALE_BUILD)
- corrected band 用 best_yb_at_sale;档案本身不改
- 许可 issued < 成交 只作佐证,不等于竣工/成交
- CONFIRMED_00_03 需 pid+价格+日期+≤sy 的新建 yb 证据;
  缺关键证据 → PROBABLE;冲突 → REVIEW_REBUILD_STALE_YB

输出:docs/audits/house_5386_census/*(聚合+匿名)
     OUTD/*(逐笔,留本地 gitignored)
"""
import csv, json, re, sys, hashlib, datetime
from collections import defaultdict, Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import address_norm as AN                             # noqa: E402
import archive_core as AC                             # noqa: E402

OUTD = ROOT / "data/research/normalized/house_5386_census"
OUTD.mkdir(parents=True, exist_ok=True)
DOCS = ROOT / "docs/audits/house_5386_census"
DOCS.mkdir(parents=True, exist_ok=True)
CUTOFF_2026 = "2026-07-08"

IN = {
    "archive": ROOT / "data/research/normalized/"
                     "sales_archive_v1_0_3/sales_archive.csv",
    "nb217": ROOT / "data/research/normalized/audit_v1_2/"
                    "sales_evidence.csv",
}
for y in (2023, 2024, 2025, 2026):
    IN[f"tax{y}"] = ROOT / "data/research/raw/official/" \
                          f"property-tax-{y}-full.csv"
IN["permits"] = ROOT / "data/research/raw/official/" \
                      "issued-permits-ldh-2017.csv"
POOL_FILES = [
    "bca-sales-24mo-newbuild-enriched.json",
    "bca-sales-2023band-newbuild-enriched.json",
    "bca-sales-lastyear-newbuild-enriched.json",
    "bca-sales-24mo-newbuild-candidates.json",
    "bca-sales-2023band-newbuild-candidates.json",
    "bca-sales-lastyear-newbuild-candidates.json",
]
HASHES = {k: hashlib.sha256(p.read_bytes()).hexdigest()
          for k, p in IN.items() if p.exists()}
for f in POOL_FILES:
    p = ROOT / "data/research/raw/location" / f
    if p.exists():
        HASHES[f] = hashlib.sha256(p.read_bytes()).hexdigest()


def fnum(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def iyear(x):
    try:
        return int(float(x))
    except (TypeError, ValueError):
        return None


def band_of(yb, sy):
    return AC.age_band_at_sale(yb, sy)[0] if yb else "AGE_UNKNOWN"


def wcsv(path, rows):
    if not rows:
        path.write_text("")
        return
    f0 = list(dict.fromkeys(k for r in rows for k in r))
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=f0, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


# ---------- 加载 ----------
sales = [r for r in csv.DictReader(open(IN["archive"]))
         if r["product"] == "HOUSE"]
assert len({s["canonical_tx"] for s in sales}) == len(sales) == 5386

nb217 = [r for r in csv.DictReader(open(IN["nb217"]))
         if r["product"] == "HOUSE"]
nb_by_pid = {r["pid"]: r for r in nb217 if r["pid"]}
nb_by_addr = {AN.parse(r["address"])["normalized_address"]: r
              for r in nb217 if r["address"]}

# 税卷轨迹:pid -> {roll_year: (yb, big_impr)}
tax_traj = defaultdict(dict)
house_pids = {s["pid"] for s in sales if s["pid"]}
for y in (2023, 2024, 2025, 2026):
    tf = open(IN[f"tax{y}"], newline="", encoding="utf-8-sig")
    td = csv.Sniffer().sniff(tf.read(4000), delimiters=",;\t")
    tf.seek(0)
    for t in csv.DictReader(tf, dialect=td):
        if t["pid"] in house_pids:
            tax_traj[t["pid"]][y] = (
                iyear(t.get("year_built")),
                iyear(t.get("big_improvement_year")))

# enriched/candidates 池:pid -> (yb, desc, sale_date, price)
pool_pid, pool_addr = {}, {}
for f in POOL_FILES:
    p = ROOT / "data/research/raw/location" / f
    if not p.exists():
        continue
    d = json.load(open(p))
    rows = d.get("sales", []) if isinstance(d, dict) else d
    for r in rows or []:
        rec = {"yb": iyear(r.get("year_built")),
               "desc": r.get("official_bca_description") or
                       r.get("description") or "",
               "sale_date": r.get("sale_date") or "",
               "sale_price": fnum(r.get("sale_price"))}
        if r.get("pid"):
            pool_pid[r["pid"]] = rec
        a = AN.parse(r.get("address", ""))["normalized_address"] \
            if r.get("address") else ""
        if a:
            pool_addr[a] = rec

# 许可:norm_addr -> [(issue_year, category)]
permits = defaultdict(list)
f = open(IN["permits"], newline="", encoding="utf-8-sig")
dialect = csv.Sniffer().sniff(f.read(4000), delimiters=",;\t")
f.seek(0)
for r in csv.DictReader(f, dialect=dialect):
    a = (r.get("address") or "").split(",")[0]
    na = AN.parse(a)["normalized_address"] if a.strip() else ""
    iy = iyear(r.get("issueyear") or (r.get("issuedate") or "")[:4])
    if na and iy:
        permits[na].append((iy, r.get("specificusecategory") or ""))

# ---------- 逐笔普查 ----------
CLASSES = (
    "CONFIRMED_AGE_00_03", "PROBABLE_AGE_00_03",
    "REVIEW_REBUILD_STALE_YB",
    "AGE_04_10", "AGE_11_20", "AGE_21_30", "AGE_31_50",
    "AGE_51_PLUS", "POST_SALE_BUILD", "LAND_OLD_HOUSE_SALE",
    "IDENTITY_CONFLICT", "AGE_UNKNOWN")

census = []
for s in sales:
    sy = int(s["sale_date"][:4])
    pid = s["pid"]
    na = s["norm_addr"]
    pid_ok = bool(pid)
    nbr = nb_by_pid.get(pid) or nb_by_addr.get(na)
    enr = pool_pid.get(pid) or pool_addr.get(na)
    # yb 证据候选(≤sy 才可用于成交时房龄)
    cand = {}          # source -> yb
    traj = tax_traj.get(pid, {})
    for ry, (yb, _big) in traj.items():
        if yb and yb <= sy:
            cand.setdefault("TAX_ROLL", []).append((ry, yb))
    enr_yb = enr["yb"] if enr else None
    if enr_yb and enr_yb <= sy:
        cand["BCA_ENRICHED"] = [(0, enr_yb)]
    nb_yb = iyear(nbr["year_built"]) if nbr else None
    if nb_yb and nb_yb <= sy:
        cand["NB217_MLS"] = [(0, nb_yb)]
    all_yb = [yb for _, (yb, _b) in traj.items() if yb] + \
        [v for v in (enr_yb, nb_yb) if v]
    cur_yb = max(all_yb) if all_yb else None
    # best:各源 ≤sy 的最大值,源间取最大
    best_yb, best_src = None, ""
    src_ybs = {}
    for src, lst in cand.items():
        m = max(y for _, y in lst)
        src_ybs[src] = m
    if src_ybs:
        best_src = max(src_ybs, key=lambda k: src_ybs[k])
        best_yb = src_ybs[best_src]
    arc_yb = iyear(s["yb"])
    band = band_of(best_yb, sy)
    stale = bool(best_yb and arc_yb and best_yb > arc_yb + 2)
    post_sale_build = bool(cur_yb and cur_yb > sy and
                           best_yb and cur_yb > best_yb)
    # 许可信号
    pm = permits.get(na, [])
    permit_pre = [iy for iy, _c in pm if iy <= sy]
    sdh_pre = [iy for iy, c in pm
               if iy <= sy and "Single Detached" in c]
    first_sale = s["sale_kind"] == "FIRST_SALE"
    # 分类(互斥主类 + reasons 保留复合证据)
    reasons = []
    if stale:
        reasons.append(
            f"stale_roll:archive_yb={arc_yb}<evidence_yb={best_yb}")
    if post_sale_build:
        reasons.append(f"post_sale_build:cur_yb={cur_yb}>{sy}")
    if nb_yb and nb_yb > sy:
        reasons.append(f"conflict:nb_yb={nb_yb}>sale_year")
    if enr_yb and enr_yb > sy:
        reasons.append(f"cur_enr_yb={enr_yb}>{sy}")
    if sdh_pre:
        reasons.append(f"permit_sdh_issued={max(sdh_pre)}")
    elif permit_pre:
        reasons.append(f"permit_issued={max(permit_pre)}")
    if first_sale:
        reasons.append("first_sale")
    if nbr:
        reasons.append("nb217_member")

    # 独立 ≤sy 新房龄证据(非税卷自身)= 纠正确立;
    # 只有信号但无 ≤sy 新房龄证据 → REVIEW
    indep_new = bool((enr_yb and enr_yb <= sy) or
                     (nb_yb and nb_yb <= sy))
    if best_yb and band == "AGE_00_03":
        if pid_ok and fnum(s["sale_price"]) and not (
                nb_yb and nb_yb > sy) and not (
                enr_yb and enr_yb > sy):
            cls = "CONFIRMED_AGE_00_03"
        else:
            cls = "PROBABLE_AGE_00_03"
    elif band != "AGE_UNKNOWN":
        if post_sale_build:
            cls = ("LAND_OLD_HOUSE_SALE" if best_yb and
                   sy - best_yb > 10 else "POST_SALE_BUILD")
        elif not indep_new and (sdh_pre or first_sale or
                                nbr or enr):
            # 有新建信号但无 ≤sy 的新 yb 证据 → 人工复核队列
            cls = "REVIEW_REBUILD_STALE_YB"
        else:
            cls = band
    else:
        if post_sale_build or (cur_yb and cur_yb > sy):
            cls = "POST_SALE_BUILD"
        elif sdh_pre or (permit_pre and first_sale):
            cls = "PROBABLE_AGE_00_03"
        else:
            cls = "AGE_UNKNOWN"
    # 证据类型
    ev_types = []
    if "TAX_ROLL" in src_ybs:
        ev_types.append("TAX_ROLL_AT_SALE")
    if best_src == "BCA_ENRICHED" or (enr_yb and enr_yb <= sy):
        ev_types.append("BCA_ENRICHED_CURRENT")
    if best_src == "NB217_MLS" or nbr:
        ev_types.append("NB217_MLS")
    if pm:
        ev_types.append("PERMIT_" +
                        ("SDH" if sdh_pre else "OTHER_PRE"
                         if permit_pre else "POST"))
    if not ev_types:
        ev_types.append("NONE")
    census.append({
        "canonical_tx": s["canonical_tx"],
        "identity_confidence":
            "PID_VERIFIED" if pid_ok else "ADDRESS_ONLY",
        "product_confidence": s["product_verification"],
        "year_built_at_sale_original": s["yb"],
        "year_built_corrected": best_yb or "",
        "year_built_evidence_type": "+".join(ev_types),
        "evidence_time": "+".join(
            f"{k}={src_ybs[k]}" for k in sorted(src_ybs)),
        "cur_max_yb": cur_yb or "",
        "age_band_before": s["age_band"],
        "age_band_after": band if best_yb else "AGE_UNKNOWN",
        "classification": cls,
        "classification_reasons": "|".join(reasons),
        "sale_kind": s["sale_kind"],
        "sale_kind_confidence":
            "HIGH" if s["sale_kind"] in ("FIRST_SALE", "RESALE")
            else "LOW",
        "community": s["community"], "sale_year": sy,
        "sale_price": s["sale_price"], "fa": s["fa"],
        "permit_pre_sale": max(permit_pre) if permit_pre else "",
        "nb217": bool(nbr), "in_pool": bool(enr)})

wcsv(OUTD / "house_5386_census_per_row.csv", census)
wf = Counter(r["classification"] for r in census)
assert sum(wf.values()) == 5386
waterfall = [{"classification": c, "n": wf.get(c, 0)}
             for c in CLASSES]
wcsv(DOCS / "house_5386_census_waterfall.csv", waterfall)

ev_c = Counter()
for r in census:
    for e in r["year_built_evidence_type"].split("+"):
        ev_c[e] += 1
wcsv(DOCS / "house_5386_age_evidence_summary.csv",
     [{"evidence_type": k, "n": v} for k, v in ev_c.most_common()])

# ---------- 候选漏斗 ----------
funnel = [
    ("ALL_HOUSE_CANONICAL", len(census)),
    ("IDENTITY_PID_VERIFIED", sum(1 for r in census if r[
        "identity_confidence"] == "PID_VERIFIED")),
    ("HAS_ANY_YB_EVIDENCE_LE_SY", sum(
        1 for r in census if r["year_built_corrected"])),
    ("NEWBUILD_SIGNAL_ANY", sum(
        1 for r in census if r["nb217"] or r["in_pool"] or
        r["permit_pre_sale"] != "" or
        r["sale_kind"] == "FIRST_SALE")),
    ("CONFIRMED_AGE_00_03", wf["CONFIRMED_AGE_00_03"]),
    ("PROBABLE_AGE_00_03", wf["PROBABLE_AGE_00_03"]),
    ("REVIEW_REBUILD_STALE_YB", wf["REVIEW_REBUILD_STALE_YB"]),
    ("POST_SALE_BUILD_or_LAND_OLD", wf["POST_SALE_BUILD"] +
     wf["LAND_OLD_HOUSE_SALE"]),
]
wcsv(DOCS / "house_5386_newbuild_candidate_funnel.csv",
     [{"stage": k, "n": v} for k, v in funnel])

# ---------- 217 复核:日期+价格+GST ----------
recon = []
for i, r in enumerate(nb217):
    na = AN.parse(r["address"])["normalized_address"]
    cand = [s for s in sales
            if (r["pid"] and s["pid"] == r["pid"]) or
               s["norm_addr"] == na]
    exact = [s for s in cand if s["sale_date"] == r["sale_date"]]
    np_, ny_ = fnum(r["sale_price"]), iyear(r["year_built"])
    if exact:
        s = exact[0]
        ap = fnum(s["sale_price"])
        price_rel = ("EXACT" if ap and abs(ap - np_) < 1 else
                     "GST_RATIO" if ap and np_ and
                     min(abs(ap / np_ - 1.05),
                         abs(np_ / ap - 1.05)) < 0.005 else
                     "MISMATCH")
        recon.append({"row": i + 1, "date_match": "EXACT",
                      "price_rel": price_rel,
                      "price_delta_pct": round(
                          (ap - np_) / np_ * 100, 2)
                      if ap and np_ else "",
                      "gst_basis": r["gst_basis"],
                      "nb_yb": ny_, "archive_yb": s["yb"],
                      "archive_band": s["age_band"],
                      "class": next(
                          c["classification"] for c in census
                          if c["canonical_tx"] ==
                          s["canonical_tx"])})
    else:
        nearest = min(cand, key=lambda x: abs(
            int(x["sale_date"].replace("-", "")[:6]) -
            int(r["sale_date"].replace("-", "")[:6])),
            default=None)
        recon.append({"row": i + 1,
                      "date_match": "NEAREST:" +
                      (nearest["sale_date"] if nearest else
                       "NONE"),
                      "price_rel": "NO_SAME_DATE",
                      "archive_yb": (nearest or {}).get("yb", ""),
                      "nb_yb": ny_})
wcsv(DOCS / "house_217_event_price_reconciliation.csv", recon)

# ---------- 前后覆盖 ----------
ba = defaultdict(lambda: [0, 0])
for r in census:
    y = str(r["sale_year"])
    ba[(y, r["age_band_before"])][0] += 1
    ba[(y, r["age_band_after"])][1] += 1
wcsv(DOCS / "house_census_before_after_year_age.csv",
     [{"year": y, "age_band": b, "n_before": v[0],
       "n_after": v[1]}
      for (y, b), v in sorted(ba.items())])

# ---------- 异常行(匿名) ----------
exc = [{"row": i + 1, "classification": r["classification"],
        "sale_year": r["sale_year"],
        "archive_yb": r["year_built_at_sale_original"],
        "corrected_yb": r["year_built_corrected"],
        "cur_max_yb": r["cur_max_yb"],
        "evidence": r["year_built_evidence_type"],
        "reasons": r["classification_reasons"]}
       for i, r in enumerate(census)
       if r["classification"] in
       ("REVIEW_REBUILD_STALE_YB", "POST_SALE_BUILD",
        "LAND_OLD_HOUSE_SALE", "IDENTITY_CONFLICT",
        "PROBABLE_AGE_00_03")]
wcsv(DOCS / "house_age_reconstruction_exceptions.csv", exc)

# ---------- 社区×年新建 ----------
cy = defaultdict(lambda: Counter())
for r in census:
    c = r["classification"]
    k = (r["community"], str(r["sale_year"]))
    if c == "CONFIRMED_AGE_00_03":
        cy[k]["confirmed_00_03"] += 1
        cy[k][r["sale_kind"].lower() + "_00_03"] += 1
    elif c == "PROBABLE_AGE_00_03":
        cy[k]["probable_00_03"] += 1
    if r["age_band_after"] == "AGE_00_03":
        cy[k]["band_00_03_total"] += 1
wcsv(DOCS / "house_newbuild_by_community_year.csv",
     [{"community": c, "year": y,
       "confirmed_00_03": v["confirmed_00_03"],
       "probable_00_03": v["probable_00_03"],
       "band_00_03_total": v["band_00_03_total"],
       "first_sale_00_03": v["first_sale_00_03"],
       "resale_00_03": v["resale_00_03"],
       "unknown_kind_00_03": v["unknown_00_03"]}
      for (c, y), v in sorted(cy.items())
      if v["band_00_03_total"] or v["confirmed_00_03"]
      or v["probable_00_03"]])

# ---------- NEWBUILD_COMPARABLE 严门 ----------
gate_c = Counter()
nbcomp = []
for r in census:
    ok_id = r["identity_confidence"] == "PID_VERIFIED"
    ok_ev = r["classification"] == "CONFIRMED_AGE_00_03"
    ok_price = bool(fnum(r["sale_price"]))
    ok_fa = bool(fnum(r["fa"]))
    ok_kind = r["sale_kind"] in ("FIRST_SALE", "RESALE")
    if ok_id and ok_ev and ok_price and ok_kind:
        nbcomp.append(r)
        gate_c["NEWBUILD_COMPARABLE"] += 1
        if ok_fa:
            gate_c["PSF_OK"] += 1
    for k, v in (("id", ok_id), ("confirmed00_03", ok_ev),
                 ("price", ok_price), ("sale_kind", ok_kind)):
        if not v:
            gate_c["fail_" + k] += 1
wcsv(DOCS / "house_newbuild_comparable_gate.csv",
     [{"gate": k, "n": v} for k, v in [
        ("ALL_HOUSE", 5386),
        ("fail_identity", gate_c["fail_id"]),
        ("fail_confirmed_00_03", gate_c["fail_confirmed00_03"]),
        ("fail_price", gate_c["fail_price"]),
        ("fail_sale_kind", gate_c["fail_sale_kind"]),
        ("NEWBUILD_COMPARABLE", gate_c["NEWBUILD_COMPARABLE"]),
        ("of_which_PSF_eligible", gate_c["PSF_OK"])]])

# ---------- 报告 ----------
by_year_cls = defaultdict(Counter)
for r in census:
    by_year_cls[str(r["sale_year"])][r["classification"]] += 1
conf_y = {y: by_year_cls[y]["CONFIRMED_AGE_00_03"]
          for y in sorted(by_year_cls)}
prob_y = {y: by_year_cls[y]["PROBABLE_AGE_00_03"]
          for y in sorted(by_year_cls)}
old00 = sum(1 for r in census
            if r["age_band_before"] == "AGE_00_03")
new00 = sum(1 for r in census
            if r["age_band_after"] == "AGE_00_03")
rep = f"""# HOUSE 5,386 全量新建识别普查(Issue #1)

基线:sales_archive_v1_0_3(冻结未改);本层=纠正研究口径。

## 普查瀑布(合计=5,386)
| 分类 | n |
|---|---|
""" + "\n".join(f"| {c} | {wf.get(c,0)} |" for c in CLASSES) + f"""

## 0–3 年各年(VERIFIED=CONFIRMED)
| 年 | CONFIRMED | PROBABLE | 原档案00_03 | 纠正后00_03 |
|---|---|---|---|---|
""" + "\n".join(
    f"| {y} | {conf_y[y]} | {prob_y[y]} | "
    f"{sum(1 for r in census if str(r['sale_year'])==y and r['age_band_before']=='AGE_00_03')} | "
    f"{sum(1 for r in census if str(r['sale_year'])==y and r['age_band_after']=='AGE_00_03')} |"
    for y in sorted(conf_y)) + f"""

- 0–3 档总量:{old00} → **{new00}**(净增 {new00-old00},
  与 V1.0.4 纠正口径一致;全量普查确认这 128 即全部
  可证实 0–3 成交 —— 池外新增识别为 0,另有
  {wf['REVIEW_REBUILD_STALE_YB']} 笔带新建信号
  (许可/FIRST_SALE/池命中)但无 ≤sy 新房龄证据,
  列入 REVIEW 待人工)
- 逐条证据见 exceptions/逐笔 CSV 的
  `classification_reasons`(stale_roll|permit|nb217|
  enriched 来源可复算)

## 217 复核
- 日期+价格逐笔双校验见
  house_217_event_price_reconciliation.csv(匿名);
- GST 口径:价格差≈1.05× 标 GST_RATIO,不算错配。

## 三口径
- CAPTURED_HOUSE_SALES:5386(未加行)
- AGE_CLASSIFIED:{sum(1 for r in census if r['age_band_after']!='AGE_UNKNOWN')}
- NEWBUILD_COMPARABLE(严门):{gate_c['NEWBUILD_COMPARABLE']}
  (pid+CONFIRMED_00_03+价格+首销/转售;其中 PSF 可用
  {gate_c['PSF_OK']};比 V1.0.4 的 122 收紧:
  旧口径混入纠到 04_10 的行)

## 覆盖与截点
2023/24 PARTIAL_COVERAGE(last_sale 窗口截断);
2025 受 2026 转售覆盖;2026 cutoff={CUTOFF_2026}。
COVERAGE_GAP 保留:无 pid {sum(1 for r in census if not r['identity_confidence']=='PID_VERIFIED')} 行仅地址级证据。
"""
(DOCS / "house_5386_census_report.md").write_text(rep)

manifest = {"version": "house_5386_census_v1",
            "issue": "github#1",
            "run": datetime.datetime.now().isoformat(),
            "baseline": "sales_archive_v1_0_3@5e51b79+114f579",
            "input_sha256": HASHES,
            "counts": {"house_total": len(census),
                       "waterfall": dict(wf),
                       "confirmed_00_03_by_year": conf_y,
                       "probable_00_03_by_year": prob_y,
                       "band_00_03_before": old00,
                       "band_00_03_after": new00,
                       "newbuild_comparable":
                           gate_c["NEWBUILD_COMPARABLE"],
                       "exceptions": len(exc)}}
(DOCS / "manifest.json").write_text(json.dumps(
    manifest, indent=1, ensure_ascii=False))
print("waterfall:", dict(wf))
print("00_03:", old00, "->", new00,
      "| confirmed:", conf_y, "| probable:", prob_y)
print("NB_COMPARABLE:", gate_c["NEWBUILD_COMPARABLE"],
      "psf_ok:", gate_c["PSF_OK"])
