#!/usr/bin/env python3
"""Acquire and optionally classify the saved Strategy V2 photo manifests.

This command works only from manifests already produced by the local
REALTOR.ca detail replay.  It is intentionally separate from the Phase 2A
screening run so a new image-byte snapshot cannot overwrite the URL-only
baseline.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
import sys
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from northshore_watchdog.research.strategy_v2_photo_acquisition import (  # noqa: E402
    DEFAULT_MAX_BYTES,
    DEFAULT_PERCEPTUAL_DISTANCE,
    DEFAULT_USER_AGENT,
    acquire_photo_manifest,
)
from northshore_watchdog.research.strategy_v2_photo_classification import (  # noqa: E402
    JsonHttpVisionClassifier,
    classify_photo_manifest,
)
from northshore_watchdog.research.strategy_v2_photo_intelligence import PhotoManifest  # noqa: E402


DEFAULT_MANIFEST_ROOT = ROOT / "data/research/raw/photos"
DEFAULT_OUTPUT_ROOT = ROOT / "data/research/raw/photo-assets"
DEFAULT_REPORT_ROOT = ROOT / "reports/strategy-v2-photo-acquisition"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _safe_key(value: str) -> str:
    safe = "".join(char.lower() if char.isalnum() else "-" for char in str(value))
    return "-".join(part for part in safe.split("-") if part)[:180] or "unknown-property"


def _read_manifest(path: Path) -> PhotoManifest:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return PhotoManifest.model_validate(payload)


def _write_manifest(path: Path, manifest: PhotoManifest) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(manifest.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _classifier_from_options(
    *,
    endpoint: str | None,
    api_key_env: str,
    model: str | None,
    timeout: float,
    wire_api: str = "responses",
    max_attempts: int = 3,
    retry_backoff_seconds: float = 1.0,
) -> JsonHttpVisionClassifier | None:
    if not endpoint or not model:
        return None
    api_key = os.environ.get(api_key_env, "")
    if not api_key:
        return None
    return JsonHttpVisionClassifier(
        endpoint=endpoint,
        api_key=api_key,
        model=model,
        timeout=timeout,
        wire_api=wire_api,
        max_attempts=max_attempts,
        retry_backoff_seconds=retry_backoff_seconds,
    )


def run_photo_acquisition(
    *,
    manifest_root: str | Path = DEFAULT_MANIFEST_ROOT,
    output_root: str | Path = DEFAULT_OUTPUT_ROOT,
    report_root: str | Path = DEFAULT_REPORT_ROOT,
    output_date: str | None = None,
    max_properties: int | None = None,
    fetcher: Any = None,
    classifier: Any = None,
    timeout: float = 20.0,
    user_agent: str = DEFAULT_USER_AGENT,
    max_bytes: int = DEFAULT_MAX_BYTES,
    perceptual_distance: int = DEFAULT_PERCEPTUAL_DISTANCE,
    max_workers: int = 4,
) -> dict[str, Any]:
    """Run acquisition for all saved manifests, with optional bounded limit."""

    manifest_directory = Path(manifest_root)
    output_directory = Path(output_root)
    report_directory = Path(report_root) / (output_date or _now().date().isoformat())
    paths = sorted(manifest_directory.glob("*/manifest.json"))
    if max_properties is not None:
        paths = paths[:max(0, max_properties)]
    generated_at = _now().isoformat()
    def _process(manifest_path: Path) -> dict[str, Any]:
        try:
            manifest = _read_manifest(manifest_path)
            property_directory = output_directory / _safe_key(manifest.property_key)
            # A rerun of the same snapshot can resume from completed local
            # assets.  Only use the prior output when its gallery cardinality
            # and source URLs still match the input manifest; a changed
            # listing creates a fresh acquisition path instead of silently
            # mixing galleries.
            existing_manifest_path = property_directory / "manifest.json"
            if existing_manifest_path.is_file():
                try:
                    existing = _read_manifest(existing_manifest_path)
                    input_urls = [str(item.source_url or "") for item in manifest.images]
                    existing_urls = [str(item.source_url or "") for item in existing.images]
                    if input_urls == existing_urls:
                        manifest = existing
                except (OSError, UnicodeError, TypeError, ValueError):
                    pass
            enriched, _acquisition = acquire_photo_manifest(
                manifest,
                property_directory,
                fetcher=fetcher,
                retrieved_at=generated_at,
                timeout=timeout,
                user_agent=user_agent,
                max_bytes=max_bytes,
                perceptual_distance=perceptual_distance,
            )
            enriched, visual = classify_photo_manifest(enriched, classifier=classifier)
            output_manifest_path = property_directory / "manifest.json"
            _write_manifest(output_manifest_path, enriched)
            return {
                "property_key": manifest.property_key,
                "input_manifest": str(manifest_path),
                "output_manifest": str(output_manifest_path),
                "acquisition_status": enriched.acquisition_status,
                "visual_status": enriched.visual_status,
                "photo_count": len(enriched.images),
                **dict(enriched.acquisition_summary),
                **{f"visual_{key}": value for key, value in dict(visual).items()},
            }
        except (OSError, UnicodeError, TypeError, ValueError, KeyError) as exc:
            return {
                "property_key": manifest_path.parent.name,
                "input_manifest": str(manifest_path),
                "output_manifest": None,
                "acquisition_status": "FAILED_TO_PROCESS",
                "visual_status": "NOT_RUN",
                "photo_count": None,
                "error": f"{type(exc).__name__}: {exc}",
            }

    rows: list[dict[str, Any]] = []
    if max_workers <= 1:
        rows = [_process(path) for path in paths]
    else:
        with ThreadPoolExecutor(max_workers=max(1, max_workers)) as executor:
            futures = [executor.submit(_process, path) for path in paths]
            for future in as_completed(futures):
                rows.append(future.result())
        rows.sort(key=lambda row: str(row.get("input_manifest") or ""))

    counts = {
        "properties_discovered": len(paths),
        "properties_processed": len(rows),
        "acquisition_status": dict(sorted(Counter(str(row.get("acquisition_status")) for row in rows).items())),
        "visual_status": dict(sorted(Counter(str(row.get("visual_status")) for row in rows).items())),
        "source_images": sum(int(row.get("total_count") or 0) for row in rows),
        "downloaded_images": sum(int(row.get("downloaded_count") or 0) for row in rows),
        "local_cache_images": sum(int(row.get("local_cache_count") or 0) for row in rows),
        "failed_images": sum(int(row.get("failed_count") or 0) for row in rows),
        "invalid_content_images": sum(int(row.get("invalid_content_count") or 0) for row in rows),
        "duplicate_url_images": sum(int(row.get("duplicate_url_count") or 0) for row in rows),
        "duplicate_sha256_images": sum(int(row.get("duplicate_sha256_count") or 0) for row in rows),
        "duplicate_perceptual_images": sum(int(row.get("duplicate_perceptual_count") or 0) for row in rows),
        "unique_content_images": sum(int(row.get("unique_content_count") or 0) for row in rows),
        "classified_images": sum(int(row.get("visual_classified_count") or 0) for row in rows),
        "visual_failed_images": sum(int(row.get("visual_failed_count") or 0) for row in rows),
        "visual_skipped_images": sum(int(row.get("visual_skipped_count") or 0) for row in rows),
    }
    payload = {
        "schema_version": "strategy-v2-phase-2a-photo-acquisition-v1",
        "generated_at": generated_at,
        "input_manifest_root": str(manifest_directory),
        "output_root": str(output_directory),
        "classifier": getattr(classifier, "name", None),
        "counts": counts,
        "properties": rows,
    }
    report_directory.mkdir(parents=True, exist_ok=True)
    json_path = report_directory / "photo-acquisition-summary.json"
    csv_path = report_directory / "photo-acquisition-summary.csv"
    markdown_path = report_directory / "photo-acquisition-report.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    fields = sorted({key for row in rows for key in row})
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    visual_note = (
        "A configured visual classifier ran after acquisition."
        if classifier is not None
        else "No visual classifier was configured; image bytes are acquired but visual labels remain UNKNOWN/UNAVAILABLE."
    )
    markdown_path.write_text(
        "# Strategy V2 Photo Acquisition\n\n"
        f"Generated: `{generated_at}`  \n"
        f"Properties processed: `{len(rows)}`  \n"
        f"Image observations: `{counts['source_images']}`  \n"
        f"Images downloaded this run: `{counts['downloaded_images']}`  \n"
        f"Images reused from local cache: `{counts['local_cache_images']}`  \n"
        f"Unique image contents: `{counts['unique_content_images']}`  \n"
        f"Duplicate observations: `{counts['duplicate_url_images'] + counts['duplicate_sha256_images'] + counts['duplicate_perceptual_images']}`  \n"
        f"Images failed: `{counts['failed_images']}`  \n"
        f"Images classified: `{counts['classified_images']}`  \n\n"
        f"{visual_note}\n\n"
        "The command uses only image URLs already present in local detail-page manifests. "
        "It does not crawl new pages or bypass access controls. Failed images remain in the "
        "manifest with an explicit status and do not become a negative property condition.\n",
        encoding="utf-8",
    )
    payload["artifacts"] = {
        "json": str(json_path),
        "csv": str(csv_path),
        "markdown": str(markdown_path),
    }
    return payload


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest-root", type=Path, default=DEFAULT_MANIFEST_ROOT)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--report-root", type=Path, default=DEFAULT_REPORT_ROOT)
    parser.add_argument("--output-date", default=None)
    parser.add_argument("--max-properties", type=int, default=None)
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--user-agent", default=DEFAULT_USER_AGENT)
    parser.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)
    parser.add_argument("--perceptual-distance", type=int, default=DEFAULT_PERCEPTUAL_DISTANCE)
    parser.add_argument("--max-workers", type=int, default=4)
    parser.add_argument("--vision-endpoint", default=None)
    parser.add_argument("--vision-api-key-env", default="VISION_API_KEY")
    parser.add_argument("--vision-model", default=None)
    parser.add_argument("--vision-wire-api", choices=("responses", "chat_completions"), default="responses")
    parser.add_argument("--vision-max-attempts", type=int, default=3)
    parser.add_argument("--vision-retry-backoff", type=float, default=1.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    classifier = _classifier_from_options(
        endpoint=args.vision_endpoint,
        api_key_env=args.vision_api_key_env,
        model=args.vision_model,
        timeout=args.timeout,
        wire_api=args.vision_wire_api,
        max_attempts=args.vision_max_attempts,
        retry_backoff_seconds=args.vision_retry_backoff,
    )
    payload = run_photo_acquisition(
        manifest_root=args.manifest_root,
        output_root=args.output_root,
        report_root=args.report_root,
        output_date=args.output_date,
        max_properties=args.max_properties,
        classifier=classifier,
        timeout=args.timeout,
        user_agent=args.user_agent,
        max_bytes=args.max_bytes,
        perceptual_distance=args.perceptual_distance,
        max_workers=args.max_workers,
    )
    print(
        json.dumps(
            {
                "status": "COMPLETED",
                "schema_version": payload["schema_version"],
                "generated_at": payload["generated_at"],
                "counts": payload["counts"],
                "artifacts": payload["artifacts"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
