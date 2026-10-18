"""Tests for the data-only auto-prefill layer of the calibration workflow.

Auto-prefill fills only objectively derivable fields (lot / location /
redevelopment / price bands, tracks, factual tags, special-situation
detection).  It must never fill Current Use, Reno, or a human KEEP/WATCH/PASS
decision — those stay blank for the photo-reviewing human.
"""

from __future__ import annotations

import pytest

from northshore_watchdog.research.strategy_v2_auto_prefill import (
    build_auto_annotation,
    build_auto_annotations,
)
from northshore_watchdog.research.strategy_v2_calibration import (
    HumanAnnotation,
    evaluate_calibration,
    parse_annotation,
    render_card_html,
    build_card_context,
)


def _shadow_row(
    key="mls:X",
    address="1 TEST STREET",
    *,
    dev_potential=70.0,
    track="HOME_REBUILD",
    gates=None,
    parcel=None,
    school=None,
    acquisition=None,
    scenarios=None,
    secondary_tracks=None,
):
    return {
        "property_key": key,
        "address": address,
        "mls": key.split(":")[-1],
        "development_potential": dev_potential,
        "asset_classification": {
            "primary_track": track,
            "secondary_tracks": secondary_tracks or [],
            "confidence": "HIGH",
        },
        "gates": gates if gates is not None else [
            {"code": "TITLE_OR_TRANSACTION", "status": "PASS", "evidence": ["Freehold"]},
        ],
        "location": {
            "parcel_facts": {
                "road_class": "local",
                "property_fronts_major_road": False,
                "distance_to_major_road_m": 300.0,
                "nearest_major_road": "MAIN ST",
                "corner_lot": False,
                "lane_observed": None,
                "park_facing": False,
                "view_type": None,
                "waterfront": False,
                "slope": "UNKNOWN",
                **(parcel or {}),
            },
            "school": school
            or {
                "elementary_catchment": "Elem",
                "secondary_catchment": "Sec",
                "elementary_performance": {"ontrack_or_extending_rate": 0.45},
            },
            "external_provider": {"status": "NOT_CONFIGURED"},
        },
        "acquisition": {
            "asking": 1_500_000,
            "market_land_basis": 1_500_000,
            "assessment_total": 1_600_000,
            **(acquisition or {}),
        },
        "development_scenarios": scenarios
        or [
            {
                "scenario_name": "BASE",
                "legal_status": "CURRENT_OFFICIAL_RULE",
                "unit_count": 4,
                "product_type": "MULTIPLEX",
                "rule_basis": {"zoning": "R1-1", "rear_access_required": False},
            }
        ],
    }


def _candidate(
    *,
    frontage_ft=33.0,
    depth_ft=120.0,
    lot_area_sqft=4000.0,
    asking=1_500_000,
    description="Nice home",
    ownership_type="Freehold",
):
    return {
        "address": "1 TEST STREET",
        "mls_number": "X",
        "asking": asking,
        "frontage_ft": frontage_ft,
        "depth_ft": depth_ft,
        "lot_area_sqft": lot_area_sqft,
        "lot_dimensions": f"{frontage_ft} x {depth_ft}",
        "geometry_source": "OFFICIAL",
        "description": description,
        "ownership_type": ownership_type,
        "bedrooms": 3,
        "bathrooms": 2,
        "finished_sqft": 2000,
        "year_built": 1950,
        "source_url": "https://example/l",
    }


class TestAutoBands:
    def test_standard_lot_gets_good_band(self):
        auto = build_auto_annotation(_shadow_row(), _candidate())
        assert auto["auto"]["human_lot_band"] == "61-80"

    def test_narrow_deep_lot_capped_and_tagged_not_premium(self):
        # 25 x 160: normal area but narrow frontage -> HOME_REBUILD +
        # NARROW_FRONTAGE + DEEP_LOT, never PREMIUM_SMALL_LOT.
        row = _shadow_row(track="PREMIUM_SMALL_LOT")
        cand = _candidate(frontage_ft=25.0, depth_ft=160.0, lot_area_sqft=4000.0)
        auto = build_auto_annotation(row, cand)
        assert auto["auto"]["human_lot_band"] == "41-60"
        assert auto["auto"]["human_primary_track"] == "HOME_REBUILD"
        assert "NARROW_FRONTAGE" in auto["auto"]["human_tags"]
        assert "DEEP_LOT" in auto["auto"]["human_tags"]

    def test_large_corner_lot_gets_top_band(self):
        cand = _candidate(frontage_ft=60.0, depth_ft=120.0, lot_area_sqft=7200.0)
        row = _shadow_row(parcel={"corner_lot": True, "lane_observed": True})
        auto = build_auto_annotation(row, cand)
        assert auto["auto"]["human_lot_band"] == "81-100"
        assert "CORNER" in auto["auto"]["human_tags"]
        assert "LANE" in auto["auto"]["human_tags"]

    def test_major_road_frontage_penalizes_location(self):
        row = _shadow_row(parcel={"property_fronts_major_road": True, "road_class": "arterial"})
        auto = build_auto_annotation(row, _candidate())
        # base 50 - 20 = 30 -> 21-40 band; without road it would be >= 41-60
        assert auto["auto"]["human_location_band"] == "21-40"
        assert "ROAD" in auto["auto"]["human_tags"]

    def test_strong_school_raises_location(self):
        school = {
            "elementary_catchment": "Elem",
            "secondary_catchment": "Sec",
            "elementary_performance": {"ontrack_or_extending_rate": 0.55},
        }
        auto = build_auto_annotation(_shadow_row(school=school), _candidate())
        assert auto["auto"]["human_location_band"] in ("61-80", "81-100")
        assert "SCHOOL" in auto["auto"]["human_tags"]

    def test_price_ratio_maps_to_band(self):
        # asking 1.1M vs BCA 1.6M = 0.69x -> 81-100 (reviewer scale: <70% full marks)
        auto = build_auto_annotation(
            _shadow_row(acquisition={"asking": 1_100_000}),
            _candidate(asking=1_100_000),
        )
        assert auto["auto"]["human_price_value_band"] == "81-100"
        assert "PRICE" in auto["auto"]["human_tags"]

    def test_expensive_price_low_band(self):
        # 2.1M vs BCA 1.6M = 1.31x -> >110% => 0-20 + reject flag
        auto = build_auto_annotation(
            _shadow_row(acquisition={"asking": 2_100_000}),
            _candidate(asking=2_100_000),
        )
        assert auto["auto"]["human_price_value_band"] == "0-20"
        assert "ASKING_OVER_BCA" in auto["needs_human_review"]
        assert auto["data_only_signal"] == "WEAK"
        assert auto["auto"]["human_decision"] == "PASS"  # 不考虑 → auto-PASS

    def test_redevelopment_band_from_potential(self):
        # Standard lot + passing location -> 61-80; R1-1's universal 4-unit
        # entitlement is not a premium differentiator.
        auto = build_auto_annotation(_shadow_row(dev_potential=80.0), _candidate())
        assert auto["auto"]["human_redevelopment_band"] == "61-80"
        # Premium needs genuinely rare geometry (>=5000 sqft or >=40 ft).
        auto_prem = build_auto_annotation(
            _shadow_row(dev_potential=80.0),
            _candidate(frontage_ft=41.0, depth_ft=120.0, lot_area_sqft=4920.0),
        )
        assert auto_prem["auto"]["human_redevelopment_band"] == "81-100"
        # Sub-standard lot cannot be rescued by entitlement.
        auto_low = build_auto_annotation(
            _shadow_row(dev_potential=25.0),
            _candidate(frontage_ft=30.0, depth_ft=100.0, lot_area_sqft=3000.0),
        )
        assert auto_low["auto"]["human_redevelopment_band"] == "21-40"
        # No geometry at all -> fall back to the shadow potential score.
        auto_fallback = build_auto_annotation(
            _shadow_row(dev_potential=80.0),
            _candidate(frontage_ft=None, depth_ft=None, lot_area_sqft=None),
        )
        assert auto_fallback["auto"]["human_redevelopment_band"] == "61-80"


class TestSpecialSituation:
    def test_court_ordered_gate_is_special(self):
        row = _shadow_row(
            gates=[{"code": "TITLE_OR_TRANSACTION", "status": "FAIL",
                    "evidence": ["COURT_ORDERED"]}]
        )
        auto = build_auto_annotation(row, _candidate())
        assert auto["auto"]["human_primary_track"] == "SPECIAL_SITUATION"
        assert auto["auto"]["human_decision"] == "SPECIAL"
        assert auto["data_only_signal"] == "SPECIAL"

    def test_foreclosure_keywords_detect_special(self):
        cand = _candidate(description="Court-Ordered Sale. Foreclosure proceedings.")
        auto = build_auto_annotation(_shadow_row(), cand)
        assert auto["auto"]["human_primary_track"] == "SPECIAL_SITUATION"
        assert auto["auto"]["human_decision"] == "SPECIAL"

    def test_small_lot_alone_is_not_special(self):
        cand = _candidate(frontage_ft=25.0, depth_ft=100.0, lot_area_sqft=2500.0)
        auto = build_auto_annotation(_shadow_row(), cand)
        assert auto["auto"]["human_primary_track"] != "SPECIAL_SITUATION"
        assert auto["auto"]["human_decision"] is None
        assert auto["data_only_signal"] != "SPECIAL"

    def test_as_is_listing_is_review_flag_not_special(self):
        cand = _candidate(description="Sold as is where is. Land value only.")
        auto = build_auto_annotation(_shadow_row(), cand)
        assert auto["auto"]["human_primary_track"] != "SPECIAL_SITUATION"
        assert any("CONDITION" in flag or "LISTING" in flag for flag in auto["needs_human_review"])


class TestNeverAutoFill:
    def test_visual_fields_and_decision_stay_empty(self):
        auto = build_auto_annotation(_shadow_row(), _candidate())
        assert auto["auto"]["human_current_use_band"] is None
        assert auto["auto"]["human_house_reno_band"] is None
        assert auto["auto"]["human_decision"] is None
        assert auto["auto"]["human_must_keep"] is False
        assert auto["auto"]["dual_override"] is False

    def test_signal_and_candidates_present(self):
        auto = build_auto_annotation(_shadow_row(), _candidate())
        assert auto["data_only_signal"] in {"STRONG", "NORMAL", "WEAK", "SPECIAL"}
        assert "must_keep_candidate" in auto
        assert "dual_override_candidate" in auto
        assert auto["confidence"] in {"HIGH", "MEDIUM", "LOW"}
        assert isinstance(auto["reasons"], list) and auto["reasons"]
        assert isinstance(auto["needs_human_review"], list)


class TestPhotoCoverageRule:
    def test_three_or_fewer_photos_fills_condition_bands(self):
        auto = build_auto_annotation(_shadow_row(), _candidate(), photo_count=3)
        assert auto["auto"]["human_current_use_band"] == "0-20"
        assert auto["auto"]["human_house_reno_band"] == "0-20"
        assert "NO_INTERIOR_PHOTOS" in auto["needs_human_review"]

    def test_four_to_eleven_photos_flag_only_no_fill(self):
        auto = build_auto_annotation(_shadow_row(), _candidate(), photo_count=5)
        assert auto["auto"]["human_current_use_band"] is None
        assert auto["auto"]["human_house_reno_band"] is None
        assert "FEW_PHOTOS" in auto["needs_human_review"]

    def test_many_photos_no_flag(self):
        auto = build_auto_annotation(_shadow_row(), _candidate(), photo_count=25)
        assert auto["auto"]["human_current_use_band"] is None
        assert "NO_INTERIOR_PHOTOS" not in auto["needs_human_review"]
        assert "FEW_PHOTOS" not in auto["needs_human_review"]

    def test_unknown_photo_count_no_fill(self):
        auto = build_auto_annotation(_shadow_row(), _candidate(), photo_count=None)
        assert auto["auto"]["human_current_use_band"] is None


class TestPriceAnchorBCA:
    def test_price_band_uses_bca_assessment(self):
        # asking 1.45M vs BCA 1.6M = 0.91x -> 41-60 (reviewer scale: 90-95%),
        # even if market_land_basis would make it look cheap.
        row = _shadow_row(
            acquisition={
                "asking": 1_450_000,
                "assessment_total": 1_600_000,
                "market_land_basis": 1_900_000,
            }
        )
        auto = build_auto_annotation(row, _candidate(asking=1_450_000))
        assert auto["auto"]["human_price_value_band"] == "41-60"

    def test_asking_above_bca_penalized(self):
        row = _shadow_row(
            acquisition={
                "asking": 1_900_000,
                "assessment_total": 1_600_000,
                "market_land_basis": 2_000_000,
            }
        )
        auto = build_auto_annotation(row, _candidate(asking=1_900_000))
        # 1.19x BCA -> >110% => 0-20 + flag; mlb ratio (0.95) must not rescue it
        assert auto["auto"]["human_price_value_band"] == "0-20"
        assert "ASKING_OVER_BCA" in auto["needs_human_review"]

    def test_asking_at_bca_boundary(self):
        # exactly at BCA -> 0-20; just below (0.99) -> 21-40
        for asking, band in ((1_600_000, "0-20"), (1_590_000, "21-40")):
            auto = build_auto_annotation(
                _shadow_row(acquisition={"asking": asking}),
                _candidate(asking=asking),
            )
            assert auto["auto"]["human_price_value_band"] == band

    def test_asking_87pct_is_good_band(self):
        # 0.87x -> 61-80; <0.85 -> 81-100
        for asking, band in ((1_392_000, "61-80"), (1_350_000, "81-100")):
            auto = build_auto_annotation(
                _shadow_row(acquisition={"asking": asking}),
                _candidate(asking=asking),
            )
            assert auto["auto"]["human_price_value_band"] == band


class TestAutoPassRule:
    def _weak_row(self):
        return _shadow_row(
            acquisition={"asking": 1_550_000},  # 0.97x BCA -> 21-40
            parcel={"property_fronts_major_road": True, "road_class": "arterial"},
            dev_potential=30.0,
        )

    def test_all_data_dims_low_prefills_pass(self):
        cand = _candidate(frontage_ft=25.0, depth_ft=100.0, lot_area_sqft=2500.0)
        auto = build_auto_annotation(self._weak_row(), cand)
        bands = [auto["auto"][f] for f in (
            "human_lot_band", "human_location_band",
            "human_redevelopment_band", "human_price_value_band")]
        assert all(b in ("0-20", "21-40") for b in bands)
        assert auto["auto"]["human_decision"] == "PASS"
        assert "AUTO_PASS_RULE" in auto["needs_human_review"]

    def test_one_good_dimension_blocks_auto_pass(self):
        cand = _candidate(frontage_ft=25.0, depth_ft=100.0, lot_area_sqft=2500.0)
        row = self._weak_row()
        row["acquisition"]["asking"] = 1_300_000  # 0.81x BCA -> 81-100
        auto = build_auto_annotation(row, cand)
        assert auto["auto"]["human_decision"] is None

    def test_special_still_beats_pass_rule(self):
        row = self._weak_row()
        row["gates"] = [{"code": "TITLE_OR_TRANSACTION", "status": "FAIL",
                         "evidence": ["COURT_ORDERED"]}]
        cand = _candidate(frontage_ft=25.0, depth_ft=100.0, lot_area_sqft=2500.0)
        auto = build_auto_annotation(row, cand)
        assert auto["auto"]["human_decision"] == "SPECIAL"


class TestUnknownNotPoor:
    def test_missing_geometry_leaves_band_null(self):
        cand = _candidate(frontage_ft=None, depth_ft=None, lot_area_sqft=None)
        cand["lot_dimensions"] = None
        auto = build_auto_annotation(_shadow_row(), cand)
        assert auto["auto"]["human_lot_band"] is None
        assert "MISSING_GEOMETRY" in auto["needs_human_review"]
        assert auto["confidence"] in {"MEDIUM", "LOW"}

    def test_missing_price_basis_not_penalized(self):
        row = _shadow_row(acquisition={"market_land_basis": None, "assessment_total": None})
        auto = build_auto_annotation(row, _candidate())
        assert auto["auto"]["human_price_value_band"] is None
        assert auto["data_only_signal"] != "WEAK"  # unknown must not look poor


class TestBatchAndPacketIntegration:
    def test_build_all_and_card_shows_auto_not_human(self):
        rows = {"mls:X": _shadow_row(), "mls:Y": _shadow_row("mls:Y", "2 OTHER AVE")}
        cands = {"mls:X": _candidate(), "mls:Y": _candidate()}
        out = build_auto_annotations(rows, cands)
        assert set(out) == {"mls:X", "mls:Y"}

        auto = out["mls:X"]
        ctx = build_card_context(
            shadow_row=rows["mls:X"], candidate=cands["mls:X"],
            photo_manifest=None, split="calibration",
            prev_key=None, next_key=None, auto=auto,
        )
        html = render_card_html(ctx)
        # auto-prefilled fields visible with distinct marker
        assert "auto" in html
        assert auto["auto"]["human_lot_band"] in html
        # human-only fields unselected: no auto value rendered for them
        assert '"human_current_use_band": null' in html
        # data-only signal surfaced
        assert auto["data_only_signal"] in html
        assert "AUTO" in html or "预填" in html

    def test_card_without_auto_still_works(self):
        ctx = build_card_context(
            shadow_row=_shadow_row(), candidate=_candidate(),
            photo_manifest=None, split="calibration",
            prev_key=None, next_key=None,
        )
        html = render_card_html(ctx)
        # No auto record baked in: the initial badge is 未标注 and no
        # auto-meta section is rendered.  (The JS string literal may still
        # contain AUTO_PRE_FILLED as a possible runtime state.)
        assert ">未标注</span>" in html
        assert "数据预填（客观字段，自动）" not in html
        assert '"data_only_signal": null' in html or "AUTO_META" in html


class TestEvalProvenance:
    def _model(self):
        return {
            "mls:X": {
                "address": "1 TEST", "current_use_score": 50.0,
                "redevelopment_score": 70.0, "dual_value_score": 60.0,
                "reno_score": 50.0, "location_quality_score": 60.0,
                "redevelopment_geometry_adequacy_score": 65.0,
                "current_package_value_for_money_score": 55.0,
                "reno_estimate": 50000,
            }
        }

    def test_unreviewed_auto_rows_not_counted(self):
        raw = {
            "property_key": "mls:X", "split": "calibration",
            "human_redevelopment_band": "61-80",  # auto-filled, never touched
            "human_set_fields": [],
            "auto_snapshot": {"human_redevelopment_band": "61-80"},
        }
        ann, errors = parse_annotation(raw, split="calibration")
        assert not errors and ann is not None
        assert ann.is_human_labeled() is False

    def test_human_override_counted(self):
        raw = {
            "property_key": "mls:X", "split": "calibration",
            "human_redevelopment_band": "41-60",
            "human_set_fields": ["human_redevelopment_band", "human_decision"],
            "human_decision": "KEEP",
            "auto_snapshot": {"human_redevelopment_band": "61-80"},
        }
        ann, errors = parse_annotation(raw, split="calibration")
        assert not errors and ann is not None
        result = evaluate_calibration(
            [ann], model_rows=self._model(), shadow_rows={}, retained_keys={"mls:X"},
        )
        dim = result["dimensions"]["redevelopment"]
        assert dim["human_set"] == 1
        assert dim["overrides"] == 1

    def test_reviewed_row_auto_acceptance_counts_as_pair(self):
        # Human reviewed (set decision) but left auto band untouched ->
        # accepted value still joins the agreement pair, flagged as auto.
        raw = {
            "property_key": "mls:X", "split": "calibration",
            "human_redevelopment_band": "61-80",
            "human_set_fields": ["human_decision"],
            "human_decision": "KEEP",
            "auto_snapshot": {"human_redevelopment_band": "61-80"},
        }
        ann, _ = parse_annotation(raw, split="calibration")
        result = evaluate_calibration(
            [ann], model_rows=self._model(), shadow_rows={}, retained_keys={"mls:X"},
        )
        dim = result["dimensions"]["redevelopment"]
        assert dim["pair_count"] == 1
        assert dim["auto_accepted"] == 1
        assert dim["overrides"] == 0
