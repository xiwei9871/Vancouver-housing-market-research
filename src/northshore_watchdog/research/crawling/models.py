from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class _CrawlModel(BaseModel):
    """Base for manifest records that may outlive the current schema."""

    model_config = ConfigDict(extra="allow")


class CrawlArtifact(_CrawlModel):
    run_id: str
    source_name: str
    source_registry_id: str
    entrypoint_url: str | None = None
    request_kind: Literal["ROBOTS", "SEARCH", "DETAIL"]
    requested_url: str
    final_url: str | None = None
    canonical_url: str | None = None
    page_number: int | None = None
    detail_url: str | None = None
    status_code: int | None = None
    content_type: str | None = None
    raw_path: str | None = None
    screenshot_path: str | None = None
    sha256: str
    size_bytes: int
    retrieved_at: datetime
    parser: str | None = None
    parse_status: str = "NOT_RUN"
    rows: list[dict[str, Any]] = Field(default_factory=list)
    next_url: str | None = None
    card_count: int = 0
    extracted_row_count: int = 0
    coverage_state: str = "IN_PROGRESS"
    skip_reason: str | None = None
    error_type: str | None = None
    retry_count: int = 0


class CrawlRunSummary(_CrawlModel):
    run_id: str
    source_name: str
    started_at: datetime
    finished_at: datetime | None = None
    status: str = "RUNNING"
    coverage_complete: bool = False
    entrypoints: int = 0
    entrypoints_complete: int = 0
    pages_fetched: int = 0
    details_fetched: int = 0
    cards_found: int = 0
    rows_extracted: int = 0
    duplicate_pages: int = 0
    skipped_details: int = 0
    failed_requests: int = 0
    coverage_gaps: list[str] = Field(default_factory=list)
