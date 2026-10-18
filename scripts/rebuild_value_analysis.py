#!/usr/bin/env python3
"""Rebuild-value analysis for watchlist candidates.

For each active candidate that clears the land gate (lot >= 3600 sqft,
frontage >= 30 ft), model three rebuild scenarios and estimate end value
against nearby NEW-home sales (BCA official sales layer):

  1. HOUSE   — 0.70 FSR
  2. DUPLEX  — 0.70 FSR
  3. TRIPLEX — 1.00 FSR (multiplex; requires lot >= multiplex_min_lot_sqft
               in a standard residential zone)

Comps: BCA ArcGIS sales of NEW homes (year_built >= `new_home_min_year`),
sold inside the trailing `comp_window_days`. Because new-build sales are
sparse, comps resolve hierarchically: 500 m radius -> official local area
-> market region, whichever first reaches `MIN_COMPS` samples; the level
used is reported per property. $/sqft = sale_price / above-grade floor area
(mean used for valuation; median shown for reference).

Build cost tiers: Vancouver Westside $400/$500, elsewhere $300/$400 —
both reported per scenario. ROI = (est_sale - asking - build_cost) /
(asking + build_cost). Excludes financing/permits/holding by design —
flagged in the report note.

Outputs reports/watchlist/rebuild-value-<scan>.html + .json.
"""

from __future__ import annotations

import glob
import json
import math
import re
import statistics
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import render_market_analytics as base

ROOT = Path(__file__).resolve().parents[1]
SALES_FILE = ROOT / "data/research/raw/location/bca-arcgis-sales-2026-09-06-enriched.json"
SALES_GEO_FILE = ROOT / "data/research/raw/location/bca-arcgis-sales-2026-09-06-geometry.json"
# Supplemental pool: last-year Vancouver sales pre-filtered to likely new
# builds (improvement share >= 30%) and enriched in bulk — much deeper than
# the original 300-record enrichment.
SALES_EXTRA_FILES = [
    ROOT / "data/research/raw/location/bca-sales-24mo-newbuild-enriched.json",
    ROOT / "data/research/raw/location/bca-sales-2023band-newbuild-enriched.json",
]
BOUNDARIES_FILE = ROOT / "data/research/boundaries/vancouver-market-boundaries-2026-09-05.json"
OUT_DIR = ROOT / "reports/watchlist"

FSR = {"HOUSE": 0.70, "DUPLEX": 0.70, "TRIPLEX": 1.00}
# Vancouver multiplex eligibility (R1-1 / RT-7,9 / R3 / R4-1 / R5 /
# First Shaughnessy — Low Density Housing Options guide).  3 units
# require ALL of: area >= 306 m², frontage >= 10 m, depth >= 30.4 m.
# Both frontage and area gates are statutory, not design judgement.
MPX_MIN_AREA_SQFT = 3294.0       # 306 m² — base multiplex tier (3-4 units)
MPX_MIN_FRONTAGE_FT = 32.8       # 10.0 m
MPX_MIN_DEPTH_FT = 99.7          # 30.4 m
MPX_MIN4_AREA_SQFT = 4984.0      # >463 m² OR >13.3 m frontage -> min 4 units
MPX_MIN4_FRONTAGE_FT = 43.6      # 13.3 m
MPX_5U_AREA_SQFT = 4995.0        # 464 m² + 13.4 m frontage -> up to 5 units
MPX_5U_FRONTAGE_FT = 44.0
MPX_6U_AREA_SQFT = 5996.0        # 557 m² + 15.1 m frontage -> up to 6 units
MPX_6U_FRONTAGE_FT = 49.5
DUPLEX_MIN_AREA_SQFT = 3294.0    # bylaw min site area for duplex use
# Zones eligible for multiplex per the city schedule; RM zones permit
# multiple dwellings under their own schedules (no modeled minimum).
MPX_ZONES = ("R1", "RS", "RA", "RR", "RT", "R3", "R4", "R5", "FSD")

# Per-zone scenario rules: (house_fsr, duplex_fsr, triplex_fsr, mpx_zone)
# mpx_zone = multiplex eligibility governed by the statutory table above;
# RM zones permit multiple dwellings under their own schedules (no
# modeled minimum — approval still needed).
def zoning_rules(z):
    z = (z or "").strip().upper()
    if z.startswith(("R1", "RS", "RA", "RR")):
        return (0.70, 0.70, 1.00, True)
    if z.startswith("R3"):
        return (0.60, 0.60, 1.00, True)    # duplex zone
    if z.startswith("RT"):
        return (0.70, 0.75, 1.00, True)    # two-family/rowhouse
    if z.startswith(("R4", "R5", "FSD")):
        return (0.70, 0.75, 1.00, True)
    if z.startswith("RM"):
        return (0.70, 0.85, 1.00, "rm")    # own schedule — no modeled min
    return (0.70, None, None, False)       # C/CD/other: house only
RADIUS_M = 500
COMP_WINDOW_DAYS = 1095  # 36 months — deeper new-build sample per community
NEW_HOME_MIN_YEAR = 2020
MIN_COMPS = 3
COST_TIERS = {"vancouver-westside": (400, 500), "_default": (300, 400)}
HOUSE_BUILD_COST = 350     # single-detached all-in $/sf — user-set, uniform
DUPLEX_BUILD_COST = 400    # duplex all-in $/sf — user-set, uniform citywide
MPX_BUILD_COST = 450       # multiplex all-in $/sf — user-set, uniform citywide
MPX_PRODUCT_FACTOR = 0.85  # stacked/small multiplex unit vs the ground-
                           # oriented townhouse/duplex comps in the strata
                           # pool: no private yard, more shared walls —
                           # clearing price ~15% below pool evidence
# Valuation $/sqft: per-community, measured from new-build comps with a
# conservative haircut vs observed mean; hoods without comps fall back to
# the regional conservative rate (east 1100 / south+central 1200 / west 1300).
REGION_PSQFT = {
    "vancouver-east": 1100,
    "vancouver-south": 1200,
    "vancouver-central": 1200,
    "vancouver-oakridge": 1200,
    "vancouver-westside": 1300,
}
CONSERVATIVE_DISCOUNT = 0.80  # applied to measured hood comp means
HOOD_MIN_COMPS = 2  # user: n=2 comps is enough to adopt a hood rate
# Analyst overrides — comp means skewed by product mix / thin samples,
# calibrated against income/amenity fundamentals. Review quarterly.
HOOD_OVERRIDES = {
    "Riley Park": 1350,          # measured 1888 inflated by premium Main-st builds
    "Sunset": 1250,              # level with Renfrew-Collingwood, below Kits tier
    "Mount Pleasant": 1300,      # central location warrants above Sunset tier
    "Hastings-Sunrise": 1300,    # comps cluster in north/water pocket; conservative
    "Renfrew-Collingwood": 1300, # comps cluster in Trout-Lake north edge; conservative
}


OUTLIER_DEVIATION = 0.40  # drop comps >40% from the hood median $/sqft


def hood_rates(comps):
    """Measured new-build $/sqft per official local area, discounted.

    Deviation-based outlier filter: comps more than OUTLIER_DEVIATION BELOW
    the hood median are excluded before averaging (a $590/sf sale in a
    $1,500 median hood is a data anomaly, not market evidence).  High-side
    values are kept — expensive sales are legitimate market evidence.
    """
    import collections
    by = collections.defaultdict(list)   # hood -> [(idx_in_hood_pool, psqft, w_age)]
    pools = collections.defaultdict(list)
    for c in comps:
        if c["floor_sqft"] >= COMP_MIN_FLOOR_SQFT and math.isfinite(c["psqft"]):
            pools[c["hood"]].append(c)
    for hood, hood_cs in pools.items():
        for i, c in enumerate(hood_cs):
            by[hood].append((i, c["psqft"], c.get("w_age", 1.0)))
    rates, stats, kept_comps = {}, {}, {}
    for hood, vs in by.items():
        hood_cs = pools[hood]
        # adoption pool: detached product at our build size — new-house $/sf
        # is the product we actually model; small strata units and mega-lots
        # distort the mean in opposite directions.  Fall back to all comps
        # when the detached pool is too thin.
        det_idx = [i for i, v, _ in vs
                   if not hood_cs[i]["strata"] and hood_cs[i]["floor_sqft"] <= 3500]
        use_idx = det_idx if len(det_idx) >= 3 else list(range(len(vs)))
        use_vals = [vs[i][1] for i in use_idx]
        med = statistics.median(use_vals)
        kept_idx = [i for i in use_idx
                    if vs[i][1] >= med * (1 - OUTLIER_DEVIATION)] or use_idx
        kept = [vs[i] for i in kept_idx]
        wsum = sum(w for _, _, w in kept)
        wmean = sum(v * w for _, v, w in kept) / wsum
        kept_vals = [v for _, v, _ in kept]
        kept_comps[hood] = hood_cs  # spatial pool stays full
        # product-fit evidence: strata multiplex units vs detached new builds
        str_vals = [v for i, v, _ in vs if hood_cs[i]["strata"]]
        stats[hood] = {"n": len(kept), "n_dropped": len(use_idx) - len(kept),
                       "n_all": len(vs), "n_det": len(det_idx),
                       "n_strata": len(str_vals),
                       "strata_med": statistics.median(str_vals) if str_vals else None,
                       "mean": wmean,
                       "median": statistics.median(kept_vals),
                       "min": min(kept_vals), "max": max(kept_vals)}
        if len(kept) >= HOOD_MIN_COMPS:
            rates[hood] = round(stats[hood]["median"] * CONSERVATIVE_DISCOUNT / 25) * 25
    rates.update(HOOD_OVERRIDES)
    # region-level strata/detached ratios as fallback for thin hoods
    reg = {}
    for c in comps:
        if c["floor_sqft"] >= COMP_MIN_FLOOR_SQFT and math.isfinite(c["psqft"]):
            r = c.get("region") or "unknown"
            e = reg.setdefault(r, {"det": [], "str": []})
            e["str" if c["strata"] else "det"].append(c["psqft"])
    region_stats = {}
    for r, e in reg.items():
        if e["str"] and e["det"]:
            region_stats[r] = {
                "n_strata": len(e["str"]),
                "ratio": statistics.median(e["str"]) / statistics.median(e["det"]),
                "strata_med": statistics.median(e["str"]),
            }
    stats["_region"] = region_stats
    kept_comps["_ALL"] = [c for cs in kept_comps.values() for c in cs]
    return rates, stats, kept_comps


def strata_prior(hood, region, stats):
    """Strata-product $/sqft prior for the fallback ladder — hood strata
    median first, then region strata median (both conservatively
    discounted like the detached hood rates).  Returns (value, label)
    or (None, None) when no strata evidence exists at all.
    """
    s = stats.get(hood) or {}
    if s.get("strata_med"):
        return s["strata_med"] * CONSERVATIVE_DISCOUNT, "社区"
    r = (stats.get("_region") or {}).get(region) or {}
    if r.get("strata_med"):
        return r["strata_med"] * CONSERVATIVE_DISCOUNT, "区域"
    return None, None


KERNEL_M = 400.0  # gaussian bandwidth for distance-weighted local pricing
# Size-decay exponents fitted on the comp set (log psqft ~ log floor_sf):
# detached raw -0.74 / within-hood fixed-effects -0.63 — location explains
# only part of it, the size discount is real; -0.55 adopted (top size bin
# is n=11 and mega-homes carry design variance we can't see); strata ~0
# measured, -0.15 adopted (mild — buyers still hit a total-price ceiling).
SIZE_K_DETACHED = -0.55
SIZE_K_STRATA = -0.15
SIZE_ADJ_CLIP = (0.65, 1.45)  # bound extrapolation beyond observed sizes
HOUSE_MAX_SQFT = 4500.0  # spec-house utility ceiling; extra land is garden


def _size_adj(target_sqft, comp_sqft, k):
    """Reprice a comp's $/sf onto the target unit size via the fitted
    power curve.  comp and target at the same size -> factor 1."""
    if not target_sqft or not comp_sqft:
        return 1.0
    f = (target_sqft / comp_sqft) ** k
    return min(max(f, SIZE_ADJ_CLIP[0]), SIZE_ADJ_CLIP[1])


def local_rate(lat, lng, hood, region, kept_comps, hood_psqft,
               unit_sqft=0, size_k=SIZE_K_DETACHED, strata=False,
               stats=None):
    """Distance-weighted comp price around the property.

    Product-matched pools: detached scenarios price off detached comps,
    strata scenarios (duplex/multiplex units) off strata comps — the
    strata price already embeds the small-unit premium and product-form
    discount, so no separate product factor is applied (applying one
    would double-count the size effect the ratio itself contains).
    Each comp's $/sf is repriced onto the scenario's UNIT size through
    the fitted size-decay curve.

    Fallback ladder for strata: kernel-weighted local strata comps ->
    hood strata median -> region strata median -> detached pool flagged
    "推断" (no strata evidence anywhere).  Returns (rate, source_label).
    """
    cs = kept_comps.get("_ALL") or []
    pool = [c for c in cs if c["strata"] == strata]
    prior, prior_label = None, None
    if strata:
        prior, prior_label = strata_prior(hood, region, stats or {})
    else:
        prior = hood_psqft.get(hood) or REGION_PSQFT.get(region)
        prior_label = "社区" if hood in hood_psqft else "区域"

    def weighted(pool):
        ws, ps = [], []
        for c in pool:
            w = math.exp(-(_haversine_m(lat, lng, c["lat"], c["lng"]) / KERNEL_M) ** 2) \
                * c.get("w_age", 1.0)
            ws.append(w)
            ps.append(c["psqft"] * _size_adj(unit_sqft, c["floor_sqft"], size_k))
        sw = sum(ws)
        sww = sum(w * w for w in ws)
        if sw <= 0 or sww <= 0:
            return None, 0.0
        eff_n = sw ** 2 / sww
        return sum(w * p for w, p in zip(ws, ps)) / sw, eff_n

    local_mean, eff_n = weighted(pool)
    det_fallback = False

    # Require a real local signal (eff_n >= 2) before deviating from the
    # prior — one thin comp must not drag the price.
    if local_mean is None or eff_n < 2.0:
        if strata and prior is None:
            # no strata evidence at any level — fall back to the detached
            # pool so the scenario still prices, flagged as inferred
            pool = [c for c in cs if not c["strata"]]
            local_mean, eff_n = weighted(pool)
            prior = hood_psqft.get(hood) or REGION_PSQFT.get(region)
            prior_label = "推断"
            det_fallback = True
        if local_mean is None or eff_n < 2.0:
            rate = prior
            if rate and unit_sqft:
                # priors are measured at typical sizes (detached ~2,500sf,
                # strata ~1,500sf); reprice onto the scenario's unit size
                ref = 1500.0 if strata else 2500.0
                rate = rate * _size_adj(unit_sqft, ref, size_k)
            return rate, prior_label or "区域"
    local_mean *= CONSERVATIVE_DISCOUNT
    blend = min(1.0, eff_n / 4.0)
    ref = 1500.0 if strata else 2500.0
    prior_adj = (prior * _size_adj(unit_sqft, ref, size_k)
                 if prior and unit_sqft else prior)
    rate = blend * local_mean + (1 - blend) * (prior_adj or local_mean)
    if det_fallback:
        label = "推断"
    else:
        label = "本地" if blend >= 0.5 else (prior_label or "区域")
    if not strata and hood in HOOD_OVERRIDES and label == "社区":
        label = "校准"
    if strata:
        label = f"{label}·strata" if not det_fallback else "推断·无strata"
    return rate, label
PERMIT_PSQFT = 40        # design/permits — user-calibrated (~$0.2M per 5,000sf)
SELLING_PCT = 0.04       # commissions/legal — ~4% of gross sale
GST_PCT = 0.05           # GST on new construction — cost-side wedge
# Financing split by capital type (user model): land sits on a mortgage
# (~5%/yr), construction is 35% own equity (3.5%/yr opportunity cost) +
# 65% construction loan (~10%/yr), over a ~2yr build+sell window.
FIN_LAND_PCT = 0.05 * 2
FIN_BUILD_PCT = (0.35 * 0.035 + 0.65 * 0.10) * 2
LOT_GATE_SQFT = 3600
FRONTAGE_GATE_FT = 30

# BCA lookups that official enrichment could not resolve (ambiguous
# addresses).  Queried directly against the ArcGIS parcel layer on
# 2026-10-14 — address verified incl. E/W direction.
MANUAL_BCA_OVERRIDES = {
    "4343 11TH AVENUE": 2462000,       # 4343 11TH AVE W VANCOUVER (land 2,345,000)
    "3135-3137 HEATHER STREET": 1082400,  # 3135 HEATHER ST duplex parcel
}

# Lot dimensions for rows missing frontage in the feed, fetched from BCA:
# frontage/depth from the property-information page where published,
# otherwise derived from the ArcGIS parcel polygon (oriented bounding rect).
DIM_OVERRIDES_PATH = Path("data/research/raw/location/manual-dimension-overrides.json")
LAND_VALUES_PATH = Path("data/research/raw/location/bca-land-values.json")

def _load_dim_overrides() -> dict:
    if DIM_OVERRIDES_PATH.exists():
        return json.loads(DIM_OVERRIDES_PATH.read_text(encoding="utf-8"))
    return {}

def _load_land_values() -> dict:
    if LAND_VALUES_PATH.exists():
        return json.loads(LAND_VALUES_PATH.read_text(encoding="utf-8"))
    return {}

CSS = base.CSS + """
.roi-pos{color:#0a7a3f;font-weight:700}
.roi-neg{color:#b3261e;font-weight:700}
tr.best td{background:#f2f8f3}
#proptable th{cursor:pointer;user-select:none}
th .arrow{color:#999;font-size:10px;margin-left:3px}
tr.filters input,tr.filters select{width:100%;box-sizing:border-box;font-size:11px;padding:3px 5px;border:1px solid #ddd;border-radius:4px}
tr.filters td{padding:4px 6px;border-bottom:2px solid #ddd}
"""

TABLE_JS = """
const table = document.getElementById('proptable');
const tbody = table.querySelector('tbody') || table;
const dataRows = Array.from(tbody.querySelectorAll('tr')).filter(r => !r.classList.contains('filters'));
let sortCol = -1, sortDir = 1;

function cellVal(row, i) {
  const c = row.cells[i];
  return c ? c.innerText.trim() : '';
}
function numOf(t) {
  const m = t.replace(/,/g, '').match(/-?\\d+\\.?\\d*/);
  return m ? parseFloat(m[0]) : null;
}
function sortBy(i) {
  if (sortCol === i) sortDir *= -1; else { sortCol = i; sortDir = 1; }
  table.querySelectorAll('th .arrow').forEach(a => a.textContent = '');
  const th = table.querySelectorAll('th')[i];
  th.querySelector('.arrow').textContent = sortDir === 1 ? '▲' : '▼';
  dataRows.sort((a, b) => {
    const ta = cellVal(a, i), tb = cellVal(b, i);
    const na = numOf(ta), nb = numOf(tb);
    if (na !== null && nb !== null) return (na - nb) * sortDir;
    return ta.localeCompare(tb, 'zh') * sortDir;
  });
  dataRows.forEach(r => tbody.appendChild(r));
}
function matchCell(t, q) {
  q = q.trim();
  if (!q) return true;
  const cmp = q.match(/^(>=|<=|>|<|=)?\\s*(.+)$/);
  const val = cmp[2], op = cmp[1];
  if (op) {
    const n = numOf(t), target = parseFloat(val.replace('%','').replace(',',''));
    if (n === null || isNaN(target)) return false;
    // percent columns: user types e.g. >30 meaning 30%
    const v = t.includes('%') && Math.abs(n) < 2 ? n * 100 : n;
    if (op === '>') return v > target;
    if (op === '<') return v < target;
    if (op === '>=') return v >= target;
    if (op === '<=') return v <= target;
    return v === target;
  }
  const vv = val.toLowerCase();
  if (vv.includes('|')) return vv.split('|').some(v => t.toLowerCase().includes(v));
  return t.toLowerCase().includes(vv);
}
function applyFilters() {
  const inputs = table.querySelectorAll('tr.filters input, tr.filters select');
  const cells = [];
  const firstDataRow = dataRows[0];
  dataRows.forEach(r => {
    let show = true;
    inputs.forEach((inp, i) => {
      if (inp.value && !matchCell(cellVal(r, i), inp.value)) show = false;
    });
    r.style.display = show ? '' : 'none';
  });
  const vis = dataRows.filter(r => r.style.display !== 'none').length;
  document.getElementById('viscount').textContent = vis + ' / ' + dataRows.length;
}
table.querySelectorAll('th').forEach((th, i) => {
  const a = document.createElement('span'); a.className = 'arrow'; th.appendChild(a);
  th.addEventListener('click', () => sortBy(i));
});
table.querySelectorAll('tr.filters input, tr.filters select').forEach(inp => {
  inp.addEventListener('input', applyFilters);
  inp.addEventListener('change', applyFilters);
});
"""


def _num(v):
    try:
        return float(str(v).replace(",", "")) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _floor_area(rec) -> float | None:
    total = sum(
        _num(rec.get(k)) or 0
        for k in ("bca_first_floor_area_sqft", "bca_second_floor_area_sqft",
                  "bca_third_floor_area_sqft", "bca_other_floor_area_sqft")
    )
    return total or _num(rec.get("finished_sqft")) or _num(rec.get("floor_area_sqft"))


def _haversine_m(a_lat, a_lng, b_lat, b_lng) -> float:
    r = 6371000.0
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dp = math.radians(b_lat - a_lat)
    dl = math.radians(b_lng - a_lng)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def load_region_polys():
    from shapely.geometry import Point, shape

    b = json.load(open(BOUNDARIES_FILE))
    polys = []
    for r in b["regions"]:
        g = r.get("official_geometry") or r.get("geometry")
        if g:
            polys.append((r["boundary_id"], shape(g)))

    def region_of(lng, lat):
        if not isinstance(lng, (int, float)):
            return "unknown"
        pt = Point(lng, lat)
        for n, p in polys:
            if p.contains(pt):
                return n
        return "unknown"

    return region_of


def load_new_home_comps(region_of):
    # enriched.json carries year_built + floor areas + price but no coords;
    # geometry.json carries lat/lng for the same record set — join on afp_oid.
    geo = {}
    if SALES_GEO_FILE.exists():
        for s in json.load(open(SALES_GEO_FILE)).get("sales", []):
            oid = s.get("bca_afp_oid") or s.get("pid")
            if oid:
                geo[oid] = (s.get("latitude"), s.get("longitude"))
    d = json.load(open(SALES_FILE))
    sales = [s for s in d.get("sales", []) if s.get("sale_date") and s.get("sale_price")]
    seen = {s.get("record_id") for s in sales}
    for extra in SALES_EXTRA_FILES:
        if not extra.exists():
            continue
        for s in json.load(open(extra)).get("sales", []):
            if s.get("sale_date") and s.get("sale_price") and s.get("record_id") not in seen:
                seen.add(s.get("record_id"))
                sales.append(s)
    max_date = max(date.fromisoformat(s["sale_date"][:10]) for s in sales)
    cutoff = max_date - timedelta(days=COMP_WINDOW_DAYS)
    comps = []
    for s in sales:
        sd = date.fromisoformat(s["sale_date"][:10])
        yb = _num(s.get("year_built"))
        area = _floor_area(s)
        lat, lng = _num(s.get("latitude")), _num(s.get("longitude"))
        if lat is None:
            g = geo.get(s.get("bca_afp_oid")) or geo.get(s.get("pid")) or (None, None)
            lat, lng = _num(g[0]), _num(g[1])
        if sd < cutoff or not yb or yb < NEW_HOME_MIN_YEAR or not area or not lat:
            continue
        if (s.get("municipality") or "") != "City of Vancouver":
            continue
        addr = s.get("address") or ""
        legal = (s.get("legal_description") or "").upper()
        strata = (
            "STRATA LOT" in legal
            or "PLAN EPS" in legal or "PLAN BCS" in legal
            or (s.get("official_bca_is_strata") or "").strip().upper() == "Y"
            or bool(re.match(r"^\d+\s*-", addr.strip()))
        )
        age_days = (max_date - sd).days
        comps.append({
            "address": addr,
            "strata": strata,
            "sale_price": _num(s["sale_price"]),
            "sale_date": s["sale_date"],
            # recency weight: 18-month half-life so older-window comps
            # inform coverage without dragging the price level down
            "w_age": 0.5 ** (age_days / 540.0),
            "year_built": yb,
            "floor_sqft": area,
            "psqft": _num(s["sale_price"]) / area,
            "lat": lat, "lng": lng,
            "hood": base.hood_from_lnglat(lng, lat) or "未知",
            "region": region_of(lng, lat),
        })
    return comps, max_date, cutoff


def resolve_comps(lat, lng, hood, region, comps, strata=None):
    """Hierarchical comp set within a product class: 500m -> hood -> region."""
    pool = comps if strata is None else [c for c in comps if c["strata"] == strata]
    near = [c for c in pool if _haversine_m(lat, lng, c["lat"], c["lng"]) <= RADIUS_M]
    if len(near) >= MIN_COMPS:
        return near, "500m"
    hood_set = [c for c in pool if c["hood"] == hood]
    if len(hood_set) >= MIN_COMPS:
        return hood_set, f"社区:{hood}"
    region_set = [c for c in pool if c.get("region") == region]
    return region_set, f"区域:{region}"


CARD_DIRS = sorted(
    glob.glob(str(ROOT / "reports/calibration/*/cards")), reverse=True
)


def card_link(mls_list) -> str | None:
    for mls in mls_list:
        if not mls:
            continue
        for d in CARD_DIRS:
            if (ROOT / d / f"mls-{mls}.html").exists():
                return f"../{d.split('reports/')[1]}/mls-{mls}.html"
    return None


def std_zone(z):
    return (z or "").strip().upper().startswith(STD_ZONE_PREFIX)


# Product price gradient on the same lot: whole detached commands the
# highest $/sqft; strata units sell at a measured discount (pooled
# strata/whole ratio 0.87 in our comp set, per-hood 0.80–0.93).
# Product-class comp matching: HOUSE sells as one whole-detached unit,
# DUPLEX/TRIPLEX sell as strata units (higher $/sqft, land cost split).
# Strata detection leans on legal_description ("STRATA LOT/PLAN EPS/BCS")
# because official_bca_is_strata is unreliable in this dataset.
SCENARIO_COMP_STRATA = {"HOUSE": False, "DUPLEX": True, "MULTIPLEX": True}
# Exclude micro-units (coach houses / smallest multiplex unit) — they
# carry inflated $/sqft that doesn't transfer to a whole project.
COMP_MIN_FLOOR_SQFT = 1000


def _comp_pool(comps, strata):
    return [
        c for c in comps
        if c["strata"] == strata and c["floor_sqft"] >= COMP_MIN_FLOOR_SQFT
    ]


def resolve_comps(lat, lng, hood, region, comps, strata=None):
    """Hierarchical comp set within a product class: 500m -> hood -> region."""
    pool = _comp_pool(comps, strata) if strata is not None else comps
    near = [c for c in pool if _haversine_m(lat, lng, c["lat"], c["lng"]) <= RADIUS_M]
    if len(near) >= MIN_COMPS:
        return near, "500m"
    hood_set = [c for c in pool if c["hood"] == hood]
    if len(hood_set) >= MIN_COMPS:
        return hood_set, f"社区:{hood}"
    region_set = [c for c in pool if c.get("region") == region]
    return region_set, f"区域:{region}"


def analyse(row, lat, lng, hood, region, comps, hood_psqft, kept_comps, hood_stats=None):
    lot = row["lot_sqft"] or 0
    tiers = COST_TIERS.get(region, COST_TIERS["_default"])
    house_fsr, duplex_fsr, triplex_fsr, mpx_zone = zoning_rules(row.get("zoning"))
    fsr_map = {"HOUSE": house_fsr, "DUPLEX": duplex_fsr,
               "MULTIPLEX": triplex_fsr}
    scenarios = {}
    frontage = row.get("frontage_ft")
    depth = row.get("depth_ft")
    mpx_ok = (  # statutory table — all gates must hold
        mpx_zone is not None
        and lot >= MPX_MIN_AREA_SQFT
        and (frontage is None or frontage >= MPX_MIN_FRONTAGE_FT)
        and (depth is None or depth >= MPX_MIN_DEPTH_FT)
    )
    # Unit-count rules from the R1-1 guide: base tier allows 3-4 units;
    # sites beyond 463 m² OR 13.3 m frontage require a MINIMUM of 4
    # units; 13.4m+464m² allows up to 5; 15.1m+557m² up to 6 (8 rental).
    big_site = (frontage is not None and frontage > MPX_MIN4_FRONTAGE_FT) \
        or lot > MPX_MIN4_AREA_SQFT
    min_units = 4 if big_site else 3
    if frontage is not None and frontage >= MPX_6U_FRONTAGE_FT \
            and lot >= MPX_6U_AREA_SQFT:
        max_units = 6
    elif frontage is not None and frontage >= MPX_5U_FRONTAGE_FT \
            and lot >= MPX_5U_AREA_SQFT:
        max_units = 5
    else:
        max_units = 4
    unit_range = f"{min_units}户" if min_units == max_units \
        else f"{min_units}-{max_units}户"
    for name, fsr in fsr_map.items():
        eligible = lot > 0 and fsr is not None
        if name == "MULTIPLEX" and not mpx_ok:
            eligible = False
        if name == "DUPLEX" and mpx_zone is True and lot < DUPLEX_MIN_AREA_SQFT:
            eligible = False
        build_sqft = lot * (fsr or 0)
        if name == "HOUSE":
            # spec-house demand has a utility ceiling — beyond ~4,500sf the
            # marginal buyer disappears; leftover land stays as garden /
            # land-bank and is not modelled as saleable GFA.
            build_sqft = min(build_sqft, HOUSE_MAX_SQFT)
        units = {"HOUSE": 1, "DUPLEX": 2, "MULTIPLEX": min_units}[name]
        sc = {"fsr": fsr, "build_sqft": build_sqft, "eligible": eligible,
              "units": units, "unit_range": unit_range if name == "MULTIPLEX" else None}
        if eligible:
            # like-for-like pools: house prices off detached comps,
            # duplex/multiplex units off actual strata unit sales — the
            # strata price already contains the small-unit premium and
            # product-form discount, so no product factor is applied.
            # Each comp is repriced to the scenario's unit size through
            # the fitted decay curve.
            unit_sqft = build_sqft / units
            is_strata = name != "HOUSE"
            size_k = SIZE_K_DETACHED if name == "HOUSE" else SIZE_K_STRATA
            base_rate, rate_label = local_rate(lat, lng, hood, region,
                                               kept_comps, hood_psqft,
                                               unit_sqft, size_k,
                                               strata=is_strata,
                                               stats=hood_stats or {})
            psqft = base_rate or 0
            if name == "MULTIPLEX":
                # strata pool is townhouse/duplex-weighted (ground-oriented,
                # bigger units, private yards); a stacked multiplex unit
                # clears below pool evidence — product-form discount
                psqft *= MPX_PRODUCT_FACTOR
                sc["mpx_factor"] = MPX_PRODUCT_FACTOR
            if is_strata:
                # evidence level for the report's provenance marker:
                # strata comps local/hood/region -> proven; detached
                # fallback -> unproven
                sc["mpx_proven"] = ("none" if "推断" in rate_label
                                    else "region" if "区域" in rate_label
                                    else "hood")
            sc["price_src"] = rate_label
            # keep comp stats for reference even though valuation is fixed
            near, level = resolve_comps(lat, lng, hood, region, comps,
                                        strata=SCENARIO_COMP_STRATA[name])
            rates = [c["psqft"] for c in near]
            sc["comps_n"] = len(near)
            sc["comp_level"] = level
            sc["comp_mean_psqft"] = statistics.mean(rates) if rates else None
            sc["comp_med_psqft"] = statistics.median(rates) if rates else None
            if psqft:
                sc["psqft_used"] = psqft
                est_sale = build_sqft * psqft
                sc["est_sale"] = est_sale
                sc["roi"] = {}
                sc["cost_total"], sc["profit"] = {}, {}
                scen_tiers = {"HOUSE": (HOUSE_BUILD_COST,),
                              "MULTIPLEX": (MPX_BUILD_COST,),
                              "DUPLEX": (DUPLEX_BUILD_COST,)}[name]
                for cost in scen_tiers:
                    hard = build_sqft * cost + build_sqft * PERMIT_PSQFT
                    financing = (row["asking"] * FIN_LAND_PCT
                                 + hard * FIN_BUILD_PCT)
                    selling = est_sale * SELLING_PCT
                    gst = est_sale * GST_PCT
                    total_cost = row["asking"] + hard + financing + selling + gst
                    sc["cost_total"][cost] = total_cost
                    sc["profit"][cost] = est_sale - total_cost
                    sc["roi"][cost] = sc["profit"][cost] / total_cost
        scenarios[name] = sc
    best = max(
        (s for s in scenarios.values() if s.get("roi")),
        key=lambda s: max(v for v in s["roi"].values()),
        default=None,
    )
    return {
        "scenarios": scenarios,
        "cost_tiers": tiers,
        "best_roi": max(best["roi"].values()) if best else None,
        "best_scenario": next((n for n, s in scenarios.items() if s is best), None),
    }


def render(rows_out, scan, comp_meta, hood_psqft, hood_stats):
    head = (
        "<tr><th>地址</th><th>社区</th><th>状态</th><th>挂牌</th><th>tier</th>"
        "<th>ask/BCA</th><th>ask/地价</th><th>地块</th><th>面宽</th><th>zoning</th>"
        "<th>HOUSE 0.7×<br><span class='stat-note'>整栋新房comps</span></th>"
        "<th>DUPLEX 0.7×<br><span class='stat-note'>strata半户comps</span></th>"
        "<th>MULTIPLEX 1.0×<br><span class='stat-note'>strata多户comps</span></th>"
        "<th>最优方案ROI</th></tr>"
    )

    def money_m(v):
        s = "-" if v < 0 else ""
        v = abs(v)
        return f"{s}${v/1e6:.1f}M" if v >= 1e6 else f"{s}${v/1e3:.0f}k"

    def scen(sc, tiers):
        if not sc["eligible"]:
            return "<td class='muted'>不适用</td>"
        if sc.get("est_sale") is None:
            return "<td class='muted'>缺comps</td>"
        roi_txt = "<br>".join(
            f"<span class='{'roi-pos' if v > 0 else 'roi-neg'}'>{v:.0%}@{c}</span>"
            f"<span class='stat-note'> 本{money_m(sc['cost_total'][c])} 利{money_m(sc['profit'][c])}</span>"
            for c, v in sc["roi"].items()
        )
        comp_note = f"<span class='stat-note'>@${sc['psqft_used']:,.0f}/sf·{sc.get('price_src','')}</span>"
        unit_note = ""
        if sc.get("units", 1) > 1:
            per = sc["build_sqft"] / sc["units"]
            warn = " ⚠" if per < 1200 else ""
            rng = f"{sc['unit_range']}·" if sc.get("unit_range") else ""
            src = {"none": " ⚠无strata成交", "region": " ·区域推断"}.get(
                sc.get("mpx_proven"), "")
            unit_note = f"<br><span class='stat-note'>{rng}{per:,.0f}sf/户{warn}{src}</span>"
        return (
            f"<td>建面{sc['build_sqft']:,.0f}sf{unit_note}<br>估售{base.money(sc['est_sale'])}"
            f"<br>{comp_note}<br>{roi_txt}</td>"
        )

    body_rows = []
    for r in rows_out:
        a = r["analysis"]
        best = (
            f"<span class='roi-pos'>{a['best_roi']:.0%} {a['best_scenario']}</span>"
            if a["best_roi"] is not None else "—"
        )
        cls = " class='best'" if a["best_roi"] is not None and a["best_roi"] >= 0.10 else ""
        lot_txt = f"{r['lot_sqft']:,.0f}" if r["lot_sqft"] else "—"
        fr_txt = f"{r['frontage_ft']:,.0f}" if r["frontage_ft"] else "—"
        addr_cell = f'<a href="{r["link"]}">{r["address"]}</a>' if r.get("link") else r["address"]
        body_rows.append(
            f"<tr{cls}><td>{addr_cell}</td><td>{r['hood']}</td><td>{r['status']}</td>"
            f"<td>{base.money(r['asking'])}</td><td>{r['tier']}</td>"
            f"<td>{f'{r['ratio']:.2f}' if r['ratio'] else '—'}</td>"
            f"<td>{f'{r['ratio_land']:.2f}' if r.get('ratio_land') else '—'}</td>"
            f"<td>{lot_txt}</td><td>{fr_txt}</td>"
            f"<td>{r.get('zoning') or '—'}</td>"
            + scen(a["scenarios"]["HOUSE"], a["cost_tiers"])
            + scen(a["scenarios"]["DUPLEX"], a["cost_tiers"])
            + scen(a["scenarios"]["MULTIPLEX"], a["cost_tiers"])
            + f"<td>{best}</td></tr>"
        )

    # product tendency per hood: which scenario wins across the tracked
    # listings answers "是 house 区还是多户区" from the model's own output
    import collections as _c
    best_by_hood = _c.defaultdict(lambda: _c.Counter())
    for r in rows_out:
        bs = (r.get("analysis") or {}).get("best_scenario")
        if bs and r.get("hood"):
            best_by_hood[r["hood"]][bs] += 1

    hood_rows = []
    for h, s in sorted(
            ((h, s) for h, s in hood_stats.items() if not h.startswith("_")),
            key=lambda kv: -kv[1]["mean"]):
        n_txt = str(s["n"])
        if s["n_dropped"]:
            n_txt += f"<span class='muted'>(-{s['n_dropped']})</span>"
        if h in hood_psqft:
            tag = "<span class='muted'>(校准)</span>" if h in HOOD_OVERRIDES else ""
            rate_txt = f"<b>${hood_psqft[h]:,.0f}/sf</b>{tag}"
        else:
            rate_txt = "<span class='muted'>样本不足，回退区域价</span>"
        cnt = best_by_hood.get(h)
        if cnt:
            parts = "/".join(f"{k[0]}{v}" for k, v in cnt.most_common())
            tend = f"{cnt.most_common(1)[0][0]}<span class='muted'> ({parts})</span>"
        else:
            tend = "<span class='muted'>—</span>"
        hood_rows.append(
            f"<tr><td>{h}</td><td>{n_txt}</td><td>${s['mean']:,.0f}</td>"
            f"<td>${s['median']:,.0f}</td><td>${s['min']:,.0f}–${s['max']:,.0f}</td>"
            f"<td>{rate_txt}</td><td>{tend}</td></tr>"
        )
    hood_rows = "".join(hood_rows)
    hood_table = (
        "<h2>社区单价分类（新房成交实测 → 保守采用价 = 均值×80%，取整$50）</h2>"
        "<table><tr><th>社区</th><th>新房成交数</th><th>均值$/sf</th>"
        "<th>中位$/sf</th><th>区间</th><th>估值采用价</th><th>最优形态</th></tr>"
        f"{hood_rows}</table>"
    )

    n_cols = 14
    ZONING_COL = 9
    family_opts = [("R1-1", "R1-1"), ("R3", "R3 系"), ("RT", "RT 系"),
                   ("RM", "RM 系"), ("R5", "R5 系"),
                   ("C-|CD|DEOD|—", "C/CD/其他")]
    zsel = ['<select><option value="">全部 zoning</option>']
    for v, label in family_opts:
        zsel.append(f'<option value="{v}">{label}</option>')
    zsel.append('</select>')
    zsel = "".join(zsel)
    filter_cells = "".join(
        f'<td>{zsel}</td>' if i == ZONING_COL else
        f'<td><input placeholder="筛选"></td>' for i in range(n_cols)
    )

    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate">
<meta http-equiv="Pragma" content="no-cache"><style>{CSS}</style>
<title>重建价值分析 — {scan}</title></head><body>
<h1>重建价值分析 — {scan}</h1>
<div class="sub">对象：全部 {len(rows_out)} 套追踪房（含已成交/撤回，用于 ROI×结局对照）。估值单价<b>按社区</b>：见下表实测新房单价 ×80% 保守折扣；样本不足的社区回退区域价（东$1,100/南中$1,200/西$1,300)。方案 FSR 按 zoning：R1/RS 系 0.7/0.7/1.0（multiplex 需地块≥3,294sqft 且面宽≥32.8ft 且进深≥99.7ft——R1-1/RT/R3 法规表，缺一不可；地块>4,984sf 或面宽>43.6ft 时法规最少须建4户；44ft+4,995sf 可至5户、49.5ft+5,996sf 可至6户；格内按最少户数建模）；R3 0.6/0.6/1.0；RT 0.7/0.75/1.0；RM 0.7/0.85/1.0 不限地块；C/CD/其他只算 HOUSE。成本 = 挂牌 + 建造（HOUSE $350 / DUPLEX $400 / MULTIPLEX $450，全市统一）+ 设计办证 ${PERMIT_PSQFT}/sf + GST {GST_PCT:.0%} + 销售费 {SELLING_PCT:.0%} + 资金（地价×{FIN_LAND_PCT:.0%} 房贷5%/年×2年 + 建安×{FIN_BUILD_PCT:.1%} 即35%自有@3.5%+65%开发贷@10%×2年；先住后建不计审批期）。MULTIPLEX 单价在 strata 实测池基础上 ×{MPX_PRODUCT_FACTOR}（叠拼多户形态折价）。ROI=(估售−总成本)/总成本。不含退让/层高细节、审批风险。绿底行 = 最优方案 ROI≥10%。<b>表头点击排序；第二行输入框筛选</b>（文本包含或 &gt;30 / &lt;0.8 / &gt;=1500000 这类数值表达式；百分数列直接输百分数如 &gt;30）。当前显示 <b id="viscount"></b> 行。</div>
{hood_table}
<table id="proptable"><thead>{head}<tr class="filters">{filter_cells}</tr></thead><tbody>{''.join(body_rows)}</tbody></table>
<script>{TABLE_JS}</script>
</body></html>"""


def main() -> int:
    w = json.load(open(base.WATCHLIST))
    scan = w.get("latest_scan") or "unknown"
    cards = base.load_card_index()
    rows = base.join_rows(w["entries"], cards, scan)
    region_of = load_region_polys()
    comps, max_date, cutoff = load_new_home_comps(region_of)
    hood_psqft, hood_stats, kept_comps = hood_rates(comps)
    dim_overrides = _load_dim_overrides()
    land_values = _load_land_values()
    print(f"comps: {len(comps)} new-home sales {cutoff}..{max_date}; hood rates: {hood_psqft}")

    out = []
    skipped = {"gate": 0, "geo": 0, "noask": 0}
    for e, r in zip(w["entries"], rows):
        # all statuses — SOLD/withdrawn rows are kept so ROI can be compared
        # against outcomes and human tiers
        if not r["asking"]:
            skipped["noask"] += 1
            continue
        c = cards.get(e.get("latest_mls") or "") or {}
        lat, lng = _num(c.get("latitude")), _num(c.get("longitude"))
        if lat is None:
            skipped["geo"] += 1
            continue
        # fill gaps: dimensions from BCA, lot area from dimensions, BCA manual lookups
        dim_ov = dim_overrides.get(r["address"])
        if dim_ov:
            if not r.get("frontage_ft") and dim_ov.get("frontage_ft"):
                r["frontage_ft"] = dim_ov["frontage_ft"]
            if not r.get("depth_ft") and dim_ov.get("depth_ft"):
                r["depth_ft"] = dim_ov["depth_ft"]
        if (not r["lot_sqft"] or r["lot_sqft"] < 500) and r.get("frontage_ft") and r.get("depth_ft"):
            r["lot_sqft"] = float(r["frontage_ft"]) * float(r["depth_ft"])
        if not r.get("bca") and r["address"] in MANUAL_BCA_OVERRIDES:
            r["bca"] = MANUAL_BCA_OVERRIDES[r["address"]]
            if r.get("asking"):
                r["ratio"] = r["asking"] / r["bca"]
        land = (land_values.get(r["address"]) or {}).get("land")
        r["bca_land"] = land
        if r.get("asking") and land:
            r["ratio_land"] = r["asking"] / land
        region = region_of(lng, lat)
        r["analysis"] = analyse(r, lat, lng, r["hood"], region, comps, hood_psqft, kept_comps, hood_stats)
        r["region"] = region
        mls_hist = [ev.get("mls") for ev in e.get("events") or [] if ev.get("mls")]
        r["link"] = card_link([e.get("latest_mls"), *(e.get("mls_history") or []), *mls_hist])
        out.append(r)

    out.sort(key=lambda r: (r["analysis"]["best_roi"] is None, -(r["analysis"]["best_roi"] or -9)))
    comp_meta = f"近{COMP_WINDOW_DAYS}天({cutoff}~{max_date})、建造年≥{NEW_HOME_MIN_YEAR}、独立屋"
    html = render(out, scan, comp_meta, hood_psqft, hood_stats)
    p_html = OUT_DIR / f"rebuild-value-{scan}.html"
    p_html.write_text(html, encoding="utf-8")
    p_json = OUT_DIR / f"rebuild-value-{scan}.json"
    p_json.write_text(json.dumps({
        "scan": scan, "comps_window": [str(cutoff), str(max_date)],
        "comps_n": len(comps), "candidates": len(out), "skipped": skipped,
        "rows": [{k: v for k, v in r.items() if k != "analysis"} | {"analysis": r["analysis"]} for r in out],
    }, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(p_html)
    print(p_json)
    print(f"analysed={len(out)} skipped={skipped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
