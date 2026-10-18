"""Pluggable visual classification for Strategy V2 photo evidence.

The production pipeline uses one structured request per image.  That request
returns both the space/content labels and the visible maintenance, cosmetic,
and defect evidence.  The prompt still asks the model to identify the space
before interpreting condition, but those are two instructions in one audited
response rather than two billable HTTP calls.

Adapters that only implement the former phase-based interface remain
supported as a compatibility fallback; they are explicitly reported as
``LEGACY_TWO_PHASE``.

The default is explicit ``UNAVAILABLE``.  A configured vision adapter may be
used later, but missing credentials or a failed request never becomes a
negative property condition.
"""

from __future__ import annotations

import base64
import json
import mimetypes
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from time import sleep
from typing import Any, Literal, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from pydantic import BaseModel, ConfigDict, Field

from .strategy_v2_photo_intelligence import (
    PHOTO_LABELS,
    PhotoConfidence,
    PhotoLabel,
    PhotoManifest,
    build_photo_manifest,
)


VisualPhase = Literal["space", "condition"]
VISUAL_SUCCESS_STATUSES = {
    "AVAILABLE",
    "DOWNLOADED",
    "LOCAL_CACHE",
}


class VisualClassificationError(RuntimeError):
    """A per-image visual classification failure."""


class VisualClassification(BaseModel):
    """Normalized response shape accepted from a visual adapter."""

    model_config = ConfigDict(extra="allow")
    status: str = "CLASSIFIED"
    labels: list[PhotoLabel] = Field(default_factory=list)
    visible_defects: list[str] = Field(default_factory=list)
    maintenance_signals: list[str] = Field(default_factory=list)
    cosmetic_signals: list[str] = Field(default_factory=list)
    confidence: PhotoConfidence = "UNKNOWN"
    evidence: list[str] = Field(default_factory=list)


class VisualPhotoClassifier(Protocol):
    name: str
    version: str

    def classify_image(
        self,
        *,
        image_path: str,
        property_key: str,
        original_order: int,
        space_labels: Sequence[str] = (),
    ) -> Mapping[str, Any]: ...

    def classify(
        self,
        *,
        phase: VisualPhase,
        image_path: str,
        property_key: str,
        original_order: int,
        space_labels: Sequence[str] = (),
    ) -> Mapping[str, Any]: ...


def _normalise_labels(value: Any) -> list[PhotoLabel]:
    if value is None:
        return []
    values: Sequence[Any]
    if isinstance(value, str):
        values = [value]
    elif isinstance(value, Sequence):
        values = value
    else:
        values = [value]
    result: list[PhotoLabel] = []
    for item in values:
        token = "".join(char if char.isalnum() else "_" for char in str(item).upper()).strip("_")
        if token in PHOTO_LABELS and token not in result:
            result.append(token)  # type: ignore[arg-type]
    return result


def _signals(value: Any) -> list[str]:
    if value is None:
        return []
    values: Sequence[Any]
    if isinstance(value, str):
        values = [value]
    elif isinstance(value, Sequence):
        values = value
    else:
        values = [value]
    return list(dict.fromkeys(str(item).strip() for item in values if str(item).strip()))


def _confidence(value: Any) -> PhotoConfidence:
    token = str(value or "UNKNOWN").upper()
    if token in {"HIGH", "MEDIUM", "LOW", "UNKNOWN"}:
        return token  # type: ignore[return-value]
    try:
        numeric = float(token)
    except (TypeError, ValueError):
        return "UNKNOWN"
    if numeric >= 0.85:
        return "HIGH"
    if numeric >= 0.65:
        return "MEDIUM"
    if numeric > 0:
        return "LOW"
    return "UNKNOWN"


def _normalise_result(value: Mapping[str, Any] | VisualClassification | None) -> VisualClassification:
    if isinstance(value, VisualClassification):
        return value
    if not isinstance(value, Mapping):
        raise VisualClassificationError("classifier returned a non-object result")
    return VisualClassification(
        status=str(value.get("status") or "CLASSIFIED"),
        labels=_normalise_labels(value.get("labels") or value.get("space_labels")),
        visible_defects=_signals(value.get("visible_defects")),
        maintenance_signals=_signals(value.get("maintenance_signals")),
        cosmetic_signals=_signals(value.get("cosmetic_signals")),
        confidence=_confidence(value.get("confidence")),
        evidence=_signals(value.get("evidence")),
    )


def classify_photo_manifest(
    manifest: PhotoManifest,
    *,
    classifier: VisualPhotoClassifier | None,
) -> tuple[PhotoManifest, dict[str, Any]]:
    """Apply structured visual evidence to unique locally available images.

    A modern classifier exposes ``classify_image`` and receives one request
    per image.  A phase-only legacy adapter is still accepted, but is called
    twice per image and marked explicitly in the returned summary.
    """

    if classifier is None:
        status = "NO_IMAGES" if not manifest.images else "UNAVAILABLE"
        summary = {
            "status": status,
            "classifier": None,
            "classification_mode": "UNAVAILABLE",
            "classified_count": 0,
            "failed_count": 0,
            "skipped_count": len(manifest.images),
            "classification_request_count": 0,
            "http_request_attempts": 0,
            "retry_attempts": 0,
        }
        return manifest.model_copy(
            update={
                "schema_version": "strategy-v2-phase-2a-photo-manifest-v2",
                "visual_status": status,
                "visual_classifier": None,
                "visual_summary": summary,
            }
        ), summary

    records: list[dict[str, Any]] = []
    classified_count = 0
    failed_count = 0
    skipped_count = 0
    combined_classifier = getattr(classifier, "classify_image", None)
    combined_mode = callable(combined_classifier)
    classification_request_count = 0
    for image in sorted(manifest.images, key=lambda item: item.original_order):
        record = image.model_dump(mode="python")
        usable = (
            not image.is_duplicate
            and bool(image.local_path)
            and image.source_status in VISUAL_SUCCESS_STATUSES
            and Path(str(image.local_path)).is_file()
        )
        if not usable:
            skipped_count += 1
            records.append(record)
            continue

        try:
            if combined_mode:
                classification_request_count += 1
                combined_result = _normalise_result(
                    combined_classifier(
                        image_path=str(image.local_path),
                        property_key=manifest.property_key,
                        original_order=image.original_order,
                    )
                )
                labels = combined_result.labels
                condition_result = combined_result
            else:
                classification_request_count += 1
                space_result = _normalise_result(
                    classifier.classify(
                        phase="space",
                        image_path=str(image.local_path),
                        property_key=manifest.property_key,
                        original_order=image.original_order,
                    )
                )
                labels = space_result.labels
                classification_request_count += 1
                condition_result = _normalise_result(
                    classifier.classify(
                        phase="condition",
                        image_path=str(image.local_path),
                        property_key=manifest.property_key,
                        original_order=image.original_order,
                        space_labels=labels,
                    )
                )
            record.update(
                {
                    "content_labels": labels,
                    "visual_status": "CLASSIFIED",
                    "visual_confidence": _confidence(
                        "HIGH"
                        if combined_mode and condition_result.confidence == "HIGH"
                        else condition_result.confidence
                    ),
                    "visible_defects": condition_result.visible_defects,
                    "maintenance_signals": condition_result.maintenance_signals,
                    "cosmetic_signals": condition_result.cosmetic_signals,
                    "visual_evidence": list(
                        dict.fromkeys(
                            condition_result.evidence
                            if combined_mode
                            else space_result.evidence + condition_result.evidence
                        )
                    ),
                }
            )
            classified_count += 1
        except (OSError, TypeError, ValueError, VisualClassificationError, HTTPError, URLError) as exc:
            detail = str(exc).strip().replace("\n", " ")[:160]
            failure_reason = f"classification_failed:{type(exc).__name__}"
            if detail:
                failure_reason = f"{failure_reason}:{detail}"
            record.update(
                {
                    "visual_status": "FAILED",
                    "visual_confidence": "UNKNOWN",
                    "visual_evidence": [failure_reason],
                }
            )
            failed_count += 1
        records.append(record)

    if classified_count and not failed_count:
        status = "CLASSIFIED"
    elif classified_count:
        status = "PARTIAL"
    elif failed_count:
        status = "FAILED"
    else:
        status = "NO_IMAGES"
    http_request_attempts = getattr(classifier, "http_request_attempts", None)
    retry_attempts = getattr(classifier, "retry_attempts", None)
    summary = {
        "status": status,
        "classifier": getattr(classifier, "name", type(classifier).__name__),
        "classifier_version": getattr(classifier, "version", "unknown"),
        "classification_mode": "ONE_REQUEST_PER_IMAGE" if combined_mode else "LEGACY_TWO_PHASE",
        "classified_count": classified_count,
        "failed_count": failed_count,
        "skipped_count": skipped_count,
        "classification_request_count": classification_request_count,
        "http_request_attempts": http_request_attempts,
        "retry_attempts": retry_attempts,
    }
    enriched = build_photo_manifest(
        property_key=manifest.property_key,
        photos=records,
        retrieved_at=manifest.retrieved_at,
    ).model_copy(
        update={
            "schema_version": "strategy-v2-phase-2a-photo-manifest-v2",
            "acquisition_status": manifest.acquisition_status,
            "acquisition_summary": manifest.acquisition_summary,
            "visual_status": status,
            "visual_classifier": f"{summary['classifier']}@{summary['classifier_version']}",
            "visual_summary": summary,
        }
    )
    return enriched, summary


class JsonHttpVisionClassifier:
    """OpenAI-compatible JSON vision adapter, kept optional and provider-neutral.

    The adapter is only invoked when the caller supplies an endpoint and key.
    It sends the local image as a data URL and never persists the credential or
    the request URL in a property artifact.
    """

    name = "json-http-vision"

    def __init__(
        self,
        *,
        endpoint: str,
        api_key: str,
        model: str,
        timeout: float = 60.0,
        version: str = "1",
        wire_api: Literal["chat_completions", "responses"] = "chat_completions",
        max_attempts: int = 3,
        retry_backoff_seconds: float = 1.0,
    ) -> None:
        if not endpoint or not api_key or not model:
            raise ValueError("endpoint, api_key, and model are required")
        if wire_api not in {"chat_completions", "responses"}:
            raise ValueError("wire_api must be chat_completions or responses")
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if retry_backoff_seconds < 0:
            raise ValueError("retry_backoff_seconds must not be negative")
        endpoint = endpoint.rstrip("/")
        if endpoint.endswith("/v1"):
            endpoint = f"{endpoint}/{wire_api_path(wire_api)}"
        self.endpoint = endpoint
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.version = version
        self.wire_api = wire_api
        self.max_attempts = max_attempts
        self.retry_backoff_seconds = retry_backoff_seconds
        self.http_request_attempts = 0
        self.retry_attempts = 0

    def classify_image(
        self,
        *,
        image_path: str,
        property_key: str,
        original_order: int,
        space_labels: Sequence[str] = (),
    ) -> Mapping[str, Any]:
        path = Path(image_path)
        content = path.read_bytes()
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        encoded = base64.b64encode(content).decode("ascii")
        prompt = (
            "You are an auditable real-estate photo evidence classifier. "
            f"Property {property_key}, gallery order {original_order}. "
            "Analyze this single listing photo in one structured pass. First identify the "
            "visible space/content, then assess only visible evidence in that frame. Return "
            "one JSON object with keys: labels (array using only EXTERIOR_FRONT, "
            "EXTERIOR_REAR, ROOF_VISIBLE, LIVING, KITCHEN, BATH, BEDROOM, BASEMENT, "
            "MECHANICAL, GARAGE, YARD, FLOOR_PLAN, MAP, OTHER), visible_defects, "
            "maintenance_signals, cosmetic_signals, confidence, and evidence. A dated but "
            "clean finish is cosmetic, not a defect. Do not infer hidden systems or anything "
            f"outside the frame. Prior space labels, if any, are {list(space_labels)!r}."
        )
        image_url = f"data:{mime};base64,{encoded}"
        if self.wire_api == "responses":
            payload = {
                "model": self.model,
                "input": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": prompt},
                            {"type": "input_image", "image_url": image_url},
                        ],
                    }
                ],
            }
        else:
            payload = {
                "model": self.model,
                "temperature": 0,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {"type": "image_url", "image_url": {"url": image_url}},
                        ],
                    }
                ],
            }
        request = Request(
            self.endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        raw = None
        for attempt in range(1, self.max_attempts + 1):
            self.http_request_attempts += 1
            if attempt > 1:
                self.retry_attempts += 1
            try:
                with urlopen(request, timeout=self.timeout) as response:  # noqa: S310 - configured endpoint
                    raw = json.loads(response.read().decode("utf-8"))
                break
            except HTTPError as exc:
                retryable = exc.code in {408, 409, 425, 429} or 500 <= exc.code < 600
                if not retryable or attempt >= self.max_attempts:
                    raise VisualClassificationError(f"vision_request_failed:http_{exc.code}") from exc
            except (URLError, TimeoutError, OSError) as exc:
                if attempt >= self.max_attempts:
                    raise VisualClassificationError(f"vision_request_failed:{type(exc).__name__}") from exc
            except json.JSONDecodeError as exc:
                if attempt >= self.max_attempts:
                    raise VisualClassificationError("vision_response_invalid_json") from exc
            sleep(self.retry_backoff_seconds * (2 ** (attempt - 1)))
        if raw is None:
            raise VisualClassificationError("vision_request_failed:empty_response")
        text = extract_vision_response_text(raw).strip()
        if text.startswith("```"):
            text = text.strip("`").replace("json", "", 1).strip()
        try:
            content_value = json.loads(text)
        except json.JSONDecodeError as exc:
            raise VisualClassificationError("vision_response_not_json") from exc
        if not isinstance(content_value, Mapping):
            raise VisualClassificationError("vision_response_not_object")
        return content_value

    def classify(
        self,
        *,
        phase: VisualPhase,
        image_path: str,
        property_key: str,
        original_order: int,
        space_labels: Sequence[str] = (),
    ) -> Mapping[str, Any]:
        """Compatibility wrapper for callers using the old phase interface.

        The request itself is still combined and sent only once.  The wrapper
        returns the subset expected by the requested legacy phase.
        """

        result = self.classify_image(
            image_path=image_path,
            property_key=property_key,
            original_order=original_order,
            space_labels=space_labels,
        )
        if phase == "space":
            return {
                "labels": result.get("labels"),
                "confidence": result.get("confidence"),
                "evidence": result.get("evidence"),
            }
        return {
            "visible_defects": result.get("visible_defects"),
            "maintenance_signals": result.get("maintenance_signals"),
            "cosmetic_signals": result.get("cosmetic_signals"),
            "confidence": result.get("confidence"),
            "evidence": result.get("evidence"),
        }


def wire_api_path(wire_api: Literal["chat_completions", "responses"]) -> str:
    """Return the API route appended to a CC Switch/OpenAI base URL."""

    return "responses" if wire_api == "responses" else "chat/completions"


def extract_vision_response_text(raw: Any) -> str:
    """Extract text from Chat Completions or Responses API JSON envelopes."""

    if not isinstance(raw, Mapping):
        return ""
    direct = raw.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct

    def _walk(value: Any) -> list[str]:
        if isinstance(value, str):
            return [value]
        if isinstance(value, Mapping):
            parts: list[str] = []
            if isinstance(value.get("text"), str):
                parts.append(value["text"])
            for key in ("content", "output", "message", "choices"):
                if key in value:
                    parts.extend(_walk(value[key]))
            return parts
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            parts: list[str] = []
            for item in value:
                parts.extend(_walk(item))
            return parts
        return []

    return "".join(_walk(raw.get("output", raw.get("choices", []))))
