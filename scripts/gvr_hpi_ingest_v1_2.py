#!/usr/bin/env python3
"""GVR Monthly HPI Ingest V1.2 — 官方月度统计包 → 结构化 HPI 序列。

来源:GVR/REBGV 月度统计包 PDF(members.gvrealtors.ca / members.rebgv.org
及两个公开镜像)。每份 PDF 的 Benchmark 表含:
  Benchmark Price | Price Index | 1M/3M/6M/1Y/3Y/5Y/10Y Change %

序列构造口径:
- DIRECT: 当月指数直接取自该月官方报告(44/45 个月)。
- DERIVED_FROM_CHANGE: 当月 PDF 缺 Benchmark 表时(仅 2025-01,
  重发版为残缺包),用最近可得月份报告的 N-month Change %
  反推 idx_missing = idx_src / (1 + chg/100),并记录全部
  可反推来源做一致性校验。
- 修订审计: 对每个 DIRECT 观测,与所有其它报告 Change 列
  反推值比较 → hpi_revision_crosscheck.csv(版本漂移证据)。

PDF 缓存在 data/research/raw/gvr_hpi_pdf/(gitignored,--fetch 重新下载)。
"""
import csv, json, re, sys, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data/research/raw/gvr_hpi_pdf"
DOCS = ROOT / "docs/audits/house_hpi_normalization_v1_2"
OUTD = ROOT / "data/research/normalized/house_hpi_normalization_v1_2"

MONTHS = [(y, m) for y in (2023, 2024, 2025, 2026)
          for m in range(1, 13) if (y, m) <= (2026, 9)]
PRODUCTS = ("Single Family Detached", "Townhouse", "Apartment")
AREAS = ("Vancouver East", "Vancouver West", "Greater Vancouver")

# (y,m) -> url that returned a valid package
SOURCE_URLS = {
    **{(2023, m): f"https://members.rebgv.org/news/REBGV-Stats-Pkg-{n}-2023.pdf"
       for m, n in enumerate(["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul",
                              "Aug", "Sept", "Oct", "Nov", "Dec"], 1)},
    (2024, 1): "https://members.rebgv.org/news/REBGV-Stats-Pkg-Jan-2024.pdf",
    (2024, 2): "https://members.gvrealtors.ca/news/GVR-Stats-Package-Feb-2024.pdf",
    (2024, 3): "https://members.gvrealtors.ca/news/GVR-Stats-Package-March-2024.pdf",
    (2024, 4): "https://members.gvrealtors.ca/news/GVR-Stats-Package-April-2024.pdf",
    (2024, 5): "https://members.gvrealtors.ca/news/GVR-Stats-Package-May-2024.pdf",
    (2024, 6): "https://members.gvrealtors.ca/news/GVR-Stats-Package-June-2024.pdf",
    (2024, 7): "https://members.gvrealtors.ca/news/GVR-Stats-Package-July-2024.pdf",
    (2024, 8): "https://members.gvrealtors.ca/news/GVR-Stats-Package-Aug-2024.pdf",
    (2024, 9): "https://members.gvrealtors.ca/news/GVR-Stats-Package-Sept-2024.pdf",
    (2024, 10): "https://members.gvrealtors.ca/news/GVR-Stats-Package-Oct-2024.pdf",
    (2024, 11): "https://members.gvrealtors.ca/news/GVR-Stats-Package-Nov-2024.pdf",
    (2024, 12): "https://members.gvrealtors.ca/news/GVR-Stats-Package-Dec-2024.pdf",
    (2025, 1): "https://members.gvrealtors.ca/news/GVR-Stats-Package-Jan-2025.pdf",
    (2025, 2): "https://members.gvrealtors.ca/news/GVR-Stats-Package-Feb-2025.pdf",
    (2025, 3): "https://members.gvrealtors.ca/news/GVR-Stats-Package-Mar-2025.pdf",
    (2025, 4): "https://members.gvrealtors.ca/news/GVR-Stats-Package-Apr-2025.pdf",
    **{(2025, m): f"https://members.gvrealtors.ca/news/GVR-Stats-Package-{n}-2025.pdf"
       for m, n in [(5, "May"), (6, "June"), (7, "July"), (8, "August"),
                    (9, "September"), (10, "October"), (11, "November"),
                    (12, "December")]},
    **{(2026, m): f"https://members.gvrealtors.ca/news/GVR-Stats-Package-{n}-2026.pdf"
       for m, n in [(1, "January"), (2, "February"), (3, "March"), (4, "April"),
                    (5, "May"), (8, "August"), (9, "September")]},
    (2026, 6): "https://members.gvrealtors.ca/news/GVR-Stats-Package-June-2026-1.pdf",
    (2026, 7): "https://www.realestatenorthshore.com/media/uploads/"
               "GVR-Stats-Package-July-2026.pdf",
}
LAGS = {1: 0, 2: 1, 3: 2, 4: 3, 5: 6, 6: 12, 7: 36, 8: 60, 9: 120}  # chg col idx -> months
LAG_MONTHS = [1, 3, 6, 12, 36, 60, 120]

ROW_RE = re.compile(r"^(.*?)\s+\$([\d,]+)\s+([\d.]+)\s+((?:-?\d+\.?\d*%\s*){4,7})\s*$")
HDRS = ["Residential / Composite", *PRODUCTS]


def fetch_pdfs():
    RAW.mkdir(parents=True, exist_ok=True)
    got = []
    for (y, m), url in SOURCE_URLS.items():
        fn = RAW / f"hpi_{y}_{m:02d}.pdf"
        if not fn.exists() or fn.stat().st_size < 50000:
            urllib.request.urlretrieve(url, fn)
        if fn.exists() and fn.stat().st_size > 50000:
            got.append((y, m))
    return got


def parse_pdf(path):
    """yield dict(product, area, bench, idx, chg[7]) for the 3 areas."""
    from pypdf import PdfReader
    cur = None
    out = []
    for page in PdfReader(str(path)).pages:
        text = page.extract_text() or ""
        if "Price Index" not in text:
            continue
        for line in text.split("\n"):
            ls = line.strip()
            for pr in HDRS:
                if ls.startswith(pr + " "):
                    rest = ls[len(pr):]
                    cur = pr
                    if ROW_RE.match(rest):
                        ls = rest
                    break
            mm = ROW_RE.match(ls)
            if not mm:
                continue
            area = mm.group(1).strip()
            if area in AREAS and cur in PRODUCTS:
                chg = [float(x[:-1]) for x in re.findall(r"-?\d+\.?\d*%", mm.group(4))]
                out.append({"product": cur, "area": area,
                            "bench": int(mm.group(2).replace(",", "")),
                            "idx": float(mm.group(3)), "chg": chg})
    return out


def ym_add(y, m, k):
    n = (y * 12 + (m - 1)) + k
    return (n // 12, n % 12 + 1)


def load_observations():
    """{(y,m): {(product,area): obs}}"""
    obs = {}
    for (y, m) in MONTHS:
        fn = RAW / f"hpi_{y}_{m:02d}.pdf"
        if not fn.exists():
            continue
        try:
            rows = parse_pdf(fn)
        except Exception as e:
            print(f"WARN parse fail {fn.name}: {e}", file=sys.stderr)
            continue
        for r in rows:
            r["source_ym"] = f"{y}-{m:02d}"
            obs.setdefault((y, m), {})[(r["product"], r["area"])] = r
    return obs


def build_series(obs):
    """month -> (product,area) -> series row (DIRECT or DERIVED)."""
    series = {}
    deriv_audit = []
    for (y, m) in MONTHS:
        for prod in PRODUCTS:
            for area in AREAS:
                key = (prod, area)
                if (y, m) in obs and key in obs[(y, m)]:
                    o = obs[(y, m)][key]
                    series[(y, m, prod, area)] = {
                        "month": f"{y}-{m:02d}", "geography": area,
                        "property_type": prod, "price_index": o["idx"],
                        "benchmark_price": o["bench"], "quality": "DIRECT",
                        "source_report_month": o["source_ym"],
                        "derived_from": "", "alternate_estimates": ""}
                    continue
                # derive: idx = idx_src / (1+chg), lag in months
                cands = []
                for lag in LAG_MONTHS:
                    sy, sm = ym_add(y, m, lag)
                    src = obs.get((sy, sm), {}).get(key)
                    if not src:
                        continue
                    ci = LAG_MONTHS.index(lag)
                    if ci < len(src["chg"]):
                        cands.append({
                            "src": f"{sy}-{sm:02d}", "lag": lag,
                            "idx": round(src["idx"] / (1 + src["chg"][ci] / 100), 2),
                            "bench": round(src["bench"] / (1 + src["chg"][ci] / 100)),
                        })
                if cands:
                    best = min(cands, key=lambda c: c["lag"])
                    alt = "; ".join(
                        f"{c['src']}({c['lag']}m):{c['idx']}" for c in cands)
                    spread = max(c["idx"] for c in cands) - \
                        min(c["idx"] for c in cands)
                    series[(y, m, prod, area)] = {
                        "month": f"{y}-{m:02d}", "geography": area,
                        "property_type": prod, "price_index": best["idx"],
                        "benchmark_price": best["bench"],
                        "quality": "DERIVED_FROM_CHANGE",
                        "source_report_month": best["src"],
                        "derived_from": f"{best['src']} {best['lag']}-month change",
                        "alternate_estimates": alt}
                    deriv_audit.append({
                        "month": f"{y}-{m:02d}", "product": prod, "area": area,
                        "n_candidates": len(cands), "chosen_idx": best["idx"],
                        "candidate_spread": round(spread, 2),
                        "candidates": alt})
                else:
                    series[(y, m, prod, area)] = {
                        "month": f"{y}-{m:02d}", "geography": area,
                        "property_type": prod, "price_index": "",
                        "benchmark_price": "", "quality": "INDEX_MISSING",
                        "source_report_month": "", "derived_from": "",
                        "alternate_estimates": ""}
    return series, deriv_audit


def revision_crosscheck(obs):
    """Every change-column implies a lag-month index; compare vs DIRECT obs."""
    rows = []
    for (y, m), cell in obs.items():
        for (prod, area), o in cell.items():
            for ci, lag in enumerate(LAG_MONTHS):
                if ci >= len(o["chg"]):
                    continue
                ly, lm = ym_add(y, m, -lag)
                tgt = obs.get((ly, lm), {}).get((prod, area))
                if not tgt:
                    continue
                implied = o["idx"] / (1 + o["chg"][ci] / 100)
                drift = implied - tgt["idx"]
                rows.append({
                    "product": prod, "area": area,
                    "report_month": f"{y}-{m:02d}", "lag_months": lag,
                    "target_month": f"{ly}-{lm:02d}",
                    "published_idx_at_target": tgt["idx"],
                    "implied_idx_from_change": round(implied, 3),
                    "drift_idx_points": round(drift, 3),
                    "drift_pct": round(100 * drift / tgt["idx"], 3)})
    return rows


def main():
    fetch = "--fetch" in sys.argv
    DOCS.mkdir(parents=True, exist_ok=True)
    OUTD.mkdir(parents=True, exist_ok=True)
    if fetch:
        got = fetch_pdfs()
        print(f"fetched/cached {len(got)} PDFs")
    obs = load_observations()
    series, deriv_audit = build_series(obs)
    rev = revision_crosscheck(obs)

    # ---- gvr_monthly_hpi_2023_2026.csv
    with open(DOCS / "gvr_monthly_hpi_2023_2026.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=[
            "month", "geography", "property_type", "price_index",
            "benchmark_price", "quality", "source_report_month",
            "derived_from", "alternate_estimates", "source_url",
            "publication_version"])
        w.writeheader()
        for (y, m, prod, area) in sorted(series, key=lambda k: (k[0], k[1], k[2], k[3])):
            r = dict(series[(y, m, prod, area)])
            y0, m0 = map(int, (r["source_report_month"] or "0-0").split("-"))
            r["source_url"] = SOURCE_URLS.get((y0, m0), "")
            r["publication_version"] = f"GVR Stats Package {r['source_report_month']}"
            w.writerow(r)

    # ---- local audits
    with open(OUTD / "hpi_derivation_audit.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=[
            "month", "product", "area", "n_candidates", "chosen_idx",
            "candidate_spread", "candidates"])
        w.writeheader()
        w.writerows(deriv_audit)
    with open(DOCS / "hpi_revision_crosscheck.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=[
            "product", "area", "report_month", "lag_months", "target_month",
            "published_idx_at_target", "implied_idx_from_change",
            "drift_idx_points", "drift_pct"])
        w.writeheader()
        w.writerows(rev)

    # raw observations (local audit)
    with open(OUTD / "hpi_raw_observations.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["report_month", "product", "area", "benchmark", "index",
                    "chg_1m", "chg_3m", "chg_6m", "chg_1y", "chg_3y",
                    "chg_5y", "chg_10y"])
        for (y, m) in sorted(obs):
            for (prod, area), o in sorted(obs[(y, m)].items()):
                w.writerow([f"{y}-{m:02d}", prod, area, o["bench"], o["idx"],
                            *[c if i < len(o["chg"]) else ""
                              for i, c in enumerate(o["chg"] + [""] * 7)]])

    n_dir = sum(1 for v in series.values() if v["quality"] == "DIRECT")
    n_der = sum(1 for v in series.values() if v["quality"] == "DERIVED_FROM_CHANGE")
    n_mis = sum(1 for v in series.values() if v["quality"] == "INDEX_MISSING")
    print(f"series cells: DIRECT={n_dir} DERIVED={n_der} MISSING={n_mis} "
          f"of {len(series)}")
    drift = [abs(r["drift_pct"]) for r in rev]
    print(f"revision crosscheck: {len(rev)} pairs, max|drift|={max(drift):.2f}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
