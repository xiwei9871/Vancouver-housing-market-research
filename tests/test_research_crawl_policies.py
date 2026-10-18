from datetime import datetime, timezone

from northshore_watchdog.research.crawling.models import CrawlArtifact
from northshore_watchdog.research.crawling.policies import (
    canonical_url,
    challenge_detected,
    page_fingerprint,
    status_from_metrics,
    summarize_artifacts,
    url_allowed,
)


OBSERVED_AT = datetime(2026, 9, 2, tzinfo=timezone.utc)
ENTRYPOINT = "https://www.example.com/listings"


def _artifact(
    *,
    request_kind="SEARCH",
    digest="a" * 64,
    entrypoint_url=ENTRYPOINT,
    requested_url=ENTRYPOINT,
    status_code=200,
    coverage_state="IN_PROGRESS",
    rows=None,
    card_count=0,
    extracted_row_count=0,
    error_type=None,
    skip_reason=None,
):
    return CrawlArtifact(
        run_id="run-1",
        source_name="example",
        source_registry_id="source:example",
        entrypoint_url=entrypoint_url,
        request_kind=request_kind,
        requested_url=requested_url,
        final_url=requested_url,
        status_code=status_code,
        content_type="text/html",
        sha256=digest,
        size_bytes=1,
        retrieved_at=OBSERVED_AT,
        rows=rows or [],
        card_count=card_count,
        extracted_row_count=extracted_row_count,
        coverage_state=coverage_state,
        error_type=error_type,
        skip_reason=skip_reason,
    )


def test_url_policy_requires_http_scheme_and_exact_or_subdomain_domain():
    allowed = {"Example.COM."}

    assert url_allowed("HTTPS://WWW.example.com./listings", allowed)
    assert url_allowed("https://example.com/listings", allowed)
    assert not url_allowed("https://example.com.evil.test/listings", allowed)
    assert not url_allowed("https://evil-example.com/listings", allowed)
    assert not url_allowed("ftp://www.example.com/listings", allowed)
    assert not url_allowed("javascript:void(0)", allowed)
    assert not url_allowed("https:///listings", allowed)


def test_canonical_url_removes_fragment_and_non_bare_trailing_slash_but_keeps_query():
    assert canonical_url("HTTPS://WWW.Example.COM/listings/?page=2&sort=price#results") == (
        "https://www.example.com/listings?page=2&sort=price"
    )
    assert canonical_url("https://WWW.Example.COM/") == "https://www.example.com/"
    assert canonical_url("https://WWW.Example.COM") == "https://www.example.com/"


def test_page_fingerprint_is_a_stable_sha256_hex_digest():
    assert page_fingerprint(b"same") == page_fingerprint(b"same")
    assert page_fingerprint(b"same") != page_fingerprint(b"different")
    assert page_fingerprint(b"same") == "0967115f2813a3541eaef77de9d9d5773f1c0c04314b0bbfe4ff3b3b1c55b5d5"


def test_challenge_detection_uses_visible_text_markers_only():
    assert challenge_detected(b"<p>Verify you are human</p>")
    assert not challenge_detected(b"<script>verify you are human</script><p>Listing</p>")


def test_summary_counts_search_pages_successful_details_cards_rows_and_duplicate_hashes():
    metrics = summarize_artifacts(
        [
            _artifact(digest="a" * 64, card_count=2, extracted_row_count=2),
            _artifact(
                digest="a" * 64,
                requested_url=f"{ENTRYPOINT}?page=2",
                card_count=1,
                extracted_row_count=1,
            ),
            _artifact(
                request_kind="DETAIL",
                digest="b" * 64,
                requested_url="https://www.example.com/listings/one",
                rows=[{"address": "1 Example Street"}],
                extracted_row_count=1,
            ),
            _artifact(
                request_kind="DETAIL",
                digest="c" * 64,
                requested_url="https://www.example.com/listings/two",
                status_code=503,
                error_type="HTTP_503",
            ),
            _artifact(
                request_kind="DETAIL",
                digest="d" * 64,
                requested_url="https://www.example.com/listings/three",
                status_code=None,
                skip_reason="REQUEST_CAP",
            ),
        ],
        expected_entrypoints=(ENTRYPOINT,),
    )

    assert metrics["pages_fetched"] == 2
    assert metrics["details_fetched"] == 1
    assert metrics["cards_found"] == 3
    assert metrics["rows_extracted"] == 4
    assert metrics["duplicate_pages"] == 1
    assert metrics["skipped_details"] == 1
    assert metrics["failed_requests"] == 2
    assert metrics["errors"] == ["HTTP_503"]


def test_summary_requires_natural_completion_for_every_entrypoint():
    second_entrypoint = "https://www.example.com/other-listings"
    metrics = summarize_artifacts(
        [
            _artifact(coverage_state="COVERAGE_COMPLETE"),
            _artifact(
                entrypoint_url=second_entrypoint,
                requested_url=second_entrypoint,
                coverage_state="IN_PROGRESS",
            ),
        ],
        expected_entrypoints=(ENTRYPOINT, second_entrypoint),
    )

    assert metrics["entrypoints"] == 2
    assert metrics["entrypoints_complete"] == 1
    assert metrics["coverage_complete"] is False
    assert status_from_metrics(metrics, []) == "PARTIAL"


def test_summary_marks_cap_and_error_as_gaps_and_never_complete():
    metrics = summarize_artifacts(
        [
            _artifact(
                coverage_state="PARTIAL_CAP_REACHED",
                card_count=1,
                extracted_row_count=1,
            ),
            _artifact(
                request_kind="DETAIL",
                requested_url="https://www.example.com/listings/one",
                error_type="  CHALLENGE_DETECTED  ",
            ),
        ],
        expected_entrypoints=(ENTRYPOINT,),
    )

    assert metrics["coverage_gaps"] == ["PARTIAL_CAP_REACHED"]
    assert metrics["errors"] == ["CHALLENGE_DETECTED"]
    assert metrics["cap_reached"] is True
    assert metrics["coverage_complete"] is False
    assert status_from_metrics(metrics, ["", None, "row 1: invalid address"]) == "PARTIAL"


def test_summary_accepts_dict_artifacts_and_complete_status_is_success():
    artifact = _artifact(
        coverage_state="COVERAGE_COMPLETE",
        card_count=1,
        extracted_row_count=1,
    )

    metrics = summarize_artifacts(
        [artifact.model_dump(mode="python")],
        expected_entrypoints=(ENTRYPOINT,),
    )

    assert metrics["coverage_complete"] is True
    assert status_from_metrics(metrics, []) == "SUCCESS"


def test_summary_never_marks_skipped_detail_coverage_complete():
    metrics = summarize_artifacts(
        [
            _artifact(
                coverage_state="COVERAGE_COMPLETE",
                card_count=1,
                extracted_row_count=1,
            ),
            _artifact(
                request_kind="DETAIL",
                requested_url="https://www.example.com/listings/one",
                status_code=200,
                skip_reason="DETAIL_POLICY",
            ),
        ],
        expected_entrypoints=(ENTRYPOINT,),
    )

    assert metrics["skipped_details"] == 1
    assert metrics["coverage_complete"] is False
    assert status_from_metrics(metrics, []) == "PARTIAL"


def test_zero_data_policy_markers_in_errors_or_gaps_are_blocked_policy():
    for metrics in (
        {"coverage_gaps": ["BLOCKED_POLICY"]},
        {"coverage_gaps": ["ROBOTS_DENIED"]},
        {"errors": ["CHALLENGE_DETECTED"]},
    ):
        assert status_from_metrics(metrics, []) == "BLOCKED_POLICY"


def test_fetched_challenge_pages_without_listing_data_are_blocked_policy():
    assert status_from_metrics(
        {
            "pages_fetched": 4,
            "failed_requests": 4,
            "errors": ["CHALLENGE_DETECTED"],
            "coverage_gaps": ["BLOCKED_POLICY"],
        },
        [],
    ) == "BLOCKED_POLICY"


def test_zero_data_runtime_error_is_failed():
    assert status_from_metrics({"errors": ["PARSER_ERROR"]}, []) == "FAILED"


def test_failed_http_status_or_missing_status_blocks_complete_coverage_and_success():
    for status_code in (500, None):
        metrics = summarize_artifacts(
            [
                _artifact(
                    status_code=status_code,
                    coverage_state="COVERAGE_COMPLETE",
                )
            ],
            expected_entrypoints=(ENTRYPOINT,),
        )

        assert metrics["failed_requests"] == 1
        assert metrics["coverage_complete"] is False
        assert status_from_metrics(metrics, []) == "PARTIAL"


def test_failed_request_metric_blocks_success_even_without_an_error_label():
    assert status_from_metrics(
        {
            "coverage_complete": True,
            "failed_requests": 1,
            "pages_fetched": 1,
        },
        [],
    ) == "PARTIAL"
