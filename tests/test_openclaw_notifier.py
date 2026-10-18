from northshore_watchdog.notifiers.openclaw_notifier import build_openclaw_payload


def test_openclaw_payload_uses_route_action_and_report_fields():
    payload = build_openclaw_payload(
        route="property_watchdog",
        action="send_weekly_report",
        summary={"a_count": 1},
        top_candidates=[{"address": "1000 Sample Ambleside Avenue", "mls_number": "R3000001"}],
        review_candidates=[{"address": "2553 Bendale Road", "mls_number": "R3126455"}],
        markdown_report="# Report",
        html_report_path="outputs/weekly_report.html",
        generated_at="2026-06-03T08:00:00+08:00",
    )

    assert payload["route"] == "property_watchdog"
    assert payload["action"] == "send_weekly_report"
    assert payload["payload"]["summary"]["a_count"] == 1
    assert payload["payload"]["top_candidates"][0]["address"] == "1000 Sample Ambleside Avenue"
    assert payload["payload"]["top_candidates"][0]["mls_number"] == "R3000001"
    assert payload["payload"]["review_candidates"][0]["mls_number"] == "R3126455"
