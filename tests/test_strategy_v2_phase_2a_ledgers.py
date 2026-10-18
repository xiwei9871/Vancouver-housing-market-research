from northshore_watchdog.research.strategy_v2_photo_intelligence import (
    build_online_holding_assessment,
)


def test_clean_dated_house_is_not_misclassified_as_poor():
    result = build_online_holding_assessment(
        listing_description="Original kitchen, clean and well maintained; updated roof in 2021.",
        card={"bedrooms": 3, "bathrooms": 2, "finished_sqft": 1800},
        photo_evidence={
            "visible_defects": [],
            "maintenance_signals": ["clean", "well maintained"],
        },
        coverage={"photo_evidence_coverage": 75, "photo_confidence": "HIGH"},
    )

    assert result.visible_maintenance_condition == "GOOD"
    assert result.cosmetic_modernization == "DATED"
    assert result.holding_usability == "GOOD"
    assert result.online_capex_band == "B"


def test_visible_water_damage_downgrades_maintenance_but_not_by_missing_photos():
    result = build_online_holding_assessment(
        listing_description="",
        card={"bedrooms": 3, "bathrooms": 2, "finished_sqft": 1800},
        photo_evidence={"visible_defects": ["WATER_STAINING"], "maintenance_signals": []},
        coverage={"photo_evidence_coverage": 25, "photo_confidence": "LOW"},
    )

    assert result.visible_maintenance_condition == "ELEVATED_RISK"
    assert result.photo_confidence == "LOW"
    assert result.online_capex_band in {"C", "D"}


def test_multiple_kitchens_are_layout_signal_not_family_room_bonus():
    result = build_online_holding_assessment(
        listing_description="Two kitchens and five bedrooms.",
        card={"bedrooms": 5, "bathrooms": 2, "finished_sqft": 2000},
        photo_evidence={"visible_defects": [], "maintenance_signals": []},
        coverage={"photo_evidence_coverage": 80, "photo_confidence": "HIGH"},
    )

    assert result.holding_usability != "VERY_GOOD"
    assert "MULTI_KITCHEN_LAYOUT_REVIEW" in result.flags


def test_missing_photos_do_not_become_a_poor_condition_score():
    result = build_online_holding_assessment(
        listing_description="",
        card={"bedrooms": 3, "bathrooms": 2, "finished_sqft": 1800},
        photo_evidence={},
        coverage={"photo_evidence_coverage": None, "photo_confidence": "UNKNOWN"},
    )

    assert result.visible_maintenance_condition == "UNKNOWN"
    assert result.cosmetic_modernization == "UNKNOWN"
    assert result.holding_usability == "GOOD"
    assert result.online_capex_band == "UNKNOWN"
    assert result.photo_confidence == "UNKNOWN"


def test_unclassified_photo_urls_do_not_claim_a_maintenance_band():
    result = build_online_holding_assessment(
        listing_description="",
        card={"bedrooms": 3, "bathrooms": 2, "finished_sqft": 1800},
        photo_evidence={},
        coverage={"photo_evidence_coverage": 0, "photo_confidence": "LOW"},
    )

    assert result.visible_maintenance_condition == "UNKNOWN"
    assert result.online_capex_band == "UNKNOWN"


def test_recently_updated_house_can_receive_low_online_capex_band():
    result = build_online_holding_assessment(
        listing_description="Fully renovated in 2022 with new roof, furnace, windows, kitchen and bathrooms.",
        card={"bedrooms": 3, "bathrooms": 2, "finished_sqft": 1800},
        photo_evidence={
            "visible_defects": [],
            "maintenance_signals": ["fully renovated", "new roof", "new furnace"],
        },
        coverage={"photo_evidence_coverage": 100, "photo_confidence": "HIGH"},
    )

    assert result.visible_maintenance_condition == "GOOD"
    assert result.cosmetic_modernization == "RECENT"
    assert result.online_capex_band == "A"


def test_clean_dated_home_has_cosmetic_cost_but_no_observed_survival_cost():
    result = build_online_holding_assessment(
        listing_description="Original kitchen, dated bathroom, clean and well maintained.",
        card={"bedrooms": 3, "bathrooms": 2, "finished_sqft": 1800},
        photo_evidence={
            "visible_defects": [],
            "maintenance_signals": ["clean", "well maintained"],
            "cosmetic_signals": ["original kitchen", "dated bathroom"],
        },
        coverage={"photo_evidence_coverage": 100, "photo_confidence": "HIGH"},
    )

    assert result.online_capex_band == "B"
    assert result.survival_capex_base == 0
    assert result.cosmetic_reno_base > 0
    assert result.observed_survival_items == []
    assert result.observed_cosmetic_items


def test_surface_weathering_is_minor_maintenance_not_a_survival_repair():
    result = build_online_holding_assessment(
        listing_description="",
        card={"bedrooms": 3, "bathrooms": 2, "finished_sqft": 1800},
        photo_evidence={
            "visible_defects": [
                "dry and patchy grass",
                "weathered fence and peeling exterior paint",
                "light rust on exterior railing",
            ],
            "maintenance_signals": [],
            "cosmetic_signals": [],
        },
        coverage={"photo_evidence_coverage": 100, "photo_confidence": "HIGH"},
    )

    assert result.online_capex_band == "B"
    assert result.survival_capex_base == 0
    assert result.minor_maintenance_items
    assert result.observed_survival_items == []


def test_visible_water_damage_creates_one_deduplicated_survival_item():
    result = build_online_holding_assessment(
        listing_description="",
        card={"bedrooms": 3, "bathrooms": 2, "finished_sqft": 1800},
        photo_evidence={
            "visible_defects": [
                "water staining beneath sink",
                "localized water damage near sink",
            ],
            "maintenance_signals": [],
            "cosmetic_signals": [],
        },
        coverage={"photo_evidence_coverage": 100, "photo_confidence": "HIGH"},
    )

    assert result.online_capex_band == "C"
    assert result.survival_capex_base > 0
    assert len(result.observed_survival_items) == 1
    assert result.observed_survival_items[0]["project_group"] == "WATER_DRAINAGE"


def test_missing_systems_are_unknown_without_invented_cost():
    result = build_online_holding_assessment(
        listing_description="",
        card={"bedrooms": 3, "bathrooms": 2, "finished_sqft": 1800},
        photo_evidence={},
        coverage={"photo_evidence_coverage": 0, "photo_confidence": "LOW"},
    )

    assert result.online_capex_band == "UNKNOWN"
    assert result.survival_capex_low == 0
    assert result.survival_capex_base == 0
    assert result.survival_capex_high == 0
    assert "ROOF" in result.unseen_systems
    assert "FURNACE" in result.unseen_systems
    assert "ELECTRICAL" in result.unseen_systems
    assert "PLUMBING" in result.unseen_systems


def test_recent_named_system_updates_can_produce_a_without_cosmetic_capex():
    result = build_online_holding_assessment(
        listing_description="Updated roof 2022, new furnace 2023, updated electrical and plumbing; clean kitchen.",
        card={"bedrooms": 3, "bathrooms": 2, "finished_sqft": 1800},
        photo_evidence={
            "visible_defects": [],
            "maintenance_signals": ["updated roof", "new furnace", "updated electrical", "updated plumbing"],
            "cosmetic_signals": [],
        },
        coverage={"photo_evidence_coverage": 100, "photo_confidence": "HIGH"},
    )

    assert result.online_capex_band == "A"
    assert result.survival_capex_base == 0
    assert result.estimate_confidence == "HIGH"


def test_online_cost_fields_are_serializable_and_versioned():
    result = build_online_holding_assessment(
        listing_description="Original kitchen.",
        card={"bedrooms": 3, "bathrooms": 2, "finished_sqft": 1800},
        photo_evidence={"cosmetic_signals": ["original kitchen"]},
        coverage={"photo_evidence_coverage": 75, "photo_confidence": "HIGH"},
    )

    payload = result.model_dump(mode="json")
    assert payload["schema_version"] == "strategy-v2-phase-2a-online-holding-v2"
    assert payload["estimate_basis"] == "online_cost_prior_v1"
    assert all(key in payload for key in (
        "survival_capex_low",
        "survival_capex_base",
        "survival_capex_high",
        "cosmetic_reno_low",
        "cosmetic_reno_base",
        "cosmetic_reno_high",
        "observed_survival_items",
        "observed_cosmetic_items",
        "minor_maintenance_items",
        "unseen_systems",
        "unobserved_critical_areas",
        "estimate_confidence",
    ))
