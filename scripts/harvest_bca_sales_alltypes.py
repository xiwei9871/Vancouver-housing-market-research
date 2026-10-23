#!/usr/bin/env python3
"""BCA ArcGIS 历史成交全类型拉取(合法 guest-token 通道)。

背景:`bca_arcgis_sales.build_bca_sales_where` 硬编码
`IS_STRATA='N' AND DESCRIPTION LIKE '%house%'`,只取非 strata
独立屋 —— 这就是 Sales Archive V1.0.1 报告"2023/24 无 CONDO"
的误报根因。本脚本用同一 map session/gistoken 通道,仅把
where 换成开放条件(价格带 + LAST_SALE_DATE 窗口),经
parse_bca_sales_features 同一解析管线落盘。

注意:BCA 层仅存每物业 LAST_SALE,期内二次转售的历史行不可见;
该缺口在 archive 覆盖矩阵标 PARTIAL_COVERAGE,不虚报全量。

运行(venv):
  .venv/bin/python scripts/harvest_bca_sales_alltypes.py \
      --start 2023-01-01 --end 2024-12-31 \
      --out data/research/raw/location/\
bca-arcgis-sales-2023-01-01-2024-12-31-alltypes.json
"""
import argparse, json, re, sys
sys.path.insert(0, "src")
from northshore_watchdog.research.bca_arcgis_sales import (  # noqa: E402
    _real_transport, _layer_version, parse_bca_sales_features,
    BCA_MAP_SEARCH_URL, BCA_DEFAULT_MAPSERVER_URL,
    BCA_DEFAULT_BBOX, _iso_now)


def fetch(start, end, min_p=100_000, max_p=50_000_000):
    req = _real_transport(30)
    page = req(BCA_MAP_SEARCH_URL, {})
    page = page.decode() if isinstance(page, bytes) else page
    token = re.search(r"\bgistoken\s*=\s*['\"]([^'\"]+)",
                      page).group(1)
    ms = re.search(r"\bmapserverUrl\s*=\s*['\"]([^'\"]+)", page)
    ms = ms.group(1) if ms else BCA_DEFAULT_MAPSERVER_URL
    qurl = f"{ms.rstrip('/')}/0/query"
    ver = _layer_version(ms)
    where = (f"SALE_PRICE >= {min_p} AND SALE_PRICE <= {max_p} "
             f"AND LAST_SALE_DATE >= DATE '{start}' "
             f"AND LAST_SALE_DATE <= DATE '{end}'")
    rows, off, pages = [], 0, 0
    obs = _iso_now()
    while True:
        p = req(qurl, {
            "where": where,
            "geometry": ",".join(str(v) for v in BCA_DEFAULT_BBOX),
            "geometryType": "esriGeometryEnvelope", "inSR": "4326",
            "spatialRel": "esriSpatialRelIntersects",
            "returnGeometry": "true", "outSR": "4326",
            "outFields": "*", "orderByFields": "OBJECTID",
            "resultOffset": off, "resultRecordCount": 1000,
            "f": "json", "token": token})
        feats = p.get("features", [])
        rows += parse_bca_sales_features(
            p, source_url=qurl, source_version=ver, observed_at=obs)
        pages += 1
        if not p.get("exceededTransferLimit") or len(feats) < 1000:
            break
        off += len(feats)
    return {"schema_version": "bca-arcgis-sales-v1",
            "source_type": "BC_ASSESSMENT", "source_url": qurl,
            "source_version": ver, "observed_at": obs,
            "retrieved_at": _iso_now(),
            "query": {"where": where, "note": "all-types open "
                      "where; layer keeps LAST_SALE only"},
            "pages": pages, "sales": rows}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    snap = fetch(a.start, a.end)
    with open(a.out, "w") as f:
        json.dump(snap, f)
    print(f"{len(snap['sales'])} rows -> {a.out}")
