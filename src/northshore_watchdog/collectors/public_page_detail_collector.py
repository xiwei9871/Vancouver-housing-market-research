from __future__ import annotations

import random
import re
import time
import urllib.robotparser
from collections import defaultdict
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from northshore_watchdog.models import Listing

from .base import Collector


STOP_PATTERNS = ["captcha", "cloudflare", "challenge", "verify you are human"]


class PublicPageDetailCollector(Collector):
    source_name = "public_page_detail"

    def __init__(self, config: dict | None = None):
        super().__init__(config)
        self.stopped_domains: set[str] = set()
        self.domain_counts: dict[str, int] = defaultdict(int)

    def collect(self) -> list[Listing]:
        if not self.config.get("enabled", False):
            return []
        listings: list[Listing] = []
        for idx, url in enumerate(self.config.get("urls", [])):
            details = self.fetch_public_detail(url)
            if details:
                details.setdefault("address", f"Public detail {idx + 1}")
                details.setdefault("city", self.config.get("default_city", "West Vancouver"))
                listings.append(Listing(source=self.source_name, source_id=url, url=url, **details))
        return listings

    def fetch_public_detail(self, url: str) -> dict | None:
        domain = urlparse(url).netloc
        if not domain or domain in self.stopped_domains:
            return None
        max_daily = int(self.config.get("max_requests_per_domain_per_day", 20))
        if self.domain_counts[domain] >= max_daily:
            return None
        if not self._robots_allows(url):
            self.stopped_domains.add(domain)
            return None
        delay = random.uniform(float(self.config.get("min_delay_seconds", 30)), float(self.config.get("max_delay_seconds", 90)))
        if self.config.get("sleep_before_request", True):
            time.sleep(delay)
        timeout = float(self.config.get("timeout_seconds", 10))
        try:
            with httpx.Client(timeout=timeout, follow_redirects=True) as client:
                response = client.get(url, headers={"User-Agent": self.config.get("user_agent", "northshore-property-watchdog/0.1")})
        except httpx.HTTPError:
            return None
        self.domain_counts[domain] += 1
        if response.status_code in {403, 429}:
            self.stopped_domains.add(domain)
            return None
        body_lower = response.text.lower()
        if any(pattern in body_lower for pattern in STOP_PATTERNS):
            self.stopped_domains.add(domain)
            return None
        return self._parse_detail(response.text)

    def _robots_allows(self, url: str) -> bool:
        parsed = urlparse(url)
        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
        parser = urllib.robotparser.RobotFileParser()
        try:
            parser.set_url(robots_url)
            parser.read()
            return parser.can_fetch(self.config.get("user_agent", "northshore-property-watchdog/0.1"), url)
        except Exception:
            return False

    def _parse_detail(self, html: str) -> dict:
        soup = BeautifulSoup(html, "html.parser")
        text = soup.get_text(" ")
        return {
            "description": re.sub(r"\s+", " ", text).strip()[:1500],
        }
