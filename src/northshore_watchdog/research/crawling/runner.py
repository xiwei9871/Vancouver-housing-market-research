"""Scrapy crawl lifecycle helpers.

The module itself is safe to import without the optional crawler extra.  The
framework and spider imports are deliberately kept behind ``run_crawl`` so
the pure settings and path helpers remain usable by the base package.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import math
from numbers import Real
from pathlib import Path
from typing import Any, Callable, Mapping

from .artifacts import ArtifactWriter
from .models import CrawlArtifact, CrawlRunSummary
from .policies import status_from_metrics, summarize_artifacts


_CRAWLER_EXTRA_ERROR = (
    "Scrapy crawler dependencies are not installed; install .[crawler]"
)
_CRAWL_JOB_DIR_NAME = "scrapy-jobdir"
_ARTIFACT_DIR_NAME = "artifacts"
_SAFETY_CAP_OVERRIDE_KEYS = frozenset(
    {
        "max_search_pages",
        "max_detail_requests",
        "max_requests",
        "max_wall_clock_seconds",
    }
)


def build_crawl_job_dir(
    base_dir: str | Path,
    source_name: str,
    run_id: str,
) -> Path:
    """Create and return the isolated Scrapy JOBDIR for one source/run."""

    path = Path(base_dir) / str(source_name) / str(run_id) / _CRAWL_JOB_DIR_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def _int_setting(value: Any, default: int) -> int:
    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default


def _float_setting(value: Any, default: float) -> float:
    if isinstance(value, bool):
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return parsed if math.isfinite(parsed) else default


def crawl_settings(
    crawl_config: Mapping[str, Any] | None,
    job_dir: str | Path,
) -> dict[str, Any]:
    """Build Scrapy/scrapy-playwright settings without importing either one."""

    config = crawl_config if isinstance(crawl_config, Mapping) else {}
    concurrency = max(1, _int_setting(config.get("concurrency_per_domain", 1), 1))
    delay = max(0.0, _float_setting(config.get("delay_seconds", 1.0), 1.0))
    retries = max(0, _int_setting(config.get("max_retries", 2), 2))
    timeout_seconds = max(
        0.0,
        _float_setting(config.get("timeout_seconds", 45), 45.0),
    )
    browser = str(config.get("browser", "chromium") or "chromium")

    return {
        "ROBOTSTXT_OBEY": bool(config.get("respect_robots", True)),
        # Let scrapy-playwright use the browser's own UA unless a source
        # explicitly opts into a configured value.  Sending Scrapy's default
        # UA alongside a browser-rendered request can produce inconsistent
        # client fingerprints at otherwise public endpoints.
        "USER_AGENT": config.get("user_agent") if "user_agent" in config else None,
        # Scrapy 2.18 asserts that robots.txt evaluation has a non-null UA.
        # Keep robots compliance enabled while target requests use Chromium's
        # native UA; '*' evaluates the site's generic robots policy.
        "ROBOTSTXT_USER_AGENT": str(config.get("robots_user_agent") or "*"),
        "CONCURRENT_REQUESTS_PER_DOMAIN": concurrency,
        "DOWNLOAD_DELAY": delay,
        "RETRY_TIMES": retries,
        "JOBDIR": str(job_dir),
        "TWISTED_REACTOR": "twisted.internet.asyncioreactor.AsyncioSelectorReactor",
        "DOWNLOAD_HANDLERS": {
            "http": "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler",
            "https": "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler",
        },
        "PLAYWRIGHT_BROWSER_TYPE": browser,
        "PLAYWRIGHT_LAUNCH_OPTIONS": {
            "headless": bool(config.get("headless", True)),
        },
        "PLAYWRIGHT_DEFAULT_NAVIGATION_TIMEOUT": int(timeout_seconds * 1000),
        "LOG_ENABLED": False,
    }


def _load_crawler_dependencies() -> tuple[Any, Any]:
    """Import optional crawler dependencies at the actual crawl boundary."""

    try:
        import scrapy  # noqa: F401
        from scrapy.crawler import CrawlerProcess
        import scrapy_playwright  # noqa: F401

        from .spider import FullMarketSpider
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise RuntimeError(_CRAWLER_EXTRA_ERROR) from exc
    return CrawlerProcess, FullMarketSpider


def _matching_source(config: Mapping[str, Any], source_name: str) -> Mapping[str, Any]:
    acquisition = config.get("acquisition", {})
    if not isinstance(acquisition, Mapping):
        raise ValueError("config acquisition must be a mapping")
    sources = acquisition.get("crawl_sources", ())
    if not isinstance(sources, (list, tuple)):
        raise ValueError("config acquisition.crawl_sources must be a list")
    for source in sources:
        if isinstance(source, Mapping) and source.get("name") == source_name:
            return source
    raise ValueError(f"crawl source not found: {source_name}")


def _base_crawl_config(config: Mapping[str, Any]) -> dict[str, Any]:
    acquisition = config.get("acquisition", {})
    crawl = acquisition.get("crawl", {}) if isinstance(acquisition, Mapping) else {}
    return deepcopy(dict(crawl)) if isinstance(crawl, Mapping) else {}


def _effective_crawl_config(
    config: Mapping[str, Any],
    overrides: Mapping[str, Any] | None,
) -> dict[str, Any]:
    effective = _base_crawl_config(config)
    if overrides is None:
        return effective
    if not isinstance(overrides, Mapping):
        raise TypeError("crawl overrides must be a mapping")

    unknown = set(overrides) - _SAFETY_CAP_OVERRIDE_KEYS
    if unknown:
        names = ", ".join(sorted(str(value) for value in unknown))
        raise ValueError(f"unsupported crawl override(s): {names}")
    for key, value in overrides.items():
        if isinstance(value, bool) or not isinstance(value, Real):
            raise TypeError(f"crawl override {key!r} must be numeric")
        numeric = float(value)
        if not math.isfinite(numeric) or numeric < 0:
            raise ValueError(f"crawl override {key!r} must be finite and non-negative")
        effective[key] = value
    return effective


def _entrypoint_urls(source: Mapping[str, Any]) -> list[str]:
    values = source.get("entrypoints", ())
    if not isinstance(values, (list, tuple)):
        return []
    return [
        str(item["url"])
        for item in values
        if isinstance(item, Mapping) and item.get("url")
    ]


def _read_manifest(path: Path) -> tuple[list[CrawlArtifact], list[str]]:
    if not path.exists():
        return [], []
    artifacts: list[CrawlArtifact] = []
    errors: list[str] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            artifacts.append(CrawlArtifact.model_validate_json(line))
        except Exception:
            errors.append(f"MANIFEST_INVALID_LINE_{line_number}")
    return artifacts, errors


def _zero_data_policy_block(metrics: Mapping[str, Any]) -> bool:
    values = [
        *metrics.get("errors", []),
        *metrics.get("coverage_gaps", []),
    ]
    markers = (
        "BLOCKED",
        "CHALLENGE",
        "CAPTCHA",
        "ROBOTS",
        "DISALLOWED",
        "HTTP_401",
        "HTTP_403",
        "HTTP_429",
        "POLICY",
    )
    has_policy_marker = any(
        marker in str(value).upper()
        for value in values
        for marker in markers
    )
    return has_policy_marker and not any(
        int(metrics.get(key, 0) or 0) > 0
        for key in ("details_fetched", "cards_found", "rows_extracted")
    )


def _has_transport_failure(artifacts: list[CrawlArtifact]) -> bool:
    """Keep policy blocks separate from HTTP/runtime request failures."""

    return any(
        artifact.status_code is None
        or isinstance(artifact.status_code, bool)
        or not 200 <= artifact.status_code < 300
        for artifact in artifacts
    )


def _summary_payload(
    *,
    run_id: str,
    source_name: str,
    started_at: datetime,
    finished_at: datetime,
    status: str,
    metrics: Mapping[str, Any],
    run_dir: Path,
    job_dir: Path,
    artifact_dir: Path,
    manifest_path: Path,
    summary_path: Path,
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "source_name": source_name,
        "started_at": started_at,
        "finished_at": finished_at,
        "status": status,
        "coverage_complete": status == "SUCCESS" and bool(metrics.get("coverage_complete")),
        "entrypoints": int(metrics.get("entrypoints", 0) or 0),
        "entrypoints_complete": int(metrics.get("entrypoints_complete", 0) or 0),
        "pages_fetched": int(metrics.get("pages_fetched", 0) or 0),
        "details_fetched": int(metrics.get("details_fetched", 0) or 0),
        "cards_found": int(metrics.get("cards_found", 0) or 0),
        "rows_extracted": int(metrics.get("rows_extracted", 0) or 0),
        "duplicate_pages": int(metrics.get("duplicate_pages", 0) or 0),
        "skipped_details": int(metrics.get("skipped_details", 0) or 0),
        "failed_requests": int(metrics.get("failed_requests", 0) or 0),
        "coverage_gaps": list(metrics.get("coverage_gaps", [])),
        "errors": list(metrics.get("errors", [])),
        "warnings": list(metrics.get("warnings", [])),
        "cap_reached": bool(metrics.get("cap_reached")),
        "run_dir": str(run_dir),
        "job_dir": str(job_dir),
        "artifact_dir": str(artifact_dir),
        "manifest_path": str(manifest_path),
        "summary_path": str(summary_path),
    }


def run_crawl(
    config: Mapping[str, Any],
    source_name: str,
    run_id: str,
    project_root: str | Path,
    crawler_process_factory: Callable[[Mapping[str, Any]], Any] | None = None,
    overrides: Mapping[str, Any] | None = None,
) -> CrawlRunSummary:
    """Run one configured crawl and persist a machine-readable run summary."""

    if not isinstance(config, Mapping):
        raise TypeError("crawl config must be a mapping")
    source = _matching_source(config, source_name)
    effective_crawl = _effective_crawl_config(config, overrides)

    crawl_root = Path(project_root) / "data" / "research" / "crawl_jobs"
    run_dir = crawl_root / str(source_name) / str(run_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    job_dir = build_crawl_job_dir(crawl_root, source_name, run_id)
    artifact_dir = run_dir / _ARTIFACT_DIR_NAME
    artifact_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = artifact_dir / "manifest.jsonl"
    summary_path = artifact_dir / "summary.json"

    source_registry_id = str(
        source.get("source_registry_id") or f"source:{source_name}"
    )
    writer = ArtifactWriter(
        artifact_dir,
        source_name=source_name,
        run_id=run_id,
        source_registry_id=source_registry_id,
    )
    started_at = datetime.now(timezone.utc)
    settings = crawl_settings(effective_crawl, job_dir)
    context = {
        "artifact_writer": writer,
        "run_id": run_id,
        "observed_at": started_at,
        "crawl": effective_crawl,
        "overrides": dict(overrides or {}),
    }

    try:
        crawler_process_cls, spider_cls = _load_crawler_dependencies()
    except ImportError as exc:
        raise RuntimeError(_CRAWLER_EXTRA_ERROR) from exc
    process_error: Exception | None = None
    try:
        process = (
            crawler_process_factory(settings)
            if crawler_process_factory is not None
            else crawler_process_cls(settings)
        )
        process.crawl(spider_cls, source, context)
        process.start()
    except Exception as exc:  # The run still gets a durable FAILED summary.
        process_error = exc

    finished_at = datetime.now(timezone.utc)
    artifacts, manifest_errors = _read_manifest(manifest_path)
    metrics = summarize_artifacts(
        artifacts,
        expected_entrypoints=_entrypoint_urls(source),
    )
    if manifest_errors:
        metrics["errors"] = [
            *metrics.get("errors", []),
            *manifest_errors,
        ]
    if process_error is not None:
        metrics["errors"] = [
            *metrics.get("errors", []),
            "RUNNER_ERROR",
        ]
        metrics["coverage_complete"] = False

    status = status_from_metrics(metrics, [])
    if process_error is not None or manifest_errors:
        status = "FAILED"
    elif _zero_data_policy_block(metrics):
        status = "BLOCKED_POLICY"
        if not _has_transport_failure(artifacts):
            metrics["failed_requests"] = 0

    summary = CrawlRunSummary.model_validate(
        _summary_payload(
            run_id=run_id,
            source_name=source_name,
            started_at=started_at,
            finished_at=finished_at,
            status=status,
            metrics=metrics,
            run_dir=run_dir,
            job_dir=job_dir,
            artifact_dir=artifact_dir,
            manifest_path=manifest_path,
            summary_path=summary_path,
        )
    )
    summary_path.write_text(summary.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return summary


__all__ = [
    "build_crawl_job_dir",
    "crawl_settings",
    "run_crawl",
]
