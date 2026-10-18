from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
from typing import Any

from .crawling.models import CrawlArtifact
from .crawling.policies import status_from_metrics, summarize_artifacts
from .sources import _BaseSource, SourceRunResult, SourceRunStatus, SourceSnapshot, _timestamp


def snapshot_from_artifact(
    artifact: CrawlArtifact,
    source_registry_id: str,
    observed_at: datetime,
) -> SourceSnapshot:
    retrieved_at = _timestamp(artifact.retrieved_at)
    return SourceSnapshot(
        source_registry_id=source_registry_id,
        url=artifact.requested_url,
        final_url=artifact.final_url,
        request_kind=artifact.request_kind,
        status_code=artifact.status_code if artifact.status_code is not None else 0,
        content_type=artifact.content_type,
        raw_path=artifact.raw_path or "",
        screenshot_path=artifact.screenshot_path,
        sha256=artifact.sha256,
        size_bytes=artifact.size_bytes,
        observed_at=_timestamp(observed_at),
        retrieved_at=retrieved_at,
    )


class CrawlManifestSource(_BaseSource):
    """Read completed crawl artifacts through the normal source contract."""

    def __init__(self, *, manifest_path: str | Path, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.manifest_path = Path(manifest_path)

    def collect(self, observed_at: datetime) -> SourceRunResult:
        started_at = _timestamp(observed_at)
        artifacts: list[CrawlArtifact] = []
        manifest_errors: list[str] = []
        try:
            lines = self.manifest_path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError) as exc:
            return self._result(
                started_at=started_at,
                finished_at=started_at,
                errors=[str(exc)],
                status=SourceRunStatus.FAILED,
            )

        for line_number, line in enumerate(lines, 1):
            if not line.strip():
                continue
            try:
                artifacts.append(CrawlArtifact.model_validate_json(line))
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                manifest_errors.append(f"line {line_number}: {exc}")

        rows = [row for artifact in artifacts for row in artifact.rows]
        normalized, normalize_errors = self._normalize_rows(rows, observed_at)
        expected_entrypoints = self.config.get("expected_entrypoints", [])
        metrics = summarize_artifacts(
            artifacts,
            expected_entrypoints=expected_entrypoints,
        )
        if manifest_errors:
            metrics["errors"] = [*metrics.get("errors", []), *manifest_errors]
        all_errors = [*manifest_errors, *normalize_errors, *metrics.get("errors", [])]
        status_text = status_from_metrics(metrics, normalize_errors or manifest_errors)
        try:
            status = SourceRunStatus(status_text)
        except ValueError:
            status = SourceRunStatus.FAILED

        snapshots = [
            snapshot_from_artifact(artifact, self.source_registry_id, observed_at)
            for artifact in artifacts
            if artifact.raw_path and artifact.status_code is not None
        ]
        timestamps = [artifact.retrieved_at for artifact in artifacts]
        return self._result(
            started_at=min(timestamps, default=started_at),
            finished_at=max(timestamps, default=started_at),
            records=normalized,
            snapshots=snapshots,
            errors=list(dict.fromkeys(all_errors)),
            warnings=metrics.get("warnings", []),
            status=status,
        ).model_copy(update={
            "coverage_complete": metrics.get("coverage_complete", False),
            "crawl_metrics": metrics,
            "coverage_gaps": metrics.get("coverage_gaps", []),
        })


__all__ = ["CrawlManifestSource", "snapshot_from_artifact"]
