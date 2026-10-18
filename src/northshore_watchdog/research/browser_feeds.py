from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import time
from typing import Any, Callable, Iterable, Mapping
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .models import SourceType
from .public_feeds import PublicFeedResponse, PublicFeedSource
from .sources import SourceRunResult, SourceRunStatus

try:  # Optional dependency: local fixtures and the rest of the CLI stay usable without it.
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover - exercised by setup documentation, not unit fixtures.
    sync_playwright = None


@dataclass(frozen=True)
class BrowserPageResponse(PublicFeedResponse):
    """Rendered page response used by the browser adapter and its test seam."""

    screenshot: bytes | None = None


class _PlaywrightBrowserSession:
    """One isolated standard Playwright context for a bounded source run."""

    def __init__(self, config: Mapping[str, Any]) -> None:
        self.config = dict(config)
        self._playwright = None
        self._browser = None
        self._context = None

    def __enter__(self) -> "_PlaywrightBrowserSession":
        if sync_playwright is None:
            raise RuntimeError(
                "Playwright is not installed; install the browser extra and Chromium before running this source"
            )
        browser_name = str(self.config.get("browser", "chromium")).lower()
        if browser_name not in {"chromium", "firefox", "webkit"}:
            raise ValueError(f"unsupported Playwright browser: {browser_name}")
        self._playwright = sync_playwright().start()
        try:
            browser_type = getattr(self._playwright, browser_name)
            self._browser = browser_type.launch(
                headless=bool(self.config.get("headless", True)),
            )
            context_options: dict[str, Any] = {
                "user_agent": str(
                    self.config.get(
                        "user_agent",
                        "northshore-property-watchdog/0.1 (+read-only-research)",
                    )
                ),
                "locale": str(self.config.get("locale", "en-CA")),
            }
            self._context = self._browser.new_context(**context_options)
            return self
        except Exception:
            self.close()
            raise

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        if self._context is not None:
            try:
                self._context.close()
            finally:
                self._context = None
        if self._browser is not None:
            try:
                self._browser.close()
            finally:
                self._browser = None
        if self._playwright is not None:
            try:
                self._playwright.stop()
            finally:
                self._playwright = None

    def _timeout_ms(self, request: Mapping[str, Any]) -> int:
        value = request.get("timeout_seconds", self.config.get("timeout_seconds", 30))
        return max(1, int(float(value) * 1000))

    def fetch_page(self, request: Mapping[str, Any]) -> BrowserPageResponse:
        method = str(request.get("method", "GET")).upper()
        if method != "GET":
            raise ValueError("Playwright public source supports GET page requests only")
        url = str(request.get("url") or "")
        if not url:
            raise ValueError("Playwright page request is missing url")
        if self._context is None:
            raise RuntimeError("Playwright browser context is not open")

        page = self._context.new_page()
        try:
            wait_until = str(
                request.get("wait_until", self.config.get("wait_until", "domcontentloaded"))
            )
            if wait_until not in {"commit", "domcontentloaded", "load", "networkidle"}:
                raise ValueError(f"unsupported Playwright wait_until value: {wait_until}")
            response = page.goto(
                url,
                wait_until=wait_until,
                timeout=self._timeout_ms(request),
            )
            wait_for_selector = request.get(
                "wait_for_selector",
                self.config.get("wait_for_selector"),
            )
            if wait_for_selector:
                page.wait_for_selector(
                    str(wait_for_selector),
                    timeout=self._timeout_ms(request),
                )
            content = page.content().encode("utf-8")
            headers = dict(response.all_headers()) if response is not None else {}
            screenshot = None
            if bool(request.get("capture_screenshot", self.config.get("capture_screenshot", False))):
                screenshot = page.screenshot(
                    type="png",
                    full_page=bool(
                        request.get(
                            "full_page_screenshot",
                            self.config.get("full_page_screenshot", False),
                        )
                    ),
                )
            return BrowserPageResponse(
                url=page.url,
                status_code=int(response.status) if response is not None else 0,
                headers=headers,
                content=content,
                method=method,
                screenshot=screenshot,
            )
        except Exception as exc:
            raise OSError(
                f"Playwright navigation failed for {url}: {type(exc).__name__}: {exc}"
            ) from exc
        finally:
            page.close()

    def fetch_robots(self, url: str) -> PublicFeedResponse:
        if self._context is None:
            raise RuntimeError("Playwright browser context is not open")
        try:
            response = self._context.request.get(
                url,
                timeout=max(1, int(float(self.config.get("timeout_seconds", 30)) * 1000)),
            )
            return PublicFeedResponse(
                url=str(response.url),
                status_code=int(response.status),
                headers=dict(response.headers),
                content=response.body(),
                method="GET",
            )
        except Exception as exc:
            raise OSError(
                f"Playwright robots request failed for {url}: {type(exc).__name__}: {exc}"
            ) from exc


class PlaywrightPublicSource(PublicFeedSource):
    """Bounded rendered-page source that reuses the public feed policy/parser."""

    def __init__(
        self,
        *,
        name: str,
        source_type: SourceType | str,
        feed_requests: Iterable[Mapping[str, Any]],
        raw_snapshot_dir: str | Path,
        config: dict[str, Any] | None = None,
        browser_transport: Callable[[Mapping[str, Any]], Any] | None = None,
        robots_transport: Callable[[str], Any] | None = None,
        sleep_fn: Callable[[float], None] | None = None,
        source_registry_id: str | None = None,
        source_url: str | None = None,
    ) -> None:
        super().__init__(
            name=name,
            source_type=source_type,
            feed_requests=feed_requests,
            raw_snapshot_dir=raw_snapshot_dir,
            config=config,
            transport=browser_transport,
            robots_transport=robots_transport,
            sleep_fn=sleep_fn or time.sleep,
            source_registry_id=source_registry_id,
            source_url=source_url,
        )
        self._browser_transport = browser_transport
        self._browser_robots_transport = robots_transport
        self._browser_page_fingerprints: set[str] = set()
        self._browser_page_urls: set[str] = set()
        self._browser_warnings: list[str] = []
        self._browser_pagination_partial = False

    def collect(self, observed_at: datetime) -> SourceRunResult:
        self._browser_page_fingerprints = set()
        self._browser_page_urls = {
            str(item.get("url"))
            for item in self.feed_requests
            if item.get("url")
        }
        self._browser_warnings = []
        self._browser_pagination_partial = False

        if self.config.get("enabled") is False or self._browser_transport is not None:
            result = super().collect(observed_at)
            return self._with_browser_warnings(result)

        started_at = datetime.now(timezone.utc)
        try:
            with _PlaywrightBrowserSession(self.config) as session:
                self.transport = session.fetch_page
                self.robots_transport = self._browser_robots_transport or session.fetch_robots
                result = super().collect(observed_at)
        except Exception as exc:
            result = self._result(
                started_at=started_at,
                status=SourceRunStatus.FAILED,
                finished_at=datetime.now(timezone.utc),
                errors=[f"Playwright runtime unavailable or failed: {type(exc).__name__}: {exc}"],
            )
        finally:
            self.transport = self._browser_transport
            self.robots_transport = self._browser_robots_transport
        return self._with_browser_warnings(result)

    def _with_browser_warnings(self, result: SourceRunResult) -> SourceRunResult:
        if not self._browser_warnings and not self._browser_pagination_partial:
            return result
        status = result.status
        if self._browser_pagination_partial and status is SourceRunStatus.SUCCESS:
            status = SourceRunStatus.PARTIAL
        return result.model_copy(
            update={
                "status": status,
                "warnings": [*result.warnings, *self._browser_warnings],
            }
        )

    def _fetch(self, request: Mapping[str, Any]) -> PublicFeedResponse:
        if str(request.get("method", "GET")).upper() != "GET":
            raise ValueError("Playwright public source supports GET page requests only")
        return super()._fetch(request)

    def _follow_up_requests(
        self,
        request: Mapping[str, Any],
        response: PublicFeedResponse,
        parsed_rows: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        del parsed_rows
        selector = str(
            request.get("next_page_selector")
            or self.config.get("next_page_selector")
            or ""
        ).strip()
        if not selector:
            return []

        page_number = int(request.get("_browser_page_number", 1))
        max_pages = max(
            1,
            int(
                request.get(
                    "max_pages_per_request",
                    self.config.get("max_pages_per_request", 1),
                )
            ),
        )
        fingerprint = sha256(response.content).hexdigest()
        if fingerprint in self._browser_page_fingerprints:
            self._browser_warnings.append(
                f"pagination fingerprint repeated; stopped at {response.url}"
            )
            self._browser_pagination_partial = True
            return []
        self._browser_page_fingerprints.add(fingerprint)

        soup = BeautifulSoup(response.content, "html.parser")
        next_link = soup.select_one(selector)
        if next_link is None:
            return []
        if page_number >= max_pages:
            self._browser_warnings.append(
                f"pagination page cap reached: {max_pages} for {request.get('url')}"
            )
            self._browser_pagination_partial = True
            return []
        href = next_link.get("href")
        if not href:
            self._browser_warnings.append(
                f"pagination selector has no href at {response.url}"
            )
            self._browser_pagination_partial = True
            return []
        next_url = urljoin(response.url, str(href))
        if not self._allowed_url(next_url):
            self._browser_warnings.append(
                f"pagination next URL is outside allowed domains: {next_url}"
            )
            self._browser_pagination_partial = True
            return []
        if next_url in self._browser_page_urls:
            self._browser_warnings.append(
                f"pagination URL repeated; stopped at {next_url}"
            )
            self._browser_pagination_partial = True
            return []
        self._browser_page_urls.add(next_url)
        return [
            {
                **dict(request),
                "url": next_url,
                "_browser_page_number": page_number + 1,
            }
        ]
