"""Tests for the Strategy V2 human-calibration workflow.

The calibration loop turns the human's structured band labels into measurable
agreement/leakage metrics so the screening criteria can be iterated with
evidence instead of ad-hoc weight edits.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from northshore_watchdog.research.strategy_v2_calibration import (
    BAND_LABELS,
    annotation_to_label,
    build_card_context,
    evaluate_calibration,
    load_annotations,
    load_split_manifest,
    render_card_html,
    render_index_html,
    write_packet,
    annotations_template_csv,
    append_eval_history,
)


def _splits() -> dict:
    return {
        "schema_version": "strategy-v2-shadow-split-v1",
        "calibration_keys": ["mls:A", "mls:B", "mls:C"],
        "blind_test_keys": ["mls:D"],
        "split_sha256": "abc",
    }


def _shadow_row(key: str = "mls:A", address: str = "1 TEST STREET") -> dict:
    return {
        "property_key": key,
        "address": address,
        "mls": key.split(":")[-1],
        "asset_classification": {"primary_track": "HOME_REBUILD"},
        "holding": {"house_utility_band": "GOOD"},
        "location": {
            "school": {"elementary_catchment": "Elem", "secondary_catchment": "Sec"},
            "parcel_facts": {
                "property_fronts_major_road": False,
                "nearest_major_road": "MAIN ST",
                "road_class": "B",
                "distance_to_major_road_m": 120.0,
                "park_facing": False,
                "view_type": "NONE",
                "waterfront": False,
                "corner_lot": True,
                "lane_observed": True,
                "slope": "FLAT",
            },
        },
        "development_scenarios": [
            {"scenario_name": "BASE", "unit_count": 3.0, "rule_basis": {"zoning": "R1-1"}},
            {"scenario_name": "CONDITIONAL", "unit_count": 5.0},
        ],
        "acquisition": {
            "asking": 1500000.0,
            "assessment_total": 1600000.0,
            "assessment_land": 1400000.0,
            "assessment_improvement": 200000.0,
            "assessment_signals": {"assessment_year": 2026},
        },
    }


def _candidate(key: str = "mls:A") -> dict:
    return {
        "address": "1 TEST STREET",
        "mls_number": key.split(":")[-1],
        "source_url": "https://www.realtor.ca/real-estate/1/test",
        "asking": 1500000,
        "bedrooms": 3,
        "bathrooms": 2,
        "finished_sqft": 1800,
        "year_built": 1975,
        "lot_area_sqft": 4026,
        "lot_dimensions": "33 x 122",
        "frontage_ft": 33.0,
        "depth_ft": 122.0,
        "neighbourhood": "Testhood",
        "municipality": "Vancouver",
        "description": "A test house.",
        "taxes": 8000,
        "dom": 12,
        "ownership_type": "Freehold",
        "property_type": "Single Family",
        "listing_status": "active",
        "elementary_school": "Elem",
        "secondary_school": "Sec",
        "geometry_source": "OFFICIAL_BCA",
    }


def _annotation(key: str, decision: str = "KEEP", band: str = "61-80", **kw) -> dict:
    row = {
        "property_key": key,
        "split": "calibration",
        "human_current_use_band": band,
        "human_redevelopment_band": band,
        "human_house_reno_band": band,
        "human_location_band": band,
        "human_lot_band": band,
        "human_price_value_band": band,
        "human_primary_track": "HOME_REBUILD",
        "human_decision": decision,
        "human_must_keep": kw.pop("must_keep", False),
        "human_tags": ["ROAD"],
        "human_reason": "test",
        "dual_override": False,
        "annotated_at": "2026-09-17T10:00:00+08:00",
    }
    row.update(kw)
    return row


def _model_row(key: str, cu: float, rd: float, dual: float, address: str = "X") -> dict:
    return {
        "property_key": key,
        "address": address,
        "current_use_score": cu,
        "redevelopment_score": rd,
        "dual_value_score": dual,
        "reno_score": 55.0,
        "location_quality_score": 60.0,
        "redevelopment_geometry_adequacy_score": 70.0,
        "current_package_value_for_money_score": 65.0,
    }


class TestAnnotationParsing:
    def test_valid_annotation_round_trip(self, tmp_path):
        path = tmp_path / "a.json"
        path.write_text(json.dumps(_annotation("mls:A")), encoding="utf-8")

        annotations, errors = load_annotations(path)

        assert errors == []
        assert len(annotations) == 1
        assert annotations[0].property_key == "mls:A"
        assert annotations[0].human_current_use_band == "61-80"

    def test_invalid_band_is_collected_as_error(self, tmp_path):
        bad = _annotation("mls:A", band="HIGH")
        path = tmp_path / "a.json"
        path.write_text(json.dumps([bad, _annotation("mls:B")]), encoding="utf-8")

        annotations, errors = load_annotations(path)

        assert len(annotations) == 1
        assert annotations[0].property_key == "mls:B"
        assert errors and "mls:A" in errors[0]

    def test_csv_loader_accepts_template_columns(self, tmp_path):
        path = tmp_path / "annotations.csv"
        with path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(
                fh,
                fieldnames=[
                    "property_key", "split",
                    "human_current_use_band", "human_redevelopment_band",
                    "human_house_reno_band", "human_location_band",
                    "human_lot_band", "human_price_value_band",
                    "human_primary_track", "human_secondary_track",
                    "human_decision", "human_must_keep", "human_tags",
                    "human_reason", "dual_override", "human_dual_band",
                    "annotated_at",
                ],
            )
            writer.writeheader()
            writer.writerow({
                "property_key": "mls:A",
                "split": "calibration",
                "human_current_use_band": "61-80",
                "human_decision": "WATCH",
                "human_tags": "ROAD;SCHOOL",
                "human_reason": "quiet but dated",
            })
            writer.writerow({"property_key": "mls:B"})  # untouched row

        annotations, errors = load_annotations(path)

        assert errors == []
        assert [a.property_key for a in annotations] == ["mls:A"]
        assert annotations[0].human_decision == "WATCH"
        assert annotations[0].human_tags == ["ROAD", "SCHOOL"]

    def test_directory_loader_reads_per_card_exports(self, tmp_path):
        (tmp_path / "mls-A.json").write_text(json.dumps(_annotation("mls:A")), encoding="utf-8")
        (tmp_path / "mls-B.json").write_text(json.dumps(_annotation("mls:B", decision="PASS")), encoding="utf-8")

        annotations, errors = load_annotations(tmp_path)

        assert errors == []
        assert {a.property_key for a in annotations} == {"mls:A", "mls:B"}


class TestDecisionMapping:
    @pytest.mark.parametrize(
        "decision,must_keep,expected",
        [
            ("KEEP", False, "KEEP"),
            ("WATCH", False, "KEEP"),
            ("PASS", False, "DROP"),
            ("SPECIAL", False, "SPECIAL"),
            ("KEEP", True, "MUST_KEEP"),
            ("WATCH", True, "MUST_KEEP"),
            (None, False, None),
        ],
    )
    def test_mapping(self, decision, must_keep, expected):
        raw = _annotation("mls:A", decision=decision, must_keep=must_keep)
        raw["human_decision"] = decision
        from northshore_watchdog.research.strategy_v2_calibration import parse_annotation

        ann, errors = parse_annotation(raw, split="calibration")
        assert errors == []
        assert annotation_to_label(ann) == expected


class TestBlindCards:
    def test_card_contains_facts_not_machine_judgments(self):
        ctx = build_card_context(
            shadow_row=_shadow_row(),
            candidate=_candidate(),
            photo_manifest={
                "images": [
                    {"source_url": "https://cdn.example/1.jpg", "original_order": 0, "local_path": None},
                    {"source_url": "https://cdn.example/2.jpg", "original_order": 1, "local_path": "/abs/p2.jpg"},
                ]
            },
            split="calibration",
            prev_key=None,
            next_key="mls:B",
        )
        html = render_card_html(ctx)

        assert "1 TEST STREET" in html
        assert "$1,500,000" in html or "1500000" in html
        assert "R1-1" in html
        assert "Elem" in html
        assert "https://cdn.example/1.jpg" in html
        assert "file:///abs/p2.jpg" in html
        # Machine judgments must not leak into the blind card.  The track enum
        # appears as annotation options, but the machine's own verdict must
        # never be pre-selected or rendered as a fact.
        for forbidden in (
            "current_use_score",
            "development_potential",
            "PARETO",
            "survival_capex",
            "cash_holding_basis",
            "house_utility_band",
        ):
            assert forbidden not in html
        # The shadow row classified this property as HOME_REBUILD; the card's
        # blank annotation schema must not pre-fill that answer.
        assert '"human_primary_track": null' in html

    def test_card_photos_use_relative_src_when_assets_dir_given(self):
        manifest = {
            "images": [
                {
                    "original_order": 1,
                    "local_path": "/repo/data/research/raw/photo-assets/mls-a/p1.jpg",
                    "source_url": "https://cdn.example/1.jpg",
                }
            ]
        }
        ctx = build_card_context(
            shadow_row=_shadow_row(), candidate=_candidate(),
            photo_manifest=manifest, split="calibration",
            prev_key=None, next_key=None,
            assets_from_dir="/repo/reports/calibration/2026-09-17/cards",
        )
        html = render_card_html(ctx)
        assert "file://" not in html
        assert "data/research/raw/photo-assets/mls-a/p1.jpg" in html
        assert ctx["photos"][0]["local"] is True

    def test_card_has_annotation_controls_and_localstorage_key(self):
        ctx = build_card_context(
            shadow_row=_shadow_row(), candidate=_candidate(), photo_manifest=None,
            split="calibration", prev_key=None, next_key=None,
        )
        html = render_card_html(ctx)

        assert "calib-annotation:mls:A" in html
        for band in BAND_LABELS:
            assert band in html
        for decision in ("KEEP", "WATCH", "PASS", "SPECIAL"):
            assert decision in html
        assert "human_reason" in html
        assert "dual_override" in html

    def test_index_lists_calibration_before_blind_and_links_cards(self):
        contexts = [
            build_card_context(shadow_row=_shadow_row("mls:A"), candidate=_candidate("mls:A"), photo_manifest=None, split="calibration", prev_key=None, next_key="mls:B"),
            build_card_context(shadow_row=_shadow_row("mls:B"), candidate=_candidate("mls:B"), photo_manifest=None, split="calibration", prev_key="mls:A", next_key="mls:C"),
            build_card_context(shadow_row=_shadow_row("mls:D", "9 BLIND ROAD"), candidate=_candidate("mls:D"), photo_manifest=None, split="blind", prev_key=None, next_key=None),
        ]
        html = render_index_html(contexts, generated_at="2026-09-17")

        assert html.index("cards/mls-A.html") < html.index("cards/mls-D.html")
        assert "blind" in html.lower()
        assert "export" in html.lower()

    def test_write_packet_emits_cards_index_and_template(self, tmp_path):
        contexts = [
            build_card_context(shadow_row=_shadow_row("mls:A"), candidate=_candidate("mls:A"), photo_manifest=None, split="calibration", prev_key=None, next_key="mls:B"),
            build_card_context(shadow_row=_shadow_row("mls:B"), candidate=_candidate("mls:B"), photo_manifest=None, split="calibration", prev_key="mls:A", next_key="mls:D"),
            build_card_context(shadow_row=_shadow_row("mls:D", "9 BLIND ROAD"), candidate=_candidate("mls:D"), photo_manifest=None, split="blind", prev_key="mls:B", next_key=None),
        ]
        out = write_packet(tmp_path, contexts, generated_at="2026-09-17")

        assert (tmp_path / "index.html").is_file()
        assert (tmp_path / "cards" / "mls-A.html").is_file()
        assert (tmp_path / "cards" / "mls-D.html").is_file()
        template = tmp_path / "annotations-template.csv"
        assert template.is_file()
        rows = list(csv.DictReader(template.open(encoding="utf-8")))
        assert len(rows) == 3
        assert rows[0]["property_key"] == "mls:A"
        assert rows[0]["split"] == "calibration"
        assert out["card_count"] == 3


class TestEvaluation:
    def _model(self):
        return {
            "mls:A": _model_row("mls:A", 70.0, 60.0, 64.8, "1 TEST STREET"),
            "mls:B": _model_row("mls:B", 30.0, 80.0, 49.0, "2 TEST STREET"),
            "mls:C": _model_row("mls:C", 50.0, 40.0, 44.7, "3 TEST STREET"),
            "mls:D": _model_row("mls:D", 90.0, 90.0, 90.0, "9 BLIND ROAD"),
        }

    def test_dimension_agreement_perfect_monotone(self):
        from northshore_watchdog.research.strategy_v2_calibration import parse_annotation

        anns = []
        for key, band in (("mls:A", "61-80"), ("mls:B", "21-40"), ("mls:C", "41-60")):
            ann, errors = parse_annotation(_annotation(key, band=band), split="calibration")
            assert errors == []
            anns.append(ann)

        result = evaluate_calibration(
            anns,
            model_rows=self._model(),
            shadow_rows={},
            retained_keys={"mls:A", "mls:B"},
            split="calibration",
        )

        dim = result["dimensions"]["current_use"]
        # Human mids 70/30/50 vs model 70/30/50 -> perfect monotone.
        assert dim["spearman"] == pytest.approx(1.0)
        assert dim["pair_count"] == 3
        assert "confusion" in dim

    def test_blind_annotations_excluded_by_default(self):
        from northshore_watchdog.research.strategy_v2_calibration import parse_annotation

        raw = _annotation("mls:D", decision="PASS")
        raw["split"] = "blind"
        ann, _ = parse_annotation(raw, split="blind")
        result = evaluate_calibration([ann], model_rows=self._model(), shadow_rows={}, retained_keys=set())

        assert result["labeled_count"] == 0
        assert result["status"] == "NOT_AVAILABLE"

    def test_pass_leakage_and_special_isolation(self):
        from northshore_watchdog.research.strategy_v2_calibration import parse_annotation

        anns = []
        for key, decision in (("mls:D", "PASS"), ("mls:A", "KEEP")):
            raw = _annotation(key, decision=decision)
            ann, _ = parse_annotation(raw, split="calibration")
            anns.append(ann)

        result = evaluate_calibration(
            anns, model_rows=self._model(), shadow_rows={}, retained_keys=set(), split="calibration"
        )

        # mls:D is human PASS but model ranks it top of every view.
        leaked = {item["property_key"] for item in result["pass_leakage"]}
        assert "mls:D" in leaked

    def test_named_case_checks(self):
        shadow = {
            "mls:MEML": {
                "address": "5454 MELBOURNE STREET",
                "development_scenarios": [
                    {"scenario_name": "BASE", "unit_count": 5.0},
                ],
            },
            "mls:NANA": {"address": "4561 NANAIMO STREET"},
            "mls:W10": {"address": "3910 W 10TH AVENUE"},
            "mls:E18": {"address": "1016 E 18TH AVENUE"},
            "mls:COL": {
                "address": "2413 COLLINGWOOD STREET",
                "asset_classification": {"primary_track": "PREMIUM_SMALL_LOT"},
            },
        }
        model = {
            "mls:NANA": _model_row("mls:NANA", 95.0, 60.0, 75.0, "4561 NANAIMO STREET"),
            "mls:E18": _model_row("mls:E18", 40.0, 95.0, 61.0, "1016 E 18TH AVENUE"),
            "mls:W10": _model_row("mls:W10", 40.0, 55.0, 47.0, "3910 W 10TH AVENUE"),
            "mls:MEML": _model_row("mls:MEML", 55.0, 80.0, 66.0, "5454 MELBOURNE STREET"),
            "mls:COL": _model_row("mls:COL", 50.0, 50.0, 50.0, "2413 COLLINGWOOD STREET"),
        }
        model["mls:W10"]["reno_estimate"] = 120000

        result = evaluate_calibration(
            [], model_rows=model, shadow_rows=shadow, retained_keys=set(), split="calibration"
        )

        checks = {c["id"]: c for c in result["named_case_checks"]}
        assert checks["nanaimo_4561_not_current_use_top10"]["status"] == "FAIL"
        assert checks["melbourne_5454_five_unit_conditional"]["status"] == "FAIL"
        assert checks["e18_1016_not_dev_top10"]["status"] == "FAIL"
        assert checks["w10_3910_not_reno_capped"]["status"] == "FAIL"
        assert checks["collingwood_2413_premium_track"]["status"] == "PASS"


class TestHistory:
    def test_append_eval_history_jsonl(self, tmp_path):
        path = tmp_path / "calibration-evals.jsonl"
        result = {"status": "AVAILABLE", "labeled_count": 5, "config_fingerprint": "abc123"}

        entry1 = append_eval_history(path, result, generated_at="2026-09-17T10:00:00+08:00")
        entry2 = append_eval_history(path, result, generated_at="2026-09-17T11:00:00+08:00")

        lines = path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 2
        assert entry1["eval_id"] != entry2["eval_id"]
        assert json.loads(lines[0])["labeled_count"] == 5


class TestTemplate:
    def test_template_csv_has_one_row_per_property_with_empty_label_fields(self):
        contexts = [
            build_card_context(shadow_row=_shadow_row("mls:A"), candidate=_candidate("mls:A"), photo_manifest=None, split="calibration", prev_key=None, next_key=None),
        ]
        text = annotations_template_csv(contexts)
        rows = list(csv.DictReader(text.splitlines()))

        assert len(rows) == 1
        assert rows[0]["property_key"] == "mls:A"
        assert rows[0]["human_decision"] == ""
        assert rows[0]["human_current_use_band"] == ""


def test_load_split_manifest(tmp_path):
    path = tmp_path / "splits.json"
    path.write_text(json.dumps(_splits()), encoding="utf-8")
    manifest = load_split_manifest(path)
    assert manifest["calibration_keys"] == ["mls:A", "mls:B", "mls:C"]
    assert manifest["blind_test_keys"] == ["mls:D"]
