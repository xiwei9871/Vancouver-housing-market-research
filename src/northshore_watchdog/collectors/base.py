from __future__ import annotations

from abc import ABC, abstractmethod

from northshore_watchdog.models import Listing


class Collector(ABC):
    source_name: str

    def __init__(self, config: dict | None = None):
        self.config = config or {}

    @abstractmethod
    def collect(self) -> list[Listing]:
        """Return listings discovered by this source."""
