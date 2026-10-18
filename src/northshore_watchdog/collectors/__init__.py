"""Collector plugin registry."""

from .base import Collector
from .email_alert_collector import EmailAlertCollector
from .manual_links_collector import ManualLinksCollector
from .official_feed_stub_collector import OfficialFeedStubCollector
from .public_page_detail_collector import PublicPageDetailCollector
from .search_page_snapshot_collector import SearchPageSnapshotCollector
from .third_party_api_collector import ThirdPartyApiCollector

COLLECTOR_REGISTRY = {
    "email_alert": EmailAlertCollector,
    "manual_links": ManualLinksCollector,
    "official_feed_stub": OfficialFeedStubCollector,
    "public_page_detail": PublicPageDetailCollector,
    "search_page_snapshot": SearchPageSnapshotCollector,
    "third_party_api": ThirdPartyApiCollector,
}

__all__ = ["COLLECTOR_REGISTRY", "Collector"]
