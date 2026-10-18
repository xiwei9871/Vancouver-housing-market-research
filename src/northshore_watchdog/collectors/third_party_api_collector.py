from __future__ import annotations

import os

from northshore_watchdog.models import Listing

from .base import Collector


class ThirdPartyApiCollector(Collector):
    source_name = "third_party_api"

    def collect(self) -> list[Listing]:
        token_env = self.config.get("token_env")
        if token_env and not os.getenv(token_env):
            return []
        rows = self.config.get("mock_listings", [])
        return [Listing(source=self.source_name, source_id=f"mock:{idx}", **row) for idx, row in enumerate(rows)]
