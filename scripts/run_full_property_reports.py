#!/usr/bin/env python3
"""Generate the full three-layer report for every saved listing card.

The default mode is a local replay: it reads the existing official-enrichment
artifact and never performs a broad acquisition.  This is the intended
Phase-1/Phase-2 boundary for the current portfolio.  Official refreshes can be
added later without changing the report schema.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from northshore_watchdog.research.discovery_pipeline import DiscoveryRunResult  # noqa: E402
from northshore_watchdog.config import load_config  # noqa: E402
from northshore_watchdog.research.discovery_official import (  # noqa: E402
    _property_id,
    enrich_discovery_cards,
    replay_cached_official_cards,
)
from northshore_watchdog.research.discovery_browser import replay_cached_realtor_details  # noqa: E402
from northshore_watchdog.research.regional_detail import regional_official_config  # noqa: E402
from northshore_watchdog.research.pipeline import build_official_enrichment_adapters  # noqa: E402
from northshore_watchdog.research.portfolio import (  # noqa: E402
    build_portfolio_analysis,
    property_key,
    render_portfolio_index,
    validate_property_reports,
    write_portfolio_reports,
)
from northshore_watchdog.research.screening import screen_discovery_candidates  # noqa: E402


DEFAULT_ARTIFACT = ROOT / "data/research/normalized/regional-official-enrichment-2026-09-07.json"
DEFAULT_RULES = ROOT / "config/vancouver-zoning-rules-2026-09-07.yaml"
DEFAULT_REALTOR_DETAIL_DIR = ROOT / "data/research/raw/discovery/realtor-ca-native-details"


def _latest_artifact() -> Path:
    paths = list((ROOT / "data/research/normalized").glob("regional-official-enrichment-*.json"))
    paths.extend((ROOT / "data/research/normalized").glob("portfolio-official-enrichment-*.json"))
    if paths:
        def artifact_key(path: Path) -> tuple[str, int, float]:
            match = re.search(r"(\d{4}-\d{2}-\d{2})", path.name)
            date_key = match.group(1) if match else "0000-00-00"
            # A same-day merged portfolio artifact includes the targeted
            # official repair, so it wins over the pre-refresh regional one.
            priority = 1 if path.name.startswith("portfolio-official-enrichment-") else 0
            return date_key, priority, path.stat().st_mtime
        return max(paths, key=artifact_key)
    return paths[-1] if paths else DEFAULT_ARTIFACT


def _load_result(path: Path) -> DiscoveryRunResult:
    if not path.exists():
        raise FileNotFoundError(f"official artifact not found: {path}")
    return DiscoveryRunResult.model_validate(json.loads(path.read_text(encoding="utf-8")))


def _missing_official_targets(cards: list[Any]) -> list[Any]:
    targets: list[Any] = []
    for card in cards:
        raw = card.raw or {}
        zoning_missing = not (raw.get("official_current_zoning") or raw.get("official_zoning"))
        school_missing = not (raw.get("official_elementary_catchment") and raw.get("official_secondary_catchment"))
        if zoning_missing or school_missing:
            targets.append(card)
    return targets


def _refresh_missing_official(base: DiscoveryRunResult) -> tuple[DiscoveryRunResult, Path]:
    """Refresh only currently missing zoning/school records.

    Existing official cards and source evidence are retained.  A failed
    targeted request therefore cannot erase a prior successful field.
    """

    if base.official_enrichment is None:
        raise RuntimeError("official artifact has no official_enrichment payload")
    config = regional_official_config(load_config(ROOT / "config/research-discovery.yaml"))
    targets = _missing_official_targets(list(base.candidates))
    if not targets:
        return base, ROOT / "data/research/normalized" / "portfolio-official-enrichment-2026-09-07.json"
    adapter_names: set[str] = set()
    for card in targets:
        raw = card.raw or {}
        if not (raw.get("official_current_zoning") or raw.get("official_zoning")):
            adapter_names.add("municipal-zoning")
        if not (raw.get("official_elementary_catchment") and raw.get("official_secondary_catchment")):
            adapter_names.add("school-catchment")
    adapters = build_official_enrichment_adapters(config, source_names=adapter_names)
    if not adapters:
        raise RuntimeError(f"no configured adapters for {sorted(adapter_names)}")
    official_config = config.get("official_enrichment")
    if isinstance(official_config, dict):
        official_config["discovery_batch_size"] = len(targets)
    for adapter in adapters:
        for settings in (
            getattr(adapter, "config", None),
            getattr(getattr(adapter, "http", None), "config", None),
        ):
            if isinstance(settings, dict):
                settings["max_requests_per_run"] = max(12 * len(targets), 1)
                settings["delay_seconds"] = 0
                settings["retry_attempts"] = 1
                settings["timeout_seconds"] = min(float(settings.get("timeout_seconds", 20)), 15.0)
                settings["trust_env"] = False
    refreshed = enrich_discovery_cards(targets, config, adapters=adapters)
    refreshed_by_id = {_property_id(card): card for card in refreshed.cards}
    replayed = replay_cached_official_cards(list(base.candidates), list(base.official_enrichment.cards))
    merged_cards = [refreshed_by_id.get(_property_id(card), card) for card in replayed]
    merged_official = base.official_enrichment.model_copy(
        update={
            "cards": merged_cards,
            "source_runs": list(base.official_enrichment.source_runs) + list(refreshed.source_runs),
            "evidence": list(base.official_enrichment.evidence) + list(refreshed.evidence),
            "warnings": list(base.official_enrichment.warnings) + list(refreshed.warnings),
            "status": "COMPLETED" if refreshed.status == "COMPLETED" and base.official_enrichment.status == "COMPLETED" else "PARTIAL",
        },
        deep=True,
    )
    updated = base.model_copy(
        update={
            "status": "COMPLETED" if merged_official.status == "COMPLETED" else "PARTIAL",
            "observed_at": datetime.now(timezone.utc),
            "candidates": merged_cards,
            "candidate_count": len(merged_cards),
            "official_enrichment": merged_official,
        },
        deep=True,
    )
    path = ROOT / "data/research/normalized" / "portfolio-official-enrichment-2026-09-07.json"
    path.write_text(json.dumps(updated.model_dump(mode="json"), ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    return updated, path


def _decisions(result: DiscoveryRunResult, config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Re-score the saved cards with the current model, then flatten decisions.

    Reports are a replayable view of the current local data, not a frozen copy
    of the screening object embedded in an older enrichment artifact.  This is
    important when scoring weights or normalization formulas change without a
    new acquisition.
    """

    screening = None
    if config is not None:
        screening_config = config.get("screening_v2", {})
        if isinstance(screening_config, dict) and screening_config.get("enabled", False):
            screening = screen_discovery_candidates(list(result.candidates), config)
    if screening is None:
        screening = result.screening
    if screening is None:
        return {}
    values: dict[str, Any] = {}
    # Specific scored lists are intentionally applied after routing lists; all
    # are the same decision objects in normal artifacts, but this ordering is
    # robust to older artifacts that persisted only a projection.
    for name in (
        "stage_a_excluded",
        "special_track_candidates",
        "stage_b_candidates",
        "stage_a_candidates",
        "current_use_candidates",
        "redevelopment_candidates",
        "investment_candidates",
        "dual_value_candidates",
    ):
        for decision in getattr(screening, name, []) or []:
            values[property_key(decision.card)] = decision
    return values


def _relative_report_paths(cards: list[Any], paths: list[Path]) -> dict[str, str]:
    return {
        property_key(card): path.name
        for card, path in zip(cards, paths)
    }


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def _validate(result: DiscoveryRunResult, output_dir: Path) -> dict[str, Any]:
    return validate_property_reports(list(result.candidates), output_dir)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--official-artifact", type=Path, default=None)
    parser.add_argument("--output-date", default="2026-09-07")
    parser.add_argument("--audit-only", action="store_true")
    parser.add_argument("--validate", action="store_true")
    parser.add_argument(
        "--refresh-missing-official",
        action="store_true",
        help="Refresh only cards missing official zoning or school catchment, then report from the merged local artifact.",
    )
    parser.add_argument("--rules-path", type=Path, default=DEFAULT_RULES)
    parser.add_argument(
        "--realtor-detail-dir",
        type=Path,
        default=DEFAULT_REALTOR_DETAIL_DIR,
        help="Replay saved REALTOR.ca detail HTML before writing reports; pass an empty/nonexistent directory to disable replay.",
    )
    args = parser.parse_args()

    artifact = args.official_artifact or _latest_artifact()
    if not artifact.is_absolute():
        artifact = ROOT / artifact
    result = _load_result(artifact)
    refreshed_official = False
    if args.refresh_missing_official:
        result, artifact = _refresh_missing_official(result)
        refreshed_official = True
    cards = list(result.candidates)
    realtor_replay_warnings: list[str] = []
    if args.realtor_detail_dir:
        cards, realtor_replay_warnings = replay_cached_realtor_details(
            cards,
            args.realtor_detail_dir,
            observed_at=result.observed_at,
        )
    scoring_config = load_config(ROOT / "config/research-discovery.yaml")
    decisions = _decisions(result, scoring_config)

    if args.audit_only:
        from northshore_watchdog.research.portfolio import audit_portfolio  # noqa: PLC0415

        payload = audit_portfolio(cards)
        payload["artifact"] = str(artifact)
        payload["screening_decisions"] = len(decisions)
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        return 0

    output_dir = ROOT / "reports/properties" / args.output_date
    paths = write_portfolio_reports(cards, decisions, output_dir, rules_path=args.rules_path)
    report_paths = _relative_report_paths(cards, paths)
    _write_json(
        output_dir / "manifest.json",
        {
            "schema_version": "property-report-manifest-v1",
            "property_keys": [property_key(card) for card in cards],
            "reports": report_paths,
        },
    )
    analysis = build_portfolio_analysis(cards, decisions, report_paths=report_paths)
    analysis["artifact"] = str(artifact)
    analysis["rules_registry"] = str(args.rules_path)
    analysis["generated_at"] = datetime.now(timezone.utc).isoformat()
    analysis_path = ROOT / "data/research/normalized" / f"portfolio-analysis-{args.output_date}.json"
    completion_path = ROOT / "data/research/normalized" / f"portfolio-completion-{args.output_date}.json"
    _write_json(analysis_path, analysis)
    index = render_portfolio_index(analysis)
    (output_dir / "index.md").write_text(index, encoding="utf-8")
    validation = _validate(result, output_dir)
    from northshore_watchdog.research.portfolio import audit_portfolio  # noqa: PLC0415

    audit = audit_portfolio(cards)
    completion = {
        "schema_version": "portfolio-completion-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "artifact": str(artifact),
        "rules_registry": str(args.rules_path),
        "property_count": len(cards),
        "reports_written": len(paths),
        "analysis": str(analysis_path),
        "index": str(output_dir / "index.md"),
        "validation": validation,
        "mode": "TARGETED_OFFICIAL_REFRESH_PLUS_LOCAL_CACHE_REPLAY" if refreshed_official else "LOCAL_CACHE_REPLAY",
        "realtor_detail_replay_dir": str(args.realtor_detail_dir) if args.realtor_detail_dir else None,
        "realtor_detail_replay_warnings": realtor_replay_warnings,
    }
    _write_json(completion_path, completion)
    acceptance = output_dir / "ACCEPTANCE-REPORT.md"
    acceptance.write_text(
        "# Full Portfolio Report Acceptance\n\n"
        f"- Input property cards: **{len(cards)}**\n"
        f"- Individual reports written: **{len(paths)}**\n"
        f"- Official artifact: `{artifact}`\n"
        f"- Zoning registry: `{args.rules_path}`\n"
        f"- Validation: **{'PASS' if validation['valid'] else 'REVIEW_REQUIRED'}**\n\n"
        f"- Official coverage: `{json.dumps(audit['official_coverage'], ensure_ascii=False)}`\n"
        f"- Official gaps: `{json.dumps(audit['missing_official'], ensure_ascii=False)}`\n"
        f"- Sold-history status: `{json.dumps(audit['sold_history_status'], ensure_ascii=False)}`\n"
        f"- REALTOR.ca detail-history status: `{json.dumps(audit['realtor_history_status'], ensure_ascii=False)}`\n"
        f"- Geometry source: `{json.dumps(audit['geometry_source_counts'], ensure_ascii=False)}`\n"
        f"- Manifest: `{output_dir / 'manifest.json'}`\n\n"
        "Completeness gaps are surfaced in the index and individual reports. They do not deduct from score. "
        "Unknown values remain unknown; derived rule values and preliminary estimates are labelled.\n",
        encoding="utf-8",
    )
    if args.validate:
        print(json.dumps(validation, ensure_ascii=False, indent=2))
        return 0 if validation["valid"] else 1
    print(json.dumps(completion, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
