#!/usr/bin/env python3
"""Run versioned external location enrichment against a saved property artifact.

This command is intentionally separate from production screening.  It can
produce a complete local snapshot with UNKNOWN provider values when credentials
are absent; missing data is never converted into zero and never excludes a
property.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from northshore_watchdog.config import load_config, resolve_path  # noqa: E402
from northshore_watchdog.research.location_providers.runner import (  # noqa: E402
    MODEL_NAME,
    MODEL_VERSION,
    enrich_properties,
    load_records,
    write_provider_artifacts,
)
from northshore_watchdog.research.location_providers.store import (  # noqa: E402
    LocationSnapshotStore,
)
from northshore_watchdog.research.location_providers.walk_score import (  # noqa: E402
    WalkScoreProvider,
)


DEFAULT_INPUT = "data/research/normalized/portfolio-official-enrichment-2026-09-07.json"
DEFAULT_SNAPSHOT = "data/research/history/location-provider-snapshots.jsonl"
DEFAULT_OUTPUT = "research/location-provider"


def _latest_input_artifact() -> Path:
    """Select the newest saved official portfolio artifact for unattended runs."""

    candidates = list((ROOT / "data/research/normalized").glob("portfolio-official-enrichment-*.json"))
    if not candidates:
        return ROOT / DEFAULT_INPUT

    def sort_key(path: Path) -> tuple[str, float]:
        match = re.search(r"(\d{4}-\d{2}-\d{2})", path.name)
        return (match.group(1) if match else "0000-00-00", path.stat().st_mtime)

    return max(candidates, key=sort_key)


def _provider_settings(config: dict[str, Any], provider: str) -> dict[str, Any]:
    section = config.get("location_providers") or {}
    value = section.get(provider) or {}
    return value if isinstance(value, dict) else {}


def _credential(settings: dict[str, Any]) -> str | None:
    env_name = settings.get("api_key_env") or settings.get("token_env")
    return os.getenv(str(env_name)) if env_name else None


def _parse_observed(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config/research-discovery.yaml")
    parser.add_argument("--input", dest="input_path", default=None)
    parser.add_argument("--snapshot-path", default=None)
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--output-json", default=None)
    parser.add_argument("--ttl-days", type=int, default=None)
    parser.add_argument("--observed-at", default=None, help="ISO-8601 timestamp")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    config = load_config(args.config)
    location_config = config.get("location_providers") or {}

    input_path = resolve_path(args.input_path, config) if args.input_path else _latest_input_artifact()
    output_dir = resolve_path(
        args.output_dir or location_config.get("output_dir") or DEFAULT_OUTPUT,
        config,
    )
    snapshot_path = resolve_path(
        args.snapshot_path or location_config.get("cache_path") or DEFAULT_SNAPSHOT,
        config,
    )
    ttl_days = int(args.ttl_days if args.ttl_days is not None else location_config.get("cache_ttl_days", 180))
    observed_at = _parse_observed(args.observed_at)

    records = load_records(input_path)
    walk_settings = _provider_settings(config, "walk_score")
    enabled = bool(location_config.get("enabled", True))
    walk_enabled = bool(walk_settings.get("enabled", True))
    model_name = str(location_config.get("model_name") or MODEL_NAME)
    model_version = str(location_config.get("model_version") or MODEL_VERSION)
    walk = WalkScoreProvider(
        api_key=_credential(walk_settings) if enabled and walk_enabled else None,
        endpoint=walk_settings.get("endpoint"),
    )
    store = LocationSnapshotStore(snapshot_path)
    snapshots = enrich_properties(
        records,
        walk_score=walk,
        store=store,
        observed_at=observed_at,
        ttl_days=ttl_days,
        model_name=model_name,
        model_version=model_version,
    )
    artifact_paths = write_provider_artifacts(
        snapshots,
        output_dir,
        model_name=model_name,
        model_version=model_version,
    )

    complete = sum(item.composite.status == "COMPLETE" for item in snapshots)
    by_composite = Counter(item.composite.status for item in snapshots)
    normalized_path = resolve_path(
        args.output_json
        or f"data/research/normalized/location-provider-enrichment-{observed_at.date().isoformat()}.json",
        config,
    )
    normalized_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "status": "COMPLETED",
        "generated_at": observed_at.isoformat(),
        "model_name": model_name,
        "model_version": model_version,
        "input_path": str(input_path),
        "snapshot_path": str(snapshot_path),
        "property_count": len(snapshots),
        "complete_count": complete,
        "composite_status_counts": dict(by_composite),
        "credentials_configured": {
            "walk_score": bool(_credential(walk_settings)) if enabled and walk_enabled else False,
        },
        "artifacts": {key: str(path) for key, path in artifact_paths.items()},
        "snapshots": [item.model_dump(mode="json") for item in snapshots],
    }
    normalized_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(json.dumps({
        "status": payload["status"],
        "input": str(input_path),
        "properties": len(snapshots),
        "complete": complete,
        "composite_status_counts": dict(by_composite),
        "credentials_configured": payload["credentials_configured"],
        "normalized": str(normalized_path),
        "artifacts": payload["artifacts"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
