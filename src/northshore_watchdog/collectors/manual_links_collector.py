from __future__ import annotations

from northshore_watchdog.models import Listing

from .base import Collector


class ManualLinksCollector(Collector):
    source_name = "manual_links"

    def collect(self) -> list[Listing]:
        listings: list[Listing] = []
        for idx, item in enumerate(self.config.get("links", [])):
            if isinstance(item, str):
                listings.append(Listing(
                    source=self.source_name,
                    source_id=f"manual:{idx}",
                    url=item,
                    address=f"Manual link {idx + 1}",
                    city=self.config.get("default_city", "West Vancouver"),
                ))
            else:
                data = dict(item)
                data["source"] = self.source_name
                data.setdefault("source_id", f"manual:{idx}")
                listings.append(Listing(**data))
        return listings
