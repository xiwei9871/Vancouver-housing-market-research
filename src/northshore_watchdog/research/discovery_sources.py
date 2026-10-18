"""Local replay adapters for filtered listing-discovery captures."""

from __future__ import annotations

import json
import math
import os
import re
from collections.abc import Mapping
from datetime import datetime
from os import PathLike
from pathlib import Path
from typing import Any, Protocol

from bs4 import BeautifulSoup, Comment, NavigableString

from .discovery import DiscoveryCapture, DiscoveryCard, DiscoveryFilterPlan
from .public_feeds import _PartialParseError, has_visible_challenge, parse_public_html


_LIGHTWEIGHT_CARD_FIELDS = (
    "source_name",
    "mls_number",
    "address",
    "municipality",
    "asking",
    "bedrooms",
    "bathrooms",
    "finished_sqft",
    "lot_area_sqft",
    "lot_dimensions",
    "frontage_ft",
    "depth_ft",
    "property_type",
    "ownership_type",
    "listing_status",
    "dom",
    "year_built",
    "taxes",
    "description",
    "neighbourhood",
    "elementary_school",
    "secondary_school",
    "source_url",
    "feed_url",
    "latitude",
    "longitude",
    "observed_at",
    "reason_codes",
    "raw",
)
_RAW_CARD_METADATA_FIELDS = (
    "capture_card_id",
    "source_card_id",
    "result_position",
    "card_position",
    "page_number",
)
_INTEGER_TEXT_RE = re.compile(r"\+?\d+")
_RESULT_NUMBER = r"(?:\d{1,3}(?:,\d{3})+|\d+)"
_RESULT_LABEL = r"(?:results?|listings?|properties|homes?)"
_COUNT_BEFORE_LABEL_RE = re.compile(
    rf"(?P<count>{_RESULT_NUMBER})\s*{_RESULT_LABEL}\b",
    re.IGNORECASE,
)
_COUNT_AFTER_LABEL_RE = re.compile(
    rf"\b{_RESULT_LABEL}\s*:?\s*(?P<count>{_RESULT_NUMBER})",
    re.IGNORECASE,
)
_ANY_RESULT_NUMBER_RE = re.compile(_RESULT_NUMBER)
_NO_RESULTS_RE = re.compile(rf"\b(?:no|zero)\s+{_RESULT_LABEL}\b", re.IGNORECASE)
_BLOCKING_STATUS_CODES = {401, 403, 429}


class DiscoverySource(Protocol):
    source_name: str

    def execute(
        self,
        plan: DiscoveryFilterPlan,
        *,
        observed_at: datetime,
    ) -> DiscoveryCapture: ...


class JsonFilteredCaptureSource:
    def __init__(
        self,
        name: str,
        path: str | PathLike[str],
        config: Mapping[str, Any],
    ) -> None:
        self.source_name = name
        self.raw_snapshot_path = os.fspath(path)
        self.path = Path(path)
        self.config = dict(config)

    def execute(
        self,
        plan: DiscoveryFilterPlan,
        *,
        observed_at: datetime,
    ) -> DiscoveryCapture:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid filtered-capture JSON in {self.raw_snapshot_path}: {exc}") from exc

        if not isinstance(payload, Mapping):
            raise ValueError("top-level JSON value must be an object")

        declared_source = payload.get("source_name")
        if declared_source is not None and declared_source != self.source_name:
            raise ValueError(
                f"declared source_name {declared_source!r} does not match configured "
                f"source_name {self.source_name!r}"
            )
        declared_preset = payload.get("preset_name")
        if declared_preset is not None and declared_preset != plan.preset_name:
            raise ValueError(
                f"declared preset_name {declared_preset!r} does not match selected "
                f"preset_name {plan.preset_name!r}"
            )

        source_url = (
            payload.get("source_url")
            or self.config.get("source_url")
            or self.raw_snapshot_path
        )

        envelope = DiscoveryCapture(
            source_name=self.source_name,
            source_url=str(source_url),
            preset_name=declared_preset or plan.preset_name,
            observed_at=payload.get("observed_at") or observed_at,
            status_code=payload.get("status_code", 200),
            result_count=payload.get("result_count"),
            count_is_complete=payload.get("count_is_complete", False),
            applied_filters=payload.get("applied_filters", {}),
            applied_filter_evidence=payload.get("applied_filter_evidence", []),
            parse_warnings=payload.get("parse_warnings", []),
            cards=[],
            detail_requests=payload.get("detail_requests", 0),
            blocked=payload.get("blocked", False),
            raw_snapshot_path=self.raw_snapshot_path,
        )
        if envelope.blocked or envelope.status_code in _BLOCKING_STATUS_CODES:
            return envelope.model_copy(
                update={"blocked": True, "cards": [], "detail_requests": 0},
                deep=True,
            )

        max_result_cards = _optional_positive_int(
            self.config.get("max_result_cards"),
            field_name="max_result_cards",
        )
        if (
            max_result_cards is not None
            and envelope.count_is_complete
            and envelope.result_count is not None
            and envelope.result_count > max_result_cards
        ):
            return envelope

        raw_cards = payload.get("cards", [])
        if not isinstance(raw_cards, list):
            raise ValueError("capture cards must be a JSON array")
        if payload.get("count_is_complete") is True and envelope.result_count is None:
            envelope = envelope.model_copy(
                update={"result_count": len(raw_cards)},
                deep=True,
            )
            if (
                max_result_cards is not None
                and envelope.result_count > max_result_cards
            ):
                return envelope
        bounded_raw_cards = (
            raw_cards
            if max_result_cards is None
            else raw_cards[: max_result_cards + 1]
        )
        cards = [
            _project_card(
                raw_card,
                source_name=self.source_name,
                feed_url=envelope.source_url,
                observed_at=envelope.observed_at,
                card_index=index,
            )
            for index, raw_card in enumerate(bounded_raw_cards)
        ]
        return envelope.model_copy(update={"cards": cards}, deep=True)


class HtmlFilteredCaptureSource:
    def __init__(
        self,
        name: str,
        path: str | PathLike[str],
        config: Mapping[str, Any],
    ) -> None:
        self.source_name = name
        self.raw_snapshot_path = os.fspath(path)
        self.path = Path(path)
        self.config = dict(config)

    def execute(
        self,
        plan: DiscoveryFilterPlan,
        *,
        observed_at: datetime,
    ) -> DiscoveryCapture:
        source_url = self.config.get("source_url")
        if not source_url:
            raise ValueError("source_url is required in the HTML source config")
        capture = parse_filtered_html(
            self.path.read_bytes(),
            source_name=self.source_name,
            source_url=str(source_url),
            observed_at=observed_at,
            selectors=self.config.get("selectors", {}),
            status_code=self.config.get("status_code", 200),
            raw_snapshot_path=self.raw_snapshot_path,
            max_result_cards=self.config.get("max_result_cards"),
            count_is_complete=self.config.get("count_is_complete"),
        )
        return capture.model_copy(update={"preset_name": plan.preset_name}, deep=True)


def parse_filtered_html(
    content: bytes | str,
    *,
    source_name: str,
    source_url: str,
    observed_at: datetime,
    selectors: Mapping[str, str],
    status_code: int = 200,
    raw_snapshot_path: str | None = None,
    max_result_cards: int | None = None,
    count_is_complete: bool | None = None,
    applied_filters: Mapping[str, Any] | None = None,
) -> DiscoveryCapture:
    # Requested/configured filter values are intent, not an observation of the
    # rendered page.  Only DOM-derived evidence may attest that they applied.
    del applied_filters
    if isinstance(content, str):
        content_bytes = content.encode("utf-8")
    elif isinstance(content, bytes):
        content_bytes = content
    else:
        raise ValueError("filtered HTML content must be bytes or text")

    try:
        normalized_status = int(status_code)
    except (TypeError, ValueError) as exc:
        raise ValueError("status_code must be an integer") from exc

    blocked = (
        normalized_status in _BLOCKING_STATUS_CODES
        or has_visible_challenge(content_bytes)
    )
    if blocked:
        return DiscoveryCapture(
            source_name=source_name,
            source_url=source_url,
            observed_at=observed_at,
            status_code=normalized_status,
            applied_filters={},
            cards=[],
            detail_requests=0,
            blocked=True,
            raw_snapshot_path=raw_snapshot_path,
        )

    if not isinstance(selectors, Mapping):
        raise ValueError("selectors must be a mapping")
    result_selector = selectors.get("result_count")
    if not isinstance(result_selector, str) or not result_selector.strip():
        raise ValueError("selectors['result_count'] must be a non-empty CSS selector")
    card_selector = selectors.get("card")
    if card_selector is not None and (
        not isinstance(card_selector, str) or not card_selector.strip()
    ):
        raise ValueError("selectors['card'] must be a non-empty CSS selector")
    if count_is_complete is not None and not isinstance(count_is_complete, bool):
        raise ValueError("count_is_complete must be true, false, or null")

    try:
        soup = BeautifulSoup(content_bytes, "html.parser")
        count_node = soup.select_one(result_selector)
        result_count = (
            _parse_result_count(count_node.get_text(" ", strip=True))
            if count_node is not None
            else None
        )
        evidence = _applied_filter_evidence(soup, selectors.get("applied_filter"))
        card_nodes = soup.select(card_selector) if card_selector is not None else None
    except Exception as exc:
        raise ValueError(f"filtered HTML selector parsing failed: {exc}") from exc

    complete = count_node is not None if count_is_complete is None else count_is_complete
    card_cap = _optional_positive_int(max_result_cards, field_name="max_result_cards")
    if count_is_complete is True and result_count is None and card_nodes is not None:
        result_count = len(card_nodes)
    envelope = DiscoveryCapture(
        source_name=source_name,
        source_url=source_url,
        observed_at=observed_at,
        status_code=normalized_status,
        result_count=result_count,
        count_is_complete=complete,
        applied_filters={},
        applied_filter_evidence=evidence,
        cards=[],
        detail_requests=0,
        blocked=False,
        raw_snapshot_path=raw_snapshot_path,
    )
    if (
        card_cap is not None
        and complete
        and result_count is not None
        and result_count > card_cap
    ):
        return envelope

    parser_content = content_bytes
    if (
        card_cap is not None
        and card_nodes is not None
        and len(card_nodes) > card_cap + 1
    ):
        parser_content = "".join(
            str(node) for node in card_nodes[: card_cap + 1]
        ).encode("utf-8")
    parse_warnings: list[str] = []
    try:
        rows = parse_public_html(
            parser_content,
            source_url,
            source_name,
            observed_at,
        )
    except _PartialParseError as exc:
        rows = exc.rows
        parse_warnings = list(exc.errors)
    except Exception as exc:
        raise ValueError(f"filtered HTML parser failed: {exc}") from exc

    if count_is_complete is True and result_count is None and card_nodes is None:
        envelope = envelope.model_copy(
            update={"result_count": len(rows)},
            deep=True,
        )
    bounded_rows = rows if card_cap is None else rows[: card_cap + 1]
    cards = [
        _project_card(
            row,
            source_name=source_name,
            feed_url=source_url,
            observed_at=envelope.observed_at,
            card_index=index,
        )
        for index, row in enumerate(bounded_rows)
    ]
    return envelope.model_copy(
        update={"cards": cards, "parse_warnings": parse_warnings},
        deep=True,
    )


def _optional_positive_int(value: Any, *, field_name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be a positive integer")
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, float):
        if not math.isfinite(value) or not value.is_integer():
            raise ValueError(f"{field_name} must be a positive integer")
        parsed = int(value)
    elif isinstance(value, str) and _INTEGER_TEXT_RE.fullmatch(value.strip()):
        parsed = int(value)
    else:
        raise ValueError(f"{field_name} must be a positive integer")
    if parsed < 1:
        raise ValueError(f"{field_name} must be a positive integer")
    return parsed


def _parse_result_count(text: str) -> int:
    if _NO_RESULTS_RE.search(text):
        return 0
    for pattern in (_COUNT_BEFORE_LABEL_RE, _COUNT_AFTER_LABEL_RE):
        match = pattern.search(text)
        if match is not None:
            return int(match.group("count").replace(",", ""))
    matches = _ANY_RESULT_NUMBER_RE.findall(text)
    if matches:
        return int(matches[-1].replace(",", ""))
    raise ValueError(f"could not parse result count from {text!r}")


def _applied_filter_evidence(
    soup: BeautifulSoup,
    selector: Any,
) -> list[str]:
    if selector is None:
        return []
    if not isinstance(selector, str) or not selector.strip():
        raise ValueError("selectors['applied_filter'] must be a non-empty CSS selector")
    evidence: list[str] = []
    for marker in soup.select(selector):
        marker_text = _visible_marker_text(marker)
        if not marker_text:
            continue
        item = f"global: applied filter marker found: {marker_text}"
        if item not in evidence:
            evidence.append(item)
    return evidence


def _visible_marker_text(marker: Any) -> str:
    if _element_or_ancestor_is_hidden(marker):
        return ""
    parts: list[str] = []
    for value in marker.descendants:
        if not isinstance(value, NavigableString) or isinstance(value, Comment):
            continue
        parent = getattr(value, "parent", None)
        if getattr(parent, "name", "") in {"script", "style", "template"}:
            continue
        if _element_or_ancestor_is_hidden(parent):
            continue
        text = " ".join(str(value).split())
        if text:
            parts.append(text)
    return " ".join(" ".join(parts).split())


def _element_or_ancestor_is_hidden(element: Any) -> bool:
    current = element
    while current is not None:
        attrs = getattr(current, "attrs", None)
        if isinstance(attrs, Mapping):
            if current.has_attr("hidden"):
                return True
            aria_hidden = attrs.get("aria-hidden")
            if str(aria_hidden or "").strip().lower() == "true":
                return True
            if _inline_style_hides(attrs.get("style")):
                return True
        current = getattr(current, "parent", None)
    return False


def _inline_style_hides(style: Any) -> bool:
    if not isinstance(style, str):
        return False
    for declaration in style.split(";"):
        property_name, separator, value = declaration.partition(":")
        if not separator:
            continue
        property_name = property_name.strip().lower()
        value = re.sub(r"\s*!important\s*$", "", value, flags=re.IGNORECASE)
        value = value.strip().lower()
        if property_name == "display" and value == "none":
            return True
        if property_name == "visibility" and value in {"hidden", "collapse"}:
            return True
    return False


def _project_card(
    value: Any,
    *,
    source_name: str,
    feed_url: str,
    observed_at: datetime,
    card_index: int,
) -> DiscoveryCard:
    if isinstance(value, DiscoveryCard):
        data = value.model_dump(mode="python")
    elif isinstance(value, Mapping):
        data = dict(value)
    else:
        raise ValueError(f"card {card_index}: expected an object")

    projected = {field: data[field] for field in _LIGHTWEIGHT_CARD_FIELDS if field in data}
    if "raw" in projected:
        projected["raw"] = _lightweight_raw_metadata(projected["raw"])
    projected["source_name"] = projected.get("source_name") or source_name
    projected["source_url"] = projected.get("source_url") or feed_url
    projected["feed_url"] = projected.get("feed_url") or feed_url
    projected["observed_at"] = projected.get("observed_at") or observed_at
    try:
        return DiscoveryCard.model_validate(projected)
    except Exception as exc:
        raise ValueError(f"card {card_index}: {exc}") from exc


def _lightweight_raw_metadata(value: Any) -> dict[str, str | int | float | bool]:
    if not isinstance(value, Mapping):
        return {}
    projected: dict[str, str | int | float | bool] = {}
    for field in _RAW_CARD_METADATA_FIELDS:
        item = value.get(field)
        if not isinstance(item, (str, int, float, bool)):
            continue
        if isinstance(item, float) and not math.isfinite(item):
            continue
        projected[field] = item
    return projected
