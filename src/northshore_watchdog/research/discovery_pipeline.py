"""Orchestration for the bounded Stage 0–4 discovery pipeline."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from pydantic import BaseModel, Field

from ..config import resolve_path
from .discovery import (
    DiscoveryCard,
    DiscoveryCapture,
    DiscoveryFilterPlan,
    DiscoveryPreset,
    DiscoveryStatus,
    DiscoveryValidation,
    SiteCapabilities,
    compile_filter_plan,
    deduplicate_cards,
    rank_shortlist,
    validate_capture,
)
from .discovery_browser import PlaywrightNativeDiscoverySource
from .discovery_safari import SafariNativeDiscoverySource
from .discovery_official import DiscoveryOfficialEnrichmentResult, enrich_discovery_cards
from .discovery_sources import (
    DiscoverySource,
    HtmlFilteredCaptureSource,
    JsonFilteredCaptureSource,
)
from .screening import ScreeningResult, screen_discovery_candidates


class DiscoveryPresetResult(BaseModel):
    preset_name: str
    status: str
    result_count: int | None = None
    filter_plan: DiscoveryFilterPlan | None = None
    validation: DiscoveryValidation | None = None
    capture: DiscoveryCapture | None = None
    candidates: list[DiscoveryCard] = Field(default_factory=list)
    error: str | None = None


class DiscoveryRunResult(BaseModel):
    status: str
    dry_run: bool = False
    observed_at: datetime | None = None
    result_count: int | None = None
    filter_plan: DiscoveryFilterPlan | None = None
    filter_plans: list[DiscoveryFilterPlan] = Field(default_factory=list)
    validation: DiscoveryValidation | None = None
    validations: list[DiscoveryValidation] = Field(default_factory=list)
    candidate_count: int = 0
    candidates: list[DiscoveryCard] = Field(default_factory=list)
    screening: ScreeningResult | None = None
    official_enrichment: DiscoveryOfficialEnrichmentResult | None = None
    preset_results: list[DiscoveryPresetResult] = Field(default_factory=list)
    duplicate_flags: list[str] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=list)


def _discovery_config(config: Mapping[str, Any]) -> Mapping[str, Any]:
    value = config.get("discovery", {})
    return value if isinstance(value, Mapping) else {}


def _materialize_preset(
    config: Mapping[str, Any],
    preset_config: Mapping[str, Any],
) -> dict[str, Any]:
    discovery = _discovery_config(config)
    defaults = {
        key: discovery.get(key)
        for key in ("max_result_cards", "sample_size", "shortlist_limit")
        if discovery.get(key) is not None
    }
    return {**defaults, **dict(preset_config)}


def _configured_presets(
    config: Mapping[str, Any],
    preset_name: str | None,
) -> list[Mapping[str, Any]]:
    configured = _discovery_config(config).get("presets", [])
    if not isinstance(configured, list):
        raise ValueError("discovery.presets must be a list")
    selected = [item for item in configured if isinstance(item, Mapping)]
    if preset_name is None:
        return selected
    return [item for item in selected if item.get("name") == preset_name]


def _configured_discovery_source(
    config: Mapping[str, Any],
    source_name: str | None,
    preset: DiscoveryPreset,
) -> Mapping[str, Any]:
    configured = _discovery_config(config).get("sources", [])
    if not isinstance(configured, list):
        raise ValueError("discovery.sources must be a list")
    sources = [item for item in configured if isinstance(item, Mapping)]
    enabled = [item for item in sources if item.get("enabled", True)]
    if source_name:
        enabled = [item for item in enabled if item.get("name") == source_name]
    if not enabled:
        raise LookupError(f"no enabled discovery source for preset {preset.name}")
    return enabled[0]


def _source_config_with_defaults(
    config: Mapping[str, Any],
    source_config: Mapping[str, Any],
) -> dict[str, Any]:
    discovery = _discovery_config(config)
    result = dict(source_config)
    for key in ("max_result_cards", "sample_size", "shortlist_limit"):
        if key not in result and discovery.get(key) is not None:
            result[key] = discovery[key]
    return result


def _build_discovery_source(
    config: Mapping[str, Any],
    source_config: Mapping[str, Any],
    capture_path: str | Path | None,
) -> DiscoverySource:
    source_type = str(source_config.get("type", "")).upper()
    source_name = source_config.get("name")
    if not isinstance(source_name, str) or not source_name.strip():
        raise ValueError("discovery source requires a non-empty name")
    effective_config = _source_config_with_defaults(config, source_config)
    if capture_path is not None:
        return JsonFilteredCaptureSource(
            name=source_name,
            path=capture_path,
            config=effective_config,
        )
    if source_type == "JSON_CAPTURE":
        path = effective_config.get("path")
        if not isinstance(path, (str, Path)) or not str(path).strip():
            raise ValueError(f"JSON_CAPTURE source {source_name} requires path")
        return JsonFilteredCaptureSource(
            name=source_name,
            path=resolve_path(path, dict(config)),
            config=effective_config,
        )
    if source_type == "HTML_CAPTURE":
        path = effective_config.get("path")
        if not isinstance(path, (str, Path)) or not str(path).strip():
            raise ValueError(f"HTML_CAPTURE source {source_name} requires path")
        return HtmlFilteredCaptureSource(
            name=source_name,
            path=resolve_path(path, dict(config)),
            config=effective_config,
        )
    if source_type == "PLAYWRIGHT_NATIVE":
        return PlaywrightNativeDiscoverySource.from_config(effective_config)
    if source_type == "SAFARI_NATIVE":
        return SafariNativeDiscoverySource.from_config(effective_config)
    raise ValueError(f"unsupported discovery source type: {source_type}")


def _selected_presets(
    config: Mapping[str, Any],
    preset_name: str | None,
) -> list[DiscoveryPreset]:
    raw_presets = _configured_presets(config, preset_name)
    if preset_name is not None and not raw_presets:
        raise ValueError(f"unknown discovery preset: {preset_name}")
    return [DiscoveryPreset.model_validate(_materialize_preset(config, item)) for item in raw_presets]


def _now(value: datetime | None) -> datetime:
    if isinstance(value, str):
        current = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        current = value or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        return current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc)


def _error_validation(message: str) -> DiscoveryValidation:
    return DiscoveryValidation(
        status=DiscoveryStatus.PARSER_ERROR,
        reason_codes=["SOURCE_EXECUTION_ERROR"],
        violations=[{"reason_code": "SOURCE_EXECUTION_ERROR", "error": message}],
    )


def run_discovery(
    config: Mapping[str, Any],
    *,
    preset_name: str | None = None,
    source_name: str | None = None,
    capture_path: str | Path | None = None,
    dry_run: bool = False,
    observed_at: datetime | None = None,
    official_enrich: bool | None = None,
) -> DiscoveryRunResult:
    """Run only the configured filtered source for the selected preset(s)."""

    presets = _selected_presets(config, preset_name)
    if not presets:
        raise ValueError("no discovery presets are configured")
    observed = _now(observed_at)

    plans: list[DiscoveryFilterPlan] = []
    preset_results: list[DiscoveryPresetResult] = []
    validations: list[DiscoveryValidation] = []
    successful_cards: list[DiscoveryCard] = []
    result_counts: list[int] = []

    for preset in presets:
        try:
            source_config = _configured_discovery_source(config, source_name, preset)
        except LookupError:
            return DiscoveryRunResult(
                status="NO_DISCOVERY_SOURCE",
                dry_run=dry_run,
                observed_at=observed,
                filter_plans=plans,
                filter_plan=plans[0] if plans else None,
                preset_results=preset_results,
            )

        capabilities = SiteCapabilities.model_validate(source_config.get("capabilities", {}))
        browser_config = source_config.get("browser", {})
        actions = browser_config.get("actions", []) if isinstance(browser_config, Mapping) else []
        plan = compile_filter_plan(
            preset,
            capabilities,
            source_name=str(source_config.get("name")),
            actions=actions,
        )
        plans.append(plan)
        if dry_run:
            continue

        try:
            source = _build_discovery_source(config, source_config, capture_path)
            capture = source.execute(plan, observed_at=observed)
            validation = validate_capture(
                capture,
                plan,
                sample_size=preset.sample_size,
                max_result_cards=preset.max_result_cards,
            )
            validations.append(validation)
            if capture.result_count is not None:
                result_counts.append(capture.result_count)
            preset_candidates = validation.candidate_cards if validation.status is DiscoveryStatus.PASS else []
            successful_cards.extend(preset_candidates)
            preset_results.append(
                DiscoveryPresetResult(
                    preset_name=preset.name,
                    status=validation.status.value,
                    result_count=capture.result_count,
                    filter_plan=plan,
                    validation=validation,
                    capture=capture,
                    candidates=preset_candidates,
                )
            )
        except Exception as exc:
            validation = _error_validation(str(exc))
            validations.append(validation)
            preset_results.append(
                DiscoveryPresetResult(
                    preset_name=preset.name,
                    status=validation.status.value,
                    filter_plan=plan,
                    validation=validation,
                    error=str(exc),
                )
            )

    if dry_run:
        return DiscoveryRunResult(
            status="DRY_RUN",
            dry_run=True,
            observed_at=observed,
            filter_plans=plans,
            filter_plan=plans[0] if plans else None,
            preset_results=preset_results,
        )

    dedupe = deduplicate_cards(successful_cards)
    official_result: DiscoveryOfficialEnrichmentResult | None = None
    scoring_cards = dedupe.cards
    if official_enrich is None:
        official_section = config.get("discovery_official_enrichment", {})
        official_section = official_section if isinstance(official_section, Mapping) else {}
        native_source_names = {
            str(item.get("name"))
            for item in _discovery_config(config).get("sources", [])
            if isinstance(item, Mapping)
            and str(item.get("type", "")).upper() in {"PLAYWRIGHT_NATIVE", "SAFARI_NATIVE"}
        }
        used_native_source = any(
            item.filter_plan is not None and item.filter_plan.source_name in native_source_names
            for item in preset_results
        )
        official_enrich = bool(official_section.get("enabled", False)) and used_native_source and capture_path is None
    if official_enrich:
        official_result = enrich_discovery_cards(
            dedupe.cards,
            config,
            observed_at=observed,
        )
        scoring_cards = official_result.cards
    candidates = rank_shortlist(
        scoring_cards,
        limit=presets[0].shortlist_limit,
    )
    screening_config = config.get("screening_v2", {})
    screening = (
        screen_discovery_candidates(scoring_cards, config)
        if isinstance(screening_config, Mapping) and screening_config.get("enabled", False)
        else None
    )
    passed = sum(1 for item in validations if item.status is DiscoveryStatus.PASS)
    if passed == len(validations) and passed:
        status = "COMPLETED"
    elif passed:
        status = "PARTIAL"
    else:
        status = "FAILED"
    return DiscoveryRunResult(
        status=status,
        dry_run=False,
        observed_at=observed,
        result_count=sum(result_counts) if result_counts else None,
        filter_plans=plans,
        filter_plan=plans[0] if plans else None,
        validation=validations[0] if validations else None,
        validations=validations,
        candidate_count=len(candidates),
        candidates=candidates,
        screening=screening,
        official_enrichment=official_result,
        preset_results=preset_results,
        duplicate_flags=dedupe.duplicate_flags,
        reason_codes=dedupe.reason_codes,
    )


__all__ = [
    "DiscoveryPresetResult",
    "DiscoveryRunResult",
    "_build_discovery_source",
    "_configured_discovery_source",
    "_configured_presets",
    "_materialize_preset",
    "run_discovery",
]
