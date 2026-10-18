"""Strategy V2 human-calibration workflow.

The 166-property universe is split into a 120-property calibration set and a
46-property blind-test set (``data/research/history/strategy-v2-shadow-splits.json``).
This module builds the *blind* reading packet — fact-only cards plus an
annotation surface — and then measures how well the machine criteria reproduce
the human's structured labels.

Design rules carried over from the approved shadow-model spec:

* Reading cards contain facts and extracted evidence only.  Machine scores,
  tracks, cost ledgers, and ranking signals are never rendered into a card.
* Human labels are bands plus a decision, never a point score.
* ``KEEP`` and ``WATCH`` both count toward the "worth human review"
  denominator; ``PASS`` maps to ``DROP``; ``SPECIAL`` is reported separately;
  an explicit ``human_must_keep`` flag promotes a row to ``MUST_KEEP``.
* Blind-split labels never feed the iteration metrics; they are evaluated once
  at the end via ``split="blind"``.
"""

from __future__ import annotations

import csv
import hashlib
import html
import io
import json
import math
import os
import re
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .strategy_v2_online_screening import evaluate_recall


BAND_LABELS: tuple[str, ...] = ("0-20", "21-40", "41-60", "61-80", "81-100")
BAND_MIDPOINT: dict[str, float] = {
    "0-20": 10.0,
    "21-40": 30.0,
    "41-60": 50.0,
    "61-80": 70.0,
    "81-100": 90.0,
}

DIMENSION_FIELDS: tuple[str, ...] = (
    "current_use",
    "redevelopment",
    "house_reno",
    "location",
    "lot",
    "price_value",
)

# Which existing model output each human dimension band is compared against.
# The mapping is deliberately explicit so a change of criteria is auditable.
DIMENSION_MODEL_FIELD: dict[str, str] = {
    "current_use": "current_use_score",
    "redevelopment": "redevelopment_score",
    "house_reno": "reno_score",
    "location": "location_quality_score",
    "lot": "redevelopment_geometry_adequacy_score",
    "price_value": "current_package_value_for_money_score",
}

TRACK_VALUES: tuple[str, ...] = (
    "HOME_REBUILD",
    "LAND_DEVELOPMENT",
    "PREMIUM_SMALL_LOT",
    "INCOME_HOLD",
    "SPECIAL_SITUATION",
    "UNCLASSIFIED_REVIEW",
)

DECISION_VALUES: tuple[str, ...] = ("KEEP", "WATCH", "PASS", "SPECIAL")

TAG_VALUES: tuple[str, ...] = (
    "ROAD",
    "RENO",
    "SCHOOL",
    "LOT",
    "PRICE",
    "ZONING",
    "LAYOUT",
    "VIEW",
    "CONDITION",
    "OTHER",
    "NARROW_FRONTAGE",
    "DEEP_LOT",
    "CORNER",
    "LANE",
)

# Decisions that count as "worth retaining for human review".
_RETENTION_LABELS = {"KEEP": "KEEP", "WATCH": "KEEP", "PASS": "DROP", "SPECIAL": "SPECIAL"}

DISAGREEMENT_THRESHOLD = 25.0
TOP_N_DEPTHS = (10, 30)
SCORE_VIEWS: tuple[str, ...] = (
    "current_use_score",
    "redevelopment_score",
    "dual_value_score",
)

ANNOTATION_FIELDS: tuple[str, ...] = (
    "human_current_use_band",
    "human_redevelopment_band",
    "human_house_reno_band",
    "human_location_band",
    "human_lot_band",
    "human_price_value_band",
    "human_primary_track",
    "human_secondary_track",
    "human_decision",
    "human_must_keep",
    "human_tags",
    "human_reason",
    "dual_override",
    "human_dual_band",
)

TEMPLATE_COLUMNS: tuple[str, ...] = (
    "property_key",
    "split",
    "address",
    "mls",
    "card_file",
    "source_url",
    *ANNOTATION_FIELDS,
    "annotated_at",
)


class _CalibModel(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)


class HumanAnnotation(_CalibModel):
    """One human's structured label for one property."""

    property_key: str
    split: Literal["calibration", "blind", "unassigned"] = "unassigned"
    human_current_use_band: str | None = None
    human_redevelopment_band: str | None = None
    human_house_reno_band: str | None = None
    human_location_band: str | None = None
    human_lot_band: str | None = None
    human_price_value_band: str | None = None
    human_primary_track: str | None = None
    human_secondary_track: str | None = None
    human_decision: Literal["KEEP", "WATCH", "PASS", "SPECIAL"] | None = None
    human_must_keep: bool = False
    human_tags: list[str] = Field(default_factory=list)
    human_reason: str = ""
    dual_override: bool = False
    human_dual_band: str | None = None
    annotated_at: str | None = None
    # Provenance for card exports: which fields the human actually touched,
    # plus the auto-prefill values they started from.  Rows imported from a
    # hand-filled CSV have neither — populated fields then count as human-set.
    human_set_fields: list[str] = Field(default_factory=list)
    auto_snapshot: dict[str, Any] | None = None
    data_only_signal: str | None = None

    def has_content(self) -> bool:
        return any(
            [
                self.human_current_use_band,
                self.human_redevelopment_band,
                self.human_house_reno_band,
                self.human_location_band,
                self.human_lot_band,
                self.human_price_value_band,
                self.human_primary_track,
                self.human_decision,
                self.human_must_keep,
                self.human_tags,
                self.human_reason.strip(),
                self.dual_override,
            ]
        )

    def is_human_labeled(self) -> bool:
        """True when a human actually entered at least one field.

        A card export carries auto-prefilled bands; if the human never touched
        anything (``human_set_fields`` empty, ``auto_snapshot`` present) the row
        is auto-only and must not count as a human label.
        """

        if self.human_set_fields:
            return True
        if self.auto_snapshot:
            return False
        return self.has_content()

    def field_is_human(self, field: str) -> bool:
        """Provenance of one annotation field for evaluation purposes."""

        if self.human_set_fields:
            return field in self.human_set_fields
        if self.auto_snapshot:
            return False
        return getattr(self, field, None) is not None

    def is_reviewed(self) -> bool:
        """The human made the final call — decision field was human-set."""

        return self.human_decision is not None and self.field_is_human(
            "human_decision"
        )


# ---------------------------------------------------------------------------
# Split manifest
# ---------------------------------------------------------------------------


def load_split_manifest(path: Path | str) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError(f"split manifest must be an object: {path}")
    return {
        "schema_version": payload.get("schema_version"),
        "random_seed": payload.get("random_seed"),
        "calibration_keys": list(payload.get("calibration_keys") or []),
        "blind_test_keys": list(payload.get("blind_test_keys") or []),
        "split_sha256": payload.get("split_sha256"),
    }


def split_for_key(splits: Mapping[str, Any], property_key: str) -> str:
    if property_key in set(splits.get("calibration_keys") or []):
        return "calibration"
    if property_key in set(splits.get("blind_test_keys") or []):
        return "blind"
    return "unassigned"


# ---------------------------------------------------------------------------
# Annotation parsing / loading
# ---------------------------------------------------------------------------


def _clean_str(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def _clean_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().upper() in {"1", "TRUE", "YES", "Y", "是"}


def _clean_band(value: Any) -> str | None:
    text = _clean_str(value)
    if text is None:
        return None
    normalized = text.upper().replace(" ", "")
    for label in BAND_LABELS:
        if normalized == label:
            return label
    raise ValueError(f"unknown band value: {text!r}; expected one of {BAND_LABELS}")


def _clean_choice(value: Any, allowed: Iterable[str]) -> str | None:
    text = _clean_str(value)
    if text is None:
        return None
    normalized = text.upper().replace(" ", "_")
    if normalized in set(allowed):
        return normalized
    raise ValueError(f"unknown value {text!r}; expected one of {sorted(set(allowed))}")


def _clean_tags(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        parts = re.split(r"[;,]", value)
    elif isinstance(value, Iterable):
        parts = [str(item) for item in value]
    else:
        parts = [str(value)]
    tags: list[str] = []
    for part in parts:
        tag = part.strip().upper().replace(" ", "_")
        if tag and tag in TAG_VALUES and tag not in tags:
            tags.append(tag)
        elif tag and tag not in TAG_VALUES:
            raise ValueError(f"unknown tag {part!r}; expected one of {sorted(TAG_VALUES)}")
    return tags


def parse_annotation(
    raw: Mapping[str, Any], *, split: str = "unassigned"
) -> tuple[HumanAnnotation | None, list[str]]:
    """Validate one raw annotation row.

    Returns ``(annotation, errors)``.  An entirely empty row (only an identity)
    returns ``(None, [])`` so untouched template rows are skipped silently;
    malformed values return ``(None, [errors])``.
    """

    key = _clean_str(raw.get("property_key"))
    if not key:
        return None, ["missing property_key"]

    errors: list[str] = []
    fields: dict[str, Any] = {"property_key": key, "split": split}

    for dim in DIMENSION_FIELDS:
        field = f"human_{dim}_band"
        try:
            fields[field] = _clean_band(raw.get(field))
        except ValueError as exc:
            errors.append(f"{key}: {exc}")

    for field, allowed in (
        ("human_primary_track", TRACK_VALUES),
        ("human_secondary_track", TRACK_VALUES),
        ("human_decision", DECISION_VALUES),
    ):
        try:
            fields[field] = _clean_choice(raw.get(field), allowed)
        except ValueError as exc:
            errors.append(f"{key}: {exc}")

    try:
        fields["human_dual_band"] = _clean_band(raw.get("human_dual_band"))
    except ValueError as exc:
        errors.append(f"{key}: {exc}")

    try:
        fields["human_tags"] = _clean_tags(raw.get("human_tags"))
    except ValueError as exc:
        errors.append(f"{key}: {exc}")

    fields["human_must_keep"] = _clean_bool(raw.get("human_must_keep"))
    fields["dual_override"] = _clean_bool(raw.get("dual_override"))
    fields["human_reason"] = str(raw.get("human_reason") or "").strip()
    fields["annotated_at"] = _clean_str(raw.get("annotated_at"))

    set_fields = raw.get("human_set_fields")
    if isinstance(set_fields, list):
        fields["human_set_fields"] = [str(f) for f in set_fields]
    auto_snapshot = raw.get("auto_snapshot")
    if isinstance(auto_snapshot, Mapping):
        fields["auto_snapshot"] = dict(auto_snapshot)
    fields["data_only_signal"] = _clean_str(raw.get("data_only_signal"))

    if errors:
        return None, errors
    annotation = HumanAnnotation(**fields)
    if not annotation.has_content():
        return None, []
    return annotation, []


def annotation_to_label(annotation: HumanAnnotation) -> str | None:
    """Map the human decision onto the recall label vocabulary."""

    if annotation.human_decision is None:
        return None
    if annotation.human_must_keep and annotation.human_decision in {"KEEP", "WATCH"}:
        return "MUST_KEEP"
    return _RETENTION_LABELS.get(annotation.human_decision)


def load_annotations(source: Path | str) -> tuple[list[HumanAnnotation], list[str]]:
    """Load annotations from a JSON file, a CSV file, or a directory of JSON files.

    JSON payloads may be a single annotation object or a list of objects — this
    is what the per-card ``导出本套`` and the index ``导出全部`` controls emit.
    """

    path = Path(source)
    raws: list[tuple[str, Mapping[str, Any]]] = []
    errors: list[str] = []

    if path.is_dir():
        for child in sorted(path.glob("*.json")):
            raws.extend(_json_annotation_payloads(child, errors))
    elif path.suffix.lower() == ".csv":
        raws.extend(_csv_annotation_rows(path, errors))
    else:
        raws.extend(_json_annotation_payloads(path, errors))

    annotations: list[HumanAnnotation] = []
    for origin, raw in raws:
        declared_split = _clean_str(raw.get("split")) or "unassigned"
        split = declared_split if declared_split in {"calibration", "blind"} else "unassigned"
        annotation, row_errors = parse_annotation(raw, split=split)
        errors.extend(f"{origin}: {err}" for err in row_errors)
        if annotation is not None:
            annotations.append(annotation)
    return annotations, errors


def _json_annotation_payloads(
    path: Path, errors: list[str]
) -> list[tuple[str, Mapping[str, Any]]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"{path.name}: cannot parse JSON ({exc})")
        return []
    if isinstance(payload, Mapping):
        # A packet export wraps rows under "annotations".
        inner = payload.get("annotations")
        if isinstance(inner, list):
            return [(f"{path.name}#{i}", row) for i, row in enumerate(inner) if isinstance(row, Mapping)]
        return [(path.name, payload)]
    if isinstance(payload, list):
        return [(f"{path.name}#{i}", row) for i, row in enumerate(payload) if isinstance(row, Mapping)]
    errors.append(f"{path.name}: unsupported JSON payload")
    return []


def _csv_annotation_rows(path: Path, errors: list[str]) -> list[tuple[str, Mapping[str, Any]]]:
    try:
        with path.open(newline="", encoding="utf-8-sig") as fh:
            return [
                (f"{path.name}:row{i + 2}", {k: v for k, v in row.items() if k})
                for i, row in enumerate(csv.DictReader(fh))
            ]
    except OSError as exc:
        errors.append(f"{path.name}: cannot read CSV ({exc})")
        return []


# ---------------------------------------------------------------------------
# Blind reading cards
# ---------------------------------------------------------------------------


def card_slug(property_key: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "-", property_key.strip()).strip("-")


def _num(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def build_card_context(
    *,
    shadow_row: Mapping[str, Any],
    candidate: Mapping[str, Any] | None,
    photo_manifest: Mapping[str, Any] | None,
    split: str,
    prev_key: str | None,
    next_key: str | None,
    assets_from_dir: Path | str | None = None,
    auto: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble the fact-only context for one blind reading card.

    Only factual fields are extracted here.  Machine judgments — scores,
    tracks, cost ledgers, gate findings — are intentionally never copied into
    the context so they cannot leak into the rendered card.
    """

    candidate = candidate or {}
    location = shadow_row.get("location") or {}
    parcel = location.get("parcel_facts") or {}
    school = location.get("school") or {}
    acquisition = shadow_row.get("acquisition") or {}
    scenarios = shadow_row.get("development_scenarios") or []
    base = next(
        (s for s in scenarios if s.get("scenario_name") == "BASE"),
        scenarios[0] if scenarios else {},
    )
    zoning = ((base.get("rule_basis") or {}).get("zoning")) or None

    photos: list[dict[str, Any]] = []
    observations: dict[str, list[str]] = {
        "visible_defects": [],
        "maintenance_signals": [],
        "cosmetic_signals": [],
        "visual_evidence": [],
    }
    classified = False
    images = (photo_manifest or {}).get("images") or []
    for image in sorted(images, key=lambda item: item.get("original_order") or 0):
        local_path = image.get("local_path")
        if local_path and assets_from_dir:
            # Repo-relative src works both from file:// pages and from an
            # HTTP server rooted at the repository root.
            src = os.path.relpath(str(local_path), str(assets_from_dir))
        elif local_path:
            src = f"file://{local_path}"
        else:
            src = image.get("source_url")
        if not src:
            continue
        photos.append(
            {
                "src": src,
                "order": image.get("original_order"),
                "local": bool(local_path),
                "labels": list(image.get("content_labels") or []),
            }
        )
        if image.get("visual_status") == "CLASSIFIED":
            classified = True
            for group in observations:
                for item in image.get(group) or []:
                    if item not in observations[group]:
                        observations[group].append(item)

    return {
        "property_key": shadow_row.get("property_key"),
        "slug": card_slug(str(shadow_row.get("property_key") or "unknown")),
        "split": split,
        "prev_href": f"{card_slug(prev_key)}.html" if prev_key else None,
        "next_href": f"{card_slug(next_key)}.html" if next_key else None,
        "address": shadow_row.get("address") or candidate.get("address"),
        "mls": shadow_row.get("mls") or candidate.get("mls_number"),
        "source_url": candidate.get("source_url"),
        "source_name": candidate.get("source_name"),
        "observed_at": candidate.get("observed_at"),
        "asking": _num(candidate.get("asking") or acquisition.get("asking")),
        "taxes": _num(candidate.get("taxes")),
        "bedrooms": candidate.get("bedrooms"),
        "bathrooms": candidate.get("bathrooms"),
        "finished_sqft": _num(candidate.get("finished_sqft")),
        "year_built": candidate.get("year_built"),
        "dom": candidate.get("dom"),
        "ownership_type": candidate.get("ownership_type"),
        "property_type": candidate.get("property_type"),
        "listing_status": candidate.get("listing_status"),
        "neighbourhood": candidate.get("neighbourhood"),
        "municipality": candidate.get("municipality"),
        "lot_area_sqft": _num(candidate.get("lot_area_sqft")),
        "lot_dimensions": candidate.get("lot_dimensions"),
        "frontage_ft": _num(candidate.get("frontage_ft")),
        "depth_ft": _num(candidate.get("depth_ft")),
        "geometry_source": candidate.get("geometry_source"),
        "assessment_total": _num(acquisition.get("assessment_total")),
        "assessment_land": _num(acquisition.get("assessment_land")),
        "assessment_improvement": _num(acquisition.get("assessment_improvement")),
        "assessment_year": (acquisition.get("assessment_signals") or {}).get("assessment_year"),
        "zoning": zoning,
        "elementary_school": school.get("elementary_catchment") or candidate.get("elementary_school"),
        "secondary_school": school.get("secondary_catchment") or candidate.get("secondary_school"),
        "road_class": parcel.get("road_class"),
        "nearest_major_road": parcel.get("nearest_major_road"),
        "distance_to_major_road_m": _num(parcel.get("distance_to_major_road_m")),
        "fronts_major_road": parcel.get("property_fronts_major_road"),
        "park_facing": parcel.get("park_facing"),
        "view_type": parcel.get("view_type"),
        "waterfront": parcel.get("waterfront"),
        "corner_lot": parcel.get("corner_lot"),
        "lane_observed": parcel.get("lane_observed"),
        "slope": parcel.get("slope"),
        "description": candidate.get("description") or "",
        "photos": photos,
        "photo_count": len(photos),
        "photo_observations": observations if classified else None,
        "auto": dict(auto) if auto else None,
    }


def _money(value: Any) -> str:
    number = _num(value)
    return f"${number:,.0f}" if number is not None else "—"


def _fact(label: str, value: Any, unit: str = "") -> str:
    if value is None or value == "":
        rendered = "—"
    elif isinstance(value, bool):
        rendered = "是" if value else "否"
    elif isinstance(value, float):
        rendered = f"{value:,.0f}{unit}"
    else:
        rendered = f"{value}{unit}"
    return f'<div class="fact"><span class="k">{html.escape(label)}</span><span class="v">{html.escape(rendered)}</span></div>'


def _card_css() -> str:
    return """
    * { box-sizing: border-box; }
    body { font-family: -apple-system, "PingFang SC", "Helvetica Neue", sans-serif;
           margin: 0; color: #1c1c1e; background: #f5f5f7; }
    header { position: sticky; top: 0; background: #fff; border-bottom: 1px solid #ddd;
             padding: 10px 20px; display: flex; align-items: center; gap: 14px; z-index: 5; flex-wrap: wrap; }
    header h1 { font-size: 18px; margin: 0; }
    .badge { font-size: 11px; padding: 2px 8px; border-radius: 8px; background: #e8e8ed; }
    .badge.blind { background: #ffe9d6; }
    .badge.calibration { background: #d8f0e0; }
    .badge.autoBadge { background: #e3ecff; color: #2456a6; }
    .badge.reviewed { background: #1d7a3a; color: #fff; }
    .auto-meta { font-size: 13px; }
    .auto-meta .sig { font-size: 15px; font-weight: 600; }
    .auto-meta ul { margin: 6px 0; padding-left: 18px; }
    .hint { font-size: 12px; color: #b3541e; margin-left: 6px; }
    nav a { font-size: 13px; color: #06c; text-decoration: none; margin-right: 10px; }
    main { max-width: 1080px; margin: 0 auto; padding: 16px 20px 80px; }
    section { background: #fff; border: 1px solid #e2e2e6; border-radius: 10px;
              padding: 14px 16px; margin-bottom: 14px; }
    h2 { font-size: 14px; margin: 0 0 10px; color: #555; text-transform: uppercase; letter-spacing: .04em; }
    .facts { display: grid; grid-template-columns: repeat(auto-fill, minmax(160px, 1fr)); gap: 8px; }
    .fact .k { display: block; font-size: 11px; color: #888; }
    .fact .v { font-size: 14px; }
    .photos { display: flex; gap: 8px; overflow-x: auto; padding-bottom: 10px; }
    .photos::-webkit-scrollbar { height: 10px; }
    .photos::-webkit-scrollbar-track { background: #eef0f3; border-radius: 5px; }
    .photos::-webkit-scrollbar-thumb { background: #b9c0c8; border-radius: 5px; }
    .photos .ph { position: relative; flex: 0 0 auto; width: 240px; }
    .photos img { width: 100%; height: 160px; object-fit: cover; display: block;
                  border-radius: 8px; border: 1px solid #ddd; background: #f2f3f5; }
    .photos .ord { position: absolute; top: 4px; left: 4px; background: rgba(0,0,0,.55);
                   color: #fff; font-size: 10px; padding: 1px 6px; border-radius: 6px; }
    .pnav { display: flex; align-items: center; gap: 8px; margin-top: 2px; }
    .pnav button { width: 30px; height: 22px; border: 1px solid #d0d3d9; border-radius: 6px;
                   background: #f6f7f9; cursor: pointer; font-size: 14px; line-height: 1; }
    .pnav input[type=range] { flex: 1; height: 18px; accent-color: #5b7ea6; cursor: pointer; }
    details summary { cursor: pointer; font-size: 13px; color: #06c; }
    .desc { white-space: pre-wrap; font-size: 13px; line-height: 1.55; }
    .optgroup { margin-bottom: 12px; }
    .optgroup .lbl { font-size: 12px; color: #666; margin-bottom: 5px; display: block; }
    .opts button { margin: 0 6px 6px 0; padding: 6px 12px; border: 1px solid #c7c7cc;
                   border-radius: 8px; background: #fff; cursor: pointer; font-size: 13px; }
    .opts button.on { background: #0a7aff; color: #fff; border-color: #0a7aff; }
    .opts button.decision.on { background: #1d7a3a; border-color: #1d7a3a; }
    .opts button.auto { border-color: #0a7aff; color: #0a7aff; background: #eaf3ff; }
    .opts button.auto::after { content: " ·auto"; font-size: 9px; opacity: .7; }
    .opts button.decision.auto { border-color: #b3541e; color: #b3541e; background: #fdf0e6; }
    textarea { width: 100%; min-height: 64px; font-size: 13px; padding: 8px;
               border: 1px solid #c7c7cc; border-radius: 8px; }
    .row { display: flex; gap: 18px; flex-wrap: wrap; }
    .row > div { min-width: 220px; flex: 1; }
    .toolbar { position: sticky; bottom: 0; background: #fff; border-top: 1px solid #ddd;
               padding: 10px 20px; display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
    .toolbar button { padding: 8px 16px; border-radius: 8px; border: 1px solid #0a7aff;
                      background: #0a7aff; color: #fff; cursor: pointer; font-size: 13px; }
    .toolbar button.ghost { background: #fff; color: #0a7aff; }
    #saveState { font-size: 12px; color: #1d7a3a; }
    ul.obs { margin: 6px 0; padding-left: 18px; font-size: 13px; }
    """


def _card_js(ctx: Mapping[str, Any]) -> str:
    storage_key = f"calib-annotation:{ctx['property_key']}"
    payload_key = json.dumps(storage_key)
    auto = ctx.get("auto") or {}
    auto_fields = dict(auto.get("auto") or {})
    auto_meta = {
        key: auto.get(key)
        for key in (
            "data_only_signal",
            "confidence",
            "must_keep_candidate",
            "dual_override_candidate",
            "needs_human_review",
            "reasons",
            "rules_version",
        )
    }
    annotation_schema = json.dumps(
        {
            "property_key": ctx["property_key"],
            "split": ctx["split"],
            "address": ctx.get("address"),
            "mls": ctx.get("mls"),
            **{field: None for field in ANNOTATION_FIELDS if field != "human_tags"},
            "human_tags": [],
            "annotated_at": None,
        },
        ensure_ascii=False,
    )
    return f"""
    const STORAGE_KEY = {payload_key};
    const BLANK = {annotation_schema};
    const AUTO = {json.dumps(auto_fields, ensure_ascii=False)};
    const AUTO_META = {json.dumps(auto_meta, ensure_ascii=False)};
    const HUMAN_FIELDS = {json.dumps(list(ANNOTATION_FIELDS), ensure_ascii=False)};
    let state = {{}};
    try {{
      const saved = JSON.parse(localStorage.getItem(STORAGE_KEY));
      if (saved && Array.isArray(saved.human_set_fields)) {{
        state = Object.fromEntries(saved.human_set_fields.map(f => [f, saved[f]]));
      }} else if (saved) {{
        // legacy record without provenance: non-null fields count as human
        HUMAN_FIELDS.forEach(f => {{
          const v = saved[f];
          if (v != null && !(Array.isArray(v) && !v.length) && v !== '') state[f] = v;
        }});
      }}
    }} catch (e) {{ state = {{}}; }}

    function collect() {{
      const out = Object.assign({{}}, BLANK, AUTO, state);
      out.property_key = BLANK.property_key;
      out.split = BLANK.split;
      out.address = BLANK.address;
      out.mls = BLANK.mls;
      out.human_reason = (document.getElementById('human_reason') || {{value: ''}}).value.trim();
      out.human_set_fields = Object.keys(state);
      out.auto_snapshot = AUTO;
      out.data_only_signal = AUTO_META.data_only_signal || null;
      return out;
    }}
    function save() {{
      const out = collect();
      out.annotated_at = new Date().toISOString();
      localStorage.setItem(STORAGE_KEY, JSON.stringify(out));
      const el = document.getElementById('saveState');
      if (el) el.textContent = '已保存 ' + new Date().toLocaleTimeString();
      refresh();
    }}
    function setField(field, value) {{ state[field] = value; save(); }}
    function toggleTag(tag) {{
      const cur = new Set(state.human_tags || AUTO.human_tags || []);
      if (cur.has(tag)) cur.delete(tag); else cur.add(tag);
      state.human_tags = Array.from(cur); save();
    }}
    function refresh() {{
      document.querySelectorAll('[data-f]').forEach(group => {{
        const f = group.getAttribute('data-f');
        const human = state[f];
        const autoV = AUTO[f];
        group.querySelectorAll('button[data-v]').forEach(b => {{
          const v = b.getAttribute('data-v');
          b.classList.toggle('on', human === v);
          b.classList.toggle('auto', autoV === v && human !== v);
        }});
      }});
      document.querySelectorAll('[data-tag]').forEach(b => {{
        const t = b.getAttribute('data-tag');
        const humanTags = state.human_tags;
        const autoTags = AUTO.human_tags || [];
        b.classList.toggle('on', (humanTags || []).includes(t));
        b.classList.toggle('auto', autoTags.includes(t) && !(humanTags || []).includes(t));
      }});
      ['human_must_keep', 'dual_override'].forEach(f => {{
        const el = document.getElementById(f);
        if (el) el.checked = !!state[f];
      }});
      if (state.human_reason && !document.getElementById('human_reason').value)
        document.getElementById('human_reason').value = state.human_reason;
      const badge = document.getElementById('reviewBadge');
      if (badge) {{
        if (state.human_decision) {{
          badge.textContent = 'HUMAN_REVIEWED'; badge.className = 'badge reviewed';
        }} else if (Object.keys(state).length) {{
          badge.textContent = '人工标注中'; badge.className = 'badge';
        }} else if (AUTO.human_decision) {{
          badge.textContent = '自动' + AUTO.human_decision + '(可改判)';
          badge.className = 'badge autoBadge';
        }} else if (AUTO_META.data_only_signal) {{
          badge.textContent = 'AUTO_PRE_FILLED'; badge.className = 'badge autoBadge';
        }}
      }}
    }}
    function exportThis() {{
      const blob = new Blob([JSON.stringify(collect(), null, 2)], {{type: 'application/json'}});
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = '{card_slug(str(ctx["property_key"]))}.json';
      a.click(); URL.revokeObjectURL(a.href);
    }}
    function copyThis() {{
      navigator.clipboard.writeText(JSON.stringify(collect(), null, 2));
    }}
    function clearThis() {{ if (confirm('清除本套人工标注?')) {{ localStorage.removeItem(STORAGE_KEY); state = {{}}; refresh(); }} }}
    function photoScroll(dir) {{
      const el = document.getElementById('photoStrip');
      if (el) el.scrollBy({{left: dir * 500, behavior: 'smooth'}});
    }}
    window.addEventListener('DOMContentLoaded', () => {{
      refresh();
      const strip = document.getElementById('photoStrip');
      const slider = document.getElementById('photoSlider');
      if (!strip || !slider) return;
      const sync = () => {{
        const max = strip.scrollWidth - strip.clientWidth;
        slider.value = max > 0 ? Math.round(strip.scrollLeft / max * 1000) : 0;
      }};
      strip.addEventListener('scroll', sync);
      slider.addEventListener('input', () => {{
        const max = strip.scrollWidth - strip.clientWidth;
        strip.scrollLeft = slider.value / 1000 * max;
      }});
    }});
    """


def render_card_html(ctx: Mapping[str, Any]) -> str:
    """Render one standalone blind reading card as a self-contained HTML file."""

    e = html.escape
    split_class = "blind" if ctx.get("split") == "blind" else "calibration"
    split_label = "盲测集" if ctx.get("split") == "blind" else "校准集"

    nav = ['<a href="../index.html">← 总面板</a>']
    if ctx.get("prev_href"):
        nav.append(f'<a href="{e(ctx["prev_href"])}">← 上一套</a>')
    if ctx.get("next_href"):
        nav.append(f'<a href="{e(ctx["next_href"])}">下一套 →</a>')

    photos = "".join(
        f'<div class="ph"><a href="{e(p["src"])}" target="_blank" rel="noopener">'
        f'<img src="{e(p["src"])}" loading="lazy" alt="photo {p["order"]}"></a>'
        f'<span class="ord">{p["order"]}{"" if p["local"] else " ·cdn"}</span></div>'
        for p in ctx.get("photos") or []
    ) or '<div class="fact"><span class="v">无照片</span></div>'

    obs_html = ""
    if ctx.get("photo_observations"):
        groups = "".join(
            f"<h3 style='font-size:12px;margin:8px 0 2px'>{e(name)}</h3><ul class='obs'>"
            + "".join(f"<li>{e(str(item))}</li>" for item in items)
            + "</ul>"
            for name, items in ctx["photo_observations"].items()
            if items
        )
        obs_html = (
            "<details><summary>系统提取的照片观察（机器识别，仅供参考，"
            "不代表结论）</summary>" + groups + "</details>"
        )

    def optgroup(field: str, label: str, values: tuple[str, ...], extra: str = "") -> str:
        buttons = "".join(
            f'<button type="button" data-v="{e(v)}" onclick="setField(\'{field}\',\'{v}\')">{e(v)}</button>'
            for v in values
        )
        return (
            f'<div class="optgroup"><span class="lbl">{e(label)}</span>'
            f'<div class="opts" data-f="{field}">{buttons}</div>{extra}</div>'
        )

    band_groups = "".join(
        optgroup(f"human_{dim}_band", label, BAND_LABELS)
        for dim, label in (
            ("current_use", "Current Use 自住价值"),
            ("redevelopment", "Redevelopment 重建价值"),
            ("house_reno", "房屋状况 / Reno 投入"),
            ("location", "Family Location 区位"),
            ("lot", "Lot / Geometry 地块"),
            ("price_value", "Price / Value 价格"),
        )
    )
    track_options = optgroup("human_primary_track", "人工判断 primary track", TRACK_VALUES)
    secondary_options = optgroup("human_secondary_track", "secondary track（可选）", TRACK_VALUES)
    decision_options = (
        '<div class="optgroup"><span class="lbl">最终判断</span>'
        '<div class="opts" data-f="human_decision">'
        + "".join(
            f'<button type="button" class="decision" data-v="{d}" '
            f'onclick="setField(\'human_decision\',\'{d}\')">{d}</button>'
            for d in DECISION_VALUES
        )
        + "</div></div>"
    )
    tag_options = (
        '<div class="optgroup"><span class="lbl">关键标签</span><div class="opts">'
        + "".join(
            f'<button type="button" data-tag="{t}" onclick="toggleTag(\'{t}\')">{t}</button>'
            for t in TAG_VALUES
        )
        + "</div></div>"
    )

    description = e(ctx.get("description") or "")
    desc_html = (
        f'<details><summary>Listing description 原文</summary>'
        f'<div class="desc">{description or "—"}</div></details>'
    )

    auto = ctx.get("auto") or {}
    auto_meta_html = ""
    if auto:
        reasons = "".join(f"<li>{e(str(r))}</li>" for r in auto.get("reasons") or [])
        flags = "".join(
            f"<li>{e(str(f))}</li>" for f in auto.get("needs_human_review") or []
        )
        cands = []
        if auto.get("must_keep_candidate"):
            cands.append("must_keep 候选")
        if auto.get("dual_override_candidate"):
            cands.append("dual_override 候选")
        auto_meta_html = (
            "<section><h2>数据预填（客观字段，自动）</h2><div class='auto-meta'>"
            f"<div>data_only_signal: <span class='sig'>{e(str(auto.get('data_only_signal') or '—'))}</span>"
            f" · confidence: {e(str(auto.get('confidence') or '—'))}"
            f" · rules: {e(str(auto.get('rules_version') or '—'))}</div>"
            + (f"<div style='margin-top:6px'>候选建议: {e('、'.join(cands))}</div>" if cands else "")
            + (f"<ul>{reasons}</ul>" if reasons else "")
            + (
                f"<div style='color:#b3541e'>需人工复核:</div><ul>{flags}</ul>"
                if flags
                else ""
            )
            + "</div></section>"
        )

    doc = f"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(str(ctx.get("address") or ctx["property_key"]))} · 校准盲读卡</title>
<style>{_card_css()}</style>
</head>
<body>
<header>
  <h1>{e(str(ctx.get("address") or "—"))}</h1>
  <span class="badge {split_class}">{split_label}</span>
  <span class="badge {'autoBadge' if auto else ''}" id="reviewBadge">{('自动' + str((auto.get('auto') or {}).get('human_decision')) + '(可改判)') if (auto.get('auto') or {}).get('human_decision') else ('AUTO_PRE_FILLED' if auto else '未标注')}</span>
  <nav>{"".join(nav)}</nav>
  {f'<a href="{e(ctx["source_url"])}" target="_blank" rel="noopener">Realtor.ca ↗</a>' if ctx.get("source_url") else ""}
</header>
<main>
<section>
  <h2>照片（{ctx.get("photo_count") or 0}）</h2>
  <div class="photos" id="photoStrip">{photos}</div>
  <div class="pnav">
    <button type="button" onclick="photoScroll(-1)" title="向左翻">‹</button>
    <input type="range" id="photoSlider" min="0" max="1000" value="0"
           aria-label="照片滚动条">
    <button type="button" onclick="photoScroll(1)" title="向右翻">›</button>
  </div>
  {obs_html}
</section>
<section>
  <h2>价格与交易</h2>
  <div class="facts">
    {_fact("Asking", _money(ctx.get("asking")))}
    {_fact("地税/年", _money(ctx.get("taxes")))}
    {_fact("DOM", ctx.get("dom"))}
    {_fact("挂牌状态", ctx.get("listing_status"))}
    {_fact("产权", ctx.get("ownership_type"))}
    {_fact("类型", ctx.get("property_type"))}
  </div>
</section>
<section>
  <h2>房屋</h2>
  <div class="facts">
    {_fact("建成年份", ctx.get("year_built"))}
    {_fact("室内面积", ctx.get("finished_sqft"), " sqft")}
    {_fact("卧室", ctx.get("bedrooms"))}
    {_fact("卫浴", ctx.get("bathrooms"))}
  </div>
</section>
<section>
  <h2>地块</h2>
  <div class="facts">
    {_fact("Lot 面积", ctx.get("lot_area_sqft"), " sqft")}
    {_fact("尺寸", ctx.get("lot_dimensions"))}
    {_fact("面宽", ctx.get("frontage_ft"), " ft")}
    {_fact("进深", ctx.get("depth_ft"), " ft")}
    {_fact("几何来源", ctx.get("geometry_source"))}
    {_fact("角地", ctx.get("corner_lot"))}
    {_fact("后巷", ctx.get("lane_observed"))}
    {_fact("坡度", ctx.get("slope"))}
  </div>
</section>
<section>
  <h2>官方与区位事实</h2>
  <div class="facts">
    {_fact("Zoning", ctx.get("zoning"))}
    {_fact("BCA 总估值", _money(ctx.get("assessment_total")))}
    {_fact("BCA 土地", _money(ctx.get("assessment_land")))}
    {_fact("BCA 建筑", _money(ctx.get("assessment_improvement")))}
    {_fact("估值年份", ctx.get("assessment_year"))}
    {_fact("小学学区", ctx.get("elementary_school"))}
    {_fact("中学学区", ctx.get("secondary_school"))}
    {_fact("社区", ctx.get("neighbourhood"))}
    {_fact("城市", ctx.get("municipality"))}
  </div>
</section>
<section>
  <h2>道路与环境事实</h2>
  <div class="facts">
    {_fact("官方道路等级", ctx.get("road_class"))}
    {_fact("最近主路", ctx.get("nearest_major_road"))}
    {_fact("距主路", ctx.get("distance_to_major_road_m"), " m")}
    {_fact("是否临街主路", ctx.get("fronts_major_road"))}
    {_fact("对公园", ctx.get("park_facing"))}
    {_fact("景观", ctx.get("view_type"))}
    {_fact("滨水", ctx.get("waterfront"))}
  </div>
</section>
<section>
  <h2>来源</h2>
  <div class="facts">
    {_fact("来源", ctx.get("source_name"))}
    {_fact("观测时间", ctx.get("observed_at"))}
    {_fact("MLS", ctx.get("mls"))}
  </div>
  {desc_html}
</section>
{auto_meta_html}
<section>
  <h2>人工标注</h2>
  <div class="row"><div>{band_groups}</div>
  <div>{track_options}{secondary_options}{decision_options}
  <div class="optgroup">
    <label><input type="checkbox" id="human_must_keep" onchange="setField('human_must_keep', this.checked)"> must_keep（一定要留住复核）</label>
    {'<span class="hint">数据建议 ✓</span>' if auto.get('must_keep_candidate') else ''}
    &nbsp;&nbsp;
    <label><input type="checkbox" id="dual_override" onchange="setField('dual_override', this.checked)"> dual_override（Dual 不应由 CU×RD 推出）</label>
    {'<span class="hint">数据建议 ✓</span>' if auto.get('dual_override_candidate') else ''}
  </div>
  {optgroup("human_dual_band", "人工 Dual band（仅 override 时填）", BAND_LABELS)}
  {tag_options}</div></div>
  <div class="optgroup"><span class="lbl">主要原因（2–4 句）</span>
    <textarea id="human_reason" onchange="setField('human_reason', this.value)"></textarea></div>
</section>
</main>
<div class="toolbar">
  <button onclick="save()">保存</button>
  <button class="ghost" onclick="exportThis()">导出本套 JSON</button>
  <button class="ghost" onclick="copyThis()">复制 JSON</button>
  <button class="ghost" onclick="clearThis()">清除</button>
  <span id="saveState"></span>
</div>
<script>{_card_js(ctx)}</script>
</body>
</html>"""
    return doc


# ---------------------------------------------------------------------------
# Packet index + writer
# ---------------------------------------------------------------------------


def _index_js(contexts: list[Mapping[str, Any]]) -> str:
    keys = json.dumps([ctx["property_key"] for ctx in contexts])
    return f"""
    const KEYS = {keys};
    function loadAll() {{
      const out = [];
      KEYS.forEach(k => {{
        try {{
          const raw = localStorage.getItem('calib-annotation:' + k);
          if (raw) out.push(JSON.parse(raw));
        }} catch (e) {{}}
      }});
      return out;
    }}
    function refresh() {{
      const recs = loadAll();
      const reviewed = new Set();
      const touched = new Set();
      recs.forEach(a => {{
        const set = a.human_set_fields;
        if (Array.isArray(set)) {{
          if (set.includes('human_decision') && a.human_decision) reviewed.add(a.property_key);
          else if (set.length) touched.add(a.property_key);
        }} else if (a.human_decision || a.human_current_use_band || a.human_reason) {{
          reviewed.add(a.property_key);  // legacy record without provenance
        }}
      }});
      const decisions = {{}};
      recs.forEach(a => {{
        const set = a.human_set_fields;
        if (a.human_decision && (!Array.isArray(set) || set.includes('human_decision'))) {{
          decisions[a.property_key] = a.human_decision;
        }}
      }});
      let autoDecided = 0;
      const tally = {{KEEP: 0, WATCH: 0, PASS: 0, SPECIAL: 0}};
      document.querySelectorAll('tr[data-key]').forEach(tr => {{
        const k = tr.getAttribute('data-key');
        const cell = tr.querySelector('.st');
        const decCell = tr.querySelector('.dec');
        const autoDec = tr.getAttribute('data-autodec');
        const dec = decisions[k];
        if (dec) {{ decCell.textContent = dec; decCell.className = 'dec dec-' + dec; if (tally[dec] !== undefined) tally[dec]++; }}
        else if (autoDec) {{ decCell.textContent = 'auto ' + autoDec; decCell.className = 'dec dec-auto'; }}
        else decCell.textContent = '—';
        if (reviewed.has(k)) cell.textContent = '✅ 已审核';
        else if (touched.has(k)) cell.textContent = '◐ 进行中';
        else if (autoDec) {{ cell.textContent = '◆ 自动' + autoDec; autoDecided++; }}
        else if (tr.getAttribute('data-auto') === '1') cell.textContent = '○ 预填';
        else cell.textContent = '—';
      }});
      const autoCount = document.querySelectorAll('tr[data-auto="1"]').length;
      document.getElementById('progress').textContent =
        reviewed.size + ' / ' + KEYS.length + ' 人工审核' +
        (touched.size ? '（' + touched.size + ' 进行中）' : '') +
        ' · ' + autoDecided + ' 自动决策' +
        ' · ' + autoCount + ' 数据预填';
      document.getElementById('decisionTally').textContent =
        'KEEP ' + tally.KEEP + ' · WATCH ' + tally.WATCH + ' · PASS ' + tally.PASS +
        (tally.SPECIAL ? ' · SPECIAL ' + tally.SPECIAL : '');
    }}
    function exportAll() {{
      const payload = {{schema_version: 'strategy-v2-calibration-annotations-v1',
                       exported_at: new Date().toISOString(), annotations: loadAll()}};
      const blob = new Blob([JSON.stringify(payload, null, 2)], {{type: 'application/json'}});
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob); a.download = 'calibration-annotations.json'; a.click();
      URL.revokeObjectURL(a.href);
    }}
    function importAll(evt) {{
      const file = evt.target.files[0]; if (!file) return;
      const reader = new FileReader();
      reader.onload = () => {{
        try {{
          const payload = JSON.parse(reader.result);
          const rows = Array.isArray(payload) ? payload : (payload.annotations || [payload]);
          rows.forEach(a => {{ if (a && a.property_key) localStorage.setItem('calib-annotation:' + a.property_key, JSON.stringify(a)); }});
          refresh();
        }} catch (e) {{ alert('导入失败: ' + e); }}
      }};
      reader.readAsText(file);
    }}
    window.addEventListener('DOMContentLoaded', refresh);
    """


def render_index_html(contexts: list[Mapping[str, Any]], *, generated_at: str) -> str:
    """Render the packet dashboard: all cards, calibration group first."""

    ordered = sorted(
        contexts,
        key=lambda c: (0 if c.get("split") == "calibration" else 1, str(c.get("address") or "")),
    )
    rows = []
    for i, ctx in enumerate(ordered, 1):
        split_label = "盲测" if ctx.get("split") == "blind" else "校准"
        auto_decision = str(
            ((ctx.get("auto") or {}).get("auto") or {}).get("human_decision") or ""
        )
        rows.append(
            f'<tr data-key="{html.escape(str(ctx["property_key"]))}" '
            f'data-auto="{1 if ctx.get("auto") else 0}" '
            f'data-autodec="{html.escape(auto_decision)}">'
            f"<td>{i}</td>"
            f'<td><a href="cards/{html.escape(ctx["slug"])}.html">{html.escape(str(ctx.get("address") or "—"))}</a></td>'
            f"<td>{html.escape(str(ctx.get('mls') or '—'))}</td>"
            f"<td>{split_label}</td>"
            f"<td>{ctx.get('photo_count') or 0}</td>"
            f'<td class="st">—</td><td class="dec">—</td></tr>'
        )

    return f"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Strategy V2 校准批阅面板 · {html.escape(generated_at)}</title>
<style>{_card_css()}
table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
th, td {{ text-align: left; padding: 6px 8px; border-bottom: 1px solid #eee; }}
th {{ color: #666; font-weight: 600; }}
.dec {{ font-weight: 700; }}
.dec-KEEP {{ color: #1a7f37; }}
.dec-WATCH {{ color: #b26a00; }}
.dec-PASS {{ color: #888; }}
.dec-SPECIAL {{ color: #6f42c1; }}
.dec-auto {{ color: #4a6fa5; font-weight: 400; }}
#decisionTally {{ font-weight: 700; }}
</style>
</head>
<body>
<header><h1>校准批阅面板</h1><span class="badge">{html.escape(generated_at)}</span>
<span class="badge" id="progress"></span>
<span class="badge" id="decisionTally"></span></header>
<main>
<section>
<h2>说明</h2>
<div class="desc">数据层字段（重建/区位/地块/价格/track/标签）已由数据自动预填，显示为 <b>蓝色描边 ·auto</b>；
你看图后只需填 <b>Current Use</b> 与 <b>房屋状况/Reno</b>，再点最终 KEEP/WATCH/PASS。点过任何人的选择变实心蓝 = 人工覆盖。
最终决策只有人能定；自动仅给出 data_only_signal。标注存 localStorage，「导出全部」交给 eval 脚本。
先标「校准」组（120 套），「盲测」组（46 套）留作最终验收。</div>
<p>
<button onclick="exportAll()">导出全部 annotations.json</button>
<label style="margin-left:12px">导入: <input type="file" accept=".json" onchange="importAll(event)"></label>
</p>
</section>
<section>
<table>
<thead><tr><th>#</th><th>地址</th><th>MLS</th><th>分组</th><th>照片</th><th>状态</th><th>决策</th></tr></thead>
<tbody>{"".join(rows)}</tbody>
</table>
</section>
</main>
<script>{_index_js(contexts)}</script>
</body>
</html>"""


def annotations_template_csv(contexts: list[Mapping[str, Any]]) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(TEMPLATE_COLUMNS))
    writer.writeheader()
    for ctx in contexts:
        writer.writerow(
            {
                "property_key": ctx["property_key"],
                "split": ctx.get("split") or "",
                "address": ctx.get("address") or "",
                "mls": ctx.get("mls") or "",
                "card_file": f"cards/{ctx['slug']}.html",
                "source_url": ctx.get("source_url") or "",
                **{field: "" for field in ANNOTATION_FIELDS},
                "annotated_at": "",
            }
        )
    return buffer.getvalue()


def write_packet(
    output_dir: Path | str,
    contexts: list[Mapping[str, Any]],
    *,
    generated_at: str,
) -> dict[str, Any]:
    """Write index.html, cards/*.html and annotations-template.csv."""

    out = Path(output_dir)
    cards_dir = out / "cards"
    cards_dir.mkdir(parents=True, exist_ok=True)
    for ctx in contexts:
        (cards_dir / f"{ctx['slug']}.html").write_text(render_card_html(ctx), encoding="utf-8")
    (out / "index.html").write_text(render_index_html(contexts, generated_at=generated_at), encoding="utf-8")
    (out / "annotations-template.csv").write_text(annotations_template_csv(contexts), encoding="utf-8")
    return {
        "output_dir": str(out),
        "index": str(out / "index.html"),
        "cards_dir": str(cards_dir),
        "template": str(out / "annotations-template.csv"),
        "card_count": len(contexts),
        "generated_at": generated_at,
    }


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------


def _ranks(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 2:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    if vx == 0 or vy == 0:
        return None
    return cov / math.sqrt(vx * vy)


def _spearman(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 2:
        return None
    return _pearson(_ranks(xs), _ranks(ys))


def _score_to_band(score: float | None) -> int | None:
    if score is None:
        return None
    if score <= 20:
        return 0
    if score <= 40:
        return 1
    if score <= 60:
        return 2
    if score <= 80:
        return 3
    return 4


def _ranked_keys(model_rows: Mapping[str, Mapping[str, Any]], field: str) -> list[str]:
    scored = [
        (key, _num(row.get(field)))
        for key, row in model_rows.items()
        if _num(row.get(field)) is not None
    ]
    scored.sort(key=lambda item: (-item[1], item[0]))
    return [key for key, _ in scored]


def _rank_of(model_rows: Mapping[str, Mapping[str, Any]], field: str, key: str) -> int | None:
    ranked = _ranked_keys(model_rows, field)
    return ranked.index(key) + 1 if key in ranked else None


def _find_key_by_address(rows: Mapping[str, Mapping[str, Any]], needle: str) -> str | None:
    needle_upper = needle.upper()
    for key, row in rows.items():
        if needle_upper in str(row.get("address") or "").upper():
            return key
    return None


NAMED_CASE_RULES: tuple[dict[str, Any], ...] = (
    {
        "id": "nanaimo_4561_not_current_use_top10",
        "address_contains": "4561 NANAIMO",
        "rule": "rank_above",
        "view": "current_use_score",
        "max_rank": 10,
    },
    {
        "id": "melbourne_5454_five_unit_conditional",
        "address_contains": "5454 MELBOURNE",
        "rule": "no_high_unit_base",
        "max_base_units": 4,
    },
    {
        "id": "w10_3910_not_reno_capped",
        "address_contains": "3910 W 10TH",
        "rule": "not_at_reno_floor",
        "floor": 40.0,
        "reno_min": 100000.0,
    },
    {
        "id": "e18_1016_not_dev_top10",
        "address_contains": "1016 E 18TH",
        "rule": "rank_above",
        "view": "redevelopment_score",
        "max_rank": 10,
    },
    {
        "id": "collingwood_2413_premium_track",
        "address_contains": "2413 COLLINGWOOD",
        "rule": "primary_track",
        "expect": "PREMIUM_SMALL_LOT",
    },
)


def _named_case_checks(
    model_rows: Mapping[str, Mapping[str, Any]],
    shadow_rows: Mapping[str, Mapping[str, Any]],
    rules: Iterable[Mapping[str, Any]] = NAMED_CASE_RULES,
) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    for rule in rules:
        entry: dict[str, Any] = {"id": rule["id"], "address_contains": rule["address_contains"]}
        key = _find_key_by_address(model_rows, rule["address_contains"]) or _find_key_by_address(
            shadow_rows, rule["address_contains"]
        )
        if key is None:
            checks.append({**entry, "status": "NOT_FOUND", "detail": "address not in supplied artifacts"})
            continue
        entry["property_key"] = key
        kind = rule["rule"]
        if kind == "rank_above":
            rank = _rank_of(model_rows, rule["view"], key)
            entry["rank"] = rank
            if rank is None:
                entry["status"] = "NOT_FOUND"
                entry["detail"] = f"no {rule['view']} for {key}"
            else:
                entry["status"] = "PASS" if rank > rule["max_rank"] else "FAIL"
                entry["detail"] = f"{rule['view']} rank={rank}; must exceed top {rule['max_rank']}"
        elif kind == "no_high_unit_base":
            scenarios = (shadow_rows.get(key) or {}).get("development_scenarios") or []
            base = next((s for s in scenarios if s.get("scenario_name") == "BASE"), {})
            units = _num(base.get("unit_count"))
            entry["base_unit_count"] = units
            over = [
                s for s in scenarios
                if s.get("scenario_name") == "BASE"
                and (_num(s.get("unit_count")) or 0) > rule["max_base_units"]
            ]
            entry["status"] = "FAIL" if over else "PASS"
            entry["detail"] = (
                f"BASE unit_count={units}; >{rule['max_base_units']} must live in CONDITIONAL/UPSIDE"
            )
        elif kind == "not_at_reno_floor":
            row = model_rows.get(key) or {}
            score = _num(row.get("current_use_score"))
            reno = _num(row.get("reno_estimate")) or 0.0
            entry["current_use_score"] = score
            entry["reno_estimate"] = reno
            at_floor = score is not None and score <= rule["floor"] and reno >= rule["reno_min"]
            entry["status"] = "FAIL" if at_floor else "PASS"
            entry["detail"] = (
                f"score={score}, reno={reno}; a >={rule['reno_min']:,.0f} reno must not pin the score at {rule['floor']:,.0f}"
            )
        elif kind == "primary_track":
            track = ((shadow_rows.get(key) or {}).get("asset_classification") or {}).get("primary_track")
            entry["primary_track"] = track
            entry["status"] = "PASS" if track == rule["expect"] else "FAIL"
            entry["detail"] = f"primary_track={track}; expected {rule['expect']}"
        checks.append(entry)
    return checks


def evaluate_calibration(
    annotations: list[HumanAnnotation],
    *,
    model_rows: Mapping[str, Mapping[str, Any]],
    shadow_rows: Mapping[str, Mapping[str, Any]],
    retained_keys: Iterable[str] = (),
    split: str = "calibration",
    config_fingerprint: str | None = None,
    pairwise: list[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Measure agreement between human labels and the current machine outputs.

    ``model_rows`` spans the whole scored universe (it drives ranks); only
    annotations whose ``split`` matches are used for label metrics.
    """

    scoped = [a for a in annotations if a.split == split]
    labeled = [a for a in scoped if a.is_human_labeled()]
    reviewed = [a for a in labeled if a.is_reviewed()]

    result: dict[str, Any] = {
        "status": "AVAILABLE" if labeled else "NOT_AVAILABLE",
        "split": split,
        "labeled_count": len(labeled),
        "reviewed_count": len(reviewed),
        "annotation_count": len(scoped),
        "config_fingerprint": config_fingerprint,
        "named_case_checks": _named_case_checks(model_rows, shadow_rows),
    }

    # --- per-dimension agreement -------------------------------------------
    # A band joins the agreement pair when the human set it explicitly, or
    # when the human reviewed the card (set the decision) and left the auto
    # value standing — implicit acceptance.  Untouched auto-prefill rows
    # never enter the metrics.
    dimensions: dict[str, Any] = {}
    disagreements: list[dict[str, Any]] = []
    for dim in DIMENSION_FIELDS:
        field = DIMENSION_MODEL_FIELD[dim]
        human_field = f"human_{dim}_band"
        pairs: list[tuple[str, float, float]] = []
        confusion = [[0] * 5 for _ in range(5)]
        human_set = auto_accepted = overrides = 0
        for ann in labeled:
            band = getattr(ann, human_field)
            if band is None:
                continue
            by_human = ann.field_is_human(human_field)
            if not by_human and not ann.is_reviewed():
                continue
            if by_human:
                human_set += 1
                auto_value = (ann.auto_snapshot or {}).get(human_field)
                if auto_value is not None and auto_value != band:
                    overrides += 1
            else:
                auto_accepted += 1
            score = _num((model_rows.get(ann.property_key) or {}).get(field))
            if score is None:
                continue
            mid = BAND_MIDPOINT[band]
            pairs.append((ann.property_key, mid, score))
            confusion[BAND_LABELS.index(band)][_score_to_band(score)] += 1
            if abs(mid - score) >= DISAGREEMENT_THRESHOLD:
                disagreements.append(
                    {
                        "property_key": ann.property_key,
                        "address": (model_rows.get(ann.property_key) or {}).get("address"),
                        "dimension": dim,
                        "human_band": band,
                        "human_mid": mid,
                        "model_field": field,
                        "model_score": score,
                        "abs_diff": abs(mid - score),
                        "decision": ann.human_decision,
                        "provenance": "human" if by_human else "auto_accepted",
                        "tags": list(ann.human_tags),
                        "reason": ann.human_reason,
                    }
                )
        xs = [p[1] for p in pairs]
        ys = [p[2] for p in pairs]
        dimensions[dim] = {
            "model_field": field,
            "pair_count": len(pairs),
            "human_set": human_set,
            "auto_accepted": auto_accepted,
            "overrides": overrides,
            "override_rate": overrides / human_set if human_set else None,
            "spearman": _spearman(xs, ys),
            "mean_abs_diff": (sum(abs(a - b) for a, b in zip(xs, ys)) / len(xs)) if xs else None,
            "confusion": confusion,
        }
    result["dimensions"] = dimensions
    disagreements.sort(key=lambda item: -item["abs_diff"])
    result["disagreements"] = disagreements

    # --- decision / retention metrics --------------------------------------
    # Only decisions the human actually clicked count; an auto SPECIAL stays
    # a signal until reviewed.
    labels = {
        ann.property_key: label
        for ann in labeled
        if ann.is_reviewed()
        and (label := annotation_to_label(ann)) is not None
    }
    result["auto_special_keys"] = [
        {
            "property_key": ann.property_key,
            "address": (model_rows.get(ann.property_key) or {}).get("address")
            or (shadow_rows.get(ann.property_key) or {}).get("address"),
        }
        for ann in scoped
        if ann.human_decision == "SPECIAL" and not ann.is_reviewed()
    ]
    result["auto_signal_distribution"] = {
        sig: sum(1 for a in scoped if a.data_only_signal == sig)
        for sig in ("STRONG", "NORMAL", "WEAK", "SPECIAL")
        if any(a.data_only_signal == sig for a in scoped)
    }
    candidates = [{"property_key": ann.property_key} for ann in labeled]
    result["recall"] = evaluate_recall(candidates, retained_keys=retained_keys, labels=labels)

    keep_keys = {key for key, label in labels.items() if label in {"MUST_KEEP", "KEEP"}}
    must_keys = {key for key, label in labels.items() if label == "MUST_KEEP"}
    drop_keys = {key for key, label in labels.items() if label == "DROP"}
    special_keys = {key for key, label in labels.items() if label == "SPECIAL"}

    top_n: dict[str, Any] = {}
    for view in SCORE_VIEWS:
        ranked = _ranked_keys(model_rows, view)
        entry: dict[str, Any] = {"keep_total": len(keep_keys), "must_keep_total": len(must_keys)}
        for depth in TOP_N_DEPTHS:
            top = set(ranked[:depth])
            entry[f"top{depth}_keep_hits"] = len(top & keep_keys)
            entry[f"top{depth}_must_keep_hits"] = len(top & must_keys)
        top_n[view] = entry
    result["top_n_overlap"] = top_n

    leakage: list[dict[str, Any]] = []
    for key in drop_keys:
        row = model_rows.get(key) or {}
        for view in SCORE_VIEWS:
            rank = _rank_of(model_rows, view, key)
            if rank is not None and rank <= max(TOP_N_DEPTHS):
                leakage.append(
                    {"property_key": key, "address": row.get("address"), "view": view, "rank": rank}
                )
    result["pass_leakage"] = leakage

    special_violations: list[dict[str, Any]] = []
    for key in special_keys:
        row = model_rows.get(key) or {}
        for view in SCORE_VIEWS:
            rank = _rank_of(model_rows, view, key)
            if rank is not None and rank <= max(TOP_N_DEPTHS):
                special_violations.append(
                    {"property_key": key, "address": row.get("address"), "view": view, "rank": rank}
                )
    result["special_isolation_violations"] = special_violations

    if pairwise:
        wins, total = 0.0, 0
        pair_rows = []
        for pair in pairwise:
            a_key, b_key = pair.get("property_key_a"), pair.get("property_key_b")
            winner = str(pair.get("winner") or "").strip().upper()
            a_score = _num((model_rows.get(a_key) or {}).get("dual_value_score"))
            b_score = _num((model_rows.get(b_key) or {}).get("dual_value_score"))
            if a_score is None or b_score is None or winner not in {"A", "B"}:
                continue
            total += 1
            preferred = a_score if winner == "A" else b_score
            other = b_score if winner == "A" else a_score
            agreed = 1.0 if preferred > other else (0.5 if preferred == other else 0.0)
            wins += agreed
            pair_rows.append({"a": a_key, "b": b_key, "winner": winner, "agreement": agreed})
        result["pairwise"] = {
            "pairs": total,
            "agreement_rate": wins / total if total else None,
            "rows": pair_rows,
        }

    return result


# ---------------------------------------------------------------------------
# Evaluation reports + history
# ---------------------------------------------------------------------------


def _fmt_num(value: Any, digits: int = 2) -> str:
    number = _num(value)
    return f"{number:.{digits}f}" if number is not None else "UNKNOWN"


def render_evaluation_markdown(result: Mapping[str, Any]) -> str:
    lines = [
        "# Strategy V2 校准评估",
        "",
        f"- split: `{result.get('split')}`",
        f"- status: `{result.get('status')}`",
        f"- labeled_count: {result.get('labeled_count')}",
        f"- reviewed_count: {result.get('reviewed_count', 0)}",
        f"- config_fingerprint: `{result.get('config_fingerprint') or 'UNKNOWN'}`",
        "",
        "## 维度一致性（人工 band 中值 vs 模型分）",
        "",
        "pair = 人工显式设置 + 已审核行中默认接受的自动预填值。",
        "",
        "| 维度 | 模型字段 | 对数 | 人工设置 | 自动接受 | 改判率 | Spearman | 平均绝对差 |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for dim, stats in (result.get("dimensions") or {}).items():
        ovr = stats.get("override_rate")
        lines.append(
            f"| {dim} | `{stats['model_field']}` | {stats['pair_count']} | "
            f"{stats.get('human_set', 0)} | {stats.get('auto_accepted', 0)} | "
            f"{_fmt_num(ovr * 100 if ovr is not None else None, 0)}% | "
            f"{_fmt_num(stats.get('spearman'), 3)} | {_fmt_num(stats.get('mean_abs_diff'), 1)} |"
        )

    signal_dist = result.get("auto_signal_distribution") or {}
    auto_special = result.get("auto_special_keys") or []
    lines += [
        "",
        "## 数据预填信号（非决策）",
        "",
        f"- data_only_signal 分布: `{json.dumps(signal_dist, ensure_ascii=False)}`",
        f"- 自动 SPECIAL（待人工确认）: {len(auto_special)}",
    ]
    for item in auto_special:
        lines.append(f"  - {item.get('address') or item['property_key']} ({item['property_key']})")

    recall = result.get("recall") or {}
    lines += [
        "",
        "## 决策 / 保留召回（仅人工已审核决策）",
        "",
        f"- status: `{recall.get('status')}`",
        f"- must_keep_recall: {_fmt_num(recall.get('must_keep_recall'), 3)} "
        f"({recall.get('must_keep_retained')}/{recall.get('must_keep_denominator')})",
        f"- keep_recall: {_fmt_num(recall.get('keep_recall'), 3)} "
        f"({recall.get('keep_retained')}/{recall.get('keep_denominator')})",
        "",
        "## 视图 Top-N 重合",
        "",
        "| 视图 | KEEP 总数 | Top10 命中 | Top30 命中 | MUST_KEEP Top10 | MUST_KEEP Top30 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for view, entry in (result.get("top_n_overlap") or {}).items():
        lines.append(
            f"| {view} | {entry.get('keep_total')} | {entry.get('top10_keep_hits')} | "
            f"{entry.get('top30_keep_hits')} | {entry.get('top10_must_keep_hits')} | "
            f"{entry.get('top30_must_keep_hits')} |"
        )

    leakage = result.get("pass_leakage") or []
    lines += ["", "## 人工 PASS 泄漏（进入模型 Top30）", ""]
    if leakage:
        lines.append("| 房源 | 视图 | 排名 |")
        lines.append("|---|---|---:|")
        for item in leakage:
            lines.append(f"| {item.get('address') or item['property_key']} | {item['view']} | {item['rank']} |")
    else:
        lines.append("- 无泄漏")

    violations = result.get("special_isolation_violations") or []
    lines += ["", "## SPECIAL 隔离检查", ""]
    if violations:
        for item in violations:
            lines.append(f"- {item.get('address') or item['property_key']}: {item['view']} rank={item['rank']}")
    else:
        lines.append("- 无违规")

    lines += ["", "## 具名案例断言", "", "| 规则 | 状态 | 说明 |", "|---|---|---|"]
    for check in result.get("named_case_checks") or []:
        lines.append(f"| `{check['id']}` | {check['status']} | {check.get('detail') or ''} |")

    pairwise = result.get("pairwise")
    if pairwise:
        lines += [
            "",
            "## Pairwise 一致性",
            "",
            f"- pairs: {pairwise['pairs']}; agreement_rate: {_fmt_num(pairwise.get('agreement_rate'), 3)}",
        ]
    lines.append("")
    return "\n".join(lines)


def render_disagreements_markdown(result: Mapping[str, Any]) -> str:
    lines = [
        "# Strategy V2 校准分歧诊断",
        "",
        "每行 = 人工 band 中值与模型分相差 ≥25 的维度。用于定位下一次标准修订方向。",
        "",
        "| 房源 | 维度 | 来源 | 人工 band | 人工中值 | 模型字段 | 模型分 | 差值 | 决策 | 标签 | 人工原因 |",
        "|---|---|---|---|---:|---|---:|---:|---|---|---|",
    ]
    for item in result.get("disagreements") or []:
        lines.append(
            f"| {item.get('address') or item['property_key']} | {item['dimension']} | "
            f"{item.get('provenance') or 'human'} | "
            f"{item['human_band']} | {item['human_mid']:.0f} | `{item['model_field']}` | "
            f"{item['model_score']:.1f} | {item['abs_diff']:.0f} | {item.get('decision') or ''} | "
            f"{';'.join(item.get('tags') or [])} | {html.escape(str(item.get('reason') or ''))} |"
        )
    if not result.get("disagreements"):
        lines.append("| — | — | — | — | — | — | — | — | — | — | — |")
    lines.append("")
    return "\n".join(lines)


def append_eval_history(
    path: Path | str,
    result: Mapping[str, Any],
    *,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Append one evaluation snapshot to the JSONL history and return it."""

    stamp = generated_at or datetime.now(timezone.utc).isoformat()
    digest = hashlib.sha256(
        json.dumps(result, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:12]
    entry = {"eval_id": f"{stamp}-{digest}", "generated_at": stamp, **dict(result)}
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
    return entry


__all__ = [
    "ANNOTATION_FIELDS",
    "BAND_LABELS",
    "BAND_MIDPOINT",
    "DECISION_VALUES",
    "DIMENSION_FIELDS",
    "DIMENSION_MODEL_FIELD",
    "NAMED_CASE_RULES",
    "TAG_VALUES",
    "TEMPLATE_COLUMNS",
    "TRACK_VALUES",
    "HumanAnnotation",
    "annotation_to_label",
    "annotations_template_csv",
    "append_eval_history",
    "build_card_context",
    "card_slug",
    "evaluate_calibration",
    "load_annotations",
    "load_split_manifest",
    "parse_annotation",
    "render_card_html",
    "render_disagreements_markdown",
    "render_evaluation_markdown",
    "render_index_html",
    "split_for_key",
    "write_packet",
]
