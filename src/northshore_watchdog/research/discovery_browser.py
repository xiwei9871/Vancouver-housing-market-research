"""Optional standard Playwright executor for filtered discovery and details.

The source first renders a user-configured filtered search page.  After the
native filters pass validation, it may open each bounded listing detail page
to enrich lot and current-use fields before shortlist ranking.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from pathlib import Path
import re
import time
from typing import Any
from urllib.parse import urlsplit

from .discovery import (
    DiscoveryCapture,
    DiscoveryCard,
    DiscoveryFilterPlan,
    DiscoveryStatus,
    validate_capture,
)
from .discovery_sources import parse_filtered_html
from .boundaries import load_boundary_registry, region_by_id
from .normalization import normalize_address
from .public_feeds import has_visible_challenge, parse_realtor_ca_detail
from .realtor_map import build_realtor_ca_map_url


class _DetailRateLimitError(RuntimeError):
    """Raised internally so a 429 does not cause more avoidable requests."""


_SUPPORTED_ACTIONS = frozenset(
    {
        "goto",
        "fill",
        "select_option",
        "check",
        "click",
        "click_filter_submit",
        "wait_for_selector",
        "apply_custom_boundary",
    }
)


class PlaywrightNativeDiscoverySource:
    """Execute a bounded, native-filtered search using standard Playwright.

    ``page_factory`` is intentionally injectable so the contract can be
    tested without installing Chromium.  A production instance imports and
    starts Playwright only when ``execute`` is called.
    """

    def __init__(
        self,
        *,
        name: str,
        config: Mapping[str, Any],
        page_factory: Callable[[], Any] | None = None,
        detail_page_factory: Callable[[], Any] | None = None,
    ) -> None:
        self.source_name = name
        self.config = dict(config)
        self._page_factory = page_factory
        self._detail_page_factory = detail_page_factory

    @classmethod
    def from_config(
        cls,
        source_config: Mapping[str, Any],
    ) -> "PlaywrightNativeDiscoverySource":
        name = source_config.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("PLAYWRIGHT_NATIVE source requires a non-empty name")
        config = dict(source_config)
        nested_browser = source_config.get("browser")
        if isinstance(nested_browser, Mapping):
            config.update(nested_browser)
        return cls(name=name, config=config)

    def execute(
        self,
        plan: DiscoveryFilterPlan,
        *,
        observed_at: datetime,
    ) -> DiscoveryCapture:
        self._owns_page = True
        self._owns_context = True
        self._owns_browser = True
        actions = self._actions()
        self._validate_actions(actions)
        selectors = self._selectors()
        entrypoint = self._entrypoint(plan)
        max_result_cards = self.config.get("max_result_cards", 100)
        pagination = self._pagination()

        page = None
        runtime = browser = context = None
        try:
            if self._page_factory is not None:
                page = self._page_factory()
            else:
                runtime, browser, context, page = self._open_playwright_page()

            response = None
            reuse_current_page = self.config.get("reuse_current_page", False)
            if not isinstance(reuse_current_page, bool):
                raise ValueError("reuse_current_page must be a boolean")
            if not any(str(action.get("action")) == "goto" for action in actions) and not reuse_current_page:
                navigation_options = self._navigation_options()
                response = page.goto(entrypoint, **navigation_options)
                self._wait_after_navigation(page)
            elif reuse_current_page:
                self._validate_reused_page(page, entrypoint)

            for action in actions:
                response = self._run_action(page, action, plan, response)
                if str(action.get("action")) == "goto":
                    self._wait_after_navigation(page)

            status_code = _response_status(response)
            content = page.content()
            source_url = str(getattr(page, "url", None) or entrypoint)
            capture = self._parse_page(
                content,
                plan=plan,
                source_url=source_url,
                observed_at=observed_at,
                selectors=selectors,
                status_code=status_code,
                max_result_cards=max_result_cards,
            )
            if capture.blocked:
                return capture
            if pagination["enabled"]:
                capture = self._collect_paginated_pages(
                    page,
                    capture,
                    plan=plan,
                    selectors=selectors,
                    observed_at=observed_at,
                    max_result_cards=max_result_cards,
                    pagination=pagination,
                )
                if capture.blocked:
                    return capture
                content = page.content()
            raw_snapshot_path = self._save_snapshot(content)
            applied_filters = self._read_applied_filters(page)
            capture = capture.model_copy(
                update={
                    "preset_name": plan.preset_name,
                    "raw_snapshot_path": raw_snapshot_path,
                    "applied_filters": applied_filters,
                },
                deep=True,
            )
            if self._detail_enrichment()["enabled"]:
                pre_detail_validation = validate_capture(
                    capture,
                    plan.model_copy(update={"post_filters": {}}),
                    sample_size=int(self.config.get("sample_size", 5) or 5),
                    max_result_cards=int(max_result_cards),
                )
                if pre_detail_validation.status is not DiscoveryStatus.PASS:
                    return capture
                capture = self._collect_detail_pages(
                    capture,
                    context=context,
                    search_page=page,
                    observed_at=observed_at,
                )
            return capture
        finally:
            if self._owns_page:
                _close_resource(page)
            if self._owns_context:
                _close_resource(context)
            if self._owns_browser:
                _close_resource(browser)
            _stop_runtime(runtime)

    @staticmethod
    def _validate_reused_page(page: Any, entrypoint: str) -> None:
        current_url = str(getattr(page, "url", "") or "").strip()
        if not current_url or current_url.startswith(("about:", "chrome:", "devtools:")):
            raise RuntimeError(
                "reuse_current_page requires an already loaded public listing page"
            )
        current_host = urlsplit(current_url).netloc.casefold()
        expected_host = urlsplit(entrypoint).netloc.casefold()
        if not current_host or current_host != expected_host:
            raise RuntimeError(
                "reuse_current_page page host does not match the configured entrypoint"
            )

    def _actions(self) -> list[Mapping[str, Any]]:
        actions = self.config.get("actions", [])
        if not isinstance(actions, list):
            raise ValueError("browser actions must be a list")
        return actions

    def _selectors(self) -> Mapping[str, str]:
        selectors = self.config.get("selectors", {})
        if not isinstance(selectors, Mapping):
            raise ValueError("browser selectors must be a mapping")
        return selectors

    def _pagination(self) -> dict[str, Any]:
        configured = self.config.get("pagination", {})
        if configured is None:
            configured = {}
        if not isinstance(configured, Mapping):
            raise ValueError("pagination must be a mapping")
        enabled = bool(configured.get("enabled", False))
        next_selector = configured.get("next_selector")
        if enabled and (not isinstance(next_selector, str) or not next_selector.strip()):
            raise ValueError("pagination.next_selector is required when pagination is enabled")
        max_pages = configured.get("max_pages", 50)
        if isinstance(max_pages, bool) or not isinstance(max_pages, int) or max_pages < 1:
            raise ValueError("pagination.max_pages must be a positive integer")
        wait_after_click_ms = configured.get("wait_after_click_ms", 250)
        if (
            isinstance(wait_after_click_ms, bool)
            or not isinstance(wait_after_click_ms, (int, float))
            or wait_after_click_ms < 0
        ):
            raise ValueError("pagination.wait_after_click_ms must be non-negative")
        progress_poll_interval_ms = configured.get("progress_poll_interval_ms", 250)
        progress_timeout_ms = configured.get("progress_timeout_ms", 10_000)
        progress_stable_polls = configured.get("progress_stable_polls", 2)
        result_count_variance_tolerance = self.config.get(
            "result_count_variance_tolerance", 0
        )
        if (
            isinstance(progress_stable_polls, bool)
            or not isinstance(progress_stable_polls, int)
            or progress_stable_polls < 1
        ):
            raise ValueError("pagination.progress_stable_polls must be a positive integer")
        if (
            isinstance(result_count_variance_tolerance, bool)
            or not isinstance(result_count_variance_tolerance, int)
            or result_count_variance_tolerance < 0
        ):
            raise ValueError("result_count_variance_tolerance must be a non-negative integer")
        for field_name, value in (
            ("progress_poll_interval_ms", progress_poll_interval_ms),
            ("progress_timeout_ms", progress_timeout_ms),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or value < 0
            ):
                raise ValueError(f"pagination.{field_name} must be non-negative")
        return {
            "enabled": enabled,
            "next_selector": str(next_selector) if next_selector else None,
            "max_pages": max_pages,
            "wait_after_click_ms": wait_after_click_ms,
            "progress_poll_interval_ms": progress_poll_interval_ms,
            "progress_timeout_ms": progress_timeout_ms,
            "progress_stable_polls": progress_stable_polls,
            "result_count_variance_tolerance": result_count_variance_tolerance,
        }

    def _detail_enrichment(self) -> dict[str, Any]:
        configured = self.config.get("detail_enrichment", {})
        if configured is None:
            configured = {}
        if not isinstance(configured, Mapping):
            raise ValueError("detail_enrichment must be a mapping")
        enabled = configured.get("enabled", False)
        continue_on_error = configured.get("continue_on_error", True)
        if not isinstance(enabled, bool):
            raise ValueError("detail_enrichment.enabled must be a boolean")
        if not isinstance(continue_on_error, bool):
            raise ValueError("detail_enrichment.continue_on_error must be a boolean")
        max_requests = configured.get("max_requests", 500)
        if isinstance(max_requests, bool) or not isinstance(max_requests, int) or max_requests < 1:
            raise ValueError("detail_enrichment.max_requests must be a positive integer")
        settle_ms = configured.get("settle_ms", 0)
        if (
            isinstance(settle_ms, bool)
            or not isinstance(settle_ms, (int, float))
            or settle_ms < 0
        ):
            raise ValueError("detail_enrichment.settle_ms must be non-negative")
        timeout_ms = configured.get("timeout_ms")
        if timeout_ms is not None and (
            isinstance(timeout_ms, bool)
            or not isinstance(timeout_ms, (int, float))
            or timeout_ms < 0
        ):
            raise ValueError("detail_enrichment.timeout_ms must be non-negative")
        delay_ms = configured.get("delay_ms", 0)
        if (
            isinstance(delay_ms, bool)
            or not isinstance(delay_ms, (int, float))
            or delay_ms < 0
        ):
            raise ValueError("detail_enrichment.delay_ms must be non-negative")
        stop_on_rate_limit = configured.get("stop_on_rate_limit", True)
        if not isinstance(stop_on_rate_limit, bool):
            raise ValueError("detail_enrichment.stop_on_rate_limit must be a boolean")
        return {
            "enabled": enabled,
            "continue_on_error": continue_on_error,
            "max_requests": max_requests,
            "settle_ms": settle_ms,
            "timeout_ms": timeout_ms,
            "delay_ms": delay_ms,
            "stop_on_rate_limit": stop_on_rate_limit,
            "raw_snapshot_dir": configured.get("raw_snapshot_dir"),
        }

    def _collect_detail_pages(
        self,
        capture: DiscoveryCapture,
        *,
        context: Any,
        search_page: Any,
        observed_at: datetime,
    ) -> DiscoveryCapture:
        detail_config = self._detail_enrichment()
        detail_page = self._open_detail_page(context, search_page)
        owns_detail_page = detail_page is not search_page
        cards: list[DiscoveryCard] = []
        warnings = list(capture.parse_warnings)
        snapshot_paths: list[str] = []
        attempted = successful = failed = 0
        rate_limited = False
        try:
            for card in capture.cards:
                if rate_limited:
                    copy = card.model_copy(deep=True)
                    _append_reason(copy, "DETAIL_NOT_ATTEMPTED_RATE_LIMITED")
                    cards.append(copy)
                    continue
                if attempted >= int(detail_config["max_requests"]):
                    copy = card.model_copy(deep=True)
                    _append_reason(copy, "DETAIL_REQUEST_LIMIT_REACHED")
                    cards.append(copy)
                    continue
                detail_url = str(card.source_url or "").strip()
                if not detail_url:
                    copy = card.model_copy(deep=True)
                    _append_reason(copy, "DETAIL_URL_MISSING")
                    warnings.append(f"DETAIL_URL_MISSING: {card.address}")
                    cards.append(copy)
                    continue
                attempted += 1
                try:
                    if attempted > 1:
                        _wait_after_pagination(detail_page, detail_config["delay_ms"])
                    options: dict[str, Any] = {"wait_until": "domcontentloaded"}
                    if detail_config["timeout_ms"] is not None:
                        options["timeout"] = int(detail_config["timeout_ms"])
                    response = detail_page.goto(detail_url, **options)
                    status_code = _response_status(response)
                    if status_code == 429:
                        raise _DetailRateLimitError("detail returned HTTP 429")
                    if not 200 <= status_code < 300:
                        raise RuntimeError(f"detail returned HTTP {status_code}")
                    _wait_after_pagination(detail_page, detail_config["settle_ms"])
                    content = detail_page.content()
                    payload = content.encode("utf-8") if isinstance(content, str) else bytes(content)
                    if has_visible_challenge(payload):
                        raise RuntimeError("detail access challenge detected")
                    detail = parse_realtor_ca_detail(payload, detail_url, observed_at)
                    snapshot_path = self._save_detail_snapshot(
                        payload,
                        card=card,
                        directory=detail_config["raw_snapshot_dir"],
                    )
                    if snapshot_path:
                        snapshot_paths.append(snapshot_path)
                    enriched = _merge_detail_card(card, detail)
                    cards.append(enriched)
                    successful += 1
                except _DetailRateLimitError as exc:
                    failed += 1
                    copy = card.model_copy(deep=True)
                    _append_reason(copy, "DETAIL_FETCH_FAILED")
                    _append_reason(copy, "DETAIL_RATE_LIMITED")
                    cards.append(copy)
                    warnings.append(
                        f"DETAIL_RATE_LIMITED: {card.mls_number or card.address}: {exc}"
                    )
                    rate_limited = bool(detail_config["stop_on_rate_limit"])
                except Exception as exc:
                    failed += 1
                    copy = card.model_copy(deep=True)
                    _append_reason(copy, "DETAIL_FETCH_FAILED")
                    cards.append(copy)
                    warnings.append(
                        f"DETAIL_FETCH_FAILED: {card.mls_number or card.address}: "
                        f"{type(exc).__name__}: {exc}"
                    )
                    if not detail_config["continue_on_error"]:
                        raise
        finally:
            if owns_detail_page:
                _close_resource(detail_page)
        return capture.model_copy(
            update={
                "cards": cards,
                "parse_warnings": warnings,
                "detail_requests": attempted,
                "detail_successes": successful,
                "detail_failures": failed,
                "detail_snapshot_paths": snapshot_paths,
            },
            deep=True,
        )

    def _open_detail_page(self, context: Any, search_page: Any) -> Any:
        if self._detail_page_factory is not None:
            return self._detail_page_factory()
        if context is not None:
            new_page = getattr(context, "new_page", None)
            if callable(new_page):
                return new_page()
        return search_page

    @staticmethod
    def _save_detail_snapshot(
        content: bytes,
        *,
        card: DiscoveryCard,
        directory: Any,
    ) -> str | None:
        if directory in (None, ""):
            return None
        target_dir = Path(str(directory))
        target_dir.mkdir(parents=True, exist_ok=True)
        identity = str(card.mls_number or card.address).strip().casefold()
        safe_identity = re.sub(r"[^a-z0-9]+", "-", identity).strip("-") or "listing"
        target = target_dir / f"{safe_identity}.html"
        target.write_bytes(content)
        return str(target)

    def _parse_page(
        self,
        content: str | bytes,
        *,
        plan: DiscoveryFilterPlan,
        source_url: str,
        observed_at: datetime,
        selectors: Mapping[str, str],
        status_code: int,
        max_result_cards: Any,
    ) -> DiscoveryCapture:
        capture = parse_filtered_html(
            content,
            source_name=self.source_name,
            source_url=source_url,
            observed_at=observed_at,
            selectors=selectors,
            status_code=status_code,
            max_result_cards=max_result_cards,
        )
        return capture.model_copy(
            update={"preset_name": plan.preset_name},
            deep=True,
        )

    def _collect_paginated_pages(
        self,
        page: Any,
        first_capture: DiscoveryCapture,
        *,
        plan: DiscoveryFilterPlan,
        selectors: Mapping[str, str],
        observed_at: datetime,
        max_result_cards: Any,
        pagination: Mapping[str, Any],
    ) -> DiscoveryCapture:
        cards: list[Any] = []
        seen_card_identities: set[str] = set()
        _extend_unique_cards(cards, first_capture.cards, seen_card_identities)
        parse_warnings = list(first_capture.parse_warnings)
        previous_fingerprint = _capture_page_fingerprint(first_capture)
        seen_page_fingerprints = {_capture_page_fingerprint(first_capture)}
        pages_fetched = 1
        pagination_complete = True

        while pages_fetched < int(pagination["max_pages"]):
            if (
                first_capture.result_count is not None
                and len(cards) >= first_capture.result_count
            ):
                break
            next_locator = self._next_locator(page, str(pagination["next_selector"]))
            if next_locator is None or not _locator_enabled(next_locator):
                break

            next_locator.click()
            _wait_after_pagination(page, pagination["wait_after_click_ms"])
            pages_fetched += 1
            current_capture = self._wait_for_page_progress(
                page,
                previous_fingerprint,
                seen_card_identities=seen_card_identities,
                plan=plan,
                observed_at=observed_at,
                selectors=selectors,
                status_code=first_capture.status_code,
                max_result_cards=max_result_cards,
                pagination=pagination,
            )
            if current_capture.blocked:
                return current_capture
            _extend_unique(parse_warnings, current_capture.parse_warnings)
            page_fingerprint = _capture_page_fingerprint(current_capture)
            if page_fingerprint and page_fingerprint in seen_page_fingerprints:
                pagination_complete = False
                break
            if page_fingerprint:
                seen_page_fingerprints.add(page_fingerprint)
            if not current_capture.cards:
                # A page can contain only malformed/placeholder cards.  The
                # page is skipped, but an available next page must still be
                # consumed; a bad card must never stop the filtered scan.
                if not current_capture.parse_warnings:
                    pagination_complete = False
                    break
                next_after_parse = self._next_locator(
                    page,
                    str(pagination["next_selector"]),
                )
                if next_after_parse is None or not _locator_enabled(next_after_parse):
                    break
                continue
            if not _capture_has_new_card(current_capture, seen_card_identities):
                pagination_complete = False
                break
            _extend_unique_cards(cards, current_capture.cards, seen_card_identities)
            previous_fingerprint = page_fingerprint

        if pages_fetched >= int(pagination["max_pages"]):
            next_locator = self._next_locator(page, str(pagination["next_selector"]))
            if next_locator is not None and _locator_enabled(next_locator):
                pagination_complete = False
        if (
            first_capture.result_count is not None
            and len(cards) < first_capture.result_count
        ):
            accounted_cards = len(cards) + len(parse_warnings)
            if accounted_cards < first_capture.result_count:
                card_gap = first_capture.result_count - len(cards)
                tolerance = int(pagination["result_count_variance_tolerance"])
                if card_gap <= tolerance:
                    parse_warnings.append(
                        "RESULT_COUNT_CARD_VARIANCE: "
                        f"reported={first_capture.result_count} "
                        f"parsed={len(cards)} gap={card_gap}"
                    )
                    # A transient/repeated AJAX page can make the progress
                    # guard conservative even after the bounded result set is
                    # otherwise accounted for within the configured variance.
                    # Keep the warning, but allow detail enrichment to run for
                    # the cards we actually acquired.
                    pagination_complete = True
                else:
                    pagination_complete = False

        return first_capture.model_copy(
            update={
                "cards": cards,
                "parse_warnings": parse_warnings,
                "count_is_complete": bool(
                    first_capture.count_is_complete and pagination_complete
                ),
            },
            deep=True,
        )

    def _wait_for_page_progress(
        self,
        page: Any,
        previous_fingerprint: str,
        *,
        seen_card_identities: set[str],
        plan: DiscoveryFilterPlan,
        observed_at: datetime,
        selectors: Mapping[str, str],
        status_code: int,
        max_result_cards: Any,
        pagination: Mapping[str, Any],
    ) -> DiscoveryCapture:
        timeout_ms = int(pagination["progress_timeout_ms"])
        interval_ms = pagination["progress_poll_interval_ms"]
        deadline = time.monotonic() + timeout_ms / 1000
        latest: DiscoveryCapture | None = None
        parse_warnings: list[str] = []
        candidate_fingerprint: str | None = None
        stable_polls = 0
        required_stable_polls = int(pagination["progress_stable_polls"])
        while True:
            content = page.content()
            source_url = str(getattr(page, "url", None) or "")
            latest = self._parse_page(
                content,
                plan=plan,
                source_url=source_url,
                observed_at=observed_at,
                selectors=selectors,
                status_code=status_code,
                max_result_cards=max_result_cards,
            )
            _extend_unique(parse_warnings, latest.parse_warnings)
            if latest.blocked:
                return latest.model_copy(
                    update={"parse_warnings": parse_warnings},
                    deep=True,
                )
            page_fingerprint = _capture_page_fingerprint(latest)
            has_new_card = _capture_has_new_card(latest, seen_card_identities)
            if (
                latest.cards
                and page_fingerprint != previous_fingerprint
                and has_new_card
            ):
                if page_fingerprint == candidate_fingerprint:
                    stable_polls += 1
                else:
                    candidate_fingerprint = page_fingerprint
                    stable_polls = 1
                if stable_polls >= required_stable_polls:
                    return latest.model_copy(
                        update={"parse_warnings": parse_warnings},
                        deep=True,
                    )
            else:
                candidate_fingerprint = None
                stable_polls = 0
            if time.monotonic() >= deadline:
                return latest.model_copy(
                    update={"parse_warnings": parse_warnings},
                    deep=True,
                )
            _wait_after_pagination(page, interval_ms)

    @staticmethod
    def _next_locator(page: Any, selector: str) -> Any | None:
        locator = page.locator(selector)
        try:
            count = locator.count()
        except (AttributeError, TypeError):
            return locator
        if count < 1:
            return None
        if count == 1:
            return locator
        # REALTOR.ca renders several pagination containers (sidebar, info box,
        # list top/bottom) whose next links share the same class.  Only the
        # active container's link is visible; resolve to that single element
        # so click()/is_enabled() do not trip Playwright strict mode.
        for index in range(count):
            candidate = locator.nth(index)
            try:
                if candidate.is_visible():
                    return candidate
            except (AttributeError, TypeError):
                return locator
        return None

    def _entrypoint(self, plan: DiscoveryFilterPlan | None = None) -> str:
        value = self.config.get("entrypoint") or self.config.get("source_url")
        if not isinstance(value, str) or not value.strip():
            raise ValueError("PLAYWRIGHT_NATIVE source requires browser.entrypoint")
        builder = self.config.get("url_builder")
        if builder is None:
            return value
        if builder == "REALTOR_CA_MAP":
            if plan is None:
                raise ValueError("REALTOR_CA_MAP URL builder requires a filter plan")
            return build_realtor_ca_map_url(plan, value)
        raise ValueError(f"unsupported discovery URL builder: {builder!r}")

    @staticmethod
    def _validate_actions(actions: list[Mapping[str, Any]]) -> None:
        for index, action in enumerate(actions):
            if not isinstance(action, Mapping):
                raise ValueError(f"browser action {index} must be a mapping")
            name = action.get("action")
            if name not in _SUPPORTED_ACTIONS:
                raise ValueError(f"unsupported discovery browser action: {name!r}")
            if name not in {"goto", "apply_custom_boundary"} and not isinstance(action.get("selector"), str):
                raise ValueError(f"browser action {index} requires a selector")
            if name in {"fill", "select_option"} and not isinstance(
                action.get("value_from"), str
            ):
                raise ValueError(f"browser action {index} requires value_from")

    def _run_action(
        self,
        page: Any,
        action: Mapping[str, Any],
        plan: DiscoveryFilterPlan,
        previous_response: Any,
    ) -> Any:
        name = action["action"]
        if name == "goto":
            url = action.get("url")
            if url is None and "value_from" not in action:
                url = self._entrypoint(plan)
            if url is None:
                url = self._resolve_value(action, plan)
            if not isinstance(url, str) or not url.strip():
                raise ValueError("goto action requires a non-empty url or value_from")
            return page.goto(url)
        if name == "fill":
            page.fill(
                str(action["selector"]),
                _stringify_action_value(self._resolve_value(action, plan)),
            )
            return previous_response
        if name == "select_option":
            page.select_option(
                str(action["selector"]),
                _stringify_action_value(self._resolve_value(action, plan)),
            )
            return previous_response
        if name == "check":
            page.check(str(action["selector"]))
            return previous_response
        if name == "click":
            page.click(str(action["selector"]))
            return previous_response
        if name == "click_filter_submit":
            page.click(str(action["selector"]))
            return previous_response
        if name == "wait_for_selector":
            timeout_ms = action.get("timeout_ms")
            if timeout_ms is None:
                page.wait_for_selector(str(action["selector"]))
            else:
                if (
                    isinstance(timeout_ms, bool)
                    or not isinstance(timeout_ms, (int, float))
                    or timeout_ms < 0
                ):
                    raise ValueError(
                        "browser wait_for_selector timeout_ms must be non-negative"
                    )
                page.wait_for_selector(
                    str(action["selector"]),
                    timeout=int(timeout_ms),
                )
            return previous_response
        if name == "apply_custom_boundary":
            polygon_wkt = self._boundary_wkt(plan)
            result = page.evaluate(
                """
                ({wkt}) => {
                  const mapPage = window.ApplicationState?.Current?.Page;
                  if (!mapPage || typeof mapPage.displayCustomDrawnPolygon !== 'function') {
                    return {ok: false, error: 'native map page object unavailable'};
                  }
                  // REALTOR.ca's own displayCustomDrawnPolygon helper adds a
                  // sentinel ring that works for simple polygons but throws
                  // on the site's MULTIPOLYGON parser.  Parse the same WKT
                  // through the page's native helper and pass the exact
                  // feature rings to its native map renderer.
                  const featureCollection = window.CustomPolygonDrawHelper?.parseWKT_ToTurfPolygon(wkt);
                  if (!featureCollection?.features?.length) {
                    return {ok: false, error: 'native WKT parser returned no polygon features'};
                  }
                  const paths = featureCollection.features.map(feature =>
                    feature.geometry.coordinates.map(ring =>
                      ring.map(([longitude, latitude]) => [Number(latitude), Number(longitude)])
                    )
                  );
                  mapPage.showMapPolgon(paths, false, true, false);
                  mapPage.showBoundaryRemoval();
                  mapPage.wkt_simplified = wkt;
                  mapPage.currentResultsCriteria.PolygonWKT = wkt;
                  if (window.SessionStorage?.LastMapPolygonDrawnWKT?.save) {
                    window.SessionStorage.LastMapPolygonDrawnWKT.save(wkt);
                  }
                  const criteria = typeof mapPage.getCurrentCriteria === 'function'
                    ? mapPage.getCurrentCriteria()
                    : {};
                  delete criteria.PGeoIds;
                  delete criteria.PolygonID;
                  delete criteria.PolygonOutlineID;
                  delete criteria.GeoIds;
                  delete criteria.GeoName;
                  criteria.PolygonWKT = wkt;
                  criteria.CurrentPage = '1';
                  mapPage.mapSearch(criteria, true, true);
                  return {ok: true, polygonWkt: mapPage.wkt_simplified};
                }
                """,
                {"wkt": polygon_wkt},
            )
            if not isinstance(result, Mapping) or not result.get("ok"):
                error = result.get("error") if isinstance(result, Mapping) else result
                raise RuntimeError(f"REALTOR.ca custom boundary application failed: {error}")
            self._wait_after_navigation(page)
            return previous_response
        raise ValueError(f"unsupported discovery browser action: {name!r}")

    @staticmethod
    def _boundary_wkt(plan: DiscoveryFilterPlan) -> str:
        geography = plan.geography if isinstance(plan.geography, Mapping) else {}
        map_params = geography.get("map_params", geography)
        if not isinstance(map_params, Mapping):
            raise ValueError("apply_custom_boundary requires geography.map_params")
        boundary_id = map_params.get("boundary_id")
        registry_path = map_params.get("boundary_registry_path")
        if not isinstance(boundary_id, str) or not boundary_id.strip():
            raise ValueError("apply_custom_boundary requires map_params.boundary_id")
        if not isinstance(registry_path, str) or not registry_path.strip():
            raise ValueError("apply_custom_boundary requires map_params.boundary_registry_path")
        registry = load_boundary_registry(registry_path)
        region = region_by_id(registry, boundary_id)
        polygon_wkt = region.get("polygon_wkt")
        if not isinstance(polygon_wkt, str) or not polygon_wkt.strip():
            raise ValueError(f"boundary {boundary_id!r} has no polygon_wkt")
        return polygon_wkt

    @staticmethod
    def _resolve_value(action: Mapping[str, Any], plan: DiscoveryFilterPlan) -> Any:
        key = action.get("value_from")
        if not isinstance(key, str) or not key.strip():
            raise ValueError("browser action value_from must be a non-empty field name")
        for values in (plan.native_filters, plan.desired_filters):
            if key in values:
                return values[key]
        raise ValueError(f"browser action value_from is not in the filter plan: {key}")

    def _save_snapshot(self, content: str | bytes) -> str | None:
        target = self.config.get("raw_snapshot_path")
        if target is None:
            return None
        path = Path(str(target))
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = content if isinstance(content, bytes) else content.encode("utf-8")
        path.write_bytes(payload)
        return str(path)

    def _navigation_options(self) -> dict[str, str]:
        wait_until = self.config.get("navigation_wait_until")
        if wait_until is None:
            return {}
        if wait_until not in {"commit", "domcontentloaded", "load", "networkidle"}:
            raise ValueError(
                "navigation_wait_until must be one of commit, domcontentloaded, load, networkidle"
            )
        return {"wait_until": str(wait_until)}

    def _wait_after_navigation(self, page: Any) -> None:
        """Allow client-rendered result cards to settle after a navigation.

        REALTOR.ca returns the search shell before its result-count and card
        elements are populated by AJAX.  A bounded settle delay is preferable
        to treating the first shell snapshot as an empty/failed result set.
        Test pages can provide Playwright's ``wait_for_timeout``; the fallback
        keeps the executor usable with minimal page adapters.
        """
        configured = self.config.get("navigation_settle_seconds", 0)
        if isinstance(configured, bool):
            raise ValueError("navigation_settle_seconds must be numeric")
        try:
            seconds = float(configured)
        except (TypeError, ValueError) as exc:
            raise ValueError("navigation_settle_seconds must be numeric") from exc
        if seconds < 0:
            raise ValueError("navigation_settle_seconds must be non-negative")
        if seconds == 0:
            return
        milliseconds = int(seconds * 1000)
        wait_for_timeout = getattr(page, "wait_for_timeout", None)
        if callable(wait_for_timeout):
            wait_for_timeout(milliseconds)
        else:
            time.sleep(seconds)

    def _read_applied_filters(self, page: Any) -> dict[str, Any]:
        configured = self.config.get("applied_filter_selectors", {})
        if configured is None:
            return {}
        if not isinstance(configured, Mapping):
            raise ValueError("applied_filter_selectors must be a mapping")

        result: dict[str, Any] = {}
        for field, specification in configured.items():
            selector, value_mapping = self._filter_selector_spec(specification, field)
            try:
                raw_value = page.locator(selector).input_value()
            except Exception as exc:
                raise ValueError(
                    f"failed to read rendered REALTOR.ca filter {field!r} from {selector!r}: {exc}"
                ) from exc
            result[str(field)] = self._map_rendered_filter_value(raw_value, value_mapping)
        return result

    @staticmethod
    def _filter_selector_spec(
        specification: Any,
        field: Any,
    ) -> tuple[str, Mapping[Any, Any]]:
        if isinstance(specification, str) and specification.strip():
            return specification, {}
        if not isinstance(specification, Mapping):
            raise ValueError(
                f"applied filter selector for {field!r} must be a CSS selector or mapping"
            )
        selector = specification.get("selector")
        if not isinstance(selector, str) or not selector.strip():
            raise ValueError(f"applied filter selector for {field!r} requires selector")
        value_mapping = specification.get("value_mapping", {})
        if not isinstance(value_mapping, Mapping):
            raise ValueError(f"applied filter value_mapping for {field!r} must be a mapping")
        return selector, value_mapping

    @staticmethod
    def _map_rendered_filter_value(
        value: Any,
        value_mapping: Mapping[Any, Any],
    ) -> Any:
        if value in value_mapping:
            return value_mapping[value]
        text = str(value)
        if text in value_mapping:
            return value_mapping[text]
        for key, mapped in value_mapping.items():
            if str(key) == text:
                return mapped
        if re.fullmatch(r"[-+]?\d+(?:\.\d+)?", text.strip()):
            numeric = float(text)
            return int(numeric) if numeric.is_integer() else numeric
        return value

    def _open_playwright_page(self) -> tuple[Any, Any, Any, Any]:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:  # pragma: no cover - depends on optional extra.
            raise RuntimeError(
                "Playwright is not installed; install the browser extra before using PLAYWRIGHT_NATIVE"
            ) from exc

        runtime = sync_playwright().start()
        browser = context = page = None
        try:
            browser_name = str(self.config.get("browser_engine", "chromium")).lower()
            if browser_name not in {"chromium", "firefox", "webkit"}:
                raise ValueError(f"unsupported Playwright browser engine: {browser_name}")
            cdp_endpoint = self.config.get("browser_cdp_endpoint")
            if cdp_endpoint is not None:
                if browser_name != "chromium":
                    raise ValueError("browser_cdp_endpoint requires chromium")
                if not isinstance(cdp_endpoint, str) or not cdp_endpoint.strip():
                    raise ValueError("browser_cdp_endpoint must be a non-empty string")
                # Chrome 152 no longer accepts Playwright's default
                # Browser.setDownloadBehavior call for an already-connected
                # default context.  no_defaults keeps this read-only
                # discovery path compatible with the user's existing Chrome
                # session; listing details are saved by the pipeline itself.
                browser = runtime.chromium.connect_over_cdp(
                    cdp_endpoint.strip(),
                    no_defaults=True,
                )
                contexts = list(browser.contexts)
                if not contexts:
                    raise RuntimeError(
                        "connected Chrome has no browser context available for discovery"
                    )
                context = contexts[0]
                pages = list(context.pages)
                open_new_page = self.config.get("open_new_page", False)
                if not isinstance(open_new_page, bool):
                    raise ValueError("open_new_page must be a boolean")
                page, self._owns_page = _prepare_cdp_page(
                    context,
                    pages,
                    open_new_page=open_new_page,
                )
                self._owns_context = False
                self._owns_browser = False
                return runtime, browser, context, page
            browser_type = getattr(runtime, browser_name)
            browser = browser_type.launch(**self._launch_options(browser_name))
            context = browser.new_context()
            page = context.new_page()
            return runtime, browser, context, page
        except Exception:
            _close_resource(page)
            _close_resource(context)
            _close_resource(browser)
            _stop_runtime(runtime)
            raise

    def _launch_options(self, browser_name: str | None = None) -> dict[str, Any]:
        """Build bounded launch options for the configured browser.

        The default remains headless Chromium for deterministic/offline tests.
        A live public-site trial can opt into the installed Google Chrome
        channel and a visible window, which avoids Safari's AppleScript DOM
        bridge while retaining the same native-filtered discovery contract.
        """

        headless = self.config.get("headless", True)
        if not isinstance(headless, bool):
            raise ValueError("headless must be a boolean")
        options: dict[str, Any] = {"headless": headless}

        channel = self.config.get("browser_channel")
        if channel is not None:
            if browser_name is not None and browser_name != "chromium":
                raise ValueError("browser_channel is only supported for chromium")
            if not isinstance(channel, str) or not channel.strip():
                raise ValueError("browser_channel must be a non-empty string")
            options["channel"] = channel.strip()

        executable_path = self.config.get("browser_executable_path")
        if executable_path is not None:
            if channel is not None:
                raise ValueError(
                    "browser_channel and browser_executable_path are mutually exclusive"
                )
            if not isinstance(executable_path, str) or not executable_path.strip():
                raise ValueError("browser_executable_path must be a non-empty string")
            options["executable_path"] = executable_path.strip()

        extra = self.config.get("launch_options", {})
        if extra is None:
            extra = {}
        if not isinstance(extra, Mapping):
            raise ValueError("launch_options must be a mapping")
        options.update(dict(extra))
        options["headless"] = headless
        return options


def _append_reason(card: DiscoveryCard, reason: str) -> None:
    if reason not in card.reason_codes:
        card.reason_codes.append(reason)


def _merge_detail_card(card: DiscoveryCard, detail: Mapping[str, Any]) -> DiscoveryCard:
    card_mls = str(card.mls_number or "").strip().casefold()
    detail_mls = str(detail.get("mls_number") or "").strip().casefold()
    if card_mls and detail_mls and card_mls != detail_mls:
        raise ValueError(
            f"detail identity MLS mismatch: card={card.mls_number} detail={detail.get('mls_number')}"
        )
    detail_address = detail.get("address")
    if detail_address:
        municipalities = {
            str(value).strip()
            for value in (card.municipality, detail.get("municipality"))
            if str(value or "").strip()
        }
        address_keys = {
            _address_key_without_municipality(card.address, municipality)
            for municipality in municipalities
        }
        address_keys.add(_address_key_without_municipality(card.address, None))
        detail_keys = {
            _address_key_without_municipality(str(detail_address), municipality)
            for municipality in municipalities
        }
        detail_keys.add(_address_key_without_municipality(str(detail_address), None))
        if not address_keys.intersection(detail_keys):
            raise ValueError(
                f"detail identity address mismatch: card={card.address} detail={detail_address}"
            )

    preferred_fields = {
        "mls_number",
        "municipality",
        "neighbourhood",
        "asking",
        "bedrooms",
        "bathrooms",
        "finished_sqft",
        "lot_area_sqft",
        "lot_dimensions",
        "frontage_ft",
        "depth_ft",
        "property_type",
        "ownership_type",
        "listing_status",
        "dom",
        "year_built",
        "taxes",
        "description",
        "lot_area_source",
        "lot_dimensions_description_conflict",
        "realtor_price_sale_history",
        "realtor_history_status",
    }
    updates = {
        field: value
        for field, value in detail.items()
        if field in preferred_fields and value not in (None, "")
    }
    if detail_address:
        updates["address"] = card.address
    if detail.get("lot_dimensions_description_conflict"):
        # Description dims described a different parcel (e.g. an assembled
        # site); drop any previously stored listing dims so official geometry
        # is the only source.
        for field in ("lot_dimensions", "frontage_ft", "depth_ft"):
            updates.setdefault(field, None)
    raw = dict(card.raw or {})
    history_fields = {
        field: detail[field]
        for field in ("realtor_price_sale_history", "realtor_history_status")
        if field in detail and detail[field] not in (None, "", [], {})
    }
    if history_fields:
        raw.update(history_fields)
        updates["raw"] = raw
    if raw.get("official_frontage_ft") is not None and raw.get("official_depth_ft") is not None:
        updates["geometry_source"] = "OFFICIAL_BCA"
    elif detail.get("frontage_ft") is not None and detail.get("depth_ft") is not None:
        updates["geometry_source"] = "LISTING_OBSERVED"
    return card.model_copy(update=updates, deep=True)


def replay_cached_realtor_details(
    cards: list[DiscoveryCard],
    snapshot_dir: str | Path,
    *,
    observed_at: datetime | None = None,
) -> tuple[list[DiscoveryCard], list[str]]:
    """Enrich cards from saved REALTOR.ca detail HTML without network access.

    This is the reusable local-replay path for a research rerun.  Missing or
    malformed snapshots are reported per listing and do not stop the batch;
    absent ``approved_use`` and absent transaction history remain UNKNOWN.
    """

    directory = Path(snapshot_dir)
    observed = observed_at or datetime.now(timezone.utc)
    replayed: list[DiscoveryCard] = []
    warnings: list[str] = []
    for card in cards:
        identity = str(card.mls_number or "").strip().casefold()
        if not identity:
            replayed.append(card)
            continue
        safe_identity = re.sub(r"[^a-z0-9]+", "-", identity).strip("-") or "listing"
        path = directory / f"{safe_identity}.html"
        if not path.exists():
            replayed.append(card)
            continue
        try:
            detail = parse_realtor_ca_detail(path.read_bytes(), str(card.source_url or ""), observed)
            replayed.append(_merge_detail_card(card, detail))
        except Exception as exc:
            warnings.append(
                f"REALTOR_DETAIL_REPLAY_FAILED: {card.mls_number or card.address}: "
                f"{type(exc).__name__}: {exc}"
            )
            replayed.append(card)
    return replayed, warnings


def _address_key_without_municipality(
    address: str,
    municipality: str | None,
) -> str:
    tokens = normalize_address(address, None).split()
    if municipality:
        suffix = normalize_address(municipality, None).split()
        if suffix and len(tokens) >= len(suffix) and tokens[-len(suffix) :] == suffix:
            tokens = tokens[: -len(suffix)]
    return " ".join(tokens)


def _response_status(response: Any) -> int:
    if response is None:
        return 200
    value = getattr(response, "status", 200)
    try:
        return int(value)
    except (TypeError, ValueError):
        return 200


def _select_cdp_page(pages: list[Any]) -> Any | None:
    """Prefer a normal user page over Chrome's internal UI targets."""

    for page in reversed(pages):
        url = str(getattr(page, "url", "") or "").lower()
        if not url.startswith(("chrome://", "devtools://", "about:")):
            return page
    return pages[-1] if pages else None


def _prepare_cdp_page(
    context: Any,
    pages: list[Any],
    *,
    open_new_page: bool = False,
) -> tuple[Any, bool]:
    """Choose a CDP page without inheriting stale map/list/pagination state."""

    if open_new_page:
        return context.new_page(), True
    page = _select_cdp_page(pages)
    if page is None:
        return context.new_page(), True
    return page, False


def _locator_enabled(locator: Any) -> bool:
    try:
        if not locator.is_enabled():
            return False
        # REALTOR.ca renders pagination links as anchors.  On the final page
        # an anchor can still be "enabled" in Playwright terms while carrying
        # the HTML disabled marker (and sometimes a disabled class).  Treat
        # those native UI markers as authoritative so a malformed one-card
        # gap does not turn a complete scan into a false pagination loop.
        get_attribute = getattr(locator, "get_attribute", None)
        if callable(get_attribute):
            if get_attribute("disabled") is not None:
                return False
            if str(get_attribute("aria-disabled") or "").lower() == "true":
                return False
            classes = str(get_attribute("class") or "").lower().split()
            if "disabled" in classes:
                return False
        return True
    except AttributeError:
        return True


def _wait_after_pagination(page: Any, milliseconds: int | float) -> None:
    if not milliseconds:
        return
    wait_for_timeout = getattr(page, "wait_for_timeout", None)
    if callable(wait_for_timeout):
        wait_for_timeout(float(milliseconds))


def _card_identity(card: Any) -> str:
    return str(card.mls_number or card.source_url or card.address).strip().casefold()


def _capture_progress_marker(capture: DiscoveryCapture) -> str:
    if not capture.cards:
        return ""
    return _card_identity(capture.cards[0])


def _capture_has_new_card(capture: DiscoveryCapture, seen_card_identities: set[str]) -> bool:
    return any(_card_identity(card) not in seen_card_identities for card in capture.cards)


def _capture_page_fingerprint(capture: DiscoveryCapture) -> str:
    identities = [
        _card_identity(card)
        for card in capture.cards
    ]
    return "\x1f".join(identities)


def _extend_unique_cards(
    target: list[Any],
    values: list[Any],
    seen_card_identities: set[str],
) -> None:
    for card in values:
        identity = _card_identity(card)
        if identity in seen_card_identities:
            continue
        seen_card_identities.add(identity)
        target.append(card)


def _extend_unique(target: list[str], values: list[str]) -> None:
    for value in values:
        if value not in target:
            target.append(value)


def _stringify_action_value(value: Any) -> str:
    if isinstance(value, (str, int, float, bool)):
        return str(value)
    raise ValueError("browser action values must be scalar")


def _close_resource(resource: Any) -> None:
    if resource is None:
        return
    close = getattr(resource, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            # Cleanup must not hide the navigation/parser error that caused
            # the source run to fail.  The resource is process-local and the
            # owning Playwright runtime is stopped immediately afterward.
            pass


def _stop_runtime(runtime: Any) -> None:
    if runtime is None:
        return
    stop = getattr(runtime, "stop", None)
    if callable(stop):
        try:
            stop()
        except Exception:
            pass
