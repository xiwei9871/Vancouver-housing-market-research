#!/usr/bin/env python3
"""Sales Archive V1 测试 — PURE(archive_core)/ FIXTURE / INTEG"""
import csv, subprocess, sys, hashlib
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

R = Counter()
def chk(n, c, skip=False):
    tag = "SKIP" if skip else ("PASS" if c else "FAIL")
    R[tag] += 1
    print(f"  {tag:4s} {n}")

REQ = [ROOT / "data/research/raw/official/issued-permits-ldh-2017.csv",
       ROOT / "data/research/normalized/standard-residential-comps-v2.csv",
       ROOT / "data/research/raw/location/"
       "bca-arcgis-sales-2025-present-all.json",
       ROOT / "data/research/boundaries/"
       "vancouver-local-area-boundary-official.geojson"]
DATA_OK = all(p.exists() for p in REQ)

# ---------- PURE ----------
import archive_core as AC                          # noqa: E402
chk("PURE 六档+两端", AC.age_band_at_sale(2020, 2023)[0] == "AGE_00_03"
    and AC.age_band_at_sale(1960, 2023)[0] == "AGE_51_PLUS")
chk("PURE 4-10", AC.age_band_at_sale(2015, 2023)[0] == "AGE_04_10")
chk("PURE unknown", AC.age_band_at_sale(None, 2023)[0] == "AGE_UNKNOWN")
chk("PURE conflict", AC.age_band_at_sale(2025, 2023)[0]
    == "AGE_TEMPORAL_CONFLICT")
chk("PURE tax2023 快照优先", AC.pick_yb_at_sale(
    [(2025, "tax2026"), (1990, "tax2023")], 2024) == (1990, "tax2023",
                                                      False))
chk("PURE 全部未来年→最小+冲突", AC.pick_yb_at_sale(
    [(2026, "tax2026"), (2025, "bca_detail")], 2023) ==
    (2025, "bca_detail", True))
chk("PURE 同价合并", AC.same_tx_price(1000000, 1005000))
chk("PURE 价差分裂", not AC.same_tx_price(1000000, 1200000))
chk("PURE tx_key 单元区分",
    AC.tx_key("", "302-1633 11TH AVE W", "2025-10-10") !=
    AC.tx_key("", "304-1633 11TH AVE W", "2025-10-10"))
chk("PURE arc 产品映射", AC.arc_desc_product("2 STY house") == "HOUSE"
    and AC.arc_desc_product("Apartment") == "CONDO"
    and AC.arc_desc_product("Strata Warehouse") == "OTHER")

# ---------- FIXTURE ----------
chk("FIXTURE 档案即对象", callable(AC.tx_key))

# ---------- INTEG ----------
if not DATA_OK:
    for n in ("档案构建", "实体守恒", "产品枚举", "房龄档",
              "瀑布单调", "幂等"):
        chk("INTEG " + n, None, skip=True)
else:
    import sales_archive_v1 as S
    D = S.OUTD
    for f in ("sales_archive.csv", "sales_archive_source_inventory.csv",
              "sales_capture_waterfall.csv",
              "transactions_by_year_product.csv",
              "transactions_by_age_band.csv",
              "transactions_by_community_age_product.csv",
              "transaction_dedup_reconciliation.csv",
              "attribute_history_quality.csv",
              "sales_archive_schema.json", "manifest.json"):
        chk("INTEG 产物 " + f, (D / f).exists())

    el = S.EL
    chk("INTEG 交易唯一 id", len({e["transaction_id"]
                                  for e in el}) == len(el))
    chk("INTEG 产品枚举合法",
        {e["product"] for e in el} <= {
            "HOUSE", "DUPLEX_HALF", "DUPLEX_WHOLE", "MULTIPLEX_UNIT",
            "MULTIPLEX_WHOLE", "TOWNHOUSE", "CONDO",
            "OTHER_RESIDENTIAL", "UNKNOWN"})
    chk("INTEG 房龄档枚举合法",
        {e["age_band"] for e in el} <= {
            "AGE_00_03", "AGE_04_10", "AGE_11_20", "AGE_21_30",
            "AGE_31_50", "AGE_51_PLUS", "AGE_UNKNOWN",
            "AGE_TEMPORAL_CONFLICT"})
    chk("INTEG sale_kind 枚举合法",
        {e["sale_kind"] for e in el} <= {
            "FIRST_SALE", "RESALE", "PRECONSTRUCTION_OR_LAND",
            "LAND_OR_VACANT", "UNKNOWN"})
    chk("INTEG 2023+窗口",
        all(e["sale_date"] >= "2023" for e in el))
    chk("INTEG 瀑布单调",
        S.wf["captured"] >= S.wf["price_known"] >=
        S.wf["comparable_ready"])
    chk("INTEG 多源实体存在",
        any(len(e["sources"]) > 1 for e in el))
    chk("INTEG 冲突非空(重建时点)", any(e["yb_conflict"] for e in el))
    chk("INTEG dup 标记存在不删", any(e.get("record_note") for e in el))
    chk("INTEG yb 源枚举", {e["yb_source"] for e in el} <=
        {"", "tax2023", "tax2026", "bca_detail", "comps"})

    # 真幂等
    tgt = D / "sales_archive.csv"
    hs = []
    for _ in range(2):
        subprocess.run([sys.executable,
                        str(ROOT / "scripts/sales_archive_v1.py")],
                       cwd=str(ROOT), capture_output=True, check=True)
        hs.append(hashlib.sha256(tgt.read_bytes()).hexdigest())
    chk("INTEG 幂等双 subprocess", hs[0] == hs[1])

print(f"\n{R['PASS']} PASS, {R['FAIL']} FAIL, {R['SKIP']} SKIPPED")
sys.exit(1 if R["FAIL"] else 0)
