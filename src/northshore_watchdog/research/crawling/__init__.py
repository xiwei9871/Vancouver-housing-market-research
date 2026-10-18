"""Optional-dependency-free crawl manifest primitives.

The package intentionally contains no spider, runner, or command-line code.
Those integrations can use these models and helpers without importing a
crawler framework.
"""

from .artifacts import ArtifactWriter
from .models import CrawlArtifact, CrawlRunSummary
from .policies import (
    canonical_url,
    challenge_detected,
    page_fingerprint,
    status_from_metrics,
    summarize_artifacts,
    url_allowed,
)
from .runner import build_crawl_job_dir, crawl_settings, run_crawl

__all__ = [
    "ArtifactWriter",
    "CrawlArtifact",
    "CrawlRunSummary",
    "build_crawl_job_dir",
    "canonical_url",
    "challenge_detected",
    "crawl_settings",
    "page_fingerprint",
    "run_crawl",
    "status_from_metrics",
    "summarize_artifacts",
    "url_allowed",
]
