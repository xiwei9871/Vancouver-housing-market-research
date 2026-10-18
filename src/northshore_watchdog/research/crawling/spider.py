"""Pure crawl helpers and the optional Scrapy spider boundary."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import inspect
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..public_feeds import parse_public_html, parse_zolo_detail as parse_zolo_detail_page
from .policies import (
    canonical_url,
    challenge_detected,
    page_fingerprint,
    url_allowed,
)


try:  # Scrapy and scrapy-playwright are optional project dependencies.
    import scrapy as _scrapy
except ImportError:  # pragma: no cover - exercised by environments without extras
    _scrapy = None

try:
    import scrapy_playwright as _scrapy_playwright
except ImportError:  # pragma: no cover - exercised by environments without extras
    _scrapy_playwright = None


_CRAWLER_EXTRA_ERROR = "Scrapy crawler extra is not installed; install the crawler extra"
_DEFAULT_MINIMUM_BEDROOMS = 3
_DEFAULT_ALLOWED_PROPERTY_TYPES = {
    "DETACHED",
    "DETACHED_HOUSE",
    "HOUSE",
    "SINGLE_FAMILY",
    "SINGLE_FAMILY_RESIDENCE",
}
_CLEARLY_EXCLUDED_PROPERTY_TYPES = {
    "APARTMENT",
    "COACH_HOUSE",
    "CONDO",
    "CONDOMINIUM",
    "DUPLEX",
    "LEASEHOLD",
    "STRATA",
    "TOWNHOUSE",
    "TRIPLEX",
}
_STATE_LIST_FIELDS = (
    "seen_page_urls",
    "page_fingerprints",
    "detail_request_keys",
)


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return [_json_safe(item) for item in sorted(value, key=repr)]
    return str(value)


def _listing_card_count(body: bytes | str) -> int:
    soup = BeautifulSoup(body, "html.parser")
    return len(soup.select("[data-listing-card], .listing-card, .listingCard, .card-listing"))


def _normalise_state(value: Any) -> dict[str, Any]:
    copied = deepcopy(value) if isinstance(value, Mapping) else {}
    state = _json_safe(copied)
    if not isinstance(state, dict):
        state = {}

    for field in _STATE_LIST_FIELDS:
        field_value = state.get(field)
        if field_value is None:
            state[field] = []
        elif not isinstance(field_value, list):
            state[field] = [field_value]

    route_states = state.get("route_completion_states")
    state["route_completion_states"] = route_states if isinstance(route_states, dict) else {}
    return state


def _next_link(soup: BeautifulSoup) -> Any | None:
    def is_next(value: Any) -> bool:
        values = value if isinstance(value, (list, tuple)) else str(value or "").split()
        return any(str(item).lower() == "next" for item in values)

    link = soup.find(["a", "link"], href=True, rel=is_next)
    if link is not None:
        return link

    for selector in (
        "a.pagination-next[href]",
        ".pagination-next[href]",
        "[data-testid='pagination-next'][href]",
        "[data-test='pagination-next'][href]",
        "a[aria-label='Next'][href]",
        "a[aria-label='next'][href]",
        "a.next[href]",
    ):
        link = soup.select_one(selector)
        if link is not None:
            return link
    return None


def next_zolo_url(
    html: bytes | str,
    current_url: str,
    allowed_domains: Iterable[str],
) -> str | None:
    """Return a canonical, same-domain Zolo next-page URL when one is present."""

    try:
        soup = BeautifulSoup(html, "html.parser")
    except (TypeError, ValueError):
        return None

    link = _next_link(soup)
    href = link.get("href") if link is not None else None
    if not isinstance(href, str) or not href.strip() or href.lstrip().startswith("#"):
        return None

    try:
        candidate = canonical_url(urljoin(current_url, href.strip()))
        return candidate if url_allowed(candidate, allowed_domains) else None
    except (TypeError, ValueError):
        return None


def realtor_page_advanced(old_ids: Iterable[Any] | None, new_ids: Iterable[Any] | None) -> bool:
    """Return whether a non-empty Realtor identity set changed."""

    old_set = set(old_ids or ())
    new_set = set(new_ids or ())
    return bool(new_set) and new_set != old_set


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).replace(",", "").replace("$", "").strip())
    except (TypeError, ValueError):
        return None


def _property_type(value: Any) -> str | None:
    if value is None:
        return None
    text = "_".join(str(value).strip().upper().split())
    return text or None


def _policy_types(policy: Mapping[str, Any]) -> tuple[set[str] | None, set[str]]:
    allowed_value = next(
        (
            policy.get(key)
            for key in (
                "allowed_property_types",
                "target_property_types",
                "current_target_property_types",
            )
            if policy.get(key)
        ),
        None,
    )
    allowed = None
    if allowed_value:
        values = [allowed_value] if isinstance(allowed_value, str) else allowed_value
        allowed = {_property_type(value) for value in values} - {None}

    excluded_value = next(
        (
            policy.get(key)
            for key in ("excluded_property_types", "excluded_current_property_types")
            if policy.get(key)
        ),
        None,
    )
    excluded = set()
    if excluded_value:
        values = [excluded_value] if isinstance(excluded_value, str) else excluded_value
        excluded = {_property_type(value) for value in values} - {None}
    return allowed, excluded


def should_fetch_detail(row: Mapping[str, Any], policy: Mapping[str, Any]) -> bool:
    """Keep unknown/plausible cards while rejecting clear cheap-field mismatches."""

    minimum_bedrooms = _number(
        policy.get("minimum_bedrooms", policy.get("min_bedrooms", _DEFAULT_MINIMUM_BEDROOMS))
    )
    if minimum_bedrooms is None:
        minimum_bedrooms = _DEFAULT_MINIMUM_BEDROOMS

    bedrooms = _number(row.get("bedrooms"))
    if bedrooms is not None and bedrooms < minimum_bedrooms:
        return False

    property_type = _property_type(row.get("property_type"))
    unknown_types = {"UNKNOWN", "N_A", "NA", "NONE", "NULL"}
    if property_type and property_type not in unknown_types:
        _, excluded = _policy_types(policy)
        if property_type in excluded:
            return False
        if property_type in _CLEARLY_EXCLUDED_PROPERTY_TYPES:
            return False

    asking = _number(row.get("asking"))
    max_asking = _number(policy.get("max_asking"))
    if asking is not None and max_asking is not None and asking > max_asking:
        # A plausible bed count is enough to retain an over-budget card for
        # detail verification; an unknown count remains an explicit budget hit.
        return bedrooms is not None and bedrooms >= minimum_bedrooms
    return True


_SpiderBase = (
    _scrapy.Spider
    if _scrapy is not None and _scrapy_playwright is not None
    else object
)


class FullMarketSpider(_SpiderBase):
    name = "northshore_full_market"
    custom_settings = {
        "ROBOTSTXT_OBEY": True,
        "CONCURRENT_REQUESTS_PER_DOMAIN": 1,
        "DOWNLOAD_DELAY": 1.0,
        "RETRY_TIMES": 2,
        "TWISTED_REACTOR": "twisted.internet.asyncioreactor.AsyncioSelectorReactor",
        "DOWNLOAD_HANDLERS": {
            "http": "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler",
            "https": "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler",
        },
        "PLAYWRIGHT_BROWSER_TYPE": "chromium",
    }

    def __init__(
        self,
        source: Mapping[str, Any] | None = None,
        context: Mapping[str, Any] | None = None,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        missing = object()
        explicit_artifact_writer = kwargs.pop("artifact_writer", missing)
        explicit_writer = kwargs.pop("writer", missing)
        if _scrapy is None or _scrapy_playwright is None:
            raise RuntimeError(_CRAWLER_EXTRA_ERROR)
        super().__init__(*args, **kwargs)
        self.source = source
        self.context = context
        source_data = source if isinstance(source, Mapping) else {}
        context_data = context if isinstance(context, Mapping) else {}
        self.source_name = str(source_data.get("name") or "")
        self.source_registry_id = str(
            source_data.get("source_registry_id") or f"source:{self.source_name}"
        )
        domains = source_data.get("allowed_domains") or []
        if isinstance(domains, (str, bytes)):
            domains = [domains]
        self.allowed_domains = [str(domain) for domain in domains if domain]
        self.run_id = str(context_data.get("run_id") or "")
        self.observed_at = context_data.get("observed_at")
        self.state = _normalise_state(context_data.get("state"))
        if explicit_artifact_writer is not missing:
            self.artifact_writer = explicit_artifact_writer
        elif explicit_writer is not missing:
            self.artifact_writer = explicit_writer
        else:
            self.artifact_writer = context_data.get("artifact_writer")
        crawl_config = context_data.get("crawl")
        self.crawl_config = dict(crawl_config) if isinstance(crawl_config, Mapping) else {}
        self.search_rows: dict[str, dict[str, Any]] = {}

    def _observed_datetime(self) -> datetime:
        value = self.observed_at
        if isinstance(value, datetime):
            if value.tzinfo is None or value.utcoffset() is None:
                return value.replace(tzinfo=timezone.utc)
            return value
        return datetime.now(timezone.utc)

    def _source_entrypoints(self) -> list[Mapping[str, Any]]:
        source = self.source if isinstance(self.source, Mapping) else {}
        values = source.get("entrypoints") or ()
        return [value for value in values if isinstance(value, Mapping)]

    def _is_realtor(self) -> bool:
        source = self.source if isinstance(self.source, Mapping) else {}
        return str(source.get("pagination_mode") or "").casefold() == "browser_control" or "realtor" in self.source_name.casefold()

    def _entrypoint_for(self, response: Any) -> str:
        meta = getattr(response, "meta", {}) or {}
        return str(meta.get("entrypoint_url") or canonical_url(str(getattr(response, "url", ""))))

    def _detail_policy(self) -> dict[str, Any]:
        source = self.source if isinstance(self.source, Mapping) else {}
        policy: dict[str, Any] = {}
        for container in (source.get("detail_policy"), self.context, self.crawl_config):
            if isinstance(container, Mapping):
                for key in ("max_asking", "minimum_bedrooms", "min_bedrooms", "excluded_property_types", "target_property_types"):
                    if key in container:
                        policy[key] = container[key]
        return policy

    def _max_search_pages(self) -> int:
        value = self.crawl_config.get("max_search_pages", 200)
        try:
            return max(1, int(value))
        except (TypeError, ValueError):
            return 200

    def _max_detail_requests(self) -> int:
        value = self.crawl_config.get("max_detail_requests", 2000)
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return 2000

    def _write_artifact(
        self,
        response: Any,
        *,
        request_kind: str,
        rows: Iterable[Mapping[str, Any]] = (),
        coverage_state: str = "IN_PROGRESS",
        error_type: str | None = None,
        parse_status: str = "PARSED",
        next_url: str | None = None,
        detail_url: str | None = None,
        body_override: bytes | str | None = None,
        final_url_override: str | None = None,
        page_number_override: int | None = None,
    ) -> None:
        writer = self.artifact_writer
        if writer is None or not hasattr(writer, "write"):
            return
        body = getattr(response, "body", b"") if body_override is None else body_override
        if isinstance(body, str):
            body = body.encode("utf-8")
        meta = getattr(response, "meta", {}) or {}
        row_values = list(rows)
        headers = getattr(response, "headers", {}) or {}
        status = getattr(response, "status", None)
        response_url = str(final_url_override or getattr(response, "url", ""))
        request = getattr(response, "request", None)
        requested_url = str(getattr(request, "url", None) or response_url)
        writer.write(
            request_kind=request_kind,
            requested_url=requested_url,
            final_url=response_url,
            entrypoint_url=str(meta.get("entrypoint_url") or self._entrypoint_for(response)),
            detail_url=detail_url,
            body=body,
            headers=headers,
            status_code=status,
            rows=row_values,
            page_number=(
                meta.get("page_number")
                if page_number_override is None
                else page_number_override
            ),
            parser="public_feeds.parse_public_html" if request_kind == "SEARCH" else "public_feeds.parse_zolo_detail",
            parse_status=parse_status,
            next_url=next_url,
            card_count=_listing_card_count(body) if request_kind == "SEARCH" else 0,
            extracted_row_count=len(row_values),
            coverage_state=coverage_state,
            error_type=error_type,
        )

    @staticmethod
    def _response_body(response: Any) -> bytes:
        body = getattr(response, "body", b"")
        return body.encode("utf-8") if isinstance(body, str) else bytes(body)

    def _request_meta(self, *, request_kind: str, page_number: int, entrypoint_url: str, detail_url: str | None = None) -> dict[str, Any]:
        meta = {
            "source_name": self.source_name,
            "source_registry_id": self.source_registry_id,
            "request_kind": request_kind,
            "page_number": page_number,
            "run_id": self.run_id,
            "playwright": True,
            "entrypoint_url": entrypoint_url,
        }
        if detail_url:
            meta["detail_url"] = detail_url
        meta["handle_httpstatus_list"] = [401, 403, 429]
        if request_kind == "SEARCH" and self._is_realtor():
            meta["playwright_include_page"] = True
        return meta

    def start_requests(self) -> Iterable[Any]:
        """Yield one bounded SEARCH request for each allowed entrypoint."""

        source = self.source if isinstance(self.source, Mapping) else {}
        for entrypoint in source.get("entrypoints") or ():
            if not isinstance(entrypoint, Mapping):
                continue
            url = entrypoint.get("url")
            if not isinstance(url, str) or not url_allowed(url, self.allowed_domains):
                continue

            meta = {
                "source_name": self.source_name,
                "source_registry_id": self.source_registry_id,
                "request_kind": "SEARCH",
                "page_number": 1,
                "run_id": self.run_id,
                "playwright": True,
                "entrypoint_url": url,
                "handle_httpstatus_list": [401, 403, 429],
            }
            if self._is_realtor():
                meta["playwright_include_page"] = True
            callback = self.parse_realtor_search if self._is_realtor() else self.parse_zolo_search
            yield _scrapy.Request(url, callback=callback, meta=meta)

    async def start(self) -> Any:
        """Support Scrapy 2.13+ while retaining the older start_requests API."""

        for request in self.start_requests():
            yield request

    def parse_zolo_search(self, response: Any) -> Iterable[Any]:
        """Parse one Zolo search page and schedule plausible detail URLs."""

        body = self._response_body(response)
        page_url = canonical_url(str(getattr(response, "url", "")))
        entrypoint_url = self._entrypoint_for(response)
        page_number = int((getattr(response, "meta", {}) or {}).get("page_number") or 1)
        fingerprints = self.state.setdefault("page_fingerprints", [])
        seen_urls = self.state.setdefault("seen_page_urls", [])
        digest = page_fingerprint(body)
        if challenge_detected(body):
            self._write_artifact(response, request_kind="SEARCH", coverage_state="BLOCKED_POLICY", error_type="CHALLENGE_DETECTED", parse_status="SKIPPED")
            self.state.setdefault("route_completion_states", {})[entrypoint_url] = "BLOCKED_POLICY"
            return []
        if page_url in seen_urls or digest in fingerprints:
            self._write_artifact(response, request_kind="SEARCH", coverage_state="REPEATED_PAGE", error_type="REPEATED_PAGE", parse_status="SKIPPED")
            self.state.setdefault("route_completion_states", {})[entrypoint_url] = "REPEATED_PAGE"
            return []
        seen_urls.append(page_url)
        fingerprints.append(digest)

        rows: list[dict[str, Any]] = []
        parse_error: str | None = None
        try:
            rows = list(parse_public_html(body, page_url, self.source_name, self._observed_datetime()))
        except ValueError as exc:
            rows = list(getattr(exc, "rows", []) or [])
            parse_error = type(exc).__name__
        for row in rows:
            key = str(row.get("source_url") or row.get("mls_number") or "").strip()
            if key:
                self.search_rows[canonical_url(key) if key.startswith(("http://", "https://")) else key] = dict(row)

        yielded: list[Any] = []
        detail_keys = self.state.setdefault("detail_request_keys", [])
        detail_cap_reached = len(detail_keys) >= self._max_detail_requests()
        for row in rows:
            detail_url = row.get("source_url")
            if not isinstance(detail_url, str) or not url_allowed(detail_url, set(self.allowed_domains)):
                continue
            if not should_fetch_detail(row, self._detail_policy()):
                continue
            detail_key = canonical_url(detail_url)
            if detail_key in detail_keys:
                continue
            if len(detail_keys) >= self._max_detail_requests():
                detail_cap_reached = True
                continue
            detail_keys.append(detail_key)
            if len(detail_keys) >= self._max_detail_requests():
                detail_cap_reached = True
            yielded.append(
                _scrapy.Request(
                    detail_key,
                    callback=self.parse_zolo_detail,
                    meta=self._request_meta(request_kind="DETAIL", page_number=page_number, entrypoint_url=entrypoint_url, detail_url=detail_key),
                )
            )

        next_url = next_zolo_url(body, page_url, set(self.allowed_domains))
        coverage_state = "IN_PROGRESS"
        if parse_error:
            coverage_state = "PARTIAL_PARSE"
        elif page_number >= self._max_search_pages():
            coverage_state = "PARTIAL_CAP_REACHED"
        elif next_url is None:
            coverage_state = "NATURAL_COMPLETE"
        elif canonical_url(next_url) in seen_urls:
            coverage_state = "REPEATED_PAGE"
            next_url = None
        else:
            yielded.append(
                _scrapy.Request(
                    next_url,
                    callback=self.parse_zolo_search,
                    meta=self._request_meta(request_kind="SEARCH", page_number=page_number + 1, entrypoint_url=entrypoint_url),
                )
            )
        if detail_cap_reached:
            coverage_state = "PARTIAL_CAP_REACHED"
        self.state.setdefault("route_completion_states", {})[entrypoint_url] = coverage_state
        self._write_artifact(response, request_kind="SEARCH", rows=rows, coverage_state=coverage_state, error_type=parse_error, parse_status="PARTIAL" if parse_error else "PARSED", next_url=next_url)
        return yielded

    def parse_zolo_detail(self, response: Any) -> Iterable[Any]:
        """Parse a Zolo detail response while preserving its search-card row."""

        body = self._response_body(response)
        requested_url = str(getattr(response, "url", ""))
        detail_url = (getattr(response, "meta", {}) or {}).get("detail_url") or requested_url
        search_row = self.search_rows.get(str(detail_url))
        if search_row is None:
            search_row = self.search_rows.get(canonical_url(str(detail_url)))
        try:
            row = parse_zolo_detail_page(body, requested_url, self._observed_datetime())
        except Exception:
            self._write_artifact(response, request_kind="DETAIL", rows=[search_row] if search_row is not None else (), coverage_state="PARTIAL", error_type="DETAIL_PARSE_ERROR", parse_status="ERROR", detail_url=detail_url)
            return []
        self._write_artifact(response, request_kind="DETAIL", rows=[row], coverage_state="COMPLETE", parse_status="PARSED", detail_url=detail_url)
        return []

    def _realtor_selector(self) -> str:
        source = self.source if isinstance(self.source, Mapping) else {}
        return str(source.get("pagination_selector") or "a[aria-label='Next']")

    @staticmethod
    def _row_identities(rows: Iterable[Mapping[str, Any]]) -> set[str]:
        return {
            str(row.get("mls_number") or row.get("source_url")).strip()
            for row in rows
            if row.get("mls_number") or row.get("source_url")
        }

    async def _page_call(self, page: Any, method_name: str, *args: Any) -> Any:
        method = getattr(page, method_name)
        value = method(*args)
        return await value if inspect.isawaitable(value) else value

    def _realtor_detail_requests(
        self,
        rows: Iterable[Mapping[str, Any]],
        *,
        page_number: int,
        entrypoint_url: str,
    ) -> tuple[list[Any], bool]:
        yielded: list[Any] = []
        detail_keys = self.state.setdefault("detail_request_keys", [])
        cap_reached = len(detail_keys) >= self._max_detail_requests()
        for row in rows:
            detail_url = row.get("source_url")
            if not isinstance(detail_url, str) or not url_allowed(
                detail_url, set(self.allowed_domains)
            ):
                continue
            if not should_fetch_detail(row, self._detail_policy()):
                continue
            detail_key = canonical_url(detail_url)
            if detail_key in detail_keys:
                continue
            if len(detail_keys) >= self._max_detail_requests():
                cap_reached = True
                continue
            detail_keys.append(detail_key)
            if len(detail_keys) >= self._max_detail_requests():
                cap_reached = True
            yielded.append(
                _scrapy.Request(
                    detail_key,
                    callback=self.parse_realtor_detail,
                    meta=self._request_meta(
                        request_kind="DETAIL",
                        page_number=page_number,
                        entrypoint_url=entrypoint_url,
                        detail_url=detail_key,
                    ),
                )
            )
        return yielded, cap_reached

    def parse_realtor_search(self, response: Any) -> Any:
        """Return the async browser-pagination coroutine for Scrapy."""

        return self._parse_realtor_search(response)

    async def _parse_realtor_search(self, response: Any) -> list[Any]:
        meta = getattr(response, "meta", {}) or {}
        page = meta.get("playwright_page")
        if page is None:
            raise ValueError("Realtor response has no playwright page")
        entrypoint_url = str(meta.get("entrypoint_url") or getattr(response, "url", ""))
        page_number = int(meta.get("page_number") or 1)
        previous_ids = {
            str(value)
            for value in meta.get("previous_ids", [])
            if value not in (None, "")
        }
        yielded: list[Any] = []
        current_body: bytes | None = None
        current_url = str(getattr(response, "url", ""))
        try:
            while page_number <= self._max_search_pages():
                html = await self._page_call(page, "content")
                current_body = html.encode("utf-8") if isinstance(html, str) else bytes(html)
                current_url = canonical_url(str(getattr(page, "url", "") or current_url))
                digest = page_fingerprint(current_body)
                if challenge_detected(current_body):
                    self._write_artifact(
                        response,
                        request_kind="SEARCH",
                        body_override=current_body,
                        final_url_override=current_url,
                        page_number_override=page_number,
                        coverage_state="BLOCKED_POLICY",
                        error_type="CHALLENGE_DETECTED",
                        parse_status="SKIPPED",
                    )
                    self.state.setdefault("route_completion_states", {})[entrypoint_url] = "BLOCKED_POLICY"
                    break
                current_rows = list(
                    parse_public_html(
                        current_body,
                        current_url,
                        self.source_name,
                        self._observed_datetime(),
                    )
                )
                current_ids = self._row_identities(current_rows)
                if page_number > 1 and not realtor_page_advanced(previous_ids, current_ids):
                    self._write_artifact(
                        response,
                        request_kind="SEARCH",
                        rows=current_rows,
                        body_override=current_body,
                        final_url_override=current_url,
                        page_number_override=page_number,
                        coverage_state="PAGINATION_NOT_ADVANCED",
                        error_type="PAGINATION_NOT_ADVANCED",
                    )
                    self.state.setdefault("route_completion_states", {})[entrypoint_url] = "PAGINATION_NOT_ADVANCED"
                    break

                seen_urls = self.state.setdefault("seen_page_urls", [])
                seen_fingerprints = self.state.setdefault("page_fingerprints", [])
                if digest in seen_fingerprints:
                    self._write_artifact(
                        response,
                        request_kind="SEARCH",
                        rows=current_rows,
                        body_override=current_body,
                        final_url_override=current_url,
                        page_number_override=page_number,
                        coverage_state="REPEATED_PAGE",
                        error_type="REPEATED_PAGE",
                    )
                    self.state.setdefault("route_completion_states", {})[entrypoint_url] = "REPEATED_PAGE"
                    break

                if current_url not in seen_urls:
                    seen_urls.append(current_url)
                seen_fingerprints.append(digest)
                for row in current_rows:
                    key = str(row.get("source_url") or row.get("mls_number") or "").strip()
                    if key:
                        self.search_rows[
                            canonical_url(key) if key.startswith(("http://", "https://")) else key
                        ] = dict(row)
                detail_requests, detail_cap_reached = self._realtor_detail_requests(
                    current_rows,
                    page_number=page_number,
                    entrypoint_url=entrypoint_url,
                )
                yielded.extend(detail_requests)

                if page_number >= self._max_search_pages() or detail_cap_reached:
                    coverage_state = "PARTIAL_CAP_REACHED"
                    self.state.setdefault("route_completion_states", {})[entrypoint_url] = coverage_state
                    self._write_artifact(
                        response,
                        request_kind="SEARCH",
                        rows=current_rows,
                        body_override=current_body,
                        final_url_override=current_url,
                        page_number_override=page_number,
                        coverage_state=coverage_state,
                        next_url=None,
                    )
                    break

                try:
                    control = await self._page_call(page, "locator", self._realtor_selector())
                    visible = await self._page_call(control, "is_visible")
                except Exception:
                    visible = False
                if not visible:
                    coverage_state = "PAGINATION_NOT_ADVANCED"
                    self.state.setdefault("route_completion_states", {})[entrypoint_url] = coverage_state
                    self._write_artifact(
                        response,
                        request_kind="SEARCH",
                        rows=current_rows,
                        body_override=current_body,
                        final_url_override=current_url,
                        page_number_override=page_number,
                        coverage_state=coverage_state,
                        error_type=coverage_state,
                    )
                    break

                try:
                    href = await self._page_call(control, "get_attribute", "href")
                except Exception:
                    href = None
                if href is None or not str(href).strip() or str(href).strip().startswith("#"):
                    coverage_state = "PAGINATION_NOT_ADVANCED"
                    self.state.setdefault("route_completion_states", {})[entrypoint_url] = coverage_state
                    self._write_artifact(
                        response,
                        request_kind="SEARCH",
                        rows=current_rows,
                        body_override=current_body,
                        final_url_override=current_url,
                        page_number_override=page_number,
                        coverage_state=coverage_state,
                        error_type=coverage_state,
                    )
                    break
                before_digest = digest
                try:
                    await self._page_call(control, "click")
                except Exception:
                    coverage_state = "PAGINATION_NOT_ADVANCED"
                    self.state.setdefault("route_completion_states", {})[entrypoint_url] = coverage_state
                    self._write_artifact(
                        response,
                        request_kind="SEARCH",
                        rows=current_rows,
                        body_override=current_body,
                        final_url_override=current_url,
                        page_number_override=page_number,
                        coverage_state=coverage_state,
                        error_type=coverage_state,
                    )
                    break
                wait_seconds = self.crawl_config.get("pagination_wait_seconds", 1.0)
                try:
                    wait_ms = max(0, int(float(wait_seconds) * 1000))
                except (TypeError, ValueError):
                    wait_ms = 1000
                if hasattr(page, "wait_for_timeout"):
                    await self._page_call(page, "wait_for_timeout", wait_ms)
                new_html = await self._page_call(page, "content")
                new_body = new_html.encode("utf-8") if isinstance(new_html, str) else bytes(new_html)
                new_url = canonical_url(str(getattr(page, "url", "") or current_url))
                new_digest = page_fingerprint(new_body)
                if challenge_detected(new_body):
                    coverage_state = "BLOCKED_POLICY"
                    self.state.setdefault("route_completion_states", {})[entrypoint_url] = coverage_state
                    self._write_artifact(
                        response,
                        request_kind="SEARCH",
                        body_override=new_body,
                        final_url_override=new_url,
                        page_number_override=page_number + 1,
                        coverage_state=coverage_state,
                        error_type="CHALLENGE_DETECTED",
                        parse_status="SKIPPED",
                    )
                    break
                new_rows = list(
                    parse_public_html(
                        new_body,
                        new_url,
                        self.source_name,
                        self._observed_datetime(),
                    )
                )
                new_ids = self._row_identities(new_rows)
                if (
                    new_digest == before_digest
                    or not realtor_page_advanced(current_ids, new_ids)
                    or new_digest in seen_fingerprints
                ):
                    coverage_state = "REPEATED_PAGE" if new_digest in seen_fingerprints else "PAGINATION_NOT_ADVANCED"
                    error_type = coverage_state
                    self.state.setdefault("route_completion_states", {})[entrypoint_url] = coverage_state
                    self._write_artifact(
                        response,
                        request_kind="SEARCH",
                        rows=new_rows,
                        body_override=new_body,
                        final_url_override=new_url,
                        page_number_override=page_number + 1,
                        coverage_state=coverage_state,
                        error_type=error_type,
                    )
                    break
                self._write_artifact(
                    response,
                    request_kind="SEARCH",
                    rows=current_rows,
                    body_override=current_body,
                    final_url_override=current_url,
                    page_number_override=page_number,
                    coverage_state="IN_PROGRESS",
                    next_url=None,
                )
                previous_ids = current_ids
                page_number += 1
                current_body = new_body
            else:
                self.state.setdefault("route_completion_states", {})[entrypoint_url] = "PARTIAL_CAP_REACHED"
            return yielded
        except Exception:
            if current_body is not None:
                try:
                    self._write_artifact(
                        response,
                        request_kind="SEARCH",
                        body_override=current_body,
                        final_url_override=current_url,
                        page_number_override=page_number,
                        coverage_state="PARTIAL",
                        error_type="SEARCH_PARSE_ERROR",
                        parse_status="ERROR",
                    )
                except Exception:
                    pass
            raise
        finally:
            await self._page_call(page, "close")

    def parse_realtor_detail(self, response: Any) -> Iterable[Any]:
        """Parse a Realtor detail page without removing its search record."""

        body = self._response_body(response)
        meta = getattr(response, "meta", {}) or {}
        detail_url = meta.get("detail_url") or getattr(response, "url", "")
        try:
            rows = list(
                parse_public_html(
                    body,
                    str(getattr(response, "url", "")),
                    self.source_name,
                    self._observed_datetime(),
                )
            )
        except Exception:
            self._write_artifact(
                response,
                request_kind="DETAIL",
                coverage_state="PARTIAL",
                error_type="DETAIL_PARSE_ERROR",
                parse_status="ERROR",
                detail_url=detail_url,
            )
            return []
        self._write_artifact(
            response,
            request_kind="DETAIL",
            rows=rows[:1],
            coverage_state="COMPLETE",
            parse_status="PARSED",
            detail_url=detail_url,
        )
        return []

    def state_snapshot(self) -> dict[str, Any]:
        """Return an independent, JSON-serializable snapshot of crawler state."""

        return _normalise_state(getattr(self, "state", {}))

    def restore_state(self, snapshot: Mapping[str, Any] | None) -> None:
        """Restore crawler state from a previously serialized snapshot."""

        self.state = _normalise_state(snapshot)
