#!/usr/bin/env python3
"""Rescue in-range cards from presets rejected as FILTER_NOT_APPLIED.

A preset rejected for FILTER_NOT_APPLIED cannot be trusted for completeness,
but the individual cards it captured are real listings with real prices.
Cards whose asking price falls inside the preset's configured price band
[price_min, price_max] are salvaged into the enrichment artifact with
``RESCUED_FROM_REJECTED_PRESET`` in ``reason_codes`` and re-enriched through
the official adapters (BCA / zoning / road / school).  Detail HTML and photos
are not recovered here — run the photo phase afterwards.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from northshore_watchdog.config import load_config  # noqa: E402
from northshore_watchdog.research.discovery import DiscoveryCard  # noqa: E402
from northshore_watchdog.research.discovery_official import (  # noqa: E402
    enrich_discovery_cards,
)

RESCUE_CODE = "RESCUED_FROM_REJECTED_PRESET"


def _cards_from_capture(preset_result: Mapping[str, Any]) -> list[dict[str, Any]]:
    capture = preset_result.get("capture") or {}
    cards = capture.get("cards") or []
    return [c for c in cards if isinstance(c, Mapping)]


def _price_band(preset_result: Mapping[str, Any]) -> tuple[float | None, float | None]:
    plan = preset_result.get("filter_plan") or {}
    capture = preset_result.get("capture") or {}
    filters = capture.get("applied_filters") or plan.get("applied_filters") or {}
    lo = filters.get("price_min") or plan.get("price_min")
    hi = filters.get("price_max") or plan.get("price_max")
    try:
        lo = float(lo) if lo is not None else None
        hi = float(hi) if hi is not None else None
    except (TypeError, ValueError):
        return None, None
    return lo, hi


def _post_filter_municipality(preset_result: Mapping[str, Any]) -> str | None:
    plan = preset_result.get("filter_plan") or {}
    post = plan.get("post_filters") or {}
    muni = post.get("municipality")
    return str(muni).strip().upper() if muni else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--enrichment", type=Path, required=True,
                        help="enrichment artifact containing rejected preset_results")
    parser.add_argument("--config", default="config/research-discovery.yaml")
    parser.add_argument("--known-mls", type=Path, nargs="*", default=[],
                        help="additional enrichment artifacts whose MLS numbers count as duplicates")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    artifact = json.loads(args.enrichment.read_text(encoding="utf-8"))
    candidates = artifact.get("candidates") or []
    known = {str(c.get("mls_number")) for c in candidates}
    for extra in args.known_mls:
        payload = json.loads(extra.read_text(encoding="utf-8"))
        for c in payload.get("candidates") or payload.get("cards") or []:
            if isinstance(c, Mapping) and c.get("mls_number"):
                known.add(str(c["mls_number"]))

    rescued: list[dict[str, Any]] = []
    for preset_result in artifact.get("preset_results") or []:
        if preset_result.get("status") != "FILTER_NOT_APPLIED":
            continue
        lo, hi = _price_band(preset_result)
        muni = _post_filter_municipality(preset_result)
        name = (preset_result.get("capture") or {}).get("preset_name") or "?"
        for card in _cards_from_capture(preset_result):
            mls = str(card.get("mls_number") or "")
            asking = card.get("asking")
            if not mls or mls in known:
                continue
            in_range = (
                (lo is None or (asking is not None and asking >= lo))
                and (hi is None or (asking is not None and asking <= hi))
            )
            if not in_range:
                continue
            if muni and str(card.get("municipality") or "").strip().upper() != muni:
                continue
            card = dict(card)
            codes = list(card.get("reason_codes") or [])
            codes.append(RESCUE_CODE)
            codes.append(f"RESCUE_PRESET:{name}")
            card["reason_codes"] = codes
            rescued.append(card)
            known.add(mls)

    print(f"rescued {len(rescued)} in-range cards from rejected presets")
    for c in rescued:
        print(f"  {c.get('address'):<32} {c.get('asking')} {c.get('mls_number')}")
    if args.dry_run or not rescued:
        return 0

    cfg = load_config(args.config)
    result = enrich_discovery_cards(rescued, cfg)
    enriched = [c.model_dump(mode="json") if hasattr(c, "model_dump") else dict(c)
                for c in result.cards]
    candidates.extend(enriched)
    artifact["candidates"] = candidates
    artifact["candidate_count"] = len(candidates)
    # Downstream consumers (auto-prefill, packet builder) prefer the enriched
    # card list when present — rescued cards must land there too.
    official = artifact.get("official_enrichment")
    if isinstance(official, dict) and isinstance(official.get("cards"), list):
        official["cards"].extend(enriched)
        official["enriched_card_count"] = len(official["cards"])
    artifact.setdefault("rescue_log", []).append({
        "rescued_count": len(enriched),
        "reason_code": RESCUE_CODE,
        "enrichment_status": result.status,
    })
    args.enrichment.write_text(json.dumps(artifact, ensure_ascii=False, indent=1))
    print(f"candidate_count -> {len(candidates)} (enrichment {result.status})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
