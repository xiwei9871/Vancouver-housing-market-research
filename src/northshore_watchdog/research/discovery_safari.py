"""Safari front-document adapter for filtered listing discovery.

The adapter controls only the user-visible Safari tab through Apple Events and
executes read-only DOM operations.  It deliberately does not inspect browser
profiles, cookies, storage, or authentication material.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
import json
import subprocess
import time
from typing import Any

from .discovery_browser import PlaywrightNativeDiscoverySource


class SafariResponse:
    def __init__(self, status: int = 200) -> None:
        self.status = status


class SafariLocator:
    def __init__(self, page: "SafariPage", selector: str) -> None:
        self.page = page
        self.selector = selector

    def count(self) -> int:
        value = self.page.evaluate(
            "document.querySelectorAll(%s).length" % json.dumps(self.selector)
        )
        if value in (None, ""):
            return 0
        try:
            return int(value)
        except (TypeError, ValueError):
            numeric = float(value)
            if not numeric.is_integer():
                raise ValueError(f"Safari selector count is not an integer: {value!r}")
            return int(numeric)

    def input_value(self) -> Any:
        return self.page.evaluate(
            "(document.querySelector(%s) || {}).value || ''" % json.dumps(self.selector)
        )

    def is_enabled(self) -> bool:
        return bool(
            self.page.evaluate(
                """(() => {
                  const visible = element => {
                    const style = getComputedStyle(element);
                    const rect = element.getBoundingClientRect();
                    return style.display !== 'none' &&
                      style.visibility !== 'hidden' &&
                      rect.width > 0 && rect.height > 0;
                  };
                  const element = Array.from(document.querySelectorAll(%s))
                    .find(candidate => visible(candidate));
                  if (!element) return false;
                  return !element.disabled &&
                    element.getAttribute('aria-disabled') !== 'true' &&
                    !element.classList.contains('disabled');
                })()""" % json.dumps(self.selector)
            )
        )

    def click(self) -> None:
        clicked = self.page.evaluate(
            """(() => {
              const visible = element => {
                const style = getComputedStyle(element);
                const rect = element.getBoundingClientRect();
                return style.display !== 'none' &&
                  style.visibility !== 'hidden' &&
                  rect.width > 0 && rect.height > 0;
              };
              const element = Array.from(document.querySelectorAll(%s))
                .find(candidate => visible(candidate));
              if (!element) return false;
              element.click();
              return true;
            })()""" % json.dumps(self.selector)
        )
        if not clicked:
            raise ValueError(f"Safari selector did not match a clickable element: {self.selector}")


class SafariPage:
    """Small page contract shared by the native discovery source and tests."""

    def __init__(
        self,
        *,
        javascript_executor: Callable[[str], Any],
        navigation_executor: Callable[[str], Any],
        navigation_settle_seconds: float = 2.0,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        self._javascript_executor = javascript_executor
        self._navigation_executor = navigation_executor
        self._navigation_settle_seconds = navigation_settle_seconds
        self._sleep = sleep_fn

    @property
    def url(self) -> str:
        return str(self.evaluate("location.href") or "")

    def evaluate(self, script: str) -> Any:
        return self._javascript_executor(script)

    def goto(self, url: str, **_: Any) -> SafariResponse:
        self._navigation_executor(url)
        self._sleep(self._navigation_settle_seconds)
        return SafariResponse()

    def content(self) -> str:
        return str(self.evaluate("document.documentElement.outerHTML") or "")

    def locator(self, selector: str) -> SafariLocator:
        return SafariLocator(self, selector)

    def wait_for_selector(self, selector: str, timeout: int = 30_000, **_: Any) -> None:
        deadline = time.monotonic() + timeout / 1000
        query = "Boolean(document.querySelector(%s))" % json.dumps(selector)
        while time.monotonic() < deadline:
            if self.evaluate(query):
                return
            self._sleep(0.25)
        raise TimeoutError(f"Safari selector did not appear before timeout: {selector}")

    def wait_for_timeout(self, milliseconds: int | float) -> None:
        self._sleep(float(milliseconds) / 1000)

    def close(self) -> None:
        # The source must not close or replace the user's Safari session.
        return None


class SafariNativeDiscoverySource(PlaywrightNativeDiscoverySource):
    """Run the existing filtered discovery contract in the active Safari tab."""

    def _open_playwright_page(self) -> tuple[Any, Any, Any, SafariPage]:
        return (
            None,
            None,
            None,
            SafariPage(
                javascript_executor=_osascript_javascript,
                navigation_executor=_osascript_navigate,
                navigation_settle_seconds=float(self.config.get("navigation_settle_seconds", 4)),
            ),
        )


def _run_osascript(script: str) -> str:
    result = subprocess.run(
        ["osascript", "-e", script],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.rstrip("\n")


def _osascript_javascript(script: str) -> Any:
    value = _run_osascript(
        "tell application \"Safari\" to tell front document to do JavaScript %s"
        % _applescript_string(script)
    )
    if value in {"true", "false"}:
        return value == "true"
    try:
        numeric = float(value)
    except ValueError:
        return value
    if numeric.is_integer():
        return int(numeric)
    return numeric


def _osascript_navigate(url: str) -> None:
    _run_osascript(
        "tell application \"Safari\" to set URL of front document to %s"
        % _applescript_string(url)
    )
    _run_osascript(
        "tell application \"Safari\" to tell front document to do JavaScript %s"
        % _applescript_string("window.location.reload()")
    )


def _applescript_string(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


__all__ = ["SafariNativeDiscoverySource", "SafariPage", "SafariLocator"]
