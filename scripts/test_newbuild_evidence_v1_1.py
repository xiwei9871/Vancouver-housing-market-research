#!/usr/bin/env python3
"""V1.1 业务单元测试 — 合成 fixture 可独立运行;真实数据存在时做集成断言。"""
import csv, json, subprocess, sys, hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import newbuild_evidence_v1_1 as E   # noqa: E402

OUTD = ROOT / "data/research/normalized/audit_v1_1"
fails = []


def check(name, ok, detail=""):
    print(("PASS" if ok else "FAIL"), name, detail)
    if not ok:
        fails.append(name)


# ---------- A. 合成 fixture 业务规则(无需私有数据) ----------
# A1 产品映射完备:所有已知 product_class 都有显式归属
KNOWN_CLASSES = ["HOUSE_DETACHED", "HOUSE_SUSPECT_STRATA",
                 "DUPLEX_HALF_STRATA", "DUPLEX_WHOLE_BLDG",
                 "MULTIPLEX_3_4_CANDIDATE", "MULTIPLEX_WHOLE_BLDG",
                 "STRATA_UNIT_UNKNOWN", "OTHER"]
check("class_map_complete", all(c in E.CLASS_MAP for c in KNOWN_CLASSES))
check("no_catchall_mpx",
      E.CLASS_MAP["STRATA_UNIT_UNKNOWN"][0] == "STRATA_UNKNOWN" and
      E.CLASS_MAP["OTHER"][0] == "OTHER")

# A2 描述判定:warehouse 不得误判 house
for desc, want in [("Strata Warehouse", "OTHER"),
                   ("Strata Apartment -Hi-Rise", "APARTMENT"),
                   ("Strata Townhouse", "TOWNHOUSE"),
                   ("2 STY Duplex-Semi-Custom", "CONFIRMED_DUPLEX"),
                   ("Mini Warehouse, High Rise", "OTHER"),
                   ("2 STY house - Custom", "CONFIRMED_HOUSE"),
                   ("Apt - Walk Up", "APARTMENT"),
                   ("", "UNRESOLVED")]:
    v, _ = E.desc_verdict(desc)
    check(f"desc[{desc[:20]}]", v == want, f"got {v}")

# A3 地址规范化与基址
check("base_addr_unit", E.base_addr("204-3189 CAMOSUN ST") ==
      "3189 CAMOSUN ST")
check("norm_avenue", E.norm_addr("1185 E 17TH AVENUE, Vancouver") ==
      "1185 17TH AVE E")

# A4 RC 细分
r = {"official_area": "Renfrew-Collingwood", "lat": "49.246"}
check("rc_north", E.rc_split(r) == "Renfrew(北)")
r["lat"] = "49.230"
check("rc_south", E.rc_split(r) == "Collingwood(南)")
r["lat"] = ""
check("rc_unsplit", E.rc_split(r) == "Renfrew-Collingwood(未分)")
r["official_area"] = "Sunset"
check("rc_passthrough", E.rc_split(r) == "Sunset")

# A5 许可解析
check("permit_mpx_units",
      E.parse_units("three-storey plus basement two-unit front building "
                    "and two-storey plus basement one-unit rear building")
      == 3)
p, _ = E.classify_permit("Multiple Dwelling", "")
check("permit_class_mpx", p == "MPX")
p, t = E.classify_permit("Single Detached House w/Sec Suite", "")
check("permit_class_house_suite", p == "HOUSE" and "SEC_SUITE" in t)

# A6 窗口
check("window_2023", E.WIN["2023+"] == "2023-01-01")
check("snapshot", bool(E.SNAPSHOT))

# ---------- B. 集成断言(真实数据存在时) ----------
if (OUTD / "sales_evidence.csv").exists():
    rows = lambda n: list(csv.DictReader(open(OUTD / n)))
    se = rows("sales_evidence.csv")
    wf = rows("coverage_waterfall.csv")
    # B1 守恒 + 口径一致:sales_evidence n == Σ usable_newbuild
    tot = sum(int(r["usable_newbuild"] or 0) for r in wf
              if r["product"] in ("HOUSE", "DUPLEX", "MPX"))
    check("sales_eq_waterfall", len(se) == tot, f"{len(se)} vs {tot}")
    # B2 保守:每行 comp_records = asof_ok + x_asof
    bad = [r for r in wf if r["comp_records"] and
           int(r["comp_records"]) != int(r["asof_ok"] or 0) +
           int(r["x_asof"] or 0)]
    check("waterfall_conservation", not bad, str(bad[:2]))
    # B3 锚点不出现在基准:独立复算 _CITY HOUSE 2800-3400 2023+
    bm = {r["key"]: r for r in rows("regional_benchmark_v1_1.csv")}
    cell = bm.get("_CITY|HOUSE|2800-3400|2023+")
    if cell:
        n_indep = sum(1 for r in se if r["product"] == "HOUSE"
                      and 2800 <= float(r["floor_above_sf"]) < 3400
                      and r["sale_date"] >= "2023-01-01"
                      and E.norm_addr(r["address"]) not in E.ANCHOR_EXCLUDE)
        check("anchor_excluded_bench", int(cell["n_sales"]) == n_indep,
              f"cell {cell['n_sales']} vs indep {n_indep}")
    # B4 MPX 诚实:无 MPX usable → 影子全 NOT_ESTIMABLE
    mpx_sh = [r for r in rows("shadow_impact.csv")
              if r["plan"] in ("TRIPLEX", "FOURPLEX", "MULTIPLEX")]
    check("mpx_not_estimable",
          all(r["grade"] == "NOT_ESTIMABLE" for r in mpx_sh),
          f"{sum(1 for r in mpx_sh if r['grade']!='NOT_ESTIMABLE')} 误估")
    # B5 strata 全分类
    sa = [r for r in rows("strata_classification_audit.csv")
          if not r["address"].startswith("[EXCLUDED")]
    check("strata_all_classified", len(sa) == 635 and
          all(r["verdict"] for r in sa))
    # B6 排除审计全量
    ax = rows("arcgis_excluded_audit.csv")
    check("arcgis_excl_audited", len(ax) == 5613 and
          all(r["verdict"] for r in ax))
    # B7 permit↔project 分离
    pr = rows("permit_project_reconciliation.csv")
    check("permit_project_split", len(pr) < 9944 and
          sum(int(r["n_permits"]) for r in pr) == 9944,
          f"projects={len(pr)}")
    # B8 manifest 含全部输入 sha256
    m = json.loads((OUTD / "manifest.json").read_text())
    check("manifest_hashes", len(m["input_sha256"]) >= 7)
    # B9 幂等
    h1 = {f: hashlib.sha256((OUTD / f).read_bytes()).hexdigest()
          for f in ["sales_evidence.csv", "regional_benchmark_v1_1.csv",
                    "shadow_impact.csv", "coverage_v1_vs_v1_1.csv"]}
    subprocess.run([sys.executable, "scripts/newbuild_evidence_v1_1.py"],
                   cwd=ROOT, check=True, capture_output=True)
    h2 = {f: hashlib.sha256((OUTD / f).read_bytes()).hexdigest() for f in h1}
    check("idempotent", h1 == h2)

print(f"\n{len(fails)} failed")
sys.exit(1 if fails else 0)
