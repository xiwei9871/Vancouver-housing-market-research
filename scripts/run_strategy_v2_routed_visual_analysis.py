#!/usr/bin/env python3
"""Run visual analysis only for the selected Strategy V2 image budget.

This runner consumes the date-stamped analysis-routing artifact.  It never
acquires listing pages or images, and it never sends a request for a property
whose final route is ``STRUCTURED_ONLY`` or ``SPECIAL_REVIEW``.  The selected
manifest is classified with one structured request per image, then merged
back into a new date-stamped visual snapshot so the acquisition baseline is
not overwritten.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from northshore_watchdog.research.strategy_v2_photo_classification import (  # noqa: E402
    JsonHttpVisionClassifier,
    classify_photo_manifest,
)
from northshore_watchdog.research.strategy_v2_photo_intelligence import (  # noqa: E402
    PhotoManifest,
    PhotoObservation,
)


DEFAULT_ROUTING_INPUT = ROOT / "data/research/normalized/strategy-v2-analysis-routing-2026-09-12.json"
DEFAULT_CONFIG = ROOT / "config/strategy-v2-phase-2a.yaml"
DEFAULT_OUTPUT_ROOT = ROOT
VISUAL_SUCCESS_STATUSES = {"AVAILABLE", "DOWNLOADED", "LOCAL_CACHE"}
CLASSIFICATION_FIELDS = (
    "content_labels",
    "visual_status",
    "visual_confidence",
    "visible_defects",
    "maintenance_signals",
    "cosmetic_signals",
    "visual_evidence",
)


def _resolve(path: str | Path, *, base: Path = ROOT) -> Path:
    value = Path(path)
    return value if value.is_absolute() else base / value


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _safe_key(value: str) -> str:
    safe = "".join(char.lower() if char.isalnum() else "-" for char in str(value))
    return "-".join(part for part in safe.split("-") if part)[:180] or "unknown-property"


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_manifest(path: Path) -> PhotoManifest:
    return PhotoManifest.model_validate(_load_json(path))


def _write_manifest(path: Path, manifest: PhotoManifest) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(manifest.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _usable_images(manifest: PhotoManifest) -> list[PhotoObservation]:
    return [
        image
        for image in sorted(manifest.images, key=lambda item: item.original_order)
        if (
            not image.is_duplicate
            and bool(image.local_path)
            and image.source_status in VISUAL_SUCCESS_STATUSES
            and Path(str(image.local_path)).is_file()
        )
    ]


def _images_for_route(
    manifest: PhotoManifest,
    *,
    analysis_route: str,
    image_count: int,
) -> list[PhotoObservation]:
    images = _usable_images(manifest)
    if analysis_route == "FULL_VISION":
        return images
    if analysis_route == "REPRESENTATIVE_VISION":
        return images[: max(0, int(image_count))]
    return []


def _merge_classification(
    original: PhotoManifest,
    classified_subset: PhotoManifest,
    *,
    selection_route: str,
    selected_image_count: int,
    summary: Mapping[str, Any],
) -> PhotoManifest:
    classified_by_order = {
        image.original_order: image for image in classified_subset.images
    }
    merged_images: list[PhotoObservation] = []
    for image in original.images:
        classified = classified_by_order.get(image.original_order)
        if classified is None:
            merged_images.append(image)
            continue
        updates = {
            field: getattr(classified, field)
            for field in CLASSIFICATION_FIELDS
        }
        merged_images.append(image.model_copy(update=updates))

    visual_summary = dict(summary)
    visual_summary.update(
        {
            "selection_route": selection_route,
            "selected_image_count": selected_image_count,
            "manifest_image_count": len(original.images),
        }
    )
    return original.model_copy(
        update={
            "visual_status": classified_subset.visual_status,
            "visual_classifier": classified_subset.visual_classifier,
            "visual_summary": visual_summary,
            "images": merged_images,
        }
    )


def _counter_value(value: Any) -> int | None:
    return int(value) if isinstance(value, int) and value >= 0 else None


def _counter_delta(before: Any, after: Any) -> int | None:
    before_value = _counter_value(before)
    after_value = _counter_value(after)
    if before_value is None or after_value is None or after_value < before_value:
        return None
    return after_value - before_value


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    fields = [
        "property_key",
        "address",
        "mls",
        "analysis_route",
        "source_manifest",
        "output_manifest",
        "status",
        "image_count",
        "classified_count",
        "failed_count",
        "skipped_count",
        "classification_mode",
        "classification_request_count",
        "http_request_attempts",
        "retry_attempts",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field) for field in fields})


def _report(payload: Mapping[str, Any]) -> str:
    lines = [
        "# Strategy V2 Routed Visual Analysis",
        "",
        f"Generated: `{payload.get('generated_at')}`  ",
        f"Selected properties: **{payload.get('selected_property_count')}**  ",
        "Only locally cached images from the routing artifact were processed.",
        "",
        "## Request accounting",
        "",
        f"- Request mode: `{payload.get('visual_request_mode')}`",
        f"- Images sent: `{payload.get('image_count')}`",
        f"- Logical image requests: `{payload.get('classification_request_count')}`",
        f"- HTTP request attempts: `{payload.get('http_request_attempts')}`",
        f"- Retry attempts: `{payload.get('retry_attempts')}`",
        "",
        "The classifier returns space labels, maintenance signals, cosmetic signals, visible defects, and evidence in one response per image. HTTP retries are counted separately from logical image requests.",
        "",
        "## Property results",
        "",
        "| Address | Route | Images | Classified | Failed | Logical requests | HTTP attempts | Retries | Status |",
        "|---|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in payload.get("properties") or []:
        lines.append(
            f"| {row.get('address') or 'UNKNOWN'} | {row.get('analysis_route')} | "
            f"{row.get('image_count', 0)} | {row.get('classified_count', 0)} | "
            f"{row.get('failed_count', 0)} | {row.get('classification_request_count', 0)} | "
            f"{row.get('http_request_attempts', 'UNKNOWN')} | {row.get('retry_attempts', 'UNKNOWN')} | "
            f"{row.get('status')} |"
        )
    lines.extend(
        [
            "",
            "## Safety boundaries",
            "",
            "- No new listing acquisition was performed.",
            "- `STRUCTURED_ONLY` and `SPECIAL_REVIEW` properties were not sent to the classifier.",
            "- The original acquisition manifests were not overwritten; classified results are stored under a new dated visual snapshot.",
            "- Visual evidence is not an inspection and does not establish hidden-system condition.",
        ]
    )
    return "\n".join(lines) + "\n"


def run_routed_visual_analysis(
    *,
    routing_path: str | Path = DEFAULT_ROUTING_INPUT,
    output_root: str | Path = DEFAULT_OUTPUT_ROOT,
    output_date: str | None = None,
    classifier: Any,
    max_properties: int | None = None,
) -> dict[str, Any]:
    """Classify only the selected routes and write a new visual snapshot."""

    if classifier is None:
        raise ValueError("classifier is required for routed visual analysis")
    routing_file = _resolve(routing_path)
    payload = _load_json(routing_file)
    properties = payload.get("properties") if isinstance(payload, Mapping) else None
    if not isinstance(properties, list):
        raise ValueError(f"routing artifact must contain a properties list: {routing_file}")
    selected = [
        row
        for row in properties
        if isinstance(row, Mapping)
        and bool(row.get("selected_for_visual"))
        and str(row.get("analysis_route")) in {"FULL_VISION", "REPRESENTATIVE_VISION"}
    ]
    if max_properties is not None:
        selected = selected[: max(0, int(max_properties))]

    date_value = output_date or _now().date().isoformat()
    generated_at = _now().isoformat()
    output_directory = _resolve(output_root)
    visual_root = output_directory / "data/research/raw/photo-assets-visual" / date_value
    report_root = output_directory / "reports/strategy-v2-routed-visual-analysis" / date_value
    rows: list[dict[str, Any]] = []
    total_images = 0
    total_logical_requests = 0
    total_http_attempts = 0
    total_retry_attempts = 0
    http_counters_available = True

    for route_row in selected:
        property_key = str(route_row.get("property_key") or "unknown-property")
        source_manifest_path = Path(str(route_row.get("photo_manifest_path") or ""))
        analysis_route = str(route_row.get("analysis_route"))
        requested_image_count = int(route_row.get("image_count") or 0)
        output_manifest_path = visual_root / _safe_key(property_key) / "manifest.json"
        result_row: dict[str, Any] = {
            "property_key": property_key,
            "address": route_row.get("address"),
            "mls": route_row.get("mls"),
            "analysis_route": analysis_route,
            "source_manifest": str(source_manifest_path),
            "output_manifest": str(output_manifest_path),
            "status": "NOT_RUN",
            "image_count": 0,
            "classified_count": 0,
            "failed_count": 0,
            "skipped_count": 0,
            "classification_mode": getattr(classifier, "name", type(classifier).__name__),
            "classification_request_count": 0,
            "http_request_attempts": None,
            "retry_attempts": None,
        }
        if not source_manifest_path.is_file():
            result_row.update({"status": "SOURCE_MANIFEST_MISSING"})
            rows.append(result_row)
            continue

        try:
            original_manifest = _read_manifest(source_manifest_path)
            selected_images = _images_for_route(
                original_manifest,
                analysis_route=analysis_route,
                image_count=requested_image_count,
            )
            subset_manifest = original_manifest.model_copy(
                update={"images": selected_images, "duplicate_count": 0}
            )
            before_http = getattr(classifier, "http_request_attempts", None)
            before_retry = getattr(classifier, "retry_attempts", None)
            classified_subset, visual_summary = classify_photo_manifest(
                subset_manifest,
                classifier=classifier,
            )
            after_http = getattr(classifier, "http_request_attempts", None)
            after_retry = getattr(classifier, "retry_attempts", None)
            http_delta = _counter_delta(before_http, after_http)
            retry_delta = _counter_delta(before_retry, after_retry)
            merged_manifest = _merge_classification(
                original_manifest,
                classified_subset,
                selection_route=analysis_route,
                selected_image_count=len(selected_images),
                summary=visual_summary,
            )
            _write_manifest(output_manifest_path, merged_manifest)
            result_row.update(
                {
                    "status": merged_manifest.visual_status,
                    "image_count": len(selected_images),
                    "classified_count": visual_summary.get("classified_count", 0),
                    "failed_count": visual_summary.get("failed_count", 0),
                    "skipped_count": visual_summary.get("skipped_count", 0),
                    "classification_mode": visual_summary.get("classification_mode"),
                    "classification_request_count": visual_summary.get("classification_request_count", 0),
                    "http_request_attempts": http_delta,
                    "retry_attempts": retry_delta,
                }
            )
        except (OSError, UnicodeError, TypeError, ValueError, KeyError) as exc:
            result_row.update(
                {
                    "status": "FAILED_TO_PROCESS",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
        rows.append(result_row)
        total_images += int(result_row.get("image_count") or 0)
        total_logical_requests += int(result_row.get("classification_request_count") or 0)
        if result_row.get("http_request_attempts") is None:
            http_counters_available = False
        else:
            total_http_attempts += int(result_row["http_request_attempts"])
        if result_row.get("retry_attempts") is None:
            http_counters_available = False
        else:
            total_retry_attempts += int(result_row["retry_attempts"])

    if not http_counters_available:
        total_http_attempts_value: int | None = None
        total_retry_attempts_value: int | None = None
    else:
        total_http_attempts_value = total_http_attempts
        total_retry_attempts_value = total_retry_attempts
    output_payload: dict[str, Any] = {
        "schema_version": "strategy-v2-routed-visual-analysis-v1",
        "generated_at": generated_at,
        "routing_input": str(routing_file),
        "visual_request_mode": "ONE_REQUEST_PER_IMAGE",
        "selected_property_count": len(selected),
        "image_count": total_images,
        "classification_request_count": total_logical_requests,
        "http_request_attempts": total_http_attempts_value,
        "retry_attempts": total_retry_attempts_value,
        "properties": rows,
    }
    report_root.mkdir(parents=True, exist_ok=True)
    normalized_root = output_directory / "data/research/normalized"
    normalized_root.mkdir(parents=True, exist_ok=True)
    json_path = normalized_root / f"strategy-v2-routed-visual-analysis-{date_value}.json"
    csv_path = normalized_root / f"strategy-v2-routed-visual-analysis-{date_value}.csv"
    report_path = report_root / "routed-visual-analysis-report.md"
    json_path.write_text(
        json.dumps(output_payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    _write_csv(csv_path, rows)
    report_path.write_text(_report(output_payload), encoding="utf-8")
    output_payload["artifacts"] = {
        "json": str(json_path),
        "csv": str(csv_path),
        "report": str(report_path),
        "visual_manifest_root": str(visual_root),
    }
    return output_payload


def _configured_classifier(config_path: Path, *, api_key_env_override: str | None = None) -> JsonHttpVisionClassifier | None:
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    photo_config = config.get("photo") if isinstance(config, Mapping) and isinstance(config.get("photo"), Mapping) else {}
    visual_config = photo_config.get("visual") if isinstance(photo_config.get("visual"), Mapping) else {}
    request_mode = str(visual_config.get("request_mode") or "one_request_per_image")
    if request_mode != "one_request_per_image":
        raise ValueError(f"unsupported visual request mode: {request_mode}")
    endpoint = str(visual_config.get("endpoint") or os.environ.get("VISION_API_ENDPOINT", "")).strip()
    model = str(visual_config.get("model") or os.environ.get("VISION_MODEL", "")).strip()
    api_key_env = api_key_env_override or str(visual_config.get("api_key_env") or "VISION_API_KEY")
    api_key = os.environ.get(api_key_env, "")
    if not endpoint or not model or not api_key:
        return None
    timeout = float(visual_config.get("timeout_seconds") or 60.0)
    wire_api = str(visual_config.get("wire_api") or "responses")
    max_attempts = int(visual_config.get("max_attempts") or 3)
    retry_backoff = float(visual_config.get("retry_backoff_seconds") or 1.0)
    return JsonHttpVisionClassifier(
        endpoint=endpoint,
        api_key=api_key,
        model=model,
        timeout=timeout,
        wire_api=wire_api,
        max_attempts=max_attempts,
        retry_backoff_seconds=retry_backoff,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--routing", type=Path, default=DEFAULT_ROUTING_INPUT)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--output-date", default=None)
    parser.add_argument("--api-key-env", default=None)
    parser.add_argument("--max-properties", type=int, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    classifier = _configured_classifier(_resolve(args.config), api_key_env_override=args.api_key_env)
    if classifier is None:
        raise SystemExit("visual classifier is not configured; set the configured API-key environment variable")
    payload = run_routed_visual_analysis(
        routing_path=args.routing,
        output_root=args.output_root,
        output_date=args.output_date,
        classifier=classifier,
        max_properties=args.max_properties,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
