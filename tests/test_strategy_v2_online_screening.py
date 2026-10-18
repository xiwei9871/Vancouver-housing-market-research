from northshore_watchdog.research.strategy_v2_online_screening import dominates, select_shortlist


def _candidate(key, track, location, holding, land, price, risk):
    return {
        "property_key": key,
        "primary_track": track,
        "online_location_score": location,
        "online_holding_score": holding,
        "online_land_score": land,
        "price_attractiveness": price,
        "permanent_risk": risk,
    }


def test_pareto_candidate_is_not_removed_by_track_target():
    candidates = [
        _candidate("a", "HOME_REBUILD", 90, 90, 60, 60, 30),
        _candidate("b", "HOME_REBUILD", 70, 70, 80, 80, 40),
        _candidate("c", "HOME_REBUILD", 75, 75, 75, 75, 50),
    ]

    selected = select_shortlist(
        candidates,
        track_targets={"HOME_REBUILD": 1},
        target_range=(1, 1),
    )

    assert {item["property_key"] for item in selected} >= {"a", "b"}


def test_special_situation_does_not_consume_normal_slot():
    candidates = [
        _candidate("special", "SPECIAL_SITUATION", 100, 100, 100, 100, 100),
        _candidate("home", "HOME_REBUILD", 60, 60, 60, 60, 20),
    ]

    selected = select_shortlist(
        candidates,
        track_targets={"HOME_REBUILD": 1},
        target_range=(1, 1),
    )

    assert [item["property_key"] for item in selected] == ["home"]


def test_selector_deduplicates_by_property_key_before_selection():
    candidates = [
        _candidate("same", "HOME_REBUILD", 90, 90, 90, 90, 10),
        _candidate("same", "HOME_REBUILD", 50, 50, 50, 50, 80),
        _candidate("other", "HOME_REBUILD", 60, 60, 60, 60, 30),
    ]

    selected = select_shortlist(
        candidates,
        track_targets={"HOME_REBUILD": 2},
        target_range=(1, 2),
    )

    keys = [item["property_key"] for item in selected]
    assert keys.count("same") == 1
    assert set(keys) == {"same", "other"}


def test_exceptional_signal_is_protected_even_when_not_pareto_dominant():
    candidates = [
        _candidate("balanced", "HOME_REBUILD", 85, 85, 85, 85, 20),
        _candidate("land_exception", "HOME_REBUILD", 40, 40, 100, 40, 70),
    ]

    selected = select_shortlist(
        candidates,
        track_targets={"HOME_REBUILD": 1},
        target_range=(1, 1),
    )

    assert {item["property_key"] for item in selected} >= {"balanced", "land_exception"}


def test_selector_can_bound_shortlist_while_preserving_protection_metadata_contract():
    candidates = [
        _candidate(f"frontier-{index}", "HOME_REBUILD", 90 - index, 60 + index, 70, 70, 40)
        for index in range(10)
    ]

    selected = select_shortlist(
        candidates,
        track_targets={"HOME_REBUILD": 2},
        target_range=(2, 2),
        protected_caps={"HOME_REBUILD": 2},
    )

    assert len(selected) == 2


def test_large_frontier_is_bounded_by_track_target_but_keeps_top_frontier_signals():
    candidates = [
        {
            "property_key": f"frontier-{index}",
            "primary_track": "HOME_REBUILD",
            "online_location_score": 90 - index,
            "online_holding_score": 60 + index,
            "online_land_score": 70,
            "price_attractiveness": 70,
            "permanent_risk": 40,
        }
        for index in range(10)
    ]

    selected = select_shortlist(
        candidates,
        track_targets={"HOME_REBUILD": 2},
        target_range=(2, 2),
    )

    assert len(selected) == 2


def test_target_range_maximum_limits_non_protected_fillers():
    candidates = [
        _candidate("best", "HOME_REBUILD", 90, 90, 90, 90, 10),
        _candidate("next", "HOME_REBUILD", 80, 80, 80, 80, 20),
        _candidate("third", "HOME_REBUILD", 70, 70, 70, 70, 30),
    ]

    selected = select_shortlist(
        candidates,
        track_targets={"HOME_REBUILD": 3},
        target_range=(1, 1),
    )

    assert [item["property_key"] for item in selected] == ["best"]


def test_pareto_dominance_treats_lower_risk_as_better():
    safer = _candidate("safer", "HOME_REBUILD", 80, 80, 80, 80, 10)
    riskier = _candidate("riskier", "HOME_REBUILD", 80, 80, 80, 80, 40)

    assert dominates(safer, riskier) is True
    assert dominates(riskier, safer) is False


def test_pareto_dominance_is_scoped_to_primary_track():
    home = _candidate("home", "HOME_REBUILD", 100, 100, 100, 100, 0)
    land = _candidate("land", "LAND_DEVELOPMENT", 1, 1, 1, 1, 100)

    assert dominates(home, land) is False


def test_missing_metric_does_not_claim_dominance():
    complete = _candidate("complete", "HOME_REBUILD", 90, 90, 90, 90, 10)
    incomplete = _candidate("incomplete", "HOME_REBUILD", 80, None, 80, 80, 20)

    assert dominates(complete, incomplete) is False
