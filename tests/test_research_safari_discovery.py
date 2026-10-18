from __future__ import annotations

from datetime import datetime, timezone

from northshore_watchdog.research.discovery import DiscoveryFilterPlan
from northshore_watchdog.research.discovery_safari import SafariNativeDiscoverySource, SafariPage
from northshore_watchdog.research import discovery_safari


OBSERVED_AT = datetime(2026, 9, 3, 2, 0, tzinfo=timezone.utc)


def test_safari_page_exposes_the_small_browser_contract_without_closing_user_tab():
    state = {
        "url": "https://example.test/start",
        "html": "<html><div class='card'></div></html>",
        "value": "2-0",
        "clicked": False,
    }

    def javascript(script: str):
        if script == "location.href":
            return state["url"]
        if script == "document.documentElement.outerHTML":
            return state["html"]
        if "Boolean(document.querySelector" in script:
            return True
        if ".click()" in script:
            state["clicked"] = True
            return "true"
        if "querySelectorAll" in script:
            return "3.0"
        if ".value" in script:
            return state["value"]
        if "aria-disabled" in script:
            return "true"
        raise AssertionError(f"unexpected JavaScript: {script}")

    def navigate(url: str):
        state["url"] = url

    page = SafariPage(
        javascript_executor=javascript,
        navigation_executor=navigate,
        sleep_fn=lambda _: None,
    )

    response = page.goto("https://example.test/filtered")
    page.wait_for_selector(".card")
    locator = page.locator(".card")

    assert response.status == 200
    assert page.url == "https://example.test/filtered"
    assert page.content() == state["html"]
    assert locator.count() == 3
    assert locator.input_value() == "2-0"
    assert locator.is_enabled() is True
    locator.click()
    page.close()
    assert state["clicked"] is True


def test_safari_locator_targets_the_visible_duplicate_control():
    scripts: list[str] = []

    def javascript(script: str):
        scripts.append(script)
        if "click()" in script and "Array.from" in script:
            return True
        if "aria-disabled" in script and "Array.from" in script:
            return True
        if "querySelectorAll" in script:
            return "3.0"
        raise AssertionError(f"unexpected JavaScript: {script}")

    page = SafariPage(
        javascript_executor=javascript,
        navigation_executor=lambda _: None,
        sleep_fn=lambda _: None,
    )

    locator = page.locator(".next")
    assert locator.is_enabled() is True
    locator.click()

    assert any("Array.from" in script for script in scripts)


def test_safari_page_waits_for_navigation_to_settle_before_reading_old_dom():
    sleeps: list[float] = []
    page = SafariPage(
        javascript_executor=lambda script: "https://example.test/filtered"
        if script == "location.href"
        else True,
        navigation_executor=lambda _: None,
        navigation_settle_seconds=2,
        sleep_fn=sleeps.append,
    )

    page.goto("https://example.test/filtered")

    assert sleeps == [2]


def test_safari_navigation_refreshes_the_new_search_state(monkeypatch):
    scripts: list[str] = []
    monkeypatch.setattr(
        discovery_safari,
        "_run_osascript",
        lambda script: scripts.append(script) or "",
    )

    discovery_safari._osascript_navigate("https://example.test/filtered")

    assert len(scripts) == 2
    assert "set URL of front document" in scripts[0]
    assert "window.location.reload()" in scripts[1]


def test_safari_native_source_reuses_paginated_discovery_logic():
    pages = [
        """
        <div class='result-count'>2 listings</div>
        <article class='listing-card' data-address='101 Safari Street'
          data-price='$1,400,000' data-bedrooms='2'
          data-url='https://example.test/listing/101'>
          <span class='beds'>2</span><span class='baths'>1</span>
          <span class='house-size'>1,250 sqft</span>
        </article>
        """,
        """
        <div class='result-count'>2 listings</div>
        <article class='listing-card' data-address='102 Safari Street'
          data-price='$1,500,000' data-bedrooms='3'
          data-url='https://example.test/listing/102'>
          <span class='beds'>3</span><span class='baths'>2</span>
          <span class='house-size'>1,500 sqft</span>
        </article>
        """,
    ]
    index = 0

    class FakeLocator:
        def count(self):
            return 1 if index < 1 else 0

        def is_enabled(self):
            return index < 1

        def click(self):
            nonlocal index
            index += 1

    class FakePage:
        url = "https://example.test/search"

        def goto(self, url, **kwargs):
            self.url = url
            return type("Response", (), {"status": 200})()

        def content(self):
            return pages[index]

        def locator(self, selector):
            assert selector == ".next"
            return FakeLocator()

        def wait_for_timeout(self, milliseconds):
            assert milliseconds == 0

        def close(self):
            pass

    source = SafariNativeDiscoverySource(
        name="safari-test",
        config={
            "entrypoint": "https://example.test/search",
            "selectors": {"result_count": ".result-count", "card": ".listing-card"},
            "pagination": {
                "enabled": True,
                "next_selector": ".next",
                "max_pages": 5,
                "wait_after_click_ms": 0,
                "progress_poll_interval_ms": 0,
                "progress_timeout_ms": 0,
                "progress_stable_polls": 1,
            },
            "max_result_cards": 10,
        },
        page_factory=FakePage,
    )
    plan = DiscoveryFilterPlan(preset_name="vancouver-broad-scan")

    capture = source.execute(plan, observed_at=OBSERVED_AT)

    assert [card.address for card in capture.cards] == [
        "101 Safari Street",
        "102 Safari Street",
    ]
