from __future__ import annotations

import csv
import hashlib
import json
import math
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from .base import LocationComposite, LocationSnapshot, ProviderResult
from .scoring import REQUIRED_EXTERNAL_SCORES, WALK_SCORE_WEIGHTS, build_location_composite
from .store import LocationSnapshotStore
from .walk_score import WalkScoreProvider


MODEL_NAME = "FAMILY_DETACHED_WALK_SCORE"
MODEL_VERSION = "1.0"
WEIGHTS_VERSION = "FAMILY_DETACHED_WALK_SCORE_V1"
BENCHMARKS = (
    ("2876 W 18TH AVENUE", "2876 W18"),
    ("3974 W 10TH AVENUE", "3974 W10"),
    ("333 E 13TH AVENUE", "333 E13"),
    ("5454 MELBOURNE STREET", "5454 Melbourne"),
    ("4561 NANAIMO STREET", "4561 Nanaimo"),
    ("486 E KING EDWARD AVENUE", "486 King Edward"),
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _address_key(value: Any) -> str:
    return " ".join(str(value or "").upper().replace(",", " ").split())


def _stable_identity(record: Mapping[str, Any]) -> bool:
    return bool(str(record.get("property_id") or "").strip() or str(record.get("mls") or record.get("mls_number") or "").strip() or _address_key(record.get("address")))


def _property_id(record: Mapping[str, Any], row_number: int = 0) -> str:
    value = str(record.get("property_id") or "").strip()
    if value:
        return value
    mls = str(record.get("mls") or record.get("mls_number") or "").strip()
    if mls:
        return f"mls:{mls}"
    address = _address_key(record.get("address"))
    if address:
        return f"address:{address}"
    digest = hashlib.sha256(json.dumps(dict(record), sort_keys=True, default=str).encode()).hexdigest()[:16]
    return f"anonymous:{row_number}:{digest}"


def _safe_coordinate(value: Any, *, lower: float, upper: float) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and lower <= number <= upper else None


def _invalid_provider(provider: str, latitude: float | None, longitude: float | None, observed_at: datetime) -> ProviderResult:
    return ProviderResult(
        provider=provider,
        status="INVALID_REQUEST",
        queried_at=observed_at,
        latitude=latitude,
        longitude=longitude,
        error="valid latitude/longitude required",
    )


def _cache_matches(
    snapshot: LocationSnapshot,
    *,
    address: str,
    mls: str | None,
    latitude: float | None,
    longitude: float | None,
    model_name: str,
    model_version: str,
) -> bool:
    if _address_key(snapshot.address) != _address_key(address):
        return False
    if mls and snapshot.mls and snapshot.mls != mls:
        return False
    if snapshot.model_name != model_name or snapshot.model_version != model_version:
        return False
    if snapshot.latitude is None or latitude is None:
        return snapshot.latitude is None and latitude is None and snapshot.longitude is None and longitude is None
    return abs(snapshot.latitude - latitude) <= 1e-6 and abs((snapshot.longitude or 0.0) - (longitude or 0.0)) <= 1e-6


def enrich_properties(
    records: Iterable[Mapping[str, Any]],
    *,
    walk_score: WalkScoreProvider,
    store: LocationSnapshotStore,
    observed_at: datetime | None = None,
    ttl_days: int = 180,
    model_name: str = MODEL_NAME,
    model_version: str = MODEL_VERSION,
) -> list[LocationSnapshot]:
    """Enrich every input property using only Walk Score and append snapshots."""

    observed = observed_at or _now()
    snapshots: list[LocationSnapshot] = []
    for row_number, record in enumerate(records):
        pid = _property_id(record, row_number)
        stable_identity = _stable_identity(record)
        address = str(record.get("address") or "")
        mls = str(record.get("mls") or record.get("mls_number") or "").strip() or None
        latitude = _safe_coordinate(record.get("latitude"), lower=-90.0, upper=90.0)
        longitude = _safe_coordinate(record.get("longitude"), lower=-180.0, upper=180.0)
        cached = store.latest(pid) if stable_identity else None
        if (
            cached is not None
            and not store.should_refresh(cached, now=observed, ttl_days=ttl_days)
            and _cache_matches(
                cached,
                address=address,
                mls=mls,
                latitude=latitude,
                longitude=longitude,
                model_name=model_name,
                model_version=model_version,
            )
        ):
            snapshots.append(cached)
            continue

        if latitude is None or longitude is None:
            walk = _invalid_provider("walk_score", latitude, longitude, observed)
        else:
            walk = walk_score.get_location_scores(address=address, latitude=latitude, longitude=longitude)
        composite = build_location_composite(
            walk.scores,
            model_name=model_name,
            weights_version=WEIGHTS_VERSION,
        )
        snapshot = LocationSnapshot(
            property_id=pid,
            address=address,
            mls=mls,
            latitude=latitude,
            longitude=longitude,
            queried_at=observed,
            model_name=model_name,
            model_version=model_version,
            walk_score=walk,
            composite=composite,
        )
        store.append(snapshot)
        snapshots.append(snapshot)
    return snapshots


def _schema(model_name: str = MODEL_NAME, model_version: str = MODEL_VERSION) -> dict[str, Any]:
    return {
        "schema_version": "location-provider-walk-score-v1",
        "model_name": model_name,
        "model_version": model_version,
        "required_external_scores": list(REQUIRED_EXTERNAL_SCORES),
        "score_scale": {"walk_score_api": "0-100", "canonical": "0-100"},
        "normalization": {"walk_score": "identity"},
        "weights": {"urban_access_and_external_location": WALK_SCORE_WEIGHTS},
        "provider_roles": {"walk_score": {"primary": ["walk", "transit", "bike"]}},
        "not_scored_here": [
            "quiet", "greenery", "parks", "car_friendly", "daycares",
            "school_quality", "area_baseline_premium", "park_facing", "view",
            "major_arterial", "truck_route", "zoning", "lane", "slope",
        ],
        "residential_environment": {
            "status": "NOT_AVAILABLE",
            "reason": "Walk Score does not provide the environmental dimensions used by the prior two-provider model.",
        },
    }


def _provider_row(snapshot: LocationSnapshot) -> dict[str, Any]:
    normalized = dict(snapshot.composite.normalized_scores)
    return {
        "property_id": snapshot.property_id,
        "address": snapshot.address,
        "mls": snapshot.mls or "",
        "queried_at": snapshot.queried_at.isoformat(),
        "walk_status": snapshot.walk_score.status,
        "composite_status": snapshot.composite.status,
        "walk": normalized.get("walk"),
        "transit": normalized.get("transit"),
        "bike": normalized.get("bike"),
        "urban_access_score": snapshot.composite.urban_access_score,
        "external_location_score": snapshot.composite.external_location_score,
        "residential_environment_score": snapshot.composite.residential_environment_score,
        "residential_environment_status": snapshot.composite.residential_environment_status,
        "missing_scores": ",".join(snapshot.composite.missing_scores),
    }


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames = [
        "benchmark", "address", "status", "property_id", "mls", "queried_at",
        "walk_status", "composite_status", "walk", "transit", "bike",
        "urban_access_score", "external_location_score", "residential_environment_score",
        "residential_environment_status", "missing_scores",
    ]
    extras = sorted({key for row in rows for key in row if key not in fieldnames})
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames + extras)
        writer.writeheader()
        writer.writerows(rows)


def _benchmark_rows(snapshots: Iterable[LocationSnapshot]) -> list[dict[str, Any]]:
    by_address = {_address_key(snapshot.address): snapshot for snapshot in snapshots}
    rows: list[dict[str, Any]] = []
    for address, label in BENCHMARKS:
        snapshot = by_address.get(_address_key(address))
        if snapshot is None:
            rows.append({"benchmark": label, "address": address, "status": "NOT_IN_INPUT"})
            continue
        row = _provider_row(snapshot)
        row.update({"benchmark": label, "address": address, "status": "FOUND"})
        rows.append(row)
    return rows


def write_provider_artifacts(
    snapshots: Iterable[LocationSnapshot],
    output_dir: str | Path,
    *,
    model_name: str = MODEL_NAME,
    model_version: str = MODEL_VERSION,
) -> dict[str, Path]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    materialized = list(snapshots)
    counts = Counter((snapshot.walk_score.status, snapshot.composite.status) for snapshot in materialized)

    schema_path = output / "provider-schema.json"
    schema_path.write_text(json.dumps(_schema(model_name, model_version), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    benchmark_path = output / "benchmark-results.csv"
    _write_csv(benchmark_path, _benchmark_rows(materialized))

    comparison_lines = [
        "# Walk Score Provider Comparison", "",
        f"- Model: `{model_name}_V{model_version}`",
        f"- Properties processed: `{len(materialized)}`",
        "- Canonical provider: Walk Score supplies walk/transit/bike.",
        "- This model has no residential-environment provider inputs.", "",
        "## Provider status", "", "| Walk Score | Composite | Count |", "|---|---|---:|",
    ]
    for (walk, composite), count in sorted(counts.items()):
        comparison_lines.append(f"| {walk} | {composite} | {count} |")
    comparison_lines.extend([
        "", "## Interpretation", "",
        "`external_location_score` is the Walk Score urban-access composite only; it is not a full residential-environment score.",
        "Raw responses and normalized values remain attached to each append-only snapshot.", "",
    ])
    comparison_path = output / "provider-comparison.md"
    comparison_path.write_text("\n".join(comparison_lines), encoding="utf-8")

    audit_path = output / "double-counting-audit.md"
    audit_path.write_text(
        "# Double-counting Audit\n\n"
        "- Walk Score `walk/transit/bike` are the only provider inputs to this model.\n"
        "- School quality/catchment, area residual, road/truck/highway exposure, park-facing, view, and parcel/development facts remain separate.\n"
        "- No custom GIS composite is used in this provider layer.\n",
        encoding="utf-8",
    )

    configured = sum(snapshot.composite.status == "COMPLETE" for snapshot in materialized)
    recommendation_path = output / "provider-recommendation.md"
    recommendation_path.write_text(
        "# Provider Recommendation\n\n"
        f"Current run processed `{len(materialized)}` properties; `{configured}` have a complete Walk Score composite.\n\n"
        "Walk Score requires an API key; until it is configured, the composite remains `UNKNOWN` and no zero-imputation or production ranking fallback is allowed.\n\n"
        "After the key is configured, run the six benchmark addresses and inspect `benchmark-results.csv` plus append-only snapshot history before using this diagnostic score in production ranking.\n",
        encoding="utf-8",
    )
    return {
        "comparison": comparison_path,
        "schema": schema_path,
        "benchmarks": benchmark_path,
        "audit": audit_path,
        "recommendation": recommendation_path,
    }


def load_records(path: str | Path) -> list[dict[str, Any]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    candidates = payload.get("candidates") if isinstance(payload, Mapping) else None
    if not isinstance(candidates, list):
        raise ValueError("input artifact must contain a candidates list")
    return [item for item in candidates if isinstance(item, Mapping)]
