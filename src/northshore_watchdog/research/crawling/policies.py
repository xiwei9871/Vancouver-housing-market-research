from __future__ import annotations

from collections import Counter
from hashlib import sha256
from typing import Any, Callable, Iterable, Mapping
from urllib.parse import urlparse, urlunparse

from pydantic import BaseModel

from ..public_feeds import _has_challenge as _visible_text_challenge
from .models import CrawlArtifact


_NATURAL_COMPLETION_STATES = {
    "COMPLETE",
    "COVERAGE_COMPLETE",
    "NATURAL_COMPLETE",
    "NATURAL_COMPLETION",
}
_IN_PROGRESS_STATES = {"", "IN_PROGRESS", "RUNNING"}
_POLICY_ERROR_MARKERS = (
    "BLOCKED",
    "CHALLENGE",
    "CAPTCHA",
    "ROBOTS",
    "DISALLOWED",
    "HTTP_401",
    "HTTP_403",
    "HTTP_429",
    "POLICY",
)


def _normalise_domain(value: Any) -> str:
    domain = str(value).strip().lower().rstrip(".")
    return domain.removeprefix("*.").removeprefix(".")


def url_allowed(url: str, allowed_domains: Iterable[str]) -> bool:
    """Return whether *url* is an HTTP(S) URL on an exact allowed host.

    Host matching is case-insensitive and ignores a DNS trailing dot.  A
    configured domain also permits its subdomains, while a mere string suffix
    match such as ``notexample.com`` is rejected.
    """

    try:
        parsed = urlparse(url)
        hostname = parsed.hostname
    except (TypeError, ValueError):
        return False
    if parsed.scheme.lower() not in {"http", "https"} or not hostname:
        return False
    host = hostname.lower().rstrip(".")
    domains = {_normalise_domain(domain) for domain in allowed_domains}
    return any(domain and (host == domain or host.endswith(f".{domain}")) for domain in domains)


def canonical_url(url: str) -> str:
    """Canonicalize URL casing, fragments, and path trailing slashes.

    Query text is intentionally preserved byte-for-byte because query
    parameters may represent pagination.  A bare host keeps its root slash;
    non-root paths lose trailing slashes.
    """

    parsed = urlparse(url)
    path = parsed.path or "/"
    if path != "/":
        path = path.rstrip("/") or "/"
    return urlunparse(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            path,
            "",
            parsed.query,
            "",
        )
    )


def page_fingerprint(body: bytes) -> str:
    """Return the stable SHA-256 hex digest for an exact response body."""

    return sha256(body).hexdigest()


def challenge_detected(body: bytes) -> bool:
    """Reuse the existing public-feed visible-text challenge markers."""

    return _visible_text_challenge(body)


def _clean_strings(values: Iterable[Any] | Any) -> list[str]:
    if values is None:
        return []
    if isinstance(values, (str, bytes)):
        values = [values]
    try:
        candidates = values if isinstance(values, Iterable) else [values]
    except TypeError:
        candidates = [values]
    result: list[str] = []
    seen: set[str] = set()
    for value in candidates:
        if value is None:
            continue
        text = value.decode("utf-8", errors="replace") if isinstance(value, bytes) else str(value)
        text = text.strip()
        if text and text not in seen:
            result.append(text)
            seen.add(text)
    return result


def _as_artifact(value: CrawlArtifact | Mapping[str, Any] | BaseModel) -> CrawlArtifact:
    if isinstance(value, CrawlArtifact):
        return value
    if isinstance(value, BaseModel):
        return CrawlArtifact.model_validate(value.model_dump(mode="python"))
    return CrawlArtifact.model_validate(value)


def _int_value(value: Any) -> int:
    if isinstance(value, bool) or value is None:
        return 0
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _successful_status(status_code: Any) -> bool:
    return not isinstance(status_code, bool) and isinstance(status_code, int) and 200 <= status_code < 300


def _state(value: Any) -> str:
    return str(value or "").strip()


def _natural_completion(artifact: CrawlArtifact) -> bool:
    return (
        artifact.request_kind == "SEARCH"
        and _state(artifact.coverage_state).upper() in _NATURAL_COMPLETION_STATES
    )


def _coverage_gap(value: Any) -> str | None:
    state = _state(value)
    if state.upper() in _IN_PROGRESS_STATES or state.upper() in _NATURAL_COMPLETION_STATES:
        return None
    return state or None


def _canonical_entrypoint(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return canonical_url(str(value))


def summarize_artifacts(
    artifacts: Iterable[CrawlArtifact | Mapping[str, Any] | BaseModel],
    expected_entrypoints: Iterable[str] = (),
) -> dict[str, Any]:
    """Aggregate crawl artifacts into stable, runner-facing metrics.

    ``expected_entrypoints`` is the authoritative route set when supplied.
    With the default empty value, non-empty artifact entrypoints are used as a
    conservative fallback.  No entrypoint evidence means coverage cannot be
    declared complete.
    """

    materialized = [_as_artifact(value) for value in artifacts]
    search_artifacts = [item for item in materialized if item.request_kind == "SEARCH"]
    detail_artifacts = [item for item in materialized if item.request_kind == "DETAIL"]

    expected = []
    expected_seen: set[str] = set()
    for value in expected_entrypoints:
        entrypoint = _canonical_entrypoint(value)
        if entrypoint and entrypoint not in expected_seen:
            expected.append(entrypoint)
            expected_seen.add(entrypoint)
    if not expected:
        for item in materialized:
            entrypoint = _canonical_entrypoint(item.entrypoint_url)
            if entrypoint and entrypoint not in expected_seen:
                expected.append(entrypoint)
                expected_seen.add(entrypoint)

    entrypoints_complete = sum(
        any(
            _canonical_entrypoint(item.entrypoint_url) == entrypoint
            and _natural_completion(item)
            for item in search_artifacts
        )
        for entrypoint in expected
    )

    errors = _clean_strings(item.error_type for item in materialized)
    coverage_gaps = _clean_strings(
        gap
        for gap in (_coverage_gap(item.coverage_state) for item in materialized)
        if gap is not None
    )
    search_hashes = [item.sha256.strip() for item in search_artifacts if item.sha256 and item.sha256.strip()]
    hash_counts = Counter(search_hashes)
    duplicate_pages = sum(count - 1 for count in hash_counts.values() if count > 1)
    skipped_details = sum(
        1
        for item in detail_artifacts
        if item.skip_reason
        or _state(item.coverage_state).upper().startswith("SKIPPED")
    )
    detail_urls = {
        canonical_url(str(row.get("source_url")))
        for item in search_artifacts
        for row in item.rows
        if isinstance(row, Mapping)
        and row.get("source_url")
        and str(row.get("source_url")).startswith(("http://", "https://"))
    }
    if skipped_details:
        coverage_gaps.append("DETAILS_SKIPPED")
    elif detail_urls and not detail_artifacts:
        coverage_gaps.append("DETAILS_NOT_FETCHED")
    coverage_gaps = list(dict.fromkeys(coverage_gaps))
    failed_requests = sum(
        1
        for item in materialized
        if item.error_type
        or item.status_code is None
        or not _successful_status(item.status_code)
    )
    cap_reached = any("CAP" in value.upper() for value in [*coverage_gaps, *errors])
    coverage_complete = bool(expected) and (
        entrypoints_complete == len(expected)
        and not coverage_gaps
        and not errors
        and not cap_reached
        and failed_requests == 0
        and duplicate_pages == 0
        and skipped_details == 0
        and not (detail_urls and not detail_artifacts)
    )
    return {
        "entrypoints": len(expected),
        "entrypoints_complete": entrypoints_complete,
        "pages_fetched": len(search_artifacts),
        "details_fetched": sum(1 for item in detail_artifacts if _successful_status(item.status_code)),
        "cards_found": sum(_int_value(item.card_count) for item in search_artifacts),
        "rows_extracted": sum(_int_value(item.extracted_row_count) for item in materialized),
        "duplicate_pages": duplicate_pages,
        "skipped_details": skipped_details,
        "failed_requests": failed_requests,
        "errors": errors,
        "warnings": list(coverage_gaps),
        "coverage_gaps": coverage_gaps,
        "cap_reached": cap_reached,
        "coverage_complete": coverage_complete,
    }


def _normalise_external_errors(
    normalize_errors: Iterable[Any] | Callable[..., Any] | None,
    existing_errors: list[str],
) -> list[str]:
    if normalize_errors is None:
        return []
    if callable(normalize_errors):
        try:
            value = normalize_errors(existing_errors)
        except TypeError:
            value = normalize_errors()
    else:
        value = normalize_errors
    return _clean_strings(value)


def status_from_metrics(
    metrics: Mapping[str, Any] | BaseModel,
    normalize_errors: Iterable[Any] | Callable[..., Any] | None,
) -> str:
    """Map crawl metrics to the existing source-run status vocabulary."""

    if isinstance(metrics, BaseModel):
        metric_values = metrics.model_dump(mode="python")
    else:
        metric_values = dict(metrics)
    errors = _clean_strings(metric_values.get("errors", []))
    errors.extend(
        value for value in _normalise_external_errors(normalize_errors, errors) if value not in errors
    )
    gaps = _clean_strings(metric_values.get("coverage_gaps", []))
    cap_reached = bool(metric_values.get("cap_reached")) or any(
        "CAP" in value.upper() for value in [*errors, *gaps]
    )
    duplicate_pages = _int_value(metric_values.get("duplicate_pages"))
    failed_requests = _int_value(metric_values.get("failed_requests"))
    has_problem = bool(errors or gaps or cap_reached or duplicate_pages or failed_requests)
    has_data = any(
        _int_value(metric_values.get(key)) > 0
        for key in ("pages_fetched", "details_fetched", "cards_found", "rows_extracted")
    )
    has_record_data = any(
        _int_value(metric_values.get(key)) > 0
        for key in ("details_fetched", "cards_found", "rows_extracted")
    )
    coverage_complete = metric_values.get("coverage_complete") is True

    # A challenge page is still a fetched page, but it is not usable source
    # data. Keep this distinct from a partial crawl that returned real cards
    # before a later request was blocked.
    policy_error = any(
        marker in value.upper()
        for value in [*errors, *gaps]
        for marker in _POLICY_ERROR_MARKERS
    )
    if policy_error and not has_record_data:
        return "BLOCKED_POLICY"

    if coverage_complete and not has_problem:
        return "SUCCESS"
    if not has_data:
        if policy_error or cap_reached:
            return "BLOCKED_POLICY"
        if errors:
            return "FAILED"
        if gaps:
            return "PARTIAL"
        return "FAILED"
    return "PARTIAL"
