from __future__ import annotations

from abc import ABC, abstractmethod


class Notifier(ABC):
    def __init__(self, config: dict | None = None):
        self.config = config or {}

    @abstractmethod
    def send(self, payload: dict) -> bool:
        """Send payload and return success."""
