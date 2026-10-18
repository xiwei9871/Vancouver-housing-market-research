from northshore_watchdog.research.strategy_v2_online_screening import evaluate_recall


def test_recall_reports_not_available_without_human_labels():
    result = evaluate_recall([{"property_key": "a"}], retained_keys={"a"})

    assert result["status"] == "NOT_AVAILABLE"


def test_recall_counts_must_keep_and_keep_separately():
    labels = {"a": "MUST_KEEP", "b": "KEEP", "c": "DROP"}

    result = evaluate_recall(
        [{"property_key": key} for key in labels],
        retained_keys={"a", "c"},
        labels=labels,
    )

    assert result["must_keep_recall"] == 1.0
    assert result["keep_recall"] == 0.5
    assert result["must_keep_denominator"] == 1
    assert result["keep_denominator"] == 2


def test_recall_uses_only_the_supplied_calibration_universe():
    labels = {"a": "MUST_KEEP", "b": "KEEP"}

    result = evaluate_recall(
        [{"property_key": "a"}, {"property_key": "b"}, {"property_key": "outside"}],
        retained_keys={"a", "outside"},
        labels=labels,
    )

    assert result["status"] == "AVAILABLE"
    assert result["universe_count"] == 2
    assert result["retained_count"] == 1
    assert result["must_keep_recall"] == 1.0
    assert result["keep_recall"] == 0.5
