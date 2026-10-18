from __future__ import annotations

import asyncio
import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import northshore_watchdog.research.crawling.spider as spider_module
from northshore_watchdog.research.crawling.spider import (
    FullMarketSpider,
    next_zolo_url,
    realtor_page_advanced,
    should_fetch_detail,
)


OBSERVED_AT = datetime(2026, 9, 2, tzinfo=timezone.utc)
ZOLO_SEARCH_URL = "https://www.zolo.ca/vancouver-real-estate/houses"
REALTOR_SEARCH_URL = "https://www.realtor.ca/bc/vancouver/single-family-homes-for-sale"


def _crawler_extra_available() -> bool:
    return bool(
        importlib.util.find_spec("scrapy")
        and importlib.util.find_spec("scrapy_playwright")
    )


def _require_crawler_extra():
    if not _crawler_extra_available():
        pytest.skip("Scrapy crawler extra is not installed")
    import scrapy

    return scrapy


def _source(name: str = "zolo-full-market") -> dict:
    if name.startswith("realtor"):
        return {
            "name": name,
            "source_registry_id": "source:realtor-full-market",
            "allowed_domains": ["www.realtor.ca", "realtor.ca"],
            "pagination_mode": "browser_control",
            "pagination_selector": "a[aria-label='Next']",
            "entrypoints": [{"municipality": "Vancouver", "url": REALTOR_SEARCH_URL}],
        }
    return {
        "name": name,
        "source_registry_id": "source:zolo-full-market",
        "allowed_domains": ["www.zolo.ca", "zolo.ca"],
        "entrypoints": [{"municipality": "Vancouver", "url": ZOLO_SEARCH_URL}],
    }


def _context(*, run_id: str = "run-1", **overrides) -> dict:
    value = {
        "run_id": run_id,
        "observed_at": OBSERVED_AT,
        "crawl": {
            "max_search_pages": 20,
            "max_detail_requests": 20,
            "pagination_wait_seconds": 0.01,
        },
    }
    value.update(overrides)
    return value


class RecordingWriter:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def write(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(**kwargs)


class FakeRequest:
    def __init__(self, url, *, callback=None, meta=None, **kwargs):
        self.url = url
        self.callback = callback
        self.meta = dict(meta or {})
        self.kwargs = kwargs


def _patch_crawler_boundary(monkeypatch):
    monkeypatch.setattr(spider_module, "_scrapy", SimpleNamespace(Request=FakeRequest))
    monkeypatch.setattr(spider_module, "_scrapy_playwright", SimpleNamespace())


def _zolo_html(*, next_href: str | None = "/vancouver-real-estate/houses?page=2", total: int | None = None) -> bytes:
    next_link = (
        f'<a rel="next" href="{next_href}">Next</a>'
        if next_href is not None
        else ""
    )
    total_node = f'<span data-total-count="{total}">{total} listings</span>' if total else ""
    return f"""
    <html><body>
      {total_node}
      <div class="card-listing">
        <div class="card-listing--location"><span class="street">401 Test Street</span><span class="city">Vancouver</span></div>
        <div class="card-listing--values"><span class="price" itemprop="price">1425000</span> 4 bed 3 bath 2,100 sqft</div>
        <a class="tile-overlay-link" href="/real-estate/401-test-street">details</a>
      </div>
      {next_link}
    </body></html>
    """.encode()


class FakeResponse:
    def __init__(self, url: str, body: bytes, request=None, *, status: int = 200, meta=None):
        self.url = url
        self.body = body
        self.status = status
        self.headers = {"content-type": b"text/html; charset=utf-8"}
        self.request = request
        self.meta = dict(meta or (request.meta if request is not None else {}))


def test_zolo_detail_parser_exception_writes_error_and_preserves_search_row(monkeypatch):
    _patch_crawler_boundary(monkeypatch)
    writer = RecordingWriter()
    spider = FullMarketSpider(_source(), _context(artifact_writer=writer))
    detail_url = "https://www.zolo.ca/real-estate/401-test-street"
    search_row = {"address": "401 Test Street", "source_url": detail_url}
    spider.search_rows[detail_url] = search_row

    def explode(*args, **kwargs):
        raise RuntimeError("parser exploded")

    monkeypatch.setattr(spider_module, "parse_zolo_detail_page", explode)
    response = FakeResponse(
        detail_url,
        b"<html></html>",
        meta={"entrypoint_url": ZOLO_SEARCH_URL, "page_number": 1, "detail_url": detail_url},
    )

    assert list(spider.parse_zolo_detail(response)) == []
    assert writer.calls[-1]["request_kind"] == "DETAIL"
    assert writer.calls[-1]["parse_status"] == "ERROR"
    assert writer.calls[-1]["error_type"] == "DETAIL_PARSE_ERROR"
    assert writer.calls[-1]["rows"] == [search_row]
    assert spider.search_rows[detail_url] == search_row


def test_write_artifact_uses_request_url_and_response_url_for_redirects(monkeypatch):
    _patch_crawler_boundary(monkeypatch)
    writer = RecordingWriter()
    spider = FullMarketSpider(_source(), _context(artifact_writer=writer))
    requested_url = "https://www.zolo.ca/redirected-detail"
    final_url = "https://www.zolo.ca/real-estate/401-test-street"
    request = FakeRequest(requested_url, meta={"entrypoint_url": requested_url, "page_number": 1})

    spider._write_artifact(
        FakeResponse(final_url, b"<html></html>", request=request),
        request_kind="DETAIL",
    )

    assert writer.calls[-1]["requested_url"] == requested_url
    assert writer.calls[-1]["final_url"] == final_url

    spider._write_artifact(FakeResponse(final_url, b"<html></html>"), request_kind="DETAIL")
    assert writer.calls[-1]["requested_url"] == final_url
    assert writer.calls[-1]["final_url"] == final_url


def test_zolo_search_artifact_counts_dom_cards_separately_from_extracted_rows(monkeypatch):
    _patch_crawler_boundary(monkeypatch)
    writer = RecordingWriter()
    spider = FullMarketSpider(_source(), _context(artifact_writer=writer))
    body = _zolo_html(next_href=None).replace(
        b"</body>",
        b"""
        <div class="card-listing">
          <div class="card-listing--location">
            <span class="street">Vancouver</span><span class="city">Vancouver</span>
          </div>
          <div class="card-listing--values">4 bed 3 bath</div>
        </div>
        </body>
        """,
    )
    response = FakeResponse(
        ZOLO_SEARCH_URL,
        body,
        meta={"entrypoint_url": ZOLO_SEARCH_URL, "page_number": 1},
    )

    list(spider.parse_zolo_search(response))

    assert writer.calls[-1]["card_count"] == 2
    assert writer.calls[-1]["extracted_row_count"] == 1


def test_zolo_next_url_prefers_rel_next_and_canonicalizes_same_domain_link():
    html = b'<a rel="next" href="/vancouver-real-estate/houses/page-2/#results">Next</a>'

    assert next_zolo_url(html, ZOLO_SEARCH_URL, {"zolo.ca"}) == (
        "https://www.zolo.ca/vancouver-real-estate/houses/page-2"
    )


def test_zolo_next_url_uses_explicit_next_selector_when_rel_is_absent():
    html = b'<nav><a class="pagination-next" href="/vancouver-real-estate/houses?page=2">Next</a></nav>'

    assert next_zolo_url(html, ZOLO_SEARCH_URL, {"www.zolo.ca"}) == (
        "https://www.zolo.ca/vancouver-real-estate/houses?page=2"
    )


def test_zolo_rel_next_takes_priority_over_explicit_next_selector():
    html = b'''
    <a rel="next" href="/vancouver-real-estate/houses/page-2">rel next</a>
    <a class="pagination-next" href="/vancouver-real-estate/houses/page-3">fallback</a>
    '''

    assert next_zolo_url(html, ZOLO_SEARCH_URL, {"zolo.ca"}).endswith("/page-2")


@pytest.mark.parametrize(
    "href",
    [
        "https://example.com/page-2",
        "javascript:void(0)",
        "#",
        "",
    ],
)
def test_zolo_next_url_rejects_external_or_invalid_link(href: str):
    html = f'<a rel="next" href="{href}">Next</a>'.encode()

    assert next_zolo_url(html, ZOLO_SEARCH_URL, {"zolo.ca"}) is None


def test_realtor_page_advanced_requires_nonempty_changed_identity_set():
    assert realtor_page_advanced({"R1", "R2"}, {"R2", "R3"})
    assert not realtor_page_advanced({"R1", "R2"}, {"R1", "R2"})
    assert not realtor_page_advanced({"R1"}, set())


@pytest.mark.parametrize(
    ("row", "policy", "expected"),
    [
        ({"asking": 1_425_000, "bedrooms": None}, {"max_asking": 1_600_000}, True),
        ({"asking": 1_425_000, "bedrooms": 4}, {"max_asking": 1_600_000}, True),
        ({"asking": 2_500_000, "bedrooms": 2}, {"max_asking": 1_600_000}, False),
        ({"asking": None, "bedrooms": None}, {"max_asking": 1_600_000}, True),
        ({"asking": 1_425_000, "bedrooms": 4, "property_type": "CONDO"}, {"max_asking": 1_600_000}, False),
    ],
)
def test_detail_policy_keeps_unknown_or_plausible_cards_and_skips_clear_mismatch(
    row: dict, policy: dict, expected: bool
):
    assert should_fetch_detail(row, policy) is expected


def test_detail_policy_does_not_discard_over_budget_card_when_bed_count_is_plausible():
    assert should_fetch_detail(
        {"asking": 2_500_000, "bedrooms": 4},
        {"max_asking": 1_600_000, "minimum_bedrooms": 3},
    )


@pytest.mark.parametrize(
    "property_type",
    [
        "RESIDENTIAL",
        "RESIDENTIAL_DETACHED",
        ["RESIDENTIAL", "DETACHED"],
        "RESIDENTIAL / DETACHED",
    ],
)
def test_detail_policy_keeps_unknown_property_types_for_detail_review(property_type):
    assert should_fetch_detail(
        {
            "asking": 1_425_000,
            "bedrooms": 4,
            "property_type": property_type,
        },
        {
            "max_asking": 1_600_000,
            "current_target_property_types": [
                "DETACHED_HOUSE",
                "SINGLE_FAMILY_RESIDENCE",
            ],
            "excluded_current_property_types": [
                "DUPLEX",
                "TRIPLEX",
                "TOWNHOUSE",
                "COACH_HOUSE",
                "STRATA",
                "LEASEHOLD",
            ],
        },
    )


def test_spider_declares_the_required_optional_crawler_settings():
    assert FullMarketSpider.name == "northshore_full_market"
    assert FullMarketSpider.custom_settings["ROBOTSTXT_OBEY"] is True
    assert FullMarketSpider.custom_settings["CONCURRENT_REQUESTS_PER_DOMAIN"] == 1
    assert FullMarketSpider.custom_settings["DOWNLOAD_DELAY"] == 1.0
    assert FullMarketSpider.custom_settings["RETRY_TIMES"] == 2
    assert (
        FullMarketSpider.custom_settings["TWISTED_REACTOR"]
        == "twisted.internet.asyncioreactor.AsyncioSelectorReactor"
    )
    assert FullMarketSpider.custom_settings["DOWNLOAD_HANDLERS"]["https"] == (
        "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler"
    )
    assert FullMarketSpider.custom_settings["PLAYWRIGHT_BROWSER_TYPE"] == "chromium"


def test_research_crawling_package_remains_importable_without_scrapy():
    import northshore_watchdog.research.crawling as crawling

    assert callable(crawling.canonical_url)


def test_spider_initializes_and_starts_only_allowed_zolo_entrypoints(monkeypatch):
    _patch_crawler_boundary(monkeypatch)

    source = _source()
    source["entrypoints"].append({"municipality": "External", "url": "https://example.com/houses"})
    context = _context()
    spider = FullMarketSpider(source, context)

    requests = list(spider.start_requests())

    assert spider.source == source
    assert spider.context == context
    assert spider.allowed_domains == ["www.zolo.ca", "zolo.ca"]
    assert set(spider.state) >= {
        "seen_page_urls",
        "page_fingerprints",
        "route_completion_states",
        "detail_request_keys",
    }
    assert len(requests) == 1
    request = requests[0]
    assert request.url == ZOLO_SEARCH_URL
    assert request.callback.__name__ == "parse_zolo_search"
    assert request.meta == {
        "source_name": "zolo-full-market",
        "source_registry_id": "source:zolo-full-market",
        "request_kind": "SEARCH",
        "page_number": 1,
        "run_id": "run-1",
        "playwright": True,
        "entrypoint_url": ZOLO_SEARCH_URL,
        "handle_httpstatus_list": [401, 403, 429],
    }


def test_spider_supports_scrapy_213_plus_async_start(monkeypatch):
    _patch_crawler_boundary(monkeypatch)

    spider = FullMarketSpider(_source(), _context())

    async def collect_requests():
        return [request async for request in spider.start()]

    requests = asyncio.run(collect_requests())

    assert len(requests) == 1
    assert requests[0].url == ZOLO_SEARCH_URL
    assert requests[0].meta["request_kind"] == "SEARCH"


@pytest.mark.parametrize("writer_keyword", ["artifact_writer", "writer"])
def test_spider_prefers_explicit_writer_kwargs_over_context_without_forwarding(
    monkeypatch, writer_keyword
):
    _patch_crawler_boundary(monkeypatch)
    context_writer = RecordingWriter()
    explicit_writer = RecordingWriter()

    spider = FullMarketSpider(
        _source(),
        _context(artifact_writer=context_writer),
        **{writer_keyword: explicit_writer},
    )

    assert spider.artifact_writer is explicit_writer


def test_zolo_detail_cap_marks_partial_when_candidate_cannot_be_scheduled(monkeypatch):
    _patch_crawler_boundary(monkeypatch)
    writer = RecordingWriter()
    spider = FullMarketSpider(
        _source(),
        _context(
            artifact_writer=writer,
            crawl={"max_search_pages": 20, "max_detail_requests": 0},
        ),
    )
    response = FakeResponse(
        ZOLO_SEARCH_URL,
        _zolo_html(next_href=None),
        meta={"entrypoint_url": ZOLO_SEARCH_URL, "page_number": 1},
    )

    assert list(spider.parse_zolo_search(response)) == []
    assert writer.calls[-1]["coverage_state"] == "PARTIAL_CAP_REACHED"
    assert spider.state["route_completion_states"][ZOLO_SEARCH_URL] == "PARTIAL_CAP_REACHED"


def test_zolo_detail_cap_marks_partial_when_limit_is_reached(monkeypatch):
    _patch_crawler_boundary(monkeypatch)
    writer = RecordingWriter()
    spider = FullMarketSpider(
        _source(),
        _context(
            artifact_writer=writer,
            crawl={"max_search_pages": 20, "max_detail_requests": 1},
        ),
    )
    response = FakeResponse(
        ZOLO_SEARCH_URL,
        _zolo_html(next_href=None),
        meta={"entrypoint_url": ZOLO_SEARCH_URL, "page_number": 1},
    )

    yielded = list(spider.parse_zolo_search(response))

    assert [item.meta["request_kind"] for item in yielded] == ["DETAIL"]
    assert writer.calls[-1]["coverage_state"] == "PARTIAL_CAP_REACHED"
    assert spider.state["route_completion_states"][ZOLO_SEARCH_URL] == "PARTIAL_CAP_REACHED"


def test_full_market_spider_has_clear_error_without_crawler_extra():
    if _crawler_extra_available():
        pytest.skip("only exercises the missing optional crawler extra")

    with pytest.raises(
        RuntimeError,
        match=r"^Scrapy crawler extra is not installed; install the crawler extra$",
    ):
        FullMarketSpider(_source(), _context())


def test_spider_state_snapshot_is_json_serializable_and_restorable():
    spider = FullMarketSpider.__new__(FullMarketSpider)
    spider.state = {
        "seen_page_urls": [ZOLO_SEARCH_URL],
        "page_fingerprints": ["a" * 64],
        "route_completion_states": {ZOLO_SEARCH_URL: "IN_PROGRESS"},
        "detail_request_keys": ["url:https://www.zolo.ca/real-estate/401-test-street"],
    }

    snapshot = spider.state_snapshot()
    assert json.loads(json.dumps(snapshot)) == snapshot

    spider.state = {}
    spider.restore_state(snapshot)
    assert spider.state == snapshot


def test_initial_requests_carry_required_meta_and_allowed_domains():
    _require_crawler_extra()
    spider = FullMarketSpider(_source(), _context())

    requests = list(spider.start_requests())

    assert len(requests) == 1
    request = requests[0]
    assert request.url == ZOLO_SEARCH_URL
    assert set(spider.allowed_domains) == {"zolo.ca", "www.zolo.ca"}
    assert {
        "source_name",
        "source_registry_id",
        "request_kind",
        "page_number",
        "run_id",
    } <= request.meta.keys()
    assert request.meta["request_kind"] == "SEARCH"
    assert request.meta["page_number"] == 1
    assert request.meta["run_id"] == "run-1"
    assert request.meta["playwright"] is True


def test_realtor_initial_request_enables_playwright_page():
    _require_crawler_extra()
    spider = FullMarketSpider(_source("realtor-full-market"), _context())

    request = next(spider.start_requests())

    assert request.url == REALTOR_SEARCH_URL
    assert request.meta["playwright"] is True
    assert request.meta["playwright_include_page"] is True
    assert request.meta["request_kind"] == "SEARCH"
    assert set(spider.allowed_domains) == {"realtor.ca", "www.realtor.ca"}


def test_zolo_search_yields_deduplicated_detail_and_one_unseen_next_request():
    scrapy = _require_crawler_extra()
    writer = RecordingWriter()
    spider = FullMarketSpider(
        _source(),
        _context(artifact_writer=writer),
    )
    request = next(spider.start_requests())
    response = scrapy.http.TextResponse(
        url=ZOLO_SEARCH_URL,
        body=_zolo_html(),
        encoding="utf-8",
        request=request,
    )

    yielded = list(spider.parse_zolo_search(response))
    detail_requests = [item for item in yielded if item.meta["request_kind"] == "DETAIL"]
    next_requests = [item for item in yielded if item.meta["request_kind"] == "SEARCH"]

    assert len(detail_requests) == 1
    assert detail_requests[0].url == "https://www.zolo.ca/real-estate/401-test-street"
    assert detail_requests[0].meta["playwright"] is True
    assert len(next_requests) == 1
    assert next_requests[0].url.endswith("?page=2")
    assert [call["request_kind"] for call in writer.calls] == ["SEARCH"]
    assert spider.state["detail_request_keys"]

    repeated = list(spider.parse_zolo_search(response))
    assert repeated == []
    assert writer.calls[-1]["coverage_state"] == "REPEATED_PAGE"


def test_zolo_search_honours_search_page_cap_and_writes_partial_coverage():
    scrapy = _require_crawler_extra()
    writer = RecordingWriter()
    spider = FullMarketSpider(
        _source(),
        _context(artifact_writer=writer, crawl={"max_search_pages": 1, "max_detail_requests": 20}),
    )
    request = next(spider.start_requests())
    response = scrapy.http.TextResponse(
        url=ZOLO_SEARCH_URL,
        body=_zolo_html(),
        encoding="utf-8",
        request=request,
    )

    yielded = list(spider.parse_zolo_search(response))

    assert [item.meta["request_kind"] for item in yielded] == ["DETAIL"]
    assert writer.calls[-1]["coverage_state"] == "PARTIAL_CAP_REACHED"
    assert spider.state["route_completion_states"][ZOLO_SEARCH_URL] == "PARTIAL_CAP_REACHED"


def test_zolo_challenge_is_an_explicit_blocked_artifact_not_natural_completion():
    scrapy = _require_crawler_extra()
    writer = RecordingWriter()
    spider = FullMarketSpider(_source(), _context(artifact_writer=writer))
    request = next(spider.start_requests())
    response = scrapy.http.TextResponse(
        url=ZOLO_SEARCH_URL,
        body=b"<html><body><h1>Verify you are human</h1></body></html>",
        encoding="utf-8",
        request=request,
    )

    assert list(spider.parse_zolo_search(response)) == []
    assert writer.calls[-1]["coverage_state"] == "BLOCKED_POLICY"
    assert writer.calls[-1]["error_type"] == "CHALLENGE_DETECTED"
    assert spider.state["route_completion_states"][ZOLO_SEARCH_URL] == "BLOCKED_POLICY"


def test_zolo_challenge_is_checked_before_duplicate_body_for_a_new_url(monkeypatch):
    _patch_crawler_boundary(monkeypatch)
    writer = RecordingWriter()
    spider = FullMarketSpider(_source(), _context(artifact_writer=writer))
    challenge_body = b"<html><body><h1>Verify you are human</h1></body></html>"
    first_response = FakeResponse(
        ZOLO_SEARCH_URL,
        challenge_body,
        meta={"entrypoint_url": ZOLO_SEARCH_URL, "page_number": 1},
    )
    second_url = "https://www.zolo.ca/vancouver-real-estate/houses?page=2"
    second_response = FakeResponse(
        second_url,
        challenge_body,
        meta={"entrypoint_url": second_url, "page_number": 2},
    )

    assert list(spider.parse_zolo_search(first_response)) == []
    assert list(spider.parse_zolo_search(second_response)) == []
    assert writer.calls[-1]["coverage_state"] == "BLOCKED_POLICY"
    assert writer.calls[-1]["error_type"] == "CHALLENGE_DETECTED"
    assert spider.state["route_completion_states"][second_url] == "BLOCKED_POLICY"


def test_realtor_repeated_page_records_gap_and_closes_page():
    _require_crawler_extra()
    fixture_dir = Path(__file__).parent / "fixtures" / "crawl"
    first_body = (fixture_dir / "realtor-search-page-1.html").read_bytes()
    repeated_body = (fixture_dir / "realtor-repeated-page.html").read_bytes()
    writer = RecordingWriter()

    class FakeControl:
        def __init__(self, page):
            self.page = page

        async def is_visible(self):
            return True

        async def get_attribute(self, name):
            return "#" if name == "href" else None

        async def click(self):
            self.page.body = repeated_body

    class FakePage:
        def __init__(self):
            self.body = first_body
            self.url = REALTOR_SEARCH_URL
            self.closed = False
            self.control = FakeControl(self)

        async def content(self):
            return self.body.decode()

        def locator(self, selector):
            return self.control

        async def close(self):
            self.closed = True

    spider = FullMarketSpider(
        _source("realtor-full-market"),
        _context(artifact_writer=writer),
    )
    request = next(spider.start_requests())
    page = FakePage()
    response = SimpleNamespace(
        url=REALTOR_SEARCH_URL,
        body=first_body,
        status=200,
        headers={"content-type": b"text/html"},
        request=request,
        meta={**request.meta, "playwright_page": page},
    )

    yielded = asyncio.run(spider.parse_realtor_search(response))

    assert page.closed is True
    assert yielded
    assert all(item.meta["request_kind"] == "DETAIL" for item in yielded)
    assert any(call["coverage_state"] == "PAGINATION_NOT_ADVANCED" for call in writer.calls)
    assert "PAGINATION_NOT_ADVANCED" in spider.state["route_completion_states"].values()


def test_realtor_page_close_is_finally_run_when_parser_raises(monkeypatch):
    _require_crawler_extra()

    class FakePage:
        url = REALTOR_SEARCH_URL
        closed = False

        async def content(self):
            raise ValueError("page content exploded")

        async def close(self):
            self.closed = True

    spider = FullMarketSpider(_source("realtor-full-market"), _context())
    request = next(spider.start_requests())
    page = FakePage()
    response = SimpleNamespace(
        url=REALTOR_SEARCH_URL,
        body=b"<html></html>",
        status=200,
        headers={},
        request=request,
        meta={**request.meta, "playwright_page": page},
    )

    with pytest.raises(ValueError, match="page content exploded"):
        asyncio.run(spider.parse_realtor_search(response))

    assert page.closed is True


def test_realtor_async_callback_is_testable_without_crawler_extra(monkeypatch):
    class FakeRequest:
        def __init__(self, url, *, callback=None, meta=None, **kwargs):
            self.url = url
            self.callback = callback
            self.meta = dict(meta or {})
            self.kwargs = kwargs

    monkeypatch.setattr(spider_module, "_scrapy", SimpleNamespace(Request=FakeRequest))
    monkeypatch.setattr(spider_module, "_scrapy_playwright", SimpleNamespace())
    fixture_dir = Path(__file__).parent / "fixtures" / "crawl"
    first_body = (fixture_dir / "realtor-search-page-1.html").read_bytes()
    repeated_body = (fixture_dir / "realtor-repeated-page.html").read_bytes()
    writer = RecordingWriter()

    class FakeControl:
        async def is_visible(self):
            return True

        async def get_attribute(self, name):
            return "#" if name == "href" else None

        async def click(self):
            page.body = repeated_body

    class FakePage:
        def __init__(self):
            self.body = first_body
            self.url = REALTOR_SEARCH_URL
            self.closed = False
            self.control = FakeControl()

        async def content(self):
            return self.body.decode()

        def locator(self, selector):
            return self.control

        async def close(self):
            self.closed = True

    spider = FullMarketSpider(
        _source("realtor-full-market"),
        _context(artifact_writer=writer),
    )
    request = next(spider.start_requests())
    page = FakePage()
    response = SimpleNamespace(
        url=REALTOR_SEARCH_URL,
        body=first_body,
        status=200,
        headers={"content-type": b"text/html"},
        request=request,
        meta={**request.meta, "playwright_page": page},
    )

    yielded = asyncio.run(spider.parse_realtor_search(response))

    assert page.closed is True
    assert len(yielded) == 1
    assert yielded[0].meta["request_kind"] == "DETAIL"
    assert any(
        call["coverage_state"] == "PAGINATION_NOT_ADVANCED"
        for call in writer.calls
    )


def test_realtor_hidden_next_records_pagination_gap_after_search_rows(monkeypatch):
    _patch_crawler_boundary(monkeypatch)
    fixture_dir = Path(__file__).parent / "fixtures" / "crawl"
    body = (fixture_dir / "realtor-search-page-1.html").read_bytes()
    writer = RecordingWriter()

    class FakeControl:
        async def is_visible(self):
            return False

        async def get_attribute(self, name):
            return "/page-2" if name == "href" else None

        async def click(self):
            raise AssertionError("hidden pagination control must not be clicked")

    class FakePage:
        url = REALTOR_SEARCH_URL

        def __init__(self):
            self.closed = False

        async def content(self):
            return body.decode()

        def locator(self, selector):
            return FakeControl()

        async def close(self):
            self.closed = True

    spider = FullMarketSpider(
        _source("realtor-full-market"),
        _context(artifact_writer=writer),
    )
    request = FakeRequest(
        REALTOR_SEARCH_URL,
        meta=spider._request_meta(
            request_kind="SEARCH",
            page_number=1,
            entrypoint_url=REALTOR_SEARCH_URL,
        ),
    )
    page = FakePage()
    response = SimpleNamespace(
        url=REALTOR_SEARCH_URL,
        body=body,
        status=200,
        headers={"content-type": b"text/html"},
        request=request,
        meta={**request.meta, "playwright_page": page},
    )

    yielded = asyncio.run(spider.parse_realtor_search(response))

    assert page.closed is True
    assert [item.meta["request_kind"] for item in yielded] == ["DETAIL"]
    assert writer.calls[-1]["coverage_state"] == "PAGINATION_NOT_ADVANCED"
    assert writer.calls[-1]["error_type"] == "PAGINATION_NOT_ADVANCED"
    assert spider.state["route_completion_states"][REALTOR_SEARCH_URL] == "PAGINATION_NOT_ADVANCED"
