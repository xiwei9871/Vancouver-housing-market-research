#!/usr/bin/env python3
"""Prefetch official school catchment polygons for reuse by listing enrichment."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

from northshore_watchdog.config import load_config
from northshore_watchdog.research.pipeline import build_official_enrichment_adapters


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    config = load_config(ROOT / "config/research-mls-first.yaml")
    adapters = build_official_enrichment_adapters(
        dict(config),
        source_names={"school-catchment"},
    )
    if not adapters:
        raise RuntimeError("school-catchment adapter is not enabled")
    observed_at = datetime.now(timezone.utc)
    result = adapters[0].prefetch_boundary_cache(observed_at)
    result["observed_at"] = observed_at.isoformat()
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.get("status") in {"REFRESHED", "CACHE_HIT"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
