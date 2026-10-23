#!/usr/bin/env python3
"""HOUSE Newbuild Discovery V2.2 — 竣工证据/产权范围/售价可比终检。

基线 d2b26bc。修四处:
1) 竣工语义:建成年份≠竣工证明。六维证据:
   YEAR_BUILT / PERMIT_ISSUED / CONSTRUCTION_STATUS /
   COMPLETION_OR_OCCUPANCY / SALE_EVENT /
   SALE_AFTER_NEW_BUILDING。
   现有免费数据无 Occupancy/Final-Inspection 字段 →
   最高只能到 ASSESSMENT_COMPLETION(税卷已按新房评估),
   缺独立竣工/入住证据者标 NEW_BUILDING_AGE_SUPPORTED。
2) 队列核销:A=589 疑似成交;B=52 REVIEW;C=旧房突变;
   D=反查无匹配地块(仅筛高优,不判未售)。
3) 血统:多 successor 一律候选;CONFIRMED 仅当唯一候选
   且强证据;duplex/multiplex 拆分不得并单元。
4) 111 价格候选:GST/面积/产权独立门;小面积逐笔审;
   NEWBUILD_PRICING_READY 需全部门过。
"""
import csv, json, sys, hashlib, hmac, datetime, secrets
from collections import defaultdict, Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import address_norm as AN                             # noqa: E402
import house_newbuild_timeline_gate_v2_1 as V         # noqa: E402

OUTD = ROOT / "data/research/normalized/newbuild_v2_2"
DOCS = ROOT / "docs/audits/house_newbuild_v2_2"
CUTOFF_2026 = "2026-07-08"
COST_PSF = 250.0
YEARS = (2023, 2024, 2025, 2026)
BASE_YEAR = 2022
NEWBUILD_FA_BAND = (1200, 4000)    # 研究目标 1500-2500,超宽哨戒
GST_RATE = 0.05


def anon_id(raw):
    return hmac.new(V.anon_key(), str(raw).encode(),
                    hashlib.sha256).hexdigest()[:12]


def fnum(x):
    return V.fnum(x)


def iyear(x):
    return V.iyear(x)


def tier(g):
    return V.tier(g)


def wcsv(path, rows):
    return V.wcsv(path, rows)


def build_lineage_v2(traj):
    """V2.2 修正:多 successor 一律候选,唯一+强证据才 CONFIRMED。

    HOUSE→duplex/multiplex 拆分:同 parcel 多个新 pid
    不得自动选其一合并。"""
    folio, loco, planlot, addr = V.build_keymaps(traj)
    graph = {}
    EV_RANK = ["", "ADDRESS", "LAND_COORD", "FOLIO", "PLAN_LOT"]
    for p, ys in traj.items():
        yrs = sorted(y for y in ys if y != BASE_YEAR)
        if not yrs or yrs[-1] >= YEARS[-1]:
            continue
        last_y = yrs[-1]
        inf = {k: "" for k in ("folio", "loco", "plan", "lot",
                               "street", "civic_lo")}
        for y in sorted(ys):
            for k in inf:
                if ys[y].get(k):
                    inf[k] = ys[y][k]
        cands = {}
        for mp, key, ev in ((planlot, (inf["plan"], inf["lot"]),
                             "PLAN_LOT"),
                            (folio, inf["folio"], "FOLIO"),
                            (loco, inf["loco"], "LAND_COORD"),
                            (addr, (inf["street"], inf["civic_lo"]),
                             "ADDRESS")):
            if not key or (isinstance(key, tuple)
                           and not all(key)):
                continue
            for q in mp.get(key, ()):
                if q == p or q not in traj:
                    continue
                qyrs = [y for y in traj[q] if y != BASE_YEAR]
                if qyrs and min(qyrs) > last_y:
                    if EV_RANK.index(ev) > EV_RANK.index(
                            cands.get(q, "")):
                        cands[q] = ev
        if not cands:
            continue
        edges = []
        for q, e in sorted(cands.items()):
            if len(cands) == 1 and e in ("PLAN_LOT", "FOLIO"):
                st = "CONFIRMED"
            elif len(cands) > 1:
                st = "SPLIT_CANDIDATE"    # 多后继全部候选
            else:
                st = "CANDIDATE_WEAK"
            edges.append({"succ": q, "key_ev": e, "status": st})
        graph[p] = edges
    return graph


def lineage_successors_v2(graph, p):
    """严格:存在任何非 CONFIRMED 边或 ≥2 后继 → 不并。"""
    es = graph.get(p, [])
    conf = [e["succ"] for e in es if e["status"] == "CONFIRMED"]
    if len(conf) == 1 and all(
            e["status"] == "CONFIRMED" for e in es):
        return conf
    return []


def evidence_dims_v2(yb_le_sy, yb_new_seen, permit, impv_jump,
                     cur_max_yb, sy):
    """六维独立证据(生产函数)。

    permit: {"iy","date","cat","desc"} 或 None
    impv_jump: {"year","delta"} 或 None
    """
    dims = {}
    # 1 建成年份证据
    dims["YEAR_BUILT_EVIDENCE"] = (
        "NEW_ROLL_YB" if yb_new_seen else
        "OLD_OR_MISSING")
    # 2 许可签发
    dims["PERMIT_ISSUED_EVIDENCE"] = (
        f"ISSUED_{permit['iy']}" if permit else "NONE")
    # 3 施工状态(评估口径:价值显化或卷面 yb 更新)
    if impv_jump and impv_jump["delta"] >= 200000:
        dims["CONSTRUCTION_STATUS_EVIDENCE"] = \
            "ASSESSED_IMPROVEMENT_JUMP"
    elif yb_new_seen:
        dims["CONSTRUCTION_STATUS_EVIDENCE"] = "ROLL_YB_NEW"
    else:
        dims["CONSTRUCTION_STATUS_EVIDENCE"] = "NONE"
    # 4 竣工/入住:无 occupancy 源 → 最高 ASSESSMENT 级
    if yb_new_seen and impv_jump and \
            impv_jump["delta"] >= 250000:
        dims["COMPLETION_OR_OCCUPANCY_EVIDENCE"] = \
            "ASSESSMENT_COMPLETION"
    elif yb_new_seen:
        dims["COMPLETION_OR_OCCUPANCY_EVIDENCE"] = \
            "NEW_BUILDING_AGE_SUPPORTED"
    else:
        dims["COMPLETION_OR_OCCUPANCY_EVIDENCE"] = "NONE"
    # 6 成交后于新建筑(≤sy 的新 yb + 时序)
    comp_yr = cur_max_yb if (
        dims["COMPLETION_OR_OCCUPANCY_EVIDENCE"] in
        ("ASSESSMENT_COMPLETION", "NEW_BUILDING_AGE_SUPPORTED")
        ) else None
    dims["SALE_AFTER_NEW_BUILDING_EVIDENCE"] = bool(
        yb_le_sy and yb_le_sy >= 2018 and
        sy - yb_le_sy <= 3 and
        dims["COMPLETION_OR_OCCUPANCY_EVIDENCE"] != "NONE")
    return dims


def gst_basis(sale_kind, sources):
    """GST 口径:免费源无 GST 字段 → UNKNOWN+情景。

    FIRST_SALE(新房开发商首销)通常含 GST → 5% 情景;
    RESALE 通常不含。"""
    if sale_kind == "FIRST_SALE":
        return {"gst_basis": "UNKNOWN_LIKELY_INCLUDED",
                "price_incl_gst_est": True}
    if sale_kind == "RESALE":
        return {"gst_basis": "UNKNOWN_LIKELY_EXCLUDED",
                "price_incl_gst_est": False}
    return {"gst_basis": "UNKNOWN",
            "price_incl_gst_est": None}


def pricing_gates(sale_ev, new_house_ev, fa, fa_above_grade_ok,
                  freehold_scope, gst, fa_plausible):
    """独立售价门(逐级,不跳档)。"""
    g = {}
    g["SALE_VERIFIED"] = sale_ev == "SALE_SOURCE_CONFIRMED"
    g["NEW_HOUSE_STATUS_VERIFIED"] = new_house_ev
    g["TOTAL_PRICE_COMPARABLE"] = bool(
        g["SALE_VERIFIED"] and freehold_scope)
    g["PSF_COMPARABLE"] = bool(
        g["TOTAL_PRICE_COMPARABLE"] and fa and
        fa_above_grade_ok and fa_plausible)
    g["GST_BASIS_VERIFIED"] = bool(
        gst["gst_basis"] not in ("UNKNOWN",
                                 "UNKNOWN_LIKELY_INCLUDED",
                                 "UNKNOWN_LIKELY_EXCLUDED"))
    g["NEWBUILD_PRICING_READY"] = all(
        (g["SALE_VERIFIED"], g["NEW_HOUSE_STATUS_VERIFIED"],
         g["TOTAL_PRICE_COMPARABLE"], g["PSF_COMPARABLE"]))
    return g


def main():
    OUTD.mkdir(parents=True, exist_ok=True)
    DOCS.mkdir(parents=True, exist_ok=True)
    traj, pid_info, prev_check, _ = V.load_traj()
    folio, loco, planlot, addr = V.build_keymaps(traj)
    graph = build_lineage_v2(traj)

    sales = list(csv.DictReader(open(V.IN["archive"])))
    sales_by_pid = defaultdict(list)
    for s in sales:
        if s["pid"]:
            sales_by_pid[s["pid"]].append(s)
    house = [s for s in sales if s["product"] == "HOUSE"]
    census = {r["canonical_tx"]: r for r in
              csv.DictReader(open(V.IN["census"]))}

    # 池 enriched 地上面积 + strata + legal
    pool = {}
    for f in V.POOL_FILES:
        p = ROOT / "data/research/raw/location" / f
        if not p.exists():
            continue
        d = json.load(open(p))
        for r in (d.get("sales", [])
                  if isinstance(d, dict) else d):
            pid = r.get("pid")
            if pid:
                pool[pid] = r
    permits = defaultdict(list)
    pf = open(V.IN["permits"], newline="",
              encoding="utf-8-sig")
    pd_ = csv.Sniffer().sniff(pf.read(4000), delimiters=",;\t")
    pf.seek(0)
    for r in csv.DictReader(pf, dialect=pd_):
        a = (r.get("address") or "").split(",")[0]
        na = AN.parse(a)["normalized_address"] \
            if a.strip() else ""
        iy = iyear(r.get("issueyear") or
                   (r.get("issuedate") or "")[:4])
        if na and iy:
            permits[na].append(
                {"iy": iy, "date": r.get("issuedate") or "",
                 "cat": r.get("specificusecategory") or "",
                 "desc": r.get("projectdescription") or ""})

    # ---------- 单笔分析 ----------
    def sale_pids(s):
        p0 = s["pid"]
        out = {p0}
        for e in graph.get(p0, []):
            if e["status"] == "CONFIRMED":
                out.add(e["succ"])
        for pr, es in graph.items():
            if any(e["status"] == "CONFIRMED" and
                   e["succ"] == p0 for e in es):
                out.add(pr)
        return out

    def analyze(s):
        p0 = s["pid"]
        sy = int(s["sale_date"][:4])
        pids = sale_pids(s)
        series = sorted((y, v["impv"]) for q in pids
                        for y, v in traj.get(q, {}).items())
        m = V.jump_metrics(series)
        ybs = [v["yb"] for q in pids
               for v in traj.get(q, {}).values()
               if v.get("yb")]
        cens = census.get(s["canonical_tx"], {})
        yb_le_sy = iyear(cens.get("year_built_corrected")) or \
            max([y for y in ybs if y <= sy], default=None)
        cur_max_yb = iyear(cens.get("cur_max_yb")) or \
            (max(ybs) if ybs else None)
        yb_new_seen = bool(cur_max_yb and cur_max_yb >= 2018)
        pm = [x for x in permits.get(s["norm_addr"], [])
              if "Single Detached" in x["cat"] or
              "single" in x["desc"].lower() or
              "one-family" in x["desc"].lower()]
        permit_pre = max([x for x in pm if x["iy"] <= sy],
                         key=lambda x: x["iy"], default=None)
        permit_post = min([x for x in pm if x["iy"] > sy],
                          key=lambda x: x["iy"], default=None)
        jump = ({"year": m["jump_year"],
                 "delta": m["staged_delta"]}
                if m and m["jump_year"] else None)
        dims = evidence_dims_v2(
            yb_le_sy, yb_new_seen, permit_pre, jump,
            cur_max_yb, sy)
        sale_ev = V.sale_evidence_grade(s)
        concl = V.classify_timeline(
            yb_le_sy, cur_max_yb,
            iyear(cens.get("year_built_at_sale_original")) or
            (min(ybs) if ybs else None),
            permit_pre["iy"] if permit_pre else None,
            permit_post["iy"] if permit_post else None,
            jump["year"] if jump else None,
            m["staged_delta"] if m else None, sy)
        fa = fnum(s["fa"])
        pl = pool.get(p0, {})
        pool_fa = (fnum((pl.get(
            "bca_first_floor_area_sqft") or "").replace(
                ",", "")) or 0) + \
            (fnum((pl.get("bca_second_floor_area_sqft") or "")
                  .replace(",", "")) or 0)
        fa_ok = bool(fa and (not pool_fa or
                             abs(fa - pool_fa) / pool_fa < 0.25))
        freehold = s["is_strata"] != "True" and not s.get(
            "duplex_scope", "").startswith("DUPLEX_HALF")
        gst = gst_basis(s["sale_kind"], s["sources"])
        nh_ev = dims["SALE_AFTER_NEW_BUILDING_EVIDENCE"]
        gates = pricing_gates(
            sale_ev, nh_ev, fa, fa_ok, freehold, gst,
            fa_plausible=bool(
                fa and NEWBUILD_FA_BAND[0] <= fa
                <= NEWBUILD_FA_BAND[1]) if fa else False)
        return {"m": m, "dims": dims, "concl": concl,
                "sale_ev": sale_ev, "gates": gates, "gst": gst,
                "fa": fa, "pool_fa": pool_fa, "fa_ok": fa_ok,
                "freehold": freehold, "cens": cens,
                "yb_le_sy": yb_le_sy, "cur_max_yb": cur_max_yb,
                "permit_pre": permit_pre,
                "permit_post": permit_post, "pl": pl}

    house_rows = []
    for s in house:
        a = analyze(s)
        house_rows.append({**a, "s": s,
                           "canonical_tx": s["canonical_tx"],
                           "sale_year": int(s["sale_date"][:4]),
                           "community": s["community"],
                           "pid": s["pid"]})
    wcsv(OUTD / "house_v2_2_per_row.csv",
         [{"canonical_tx": r["canonical_tx"],
           "pid": r["pid"], "sale_year": r["sale_year"],
           "community": r["community"], "concl": r["concl"],
           "sale_ev": r["sale_ev"], "fa": r["fa"],
           "pool_fa": r["pool_fa"], "freehold": r["freehold"],
           "gst_basis": r["gst"]["gst_basis"],
           "yb_le_sy": r["yb_le_sy"],
           "census_class":
               r["cens"].get("classification", ""),
           **{f"dim_{k}": v for k, v in r["dims"].items()},
           **{f"gate_{k}": v for k, v in r["gates"].items()}}
          for r in house_rows])

    # ---------- P0-C 血统重核 ----------
    lin_rows = []
    for p, es in graph.items():
        for e in es:
            lin_rows.append({
                "from": anon_id(p), "to": anon_id(e["succ"]),
                "key_ev": e["key_ev"], "status": e["status"],
                "n_succ": len(es),
                "duplex_risk": int(
                    len(es) > 1 and e["status"] != "CONFIRMED")})
    wcsv(DOCS / "pid_lineage_split_conflict_audit.csv",
         lin_rows)

    # ---------- P0-B 队列核销 ----------
    # 复用 V2.1 的 rev 集(jump 反查成交)
    jump_all = {}
    for p, ys in traj.items():
        m = V.jump_metrics(
            sorted((y, v["impv"]) for y, v in ys.items()))
        if not m:
            continue
        fa = fnum(pool.get(p, {}).get(
            "bca_first_floor_area_sqft") or 0)
        g = V.jump_grade(
            m, fa * COST_PSF if fa else None, YEARS[-1])
        if g.startswith(("STRONG", "MODERATE")) or \
                (m["near_zero_base"] and
                 m["staged_delta"] > 200000):
            jump_all[p] = m
    rev = []
    for p, m in jump_all.items():
        info = pid_info.get(p, {})
        pid_set = {p}
        for mp, key in ((folio, info.get("folio")),
                        (loco, info.get("loco")),
                        (planlot,
                         (info.get("plan"), info.get("lot"))),
                        (addr, (info.get("street"),
                                info.get("civic_lo")))):
            pid_set |= mp.get(key, set())
        for e in graph.get(p, []):
            if e["status"] == "CONFIRMED":
                pid_set.add(e["succ"])
        seen = {s["canonical_tx"]: s for q in pid_set
                for s in sales_by_pid.get(q, [])}
        for s in seen.values():
            rev.append({"jump_pid": p, "s": s})
        if not seen:
            rev.append({"jump_pid": p, "s": None})

    # 队列 A:589 疑似成交(V2.1 uniq) → 重跑 concl
    qA = []
    for r in rev:
        s = r["s"]
        if not s or s["product"] != "HOUSE":
            continue
        a = analyze(s)
        if a["concl"] in ("PROBABLE_NEW_HOUSE_SALE",
                          "UNRESOLVED_TEMPORAL_ORDER",
                          "CONFIRMED_NEW_HOUSE_SALE",
                          "CONSTRUCTION_STAGE_SALE"):
            qA.append({**a, "s": s})
    qA_tx = {x["s"]["canonical_tx"]: x for x in qA}
    in128 = {x for x in qA_tx
             if census.get(x, {}).get("classification") ==
             "CONFIRMED_AGE_00_03"}
    wcsv(DOCS / "candidate_589_identity_waterfall.csv",
         [{"layer": "MATCH_ROWS_HOUSE_SUSPECT",
           "n": len(qA)},
          {"layer": "UNIQUE_TX", "n": len(qA_tx)},
          {"layer": "ALREADY_IN_128", "n": len(in128)},
          {"layer": "NEW_CANDIDATES",
           "n": len(qA_tx) - len(in128)},
          {"layer": "CONCL_CONFIRMED",
           "n": sum(1 for x in qA_tx.values()
                    if x["concl"] ==
                    "CONFIRMED_NEW_HOUSE_SALE")},
          {"layer": "CONCL_PROBABLE",
           "n": sum(1 for x in qA_tx.values()
                    if x["concl"] == "PROBABLE_NEW_HOUSE_SALE")},
          {"layer": "CONCL_STAGE",
           "n": sum(1 for x in qA_tx.values()
                    if x["concl"] ==
                    "CONSTRUCTION_STAGE_SALE")},
          {"layer": "CONCL_UNRESOLVED",
           "n": sum(1 for x in qA_tx.values()
                    if x["concl"] ==
                    "UNRESOLVED_TEMPORAL_ORDER")}])

    # 队列 B:52 REVIEW 终核
    rows52 = []
    for r in (x for x in house_rows
              if x["cens"].get("classification") ==
              "REVIEW_REBUILD_STALE_YB"):
        rows52.append({
            "row": len(rows52) + 1,
            "sale_year": r["sale_year"],
            "community": r["community"],
            "concl": r["concl"],
            "completion_ev":
                r["dims"]["COMPLETION_OR_OCCUPANCY_EVIDENCE"],
            "permit": (r["permit_pre"] or {}).get("iy") or
                (r["permit_post"] or {}).get("iy") or "",
            "yb_le_sy": r["yb_le_sy"] or "",
            "cur_max_yb": r["cur_max_yb"] or "",
            "new_house_ev":
                r["dims"]["SALE_AFTER_NEW_BUILDING_EVIDENCE"]})
    wcsv(DOCS / "review_52_final_reconciliation.csv", rows52)

    # 队列 C:旧房突变反例
    old_rows = []
    for r in (x for x in house_rows
              if x["cens"].get("classification") in
              ("AGE_31_50", "AGE_51_PLUS")):
        m = r["m"]
        if not m or m["staged_delta"] < 250000:
            continue
        old_rows.append({
            "row": len(old_rows) + 1,
            "sale_year": r["sale_year"],
            "community": r["community"],
            "staged_delta": round(m["staged_delta"]),
            "jump_year": m["jump_year"] or "",
            "permit_pre": (r["permit_pre"] or {}).get("iy") or "",
            "permit_post":
                (r["permit_post"] or {}).get("iy") or "",
            "concl": r["concl"],
            "completion_ev":
                r["dims"]["COMPLETION_OR_OCCUPANCY_EVIDENCE"]})
    wcsv(DOCS / "old_house_timing_counterexamples.csv",
         old_rows)

    # ---------- P0-A 竣工证据审计(128) ----------
    comp_rows = []
    for r in (x for x in house_rows
              if x["cens"].get("classification") ==
              "CONFIRMED_AGE_00_03"):
        comp_rows.append({
            "row": len(comp_rows) + 1,
            "sale_year": r["sale_year"],
            "community": r["community"],
            "yb_le_sy": r["yb_le_sy"],
            "cur_max_yb": r["cur_max_yb"],
            "permit_iy": (r["permit_pre"] or {}).get("iy") or "",
            "completion_ev":
                r["dims"]["COMPLETION_OR_OCCUPANCY_EVIDENCE"],
            "construction_ev":
                r["dims"]["CONSTRUCTION_STATUS_EVIDENCE"],
            "new_house_ev":
                r["dims"]["SALE_AFTER_NEW_BUILDING_EVIDENCE"]})
    wcsv(DOCS / "newbuild_completion_evidence_audit.csv",
         comp_rows)

    # ---------- P0-D 111 价格专项 ----------
    p111 = [r for r in house_rows
            if r["cens"].get("classification") ==
            "CONFIRMED_AGE_00_03" and
            r["sale_ev"] == "SALE_SOURCE_CONFIRMED"]
    price_rows = []
    for r in p111:
        fa = r["fa"]
        price = fnum(r["s"]["sale_price"])
        price_rows.append({
            "row": len(price_rows) + 1,
            "sale_year": r["sale_year"],
            "community": r["community"],
            "sale_kind": r["s"]["sale_kind"],
            "price": price,
            "fa": fa, "pool_fa": r["pool_fa"] or "",
            "fa_ok": r["fa_ok"], "freehold": r["freehold"],
            "is_strata": r["s"]["is_strata"],
            "duplex_scope": r["s"].get("duplex_scope", ""),
            "gst_basis": r["gst"]["gst_basis"],
            "price_incl_gst_est":
                r["gst"]["price_incl_gst_est"],
            "price_scenario_net":
                round(price / 1.05) if
                r["gst"]["price_incl_gst_est"] else "",
            "small_area_flag": bool(fa and fa < 1200),
            "completion_ev": r["dims"]
                ["COMPLETION_OR_OCCUPANCY_EVIDENCE"],
            **{f"gate_{k}": v for k, v in
               r["gates"].items()}})
    wcsv(DOCS / "newbuild_111_gst_scope_reconciliation.csv",
         price_rows)

    ready = [r for r in house_rows
             if r["gates"]["NEWBUILD_PRICING_READY"]]
    wcsv(DOCS / "newbuild_total_price_comps.csv",
         [{"row": i + 1, "sale_year": r["sale_year"],
           "community": r["community"],
           "sale_kind": r["s"]["sale_kind"],
           "price": fnum(r["s"]["sale_price"]),
           "lot_sqft": fnum(r["s"]["lot_sqft"]),
           "zoning": r["s"]["zoning"],
           "gst_basis": r["gst"]["gst_basis"]}
          for i, r in enumerate(ready)])
    wcsv(DOCS / "newbuild_psf_comps.csv",
         [{"row": i + 1, "sale_year": r["sale_year"],
           "community": r["community"],
           "sale_kind": r["s"]["sale_kind"],
           "psf": round(fnum(r["s"]["sale_price"]) / r["fa"], 1),
           "fa": r["fa"],
           "gst_basis": r["gst"]["gst_basis"]}
          for i, r in enumerate(ready if ready else [])
          if r["fa"]])

    # ---------- P1 四池 ----------
    def pool4(r):
        band = {"CONFIRMED_AGE_00_03": "A_00_03",
                "PROBABLE_AGE_00_03": "A_00_03"}.get(
            r["cens"].get("classification"),
            {"AGE_04_10": "C_04_10"}.get(
                r["cens"].get("classification"),
                "D_11_PLUS" if r["cens"].get(
                    "classification", "").startswith("AGE_")
                else "X"))
        return band
    p_stats = Counter()
    for r in house_rows:
        p4 = pool4(r)
        sk = r["s"]["sale_kind"]
        grp = (p4 + ("_FIRST" if sk == "FIRST_SALE"
                     else "_RESALE" if sk == "RESALE"
                     else "_UNK"),
               r["sale_year"], r["community"])
        p_stats[grp] += 1
    wcsv(DOCS / "first_sale_vs_resale_by_region.csv",
         [{"pool": p, "year": y, "community": c, "n": n}
          for (p, y, c), n in sorted(p_stats.items())])

    # ---------- 报告 ----------
    n_ready = sum(1 for r in house_rows
                  if r["gates"]["NEWBUILD_PRICING_READY"])
    n_sale_ev = sum(1 for r in house_rows
                    if r["gates"]["SALE_VERIFIED"])
    n_nh_ev = sum(1 for r in house_rows
                  if r["gates"]["NEW_HOUSE_STATUS_VERIFIED"])
    n_total = sum(1 for r in house_rows
                  if r["gates"]["TOTAL_PRICE_COMPARABLE"])
    n_psf = sum(1 for r in house_rows
                if r["gates"]["PSF_COMPARABLE"])
    n_gst_ver = sum(1 for r in house_rows
                    if r["gates"]["GST_BASIS_VERIFIED"])
    n_assess = sum(1 for r in comp_rows
                   if r["completion_ev"] ==
                   "ASSESSMENT_COMPLETION")
    n_occ = sum(1 for r in comp_rows
                if r["completion_ev"] == "OCCUPANCY_VERIFIED")
    n_lin_conf = sum(1 for es in graph.values()
                     for e in es if e["status"] == "CONFIRMED")
    n_lin_split = sum(1 for es in graph.values()
                      for e in es
                      if e["status"] == "SPLIT_CANDIDATE")
    rep = f"""# Newbuild V2.2 报告

基线 d2b26bc;V3s 冻结。cutoff={CUTOFF_2026}。
匿名=HMAC-SHA256。竣工证据无 occupancy 源→
最高 ASSESSMENT_COMPLETION,不冒名 VERIFIED。

## P0-A 竣工证据分层(128)
- ASSESSMENT_COMPLETION:{n_assess}
- OCCUPANCY_VERIFIED:{n_occ}(数据源缺,如实 0)
- 其余 NEW_BUILDING_AGE_SUPPORTED/NONE:
  {128 - n_assess - n_occ}
  缺什么:无 Final-Inspection/Occupancy 免费源;
  需 BCA/市政竣工记录才能升 COMPLETION_VERIFIED。

## P0-D 价格门(累计,不跳档;域=全部 5,386 HOUSE)
| 门 | n |
|---|---|
| SALE_VERIFIED | {n_sale_ev} |
| NEW_HOUSE_STATUS_VERIFIED | {n_nh_ev} |
| TOTAL_PRICE_COMPARABLE | {n_total} |
| PSF_COMPARABLE | {n_psf} |
| GST_BASIS_VERIFIED | {n_gst_ver}(无 GST 免费源) |
| NEWBUILD_PRICING_READY | {n_ready} |

注:前四门为全 5,386 的独立证据统计;
READY=五门交集(V2.1 严格 111 中剔除小面积/
非 freehold/面积口径不合者)。
- 小面积(<1200sqft)逐笔:见 111 对账 small_area_flag;
- GST:FIRST_SALE→UNKNOWN_LIKELY_INCLUDED+5% 净价情景;
  RESALE→UNKNOWN_LIKELY_EXCLUDED。

## P0-B 队列瀑布
- A:589 疑似(V2.1 口径):见
  candidate_589_identity_waterfall.csv
- B:52 REVIEW:见 review_52_final_reconciliation.csv
- C:旧房突变反例:{len(old_rows)} 行
- D:无匹配地块 jump={len(jump_all)},无成交匹配=
  {sum(1 for r in rev if r['s'] is None)}
  (标 SALE_NOT_FOUND_IN_ARCHIVE,不判未售)

## 血统
CONFIRMED {n_lin_conf} / SPLIT {n_lin_split} /
全部:{sum(len(es) for es in graph.values())}
多后继不再自动 CONFIRMED;duplex 拆分不误并。

## 四池统计
first_sale_vs_resale_by_region.csv:
A=0-3 首销,B=0-3 转售,C=4-10 近新,D=11+旧房,
分池不混。

## 阻塞(免费数据尽头)
- Occupancy/Final-Inspection:无合法免费源;
- GST 口径:MLS/BCA 均无字段,只能 UNKNOWN+情景;
- 许可≠开工≠竣工:无施工进度数据;
- 需付费/人工源:BCA 详情、LTSA 产权、MLS GST 标注。
"""
    (DOCS / "newbuild_v2_2_report.md").write_text(rep)
    man = {"version": "v2_2",
           "run": datetime.datetime.now().isoformat(),
           "baseline": "d2b26bc", "cutoff": CUTOFF_2026,
           "counts": {
               "house": len(house_rows),
               "qA_unique": len(qA_tx),
               "qA_in128": len(in128),
               "sale_verified": n_sale_ev,
               "nh_status_verified": n_nh_ev,
               "total_price": n_total, "psf": n_psf,
               "gst_verified": n_gst_ver,
               "pricing_ready": n_ready,
               "assess_completion_128": n_assess,
               "lin_confirmed": n_lin_conf,
               "lin_split": n_lin_split,
               "jump": len(jump_all)}}
    (DOCS / "manifest.json").write_text(
        json.dumps(man, indent=1, ensure_ascii=False))
    print("gates:", {k: v for k, v in man["counts"].items()
                     if k != "house"})
    print("house:", len(house_rows))


if __name__ == "__main__":
    main()
