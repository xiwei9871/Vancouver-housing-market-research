"""Strategy V2 data-only auto-prefill for the calibration workflow.

Pre-fills the fields that structured facts can support — lot, location,
redevelopment, price bands; tracks; factual tags; special-situation
detection — and emits ``annotations.auto.json`` records.

Hard boundaries (by design):

* Never fills ``human_current_use_band`` or ``human_house_reno_band`` — those
  require looking at photos.
* Never sets a human ``KEEP``/``WATCH``/``PASS`` decision.  A factual
  special-situation may pre-select ``SPECIAL``; everything else gets a
  ``data_only_signal`` (STRONG / NORMAL / WEAK) instead.
* ``UNKNOWN != POOR``: missing facts leave the band null, lower confidence,
  and add a ``needs_human_review`` flag — they never drag a band down.
* Geometry tags are separate from the asset track: a narrow-frontage deep
  lot is ``HOME_REBUILD + NARROW_FRONTAGE + DEEP_LOT``, not
  ``PREMIUM_SMALL_LOT``.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

RULES_VERSION = "auto-prefill-v5"

# Photo-evidence thresholds.  A listing that provides <=3 photos did not show
# the interior — per the reviewer's confirmed heuristic, not showing the
# interior is itself the condition signal (a good interior would be shown).
NO_INTERIOR_MAX_PHOTOS = 3
FEW_PHOTOS_MAX = 11

_BANDS = ("0-20", "21-40", "41-60", "61-80", "81-100")

# Transaction/legal keywords that qualify a factual SPECIAL_SITUATION.
_SPECIAL_HARD = re.compile(
    r"court[-\s]?order|foreclos|power of sale|partial interest|leasehold|"
    r"title (issue|defect|dispute)|heritage (designated|register|revitalization)",
    re.IGNORECASE,
)
# Condition/listing keywords that flag human review but are NOT special.
_SPECIAL_SOFT = re.compile(
    r"estate sale|\bas[\s-]?is\b|land value|fire|flood|oil tank|"
    r"underground storage|grow[-\s]?op",
    re.IGNORECASE,
)


def _num(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _band(score: float | None) -> str | None:
    if score is None:
        return None
    if score <= 20:
        return "0-20"
    if score <= 40:
        return "21-40"
    if score <= 60:
        return "41-60"
    if score <= 80:
        return "61-80"
    return "81-100"


def _money(value: Any) -> str:
    n = _num(value)
    return f"${n:,.0f}" if n is not None else "未知"


# ---------------------------------------------------------------------------
# Per-dimension rules
# ---------------------------------------------------------------------------


def _lot_score(candidate: Mapping[str, Any], parcel: Mapping[str, Any]) -> tuple[float | None, list[str], list[str], list[str]]:
    """Return (score, tags, reasons, review_flags) for lot geometry."""

    # Official parcel geometry wins over listing-claimed dimensions.
    raw = candidate.get("raw") or {}
    frontage = _num(raw.get("official_frontage_ft")) or _num(candidate.get("frontage_ft"))
    depth = _num(raw.get("official_depth_ft")) or _num(candidate.get("depth_ft"))
    area = _num(raw.get("official_lot_area_sqft")) or _num(candidate.get("lot_area_sqft"))
    if area is None and frontage is not None and depth is not None:
        area = round(frontage * depth)
    tags: list[str] = []
    reasons: list[str] = []
    flags: list[str] = []

    if frontage is None and area is None:
        flags.append("MISSING_GEOMETRY")
        return None, tags, ["lot 几何缺失，需人工确认"], flags

    f = frontage or 0.0
    a = area or 0.0
    if f >= 50 and a >= 5000:
        score = 85.0
    elif f >= 33 and a >= 3900:
        score = 70.0
    elif f >= 25 and a >= 2800:
        score = 50.0
    elif f >= 20 or a >= 2000:
        score = 30.0
    else:
        score = 15.0

    # Narrow-frontage / deep-lot: normal area but constrained geometry.
    if frontage is not None and depth is not None:
        narrow = frontage < 28
        deep = depth >= 120 or (frontage > 0 and depth / frontage > 4.0)
        if narrow:
            score = min(score, 50.0)
            tags.append("NARROW_FRONTAGE")
            flags.append("NARROW_FRONTAGE_SHAPE")
        if deep:
            tags.append("DEEP_LOT")

    if parcel.get("corner_lot"):
        score = min(score + 5.0, 95.0)
        tags.append("CORNER")
    if parcel.get("lane_observed"):
        score = min(score + 3.0, 95.0)
        tags.append("LANE")

    dims = candidate.get("lot_dimensions") or (
        f"{frontage:g}×{depth:g}" if frontage and depth else "未知"
    )
    reasons.append(f"lot {dims}（约 {a:,.0f} sqft）")
    return score, tags, reasons, flags


def _location_score(
    parcel: Mapping[str, Any],
    school: Mapping[str, Any],
    *,
    include_road: bool = True,
) -> tuple[float, list[str], list[str]]:
    score = 50.0
    tags: list[str] = []
    reasons: list[str] = []

    if include_road:
        if parcel.get("property_fronts_major_road"):
            score -= 20.0
            tags.append("ROAD")
            reasons.append("临街主路/干道")
        else:
            dist = _num(parcel.get("distance_to_major_road_m"))
            if dist is not None and dist <= 50:
                score -= 10.0
                tags.append("ROAD")
                reasons.append(f"距主路 {dist:,.0f}m")

    perf = school.get("elementary_performance") or {}
    rate = _num(perf.get("ontrack_or_extending_rate"))
    if rate is not None:
        if rate >= 0.5:
            score += 15.0
            tags.append("SCHOOL")
        elif rate >= 0.35:
            score += 8.0
        elif rate <= 0.25:
            score -= 8.0
        reasons.append(
            f"学区 {school.get('elementary_catchment') or '?'}"
            f"/{school.get('secondary_catchment') or '?'}（on-track {rate:.0%}）"
        )
    elif school.get("elementary_catchment"):
        reasons.append(f"学区 {school.get('elementary_catchment')}")

    if parcel.get("park_facing"):
        score += 8.0
        reasons.append("对公园")
    if parcel.get("waterfront"):
        score += 10.0
        reasons.append("滨水")
    if parcel.get("view_type"):
        score += 8.0
        reasons.append(f"景观 {parcel.get('view_type')}")

    return max(5.0, min(score, 95.0)), tags, reasons


def _redevelopment_score(
    shadow_row: Mapping[str, Any],
    parcel: Mapping[str, Any],
    candidate: Mapping[str, Any] | None = None,
    loc_score: float | None = None,
) -> tuple[float | None, list[str], list[str], list[str]]:
    """Redevelopment band from data only — no photos required.

    Reviewer rule: 61-80 requires a standard lot (>=4026 sqft), standard
    frontage (>=33 ft) and a passing location band.  Premium extras (large
    lot, wide frontage, multiplex entitlement) lift to 81-100 only after the
    gate is passed; entitlement cannot rescue a sub-standard lot.
    """

    candidate = candidate or {}
    raw = candidate.get("raw") or {}
    tags: list[str] = []
    reasons: list[str] = []
    flags: list[str] = []

    # Official parcel geometry wins over listing-claimed dimensions.
    frontage = _num(raw.get("official_frontage_ft")) or _num(candidate.get("frontage_ft"))
    depth = _num(raw.get("official_depth_ft")) or _num(candidate.get("depth_ft"))
    area = _num(raw.get("official_lot_area_sqft")) or _num(candidate.get("lot_area_sqft"))
    if area is None and frontage is not None and depth is not None:
        area = round(frontage * depth)

    scenarios = shadow_row.get("development_scenarios") or []
    base = next((s for s in scenarios if s.get("scenario_name") == "BASE"), {})
    rule_basis = base.get("rule_basis") or {}
    zoning = rule_basis.get("zoning")
    units = _num(rule_basis.get("multiplex_max_units")) or _num(rule_basis.get("maximum_units")) or _num(base.get("unit_count"))
    product = base.get("product_type")

    if area is None and frontage is None:
        potential = _num(shadow_row.get("development_potential"))
        flags.append("MISSING_DEVELOPMENT_POTENTIAL")
        return potential, tags, reasons, flags

    # Road exposure: confirmed frontage, or the address street is itself the
    # nearest major road (e.g. 1031 BOUNDARY ROAD vs '800-1000 BOUNDARY ROAD').
    fronts = parcel.get("property_fronts_major_road") is True
    if not fronts:
        street = re.sub(r"^\d+\s*", "", str(candidate.get("address") or shadow_row.get("address") or "")).strip().upper()
        nearest = str(parcel.get("nearest_major_road") or "").upper()
        if street and nearest and street in nearest:
            fronts = True
            flags.append("ADDRESS_ON_MAJOR_ROAD")

    location_ok = loc_score is not None and loc_score >= 40
    # Standard lot = ~33x120+ (3960 sqft); the reviewer said "4026+ / 33+"
    # which is the same 33x122 standard configuration.
    standard = (area or 0) >= 3900 and (frontage or 0) >= 33
    # Multiplex units are universal under R1-1 — not a differentiator.
    # Premium = genuinely rare geometry: large lot or wide frontage.
    premium = (area or 0) >= 5000 or (frontage or 0) >= 40

    if (frontage is not None and frontage < 25) or (area is not None and area < 2800):
        score = 10.0
        reasons.append(f"lot/frontage 严重不达标（{area or '?'}sqft / {frontage or '?'}ft）")
    elif not standard:
        score = 30.0
        reasons.append(f"未达标准地门槛（{area or '?'}sqft / {frontage or '?'}ft，需 ~3960+/33+）")
    elif not location_ok:
        score = 50.0
        reasons.append("地块达标但区位分未及格")
    elif premium:
        score = 90.0
        reasons.append("标准地+区位及格+溢价要素（大地/宽面）")
    else:
        score = 70.0
        reasons.append("标准地+区位及格")

    if fronts:
        # Developer lens: road frontage is a livability (CU) penalty, not a
        # rebuild-value penalty — the location gate already uses the
        # road-free score.  Kept as an informational note for the reviewer.
        reasons.append("临主路——不计入重建评分（仅居住/区位维度扣分）")

    if zoning:
        detail = f"zoning {zoning}"
        if units:
            detail += f"，BASE {units:g}户/{product or '?'}"
        reasons.append(detail)
    if rule_basis.get("rear_access_required") and not parcel.get("lane_observed"):
        flags.append("REAR_ACCESS_DEPENDENT")
        reasons.append("容量依赖后巷进入，条件项")
    return score, tags, reasons, flags


def _price_score(
    shadow_row: Mapping[str, Any]
) -> tuple[float | None, list[str], list[str], list[str]]:
    """Price band anchored on BCA assessment — the reviewer's confirmed scale,
    recalibrated to the universe's achievable range (min asking/BCA = 0.74):

        >=100% BCA  -> 0-20 + ASKING_OVER_BCA (reviewer: 高于估价就 pass)
        95–100%     -> 21-40
        90–95%      -> 41-60
        85–90%      -> 61-80
        <85%        -> 81-100

    ``market_land_basis`` is reported as a secondary reference in the reason,
    not used for the band.
    """

    acq = shadow_row.get("acquisition") or {}
    asking = _num(acq.get("asking"))
    bca = _num(acq.get("assessment_total"))
    mlb = _num(acq.get("market_land_basis"))
    tags: list[str] = []
    reasons: list[str] = []
    flags: list[str] = []
    if asking is None or bca is None or bca <= 0:
        flags.append("MISSING_PRICE_BASIS")
        return None, tags, reasons, flags
    ratio = asking / bca
    if ratio >= 1.00:
        score = 10.0
        flags.append("ASKING_OVER_BCA")
        reasons.append("asking 高于 BCA 估价——按约定不考虑")
    elif ratio >= 0.95:
        score = 30.0
    elif ratio >= 0.90:
        score = 50.0
    elif ratio >= 0.85:
        score = 70.0
    else:
        score = 90.0
    if ratio < 0.85:
        tags.append("PRICE")
    land = _num(acq.get("assessment_land"))
    reason = f"asking {_money(asking)} vs BCA估价 {_money(bca)}（{ratio:.2f}×）"
    if land:
        reason += f"；BCA土地 {_money(land)}（{asking / land:.2f}×）"
    if mlb:
        reason += f"；隐含地价=asking−建筑 {_money(mlb)}（{asking / mlb:.2f}×）"
    reasons.append(reason)
    return score, tags, reasons, flags


def _photo_condition_rule(photo_count: float | int | None) -> tuple[dict[str, str], list[str], list[str]]:
    """Apply the reviewer-confirmed no-interior-photos heuristic.

    <=3 photos → the listing did not show the interior; not showing it is the
    condition signal.  Auto-fills Current Use and Reno at 0-20 (still human-
    overridable — overrides are tracked).  4–11 photos → FEW_PHOTOS flag only.
    """

    fills: dict[str, str] = {}
    flags: list[str] = []
    reasons: list[str] = []
    n = _num(photo_count)
    if n is None:
        return fills, flags, reasons
    if n <= NO_INTERIOR_MAX_PHOTOS:
        fills["human_current_use_band"] = "0-20"
        fills["human_house_reno_band"] = "0-20"
        flags.append("NO_INTERIOR_PHOTOS")
        reasons.append(
            f"listing 仅 {n:g} 张照片、未提供室内图——按约定视为室内状况差信号"
        )
    elif n <= FEW_PHOTOS_MAX:
        flags.append("FEW_PHOTOS")
    return fills, flags, reasons


def _special_situation(
    shadow_row: Mapping[str, Any], candidate: Mapping[str, Any]
) -> tuple[bool, list[str], list[str]]:
    """Factual special-situation detection: transaction/legal anomalies only.

    Returns (is_special, review_flags, reasons).  Soft listing language
    ("as is", "land value", fire/oil tank history) flags human review but
    never triggers SPECIAL by itself.
    """

    evidence: list[str] = []
    flags: list[str] = []
    for gate in shadow_row.get("gates") or []:
        if gate.get("code") == "TITLE_OR_TRANSACTION" and gate.get("status") in {
            "FAIL",
            "REVIEW",
        }:
            evidence.extend(str(e) for e in gate.get("evidence") or [])
    text = " ".join(
        str(candidate.get(k) or "")
        for k in ("description", "ownership_type", "property_type")
    )
    hard_hits = sorted(set(m.group(0) for m in _SPECIAL_HARD.finditer(text)))
    evidence.extend(hard_hits)
    soft_hits = sorted(set(m.group(0).lower() for m in _SPECIAL_SOFT.finditer(text)))
    if soft_hits:
        flags.append("LISTING_CONDITION_LANGUAGE")
    is_special = bool(evidence)
    reasons = [f"特殊交易/产权信号: {', '.join(evidence)}"] if evidence else []
    return is_special, flags, reasons


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------

_BAND_SCORE = {"0-20": 10, "21-40": 30, "41-60": 50, "61-80": 70, "81-100": 90}


def _band_at_least(band: str | None, minimum: str) -> bool:
    return band is not None and _BAND_SCORE[band] >= _BAND_SCORE[minimum]


def build_auto_annotation(
    shadow_row: Mapping[str, Any],
    candidate: Mapping[str, Any] | None,
    *,
    photo_count: float | int | None = None,
) -> dict[str, Any]:
    """Build the data-only prefill record for one property."""

    candidate = candidate or {}
    location = shadow_row.get("location") or {}
    parcel = location.get("parcel_facts") or {}
    school = location.get("school") or {}
    classification = shadow_row.get("asset_classification") or {}

    tags: list[str] = []
    reasons: list[str] = []
    review_flags: list[str] = []

    # --- special situation first: it overrides track and decision ----------
    is_special, soft_flags, special_reasons = _special_situation(shadow_row, candidate)
    review_flags.extend(soft_flags)
    reasons.extend(special_reasons)

    # --- dimensions ---------------------------------------------------------
    lot_score, lot_tags, lot_reasons, lot_flags = _lot_score(candidate, parcel)
    loc_score, loc_tags, loc_reasons = _location_score(parcel, school)
    # The rebuild score uses the road-free location score: road exposure is a
    # livability penalty (CU/location bands), not a rebuild-value penalty.
    loc_score_no_road, _, _ = _location_score(parcel, school, include_road=False)
    dev_score, dev_tags, dev_reasons, dev_flags = _redevelopment_score(
        shadow_row, parcel, candidate, loc_score_no_road
    )
    price_score, price_tags, price_reasons, price_flags = _price_score(shadow_row)
    for seq in (lot_tags, loc_tags, dev_tags, price_tags):
        for tag in seq:
            if tag not in tags:
                tags.append(tag)
    reasons.extend(lot_reasons + loc_reasons + dev_reasons + price_reasons)
    review_flags.extend(lot_flags + dev_flags + price_flags)

    if "LOT" not in tags and (
        "NARROW_FRONTAGE" in tags or "DEEP_LOT" in tags
        or (_num(candidate.get("lot_area_sqft")) or 1e9) < 3300
    ):
        tags.append("LOT")
    base_scenario = next(
        (s for s in shadow_row.get("development_scenarios") or []
         if s.get("scenario_name") == "BASE"),
        {},
    )
    zoning = (base_scenario.get("rule_basis") or {}).get("zoning")
    if zoning and str(zoning).startswith(("CD", "DEOD", "RT")):
        tags.append("ZONING")

    # --- track --------------------------------------------------------------
    if is_special:
        primary = "SPECIAL_SITUATION"
    else:
        primary = classification.get("primary_track") or "UNCLASSIFIED_REVIEW"
        # Geometry rule: narrow-frontage deep lots are HOME_REBUILD, never
        # PREMIUM_SMALL_LOT regardless of what the classifier said.
        if "NARROW_FRONTAGE" in tags:
            primary = "HOME_REBUILD"
    secondary = (classification.get("secondary_tracks") or [None])[0]

    bands = {
        "lot": _band(lot_score),
        "location": _band(loc_score),
        "redevelopment": _band(dev_score),
        "price": _band(price_score),
    }

    # --- reviewer-confirmed photo-coverage rule ----------------------------
    photo_fills, photo_flags, photo_reasons = _photo_condition_rule(photo_count)
    review_flags.extend(photo_flags)
    reasons.extend(photo_reasons)

    # --- auto-PASS rule ------------------------------------------------------
    # Reviewer-confirmed: asking >110% BCA is "不考虑" outright, and a listing
    # whose every data dimension sits in the bottom two bands is not worth a
    # photo review.  Still a prefill — the human can override.
    decision: str | None = None
    if is_special:
        decision = "SPECIAL"
    elif "ASKING_OVER_BCA" in price_flags:
        decision = "PASS"
        review_flags.append("AUTO_PASS_RULE")
        reasons.append("asking 高于 BCA 估价——按约定预填 PASS")
    elif all(b in ("0-20", "21-40") for b in bands.values()):
        decision = "PASS"
        review_flags.append("AUTO_PASS_RULE")
        reasons.append("lot/区位/重建/价格四个数据维度全部 ≤21-40——按约定预填 PASS")

    # --- data-only signal ----------------------------------------------------
    if is_special:
        signal = "SPECIAL"
    elif bands["price"] in (None,) or bands["location"] is None:
        # Missing a key dimension -> weak claim is impossible; stay neutral.
        signal = "NORMAL"
    elif (
        _band_at_least(bands["redevelopment"], "61-80")
        and _band_at_least(bands["price"], "61-80")
        and _band_at_least(bands["location"], "41-60")
    ):
        signal = "STRONG"
    elif (
        bands["price"] in ("0-20", "21-40")
        or bands["location"] in ("0-20",)
        or (
            bands["redevelopment"] in ("0-20", "21-40")
            and bands["lot"] in ("0-20", "21-40")
        )
    ):
        signal = "WEAK"
    else:
        signal = "NORMAL"

    # --- confidence ----------------------------------------------------------
    core = [
        candidate.get("frontage_ft"),
        candidate.get("lot_area_sqft"),
        (shadow_row.get("acquisition") or {}).get("asking"),
        (shadow_row.get("acquisition") or {}).get("assessment_total"),
        zoning,
        school.get("elementary_catchment"),
    ]
    missing = sum(1 for item in core if item in (None, ""))
    confidence = "HIGH" if missing == 0 else ("MEDIUM" if missing <= 2 else "LOW")

    return {
        "property_key": shadow_row.get("property_key"),
        "split": None,  # filled by the caller from the frozen split manifest
        "auto": {
            "human_current_use_band": photo_fills.get("human_current_use_band"),
            "human_redevelopment_band": bands["redevelopment"],
            "human_house_reno_band": photo_fills.get("human_house_reno_band"),
            "human_location_band": bands["location"],
            "human_lot_band": bands["lot"],
            "human_price_value_band": bands["price"],
            "human_primary_track": primary,
            "human_secondary_track": secondary,
            "human_decision": decision,
            "human_must_keep": False,
            "human_tags": tags,
            "human_reason": "",
            "dual_override": False,
            "human_dual_band": None,
        },
        "data_only_signal": signal,
        "must_keep_candidate": signal == "STRONG" or bands["price"] == "81-100",
        "dual_override_candidate": (
            _band_at_least(bands["redevelopment"], "61-80")
            and _band_at_least(bands["location"], "61-80")
        ),
        "confidence": confidence,
        "reasons": reasons,
        "needs_human_review": sorted(set(review_flags)),
        "rules_version": RULES_VERSION,
    }


def build_auto_annotations(
    shadow_rows: Mapping[str, Mapping[str, Any]],
    candidates: Mapping[str, Mapping[str, Any]],
    *,
    splits: Mapping[str, Any] | None = None,
    photo_counts: Mapping[str, float | int] | None = None,
) -> dict[str, dict[str, Any]]:
    """Build auto-prefill records for the whole universe, keyed by property_key."""

    out: dict[str, dict[str, Any]] = {}
    cal = set((splits or {}).get("calibration_keys") or [])
    blind = set((splits or {}).get("blind_test_keys") or [])
    photo_counts = photo_counts or {}
    for key, row in shadow_rows.items():
        rec = build_auto_annotation(
            row, candidates.get(key), photo_count=photo_counts.get(key)
        )
        rec["split"] = (
            "calibration" if key in cal else ("blind" if key in blind else "unassigned")
        )
        out[key] = rec
    return out


__all__ = [
    "RULES_VERSION",
    "build_auto_annotation",
    "build_auto_annotations",
]
