"""Configuration helpers for the bounded Vancouver detail run."""

from __future__ import annotations

from typing import Any, Mapping


REGIONAL_PRESETS = (
    "vancouver-west",
    "vancouver-east",
    "vancouver-central-core",
    "vancouver-oakridge",
    "vancouver-south",
)


def regional_detail_config(config: Mapping[str, Any]) -> dict[str, Any]:
    """Return a run config scoped to the five persisted Vancouver polygons."""

    result = dict(config)
    discovery = config.get("discovery", {})
    discovery = dict(discovery) if isinstance(discovery, Mapping) else {}
    configured = discovery.get("presets", [])
    by_name = {
        str(item.get("name")): dict(item)
        for item in configured
        if isinstance(item, Mapping) and item.get("name")
    }
    missing = [name for name in REGIONAL_PRESETS if name not in by_name]
    if missing:
        raise ValueError(f"regional detail presets are not configured: {', '.join(missing)}")
    discovery["presets"] = [by_name[name] for name in REGIONAL_PRESETS]
    result["discovery"] = discovery
    official = config.get("discovery_official_enrichment", {})
    official = dict(official) if isinstance(official, Mapping) else {}
    official["enabled"] = False
    result["discovery_official_enrichment"] = official
    screening = config.get("screening_v2", {})
    screening = dict(screening) if isinstance(screening, Mapping) else {}
    screening["enabled"] = False
    result["screening_v2"] = screening
    return result


def regional_official_config(config: Mapping[str, Any]) -> dict[str, Any]:
    """Enable full official enrichment for the saved regional detail cards."""

    result = dict(config)
    official = config.get("official_enrichment", {})
    official = dict(official) if isinstance(official, Mapping) else {}
    official["enabled"] = True
    official["max_properties_per_run"] = 0
    official["discovery_batch_size"] = 20
    result["official_enrichment"] = official
    discovery_official = config.get("discovery_official_enrichment", {})
    discovery_official = (
        dict(discovery_official)
        if isinstance(discovery_official, Mapping)
        else {}
    )
    discovery_official["enabled"] = True
    result["discovery_official_enrichment"] = discovery_official
    return result


__all__ = [
    "REGIONAL_PRESETS",
    "regional_detail_config",
    "regional_official_config",
]
