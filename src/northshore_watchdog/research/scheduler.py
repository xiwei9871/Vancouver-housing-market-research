from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .pipeline import ResearchRunResult, run_research_pipeline
from ..config import resolve_path


def _aware(value: datetime | None) -> datetime:
    value = value or datetime.now(timezone.utc)
    return value if value.tzinfo and value.utcoffset() is not None else value.replace(tzinfo=timezone.utc)


def _lock_path(config: dict[str, Any]) -> Path:
    configured = config.get("scheduler", {}).get("lock_path")
    if configured:
        return resolve_path(configured, config)
    storage = resolve_path(config.get("storage", {}).get("sqlite_path", "outputs/research_watchdog.sqlite"), config)
    return storage.parent / ".research-weekly.lock"


def _locked_result(observed_at: datetime, lock_path: Path, *, dry_run: bool) -> ResearchRunResult:
    return ResearchRunResult(
        run_id=None,
        run_type="weekly",
        status="LOCKED",
        observed_at=observed_at,
        dry_run=dry_run,
        stage_status={"scheduler": "LOCKED"},
        summary={"locked": True, "lock_path": str(lock_path)},
    )


def run_weekly_scheduled(
    config: dict[str, Any],
    *,
    observed_at: datetime | None = None,
    dry_run: bool = False,
    source_names: set[str] | None = None,
) -> ResearchRunResult:
    """Run one weekly job under an atomic local lock and always release it."""

    timestamp = _aware(observed_at)
    lock_path = _lock_path(config)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return _locked_result(timestamp, lock_path, dry_run=dry_run)
    try:
        os.write(descriptor, f"pid={os.getpid()}\nstarted_at={timestamp.isoformat()}\n".encode("utf-8"))
        os.close(descriptor)
        return run_research_pipeline(
            config,
            run_type="weekly",
            observed_at=timestamp,
            source_names=source_names,
            dry_run=dry_run,
        )
    finally:
        try:
            os.close(descriptor)
        except OSError:
            pass
        lock_path.unlink(missing_ok=True)
