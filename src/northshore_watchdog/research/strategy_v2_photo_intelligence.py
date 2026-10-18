"""Photo evidence and online holding ledgers for Strategy V2 Phase 2A.

This module intentionally stops at observable online evidence.  It does not
pretend that listing photographs are an inspection, and it does not turn a
missing photograph into a negative condition finding.

The public functions are deliberately small and deterministic:

* :func:`build_photo_manifest` creates an auditable, order-preserving photo
  manifest and records exact/perceptual duplicate evidence;
* :func:`summarize_photo_coverage` measures coverage by space group rather
  than by the number of attractive listing photographs; and
* :func:`build_online_holding_assessment` keeps visible maintenance,
  cosmetic modernisation, and family holding usability as separate ledgers.

Network acquisition and visual classification remain separate adapters.  They
can pass their results through the manifest without changing the holding
ledger contract.
"""

from __future__ import annotations

import hashlib
import html as html_lib
import io
import re
import shutil
import struct
import subprocess
import tempfile
import zlib
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


PHOTO_LABELS = (
    "EXTERIOR_FRONT",
    "EXTERIOR_REAR",
    "ROOF_VISIBLE",
    "LIVING",
    "KITCHEN",
    "BATH",
    "BEDROOM",
    "BASEMENT",
    "MECHANICAL",
    "GARAGE",
    "YARD",
    "FLOOR_PLAN",
    "MAP",
    "OTHER",
)
PhotoLabel = Literal[
    "EXTERIOR_FRONT",
    "EXTERIOR_REAR",
    "ROOF_VISIBLE",
    "LIVING",
    "KITCHEN",
    "BATH",
    "BEDROOM",
    "BASEMENT",
    "MECHANICAL",
    "GARAGE",
    "YARD",
    "FLOOR_PLAN",
    "MAP",
    "OTHER",
]

REQUIRED_SPACE_GROUPS = ("exterior", "living", "basement", "site")
SPACE_GROUP_LABELS: dict[str, set[str]] = {
    "exterior": {"EXTERIOR_FRONT", "EXTERIOR_REAR", "ROOF_VISIBLE"},
    "living": {"LIVING", "KITCHEN", "BATH", "BEDROOM"},
    "basement": {"BASEMENT", "MECHANICAL"},
    "site": {"GARAGE", "YARD"},
}

VISIBLE_DEFECT_LABELS = {
    "WATER_STAINING",
    "VISIBLE_MOLD",
    "FOUNDATION_CRACKING",
    "FLOOR_DEFORMATION",
    "SIDING_DAMAGE",
    "ROOF_DAMAGE",
    "BASEMENT_MOISTURE",
    "UNFINISHED_MAJOR_DEMOLITION",
    "FIRE_DAMAGE",
    "COLLAPSE",
    "UNINHABITABLE",
}

MaintenanceCondition = Literal[
    "GOOD", "ACCEPTABLE", "ELEVATED_RISK", "SEVERE", "UNKNOWN"
]
CosmeticModernization = Literal["RECENT", "MODERATE", "DATED", "UNKNOWN"]
HoldingUsability = Literal["VERY_GOOD", "GOOD", "ACCEPTABLE", "POOR", "UNKNOWN"]
OnlineCapexBand = Literal["A", "B", "C", "D", "E", "UNKNOWN"]
PhotoConfidence = Literal["HIGH", "MEDIUM", "LOW", "UNKNOWN"]
EvidenceCategory = Literal[
    "CRITICAL",
    "MAJOR",
    "MODERATE",
    "MINOR_MAINTENANCE",
    "COSMETIC",
    "NOT_OBSERVED",
    "UNKNOWN",
]
EvidenceScope = Literal["LOCAL", "SYSTEMIC", "NONE", "UNKNOWN"]
CostType = Literal["SURVIVAL", "COSMETIC", "MINOR_MAINTENANCE", "FUNCTIONAL"]


class _PhotoModel(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)


class PhotoObservation(_PhotoModel):
    """One listing photograph plus its provenance and classification evidence."""

    property_key: str
    source_url: str | None = None
    original_order: int
    retrieved_at: datetime | None = None
    sha256: str | None = None
    # ``sha256`` is reserved for image content bytes.  URL-only records keep
    # that field null; ``source_digest`` is a separate reference fingerprint.
    hash_basis: Literal["CONTENT", "SUPPLIED", "NONE"] = "NONE"
    source_digest: str | None = None
    source_photo_id: str | None = None
    perceptual_hash: str | None = None
    width: int | None = Field(default=None, ge=0)
    height: int | None = Field(default=None, ge=0)
    local_path: str | None = None
    source_status: str = "UNKNOWN"
    content_labels: list[PhotoLabel] = Field(default_factory=list)
    is_duplicate: bool = False
    duplicate_of_order: int | None = Field(default=None, ge=0)
    duplicate_reason: str | None = None
    perceptual_hash_algorithm: str | None = None
    content_type: str | None = None
    file_size_bytes: int | None = Field(default=None, ge=0)
    visual_status: str = "NOT_RUN"
    visual_confidence: PhotoConfidence = "UNKNOWN"
    visible_defects: list[str] = Field(default_factory=list)
    maintenance_signals: list[str] = Field(default_factory=list)
    cosmetic_signals: list[str] = Field(default_factory=list)
    visual_evidence: list[str] = Field(default_factory=list)


class PhotoManifest(_PhotoModel):
    """Auditable ordered photo collection for one property."""

    schema_version: str = "strategy-v2-phase-2a-photo-manifest-v1"
    property_key: str
    source_status: str
    retrieved_at: datetime | None = None
    images: list[PhotoObservation] = Field(default_factory=list)
    duplicate_count: int = Field(default=0, ge=0)
    acquisition_status: str = "NOT_RUN"
    acquisition_summary: dict[str, Any] = Field(default_factory=dict)
    visual_status: str = "NOT_RUN"
    visual_classifier: str | None = None
    visual_summary: dict[str, Any] = Field(default_factory=dict)


class OnlineCostItem(_PhotoModel):
    """One unique online-observed project group and its screening prior."""

    project_group: str
    category: EvidenceCategory
    scope: EvidenceScope
    cost_type: CostType
    cost_low: float = Field(default=0, ge=0)
    cost_base: float = Field(default=0, ge=0)
    cost_high: float = Field(default=0, ge=0)
    source: str
    evidence: list[str] = Field(default_factory=list)
    confidence: PhotoConfidence = "UNKNOWN"


class OnlineHoldingAssessment(_PhotoModel):
    """Separate online evidence ledgers for current holding use."""

    schema_version: str = "strategy-v2-phase-2a-online-holding-v2"
    visible_maintenance_condition: MaintenanceCondition = "UNKNOWN"
    visible_maintenance_score: float | None = Field(default=None, ge=0, le=100)
    cosmetic_modernization: CosmeticModernization = "UNKNOWN"
    cosmetic_modernization_score: float | None = Field(default=None, ge=0, le=100)
    holding_usability: HoldingUsability = "UNKNOWN"
    holding_usability_score: float | None = Field(default=None, ge=0, le=100)
    online_capex_band: OnlineCapexBand = "UNKNOWN"
    photo_evidence_coverage: float | None = Field(default=None, ge=0, le=100)
    photo_confidence: PhotoConfidence = "UNKNOWN"
    kitchen_count: int | None = Field(default=None, ge=0)
    visible_defects: list[str] = Field(default_factory=list)
    maintenance_evidence: list[str] = Field(default_factory=list)
    cosmetic_evidence: list[str] = Field(default_factory=list)
    usability_evidence: list[str] = Field(default_factory=list)
    flags: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    survival_capex_low: float = Field(default=0, ge=0)
    survival_capex_base: float = Field(default=0, ge=0)
    survival_capex_high: float = Field(default=0, ge=0)
    cosmetic_reno_low: float = Field(default=0, ge=0)
    cosmetic_reno_base: float = Field(default=0, ge=0)
    cosmetic_reno_high: float = Field(default=0, ge=0)
    observed_survival_items: list[dict[str, Any]] = Field(default_factory=list)
    observed_cosmetic_items: list[dict[str, Any]] = Field(default_factory=list)
    minor_maintenance_items: list[dict[str, Any]] = Field(default_factory=list)
    unseen_systems: list[str] = Field(default_factory=list)
    unobserved_critical_areas: list[str] = Field(default_factory=list)
    estimate_confidence: PhotoConfidence = "UNKNOWN"
    estimate_basis: str = "online_cost_prior_v1"


def _value(item: Any, name: str, default: Any = None) -> Any:
    if isinstance(item, Mapping):
        return item.get(name, default)
    return getattr(item, name, default)


def _as_text(value: Any) -> str:
    return " ".join(str(value or "").replace("&amp;", "and").lower().split())


def _normalise_datetime(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        result = value
    else:
        try:
            result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    if result.tzinfo is None:
        return result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def _normalise_labels(value: Any) -> list[PhotoLabel]:
    if value is None:
        return []
    if isinstance(value, str):
        values: Sequence[Any] = [value]
    elif isinstance(value, Sequence):
        values = value
    else:
        values = [value]
    result: list[PhotoLabel] = []
    for raw in values:
        label = re.sub(r"[^A-Za-z0-9]+", "_", str(raw).strip()).strip("_").upper()
        if not label:
            continue
        if label not in PHOTO_LABELS:
            label = "OTHER"
        if label not in result:
            result.append(label)  # type: ignore[arg-type]
    return result


def _content_bytes(photo: Any) -> bytes | None:
    for name in ("content", "content_bytes", "image_bytes", "bytes"):
        value = _value(photo, name)
        if value is None:
            continue
        if isinstance(value, bytes):
            return value
        if isinstance(value, (bytearray, memoryview)):
            return bytes(value)
    local_path = _value(photo, "local_path") or _value(photo, "path")
    if local_path:
        try:
            return Path(str(local_path)).read_bytes()
        except (OSError, ValueError):
            return None
    return None


def _image_metadata(content: bytes | None) -> tuple[int | None, int | None, str | None]:
    """Best-effort dimensions and dHash; absence of Pillow is not an error."""

    if not content:
        return None, None, None
    try:
        from PIL import Image  # type: ignore[import-not-found]

        with Image.open(io.BytesIO(content)) as image:
            width, height = image.size
            # dHash is intentionally implemented here instead of adding a
            # second image-hash dependency.  It is robust to common JPEG
            # re-compression/resizing while remaining auditable and cheap.
            grayscale = image.convert("L").resize((9, 8))
            pixels = list(grayscale.getdata())
            bits = 0
            for row in range(8):
                offset = row * 9
                for column in range(8):
                    bits = (bits << 1) | int(pixels[offset + column] > pixels[offset + column + 1])
            perceptual_hash = f"{bits:016x}"
            return width, height, perceptual_hash
    except (ImportError, OSError, ValueError):
        # The project also runs on macOS machines where the system ``sips``
        # decoder is available even when Pillow has not been installed yet.
        # Use it only as a local, deterministic fallback; if neither decoder
        # exists the manifest keeps the metadata explicitly unknown.
        width, height = _header_dimensions(content)
        return width, height, _sips_dhash(content)


def _header_dimensions(content: bytes) -> tuple[int | None, int | None]:
    if content.startswith(b"\x89PNG\r\n\x1a\n") and len(content) >= 24:
        return int.from_bytes(content[16:20], "big"), int.from_bytes(content[20:24], "big")
    if content.startswith((b"GIF87a", b"GIF89a")) and len(content) >= 10:
        return int.from_bytes(content[6:8], "little"), int.from_bytes(content[8:10], "little")
    if content.startswith(b"\xff\xd8\xff"):
        offset = 2
        while offset + 9 < len(content):
            if content[offset] != 0xFF:
                offset += 1
                continue
            marker = content[offset + 1]
            offset += 2
            if marker in {0xD8, 0xD9}:
                continue
            if offset + 2 > len(content):
                break
            length = int.from_bytes(content[offset:offset + 2], "big")
            if length < 2 or offset + length > len(content):
                break
            if marker in set(range(0xC0, 0xC4)) | set(range(0xC5, 0xC8)) | set(range(0xC9, 0xCC)) | set(range(0xCD, 0xD0)):
                if offset + 7 <= len(content):
                    return int.from_bytes(content[offset + 5:offset + 7], "big"), int.from_bytes(content[offset + 3:offset + 5], "big")
            offset += length
    return None, None


def _png_grayscale_pixels(content: bytes) -> tuple[int, int, list[int]] | None:
    """Decode the small 8-bit PNG emitted by the macOS fallback."""

    signature = b"\x89PNG\r\n\x1a\n"
    if not content.startswith(signature):
        return None
    offset = len(signature)
    width = height = bit_depth = color_type = interlace = None
    compressed = bytearray()
    while offset + 12 <= len(content):
        length = struct.unpack(">I", content[offset:offset + 4])[0]
        chunk_type = content[offset + 4:offset + 8]
        chunk = content[offset + 8:offset + 8 + length]
        offset += length + 12
        if chunk_type == b"IHDR" and len(chunk) >= 13:
            width, height, bit_depth, color_type, _compression, _filter, interlace = struct.unpack(
                ">IIBBBBB", chunk[:13]
            )
        elif chunk_type == b"IDAT":
            compressed.extend(chunk)
        elif chunk_type == b"IEND":
            break
    if not width or not height or bit_depth != 8 or interlace != 0 or color_type not in {0, 2, 4, 6}:
        return None
    channels = {0: 1, 2: 3, 4: 2, 6: 4}[color_type]
    row_bytes = width * channels
    try:
        raw = zlib.decompress(bytes(compressed))
    except zlib.error:
        return None
    if len(raw) < height * (row_bytes + 1):
        return None
    rows: list[bytes] = []
    cursor = 0
    previous = bytearray(row_bytes)
    for _row in range(height):
        filter_type = raw[cursor]
        cursor += 1
        current = bytearray(raw[cursor:cursor + row_bytes])
        cursor += row_bytes
        for index in range(row_bytes):
            left = current[index - channels] if index >= channels else 0
            up = previous[index]
            upper_left = previous[index - channels] if index >= channels else 0
            if filter_type == 1:
                current[index] = (current[index] + left) & 0xFF
            elif filter_type == 2:
                current[index] = (current[index] + up) & 0xFF
            elif filter_type == 3:
                current[index] = (current[index] + ((left + up) // 2)) & 0xFF
            elif filter_type == 4:
                estimate = left + up - upper_left
                distance_left = abs(estimate - left)
                distance_up = abs(estimate - up)
                distance_upper_left = abs(estimate - upper_left)
                predictor = left if distance_left <= distance_up and distance_left <= distance_upper_left else up if distance_up <= distance_upper_left else upper_left
                current[index] = (current[index] + predictor) & 0xFF
            elif filter_type != 0:
                return None
        rows.append(bytes(current))
        previous = current
    pixels: list[int] = []
    for row in rows:
        for pixel in range(width):
            offset = pixel * channels
            pixels.append(row[offset])
    return width, height, pixels


def _sips_dhash(content: bytes) -> str | None:
    sips = shutil.which("sips")
    if not sips:
        return None
    try:
        with tempfile.TemporaryDirectory(prefix="northshore-photo-") as directory:
            source = Path(directory) / "source.img"
            rendered = Path(directory) / "rendered.png"
            source.write_bytes(content)
            subprocess.run(
                [sips, "-z", "8", "9", "-s", "format", "png", "-o", str(rendered), str(source)],
                check=True,
                capture_output=True,
                timeout=10,
            )
            decoded = _png_grayscale_pixels(rendered.read_bytes())
            if decoded is None:
                return None
            width, height, pixels = decoded
            if width != 9 or height != 8 or len(pixels) != 72:
                return None
            bits = 0
            for row in range(8):
                row_offset = row * 9
                for column in range(8):
                    bits = (bits << 1) | int(pixels[row_offset + column] > pixels[row_offset + column + 1])
            return f"{bits:016x}"
    except (OSError, subprocess.SubprocessError, ValueError, zlib.error):
        return None


def _photo_hash(photo: Any, content: bytes | None) -> tuple[str | None, str]:
    supplied = _value(photo, "sha256")
    if supplied:
        supplied_basis = str(_value(photo, "hash_basis") or "SUPPLIED").upper()
        basis = supplied_basis if supplied_basis in {"CONTENT", "SUPPLIED"} else "SUPPLIED"
        return str(supplied).lower(), basis
    if content is not None:
        return hashlib.sha256(content).hexdigest(), "CONTENT"
    return None, "NONE"


def _source_digest(photo: Any, source_url: str | None) -> str | None:
    supplied = _value(photo, "source_digest")
    if supplied:
        return str(supplied).lower()
    if source_url:
        return hashlib.sha256(source_url.encode("utf-8")).hexdigest()
    return None


def _hex_hamming_distance(left: str, right: str) -> int | None:
    try:
        if len(left) != len(right):
            return None
        return (int(left, 16) ^ int(right, 16)).bit_count()
    except (TypeError, ValueError):
        return None


class _GalleryPhotoParser(HTMLParser):
    """Extract Realtor.ca gallery image references without fetching them."""

    _id_pattern = re.compile(r"^imageGallerySidebarPhoto(\d+)$", re.IGNORECASE)

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.records: list[tuple[int, int, str]] = []
        self._dom_order = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "img":
            return
        attributes = {str(name).lower(): value for name, value in attrs}
        match = self._id_pattern.fullmatch(str(attributes.get("id") or ""))
        if not match:
            return
        source_url = (
            attributes.get("src")
            or attributes.get("data-src")
            or attributes.get("data-original")
            or attributes.get("data-lazy-src")
        )
        if not source_url:
            return
        self.records.append(
            (
                int(match.group(1)),
                self._dom_order,
                html_lib.unescape(str(source_url)).strip(),
            )
        )
        self._dom_order += 1

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)


def extract_photo_records_from_html(html: str | None, property_key: str) -> list[dict[str, Any]]:
    """Extract ordered ``imageGallerySidebarPhoto1..N`` URL records.

    This pure parser never downloads images and never computes a content
    digest.  HTML only establishes the URL/reference and ordering; room
    labels remain empty until a visual classifier supplies them.
    """

    if not html:
        return []
    parser = _GalleryPhotoParser()
    try:
        parser.feed(str(html))
        parser.close()
    except (TypeError, ValueError):
        return []
    ordered = sorted(parser.records, key=lambda item: (item[0], item[1]))
    return [
        {
            "property_key": property_key,
            "source_url": source_url,
            "original_order": index,
            "source_photo_id": f"imageGallerySidebarPhoto{photo_number}",
            "content_labels": [],
        }
        for index, (photo_number, _dom_order, source_url) in enumerate(ordered)
    ]


def build_photo_manifest(
    property_key: str,
    photos: Sequence[Mapping[str, Any] | PhotoObservation] | None,
    retrieved_at: datetime | None,
) -> PhotoManifest:
    """Build an ordered manifest without raising on unavailable sources.

    ``photos`` may contain URL-only references, local bytes, local paths, or
    pre-classified labels.  The function never downloads a URL.  If only the
    URL is available, ``sha256`` remains ``None``; the separate
    ``source_digest`` identifies that URL for audit and duplicate detection.
    """

    manifest_time = _normalise_datetime(retrieved_at)
    entries = list(photos or [])
    if not entries:
        return PhotoManifest(
            property_key=property_key,
            source_status="PHOTO_SOURCE_UNAVAILABLE",
            retrieved_at=manifest_time,
            images=[],
            duplicate_count=0,
        )

    observations: list[PhotoObservation] = []
    seen_urls: dict[str, int] = {}
    seen_hashes: dict[str, int] = {}
    seen_source_digests: dict[str, int] = {}
    seen_perceptual_hashes: list[tuple[str, int]] = []

    for index, photo in enumerate(entries):
        source_url = _value(photo, "source_url")
        source_url = str(source_url) if source_url else None
        local_path = _value(photo, "local_path") or _value(photo, "path")
        local_path = str(local_path) if local_path else None
        content = _content_bytes(photo)
        digest, hash_basis = _photo_hash(photo, content)
        source_digest = _source_digest(photo, source_url)
        supplied_phash = _value(photo, "perceptual_hash")
        width = _value(photo, "width")
        height = _value(photo, "height")
        auto_width, auto_height, auto_phash = _image_metadata(content)
        width = width if width is not None else auto_width
        height = height if height is not None else auto_height
        perceptual_hash = str(supplied_phash) if supplied_phash else auto_phash

        explicit_status = _value(photo, "source_status")
        if explicit_status:
            source_status = str(explicit_status)
        elif content is not None or source_url or local_path:
            source_status = "AVAILABLE"
        else:
            source_status = "UNKNOWN"

        duplicate = False
        duplicate_of_order: int | None = None
        duplicate_reason: str | None = None
        if source_url and source_url in seen_urls:
            duplicate = True
            duplicate_of_order = seen_urls[source_url]
            duplicate_reason = "URL"
        if digest and digest in seen_hashes:
            duplicate = True
            duplicate_of_order = duplicate_of_order if duplicate_of_order is not None else seen_hashes[digest]
            duplicate_reason = duplicate_reason or "SHA256"
        if source_digest and source_digest in seen_source_digests:
            duplicate = True
            duplicate_of_order = duplicate_of_order if duplicate_of_order is not None else seen_source_digests[source_digest]
            duplicate_reason = duplicate_reason or "SOURCE_URL_DIGEST"
        if perceptual_hash:
            for previous_hash, previous_order in seen_perceptual_hashes:
                distance = _hex_hamming_distance(perceptual_hash, previous_hash)
                if distance is not None and distance <= 5:
                    duplicate = True
                    duplicate_of_order = duplicate_of_order if duplicate_of_order is not None else previous_order
                    duplicate_reason = duplicate_reason or "PERCEPTUAL"
                    break

        if source_url:
            seen_urls.setdefault(source_url, index)
        if digest:
            seen_hashes.setdefault(digest, index)
        if source_digest:
            seen_source_digests.setdefault(source_digest, index)
        if perceptual_hash:
            seen_perceptual_hashes.append((perceptual_hash, index))

        order_value = _value(photo, "original_order")
        try:
            original_order = int(order_value) if order_value is not None else index
        except (TypeError, ValueError):
            original_order = index

        observation = PhotoObservation(
            property_key=property_key,
            source_url=source_url,
            original_order=original_order,
            retrieved_at=_normalise_datetime(_value(photo, "retrieved_at")) or manifest_time,
            sha256=digest,
            hash_basis=hash_basis,
            source_digest=source_digest,
            source_photo_id=_value(photo, "source_photo_id"),
            perceptual_hash=perceptual_hash,
            width=width,
            height=height,
            local_path=local_path,
            source_status=source_status,
            content_labels=_normalise_labels(_value(photo, "content_labels")),
            is_duplicate=duplicate,
            duplicate_of_order=duplicate_of_order,
            duplicate_reason=duplicate_reason,
            perceptual_hash_algorithm=("dhash64-v1" if perceptual_hash else None),
            content_type=_value(photo, "content_type"),
            file_size_bytes=_value(photo, "file_size_bytes")
            or (len(content) if content is not None else None),
            visual_status=str(_value(photo, "visual_status") or "NOT_RUN"),
            visual_confidence=str(_value(photo, "visual_confidence") or "UNKNOWN"),
            visible_defects=_normalise_signal_list(_value(photo, "visible_defects")),
            maintenance_signals=_normalise_signal_list(_value(photo, "maintenance_signals")),
            cosmetic_signals=_normalise_signal_list(_value(photo, "cosmetic_signals")),
            visual_evidence=_normalise_signal_list(_value(photo, "visual_evidence")),
        )
        observations.append(observation)

    available_statuses = {
        "AVAILABLE",
        "DOWNLOADED",
        "LOCAL_CACHE",
        "DUPLICATE_URL",
        "DUPLICATE_SHA256",
        "DUPLICATE_CONTENT",
        "DUPLICATE_PERCEPTUAL",
    }
    missing_statuses = {"UNKNOWN", "PHOTO_SOURCE_UNAVAILABLE", "NO_SOURCE_URL"}
    available_count = sum(item.source_status in available_statuses for item in observations)
    failed_count = sum(
        item.source_status not in available_statuses and item.source_status not in missing_statuses
        for item in observations
    )
    if available_count:
        source_status = "PHOTO_SOURCE_PARTIAL" if failed_count else "AVAILABLE"
    elif all(item.source_status in missing_statuses for item in observations):
        source_status = "PHOTO_SOURCE_UNAVAILABLE"
    else:
        source_status = "PHOTO_SOURCE_PARTIAL"

    return PhotoManifest(
        property_key=property_key,
        source_status=source_status,
        retrieved_at=manifest_time,
        images=observations,
        duplicate_count=sum(item.is_duplicate for item in observations),
    )


def _space_groups_for_labels(labels: Sequence[str]) -> set[str]:
    groups: set[str] = set()
    for group, allowed in SPACE_GROUP_LABELS.items():
        if any(label in allowed for label in labels):
            groups.add(group)
    return groups


def summarize_photo_coverage(
    manifest: PhotoManifest | Mapping[str, Any],
    required_space_groups: Sequence[str] = REQUIRED_SPACE_GROUPS,
) -> dict[str, Any]:
    """Summarise evidence coverage by distinct required space groups."""

    if isinstance(manifest, PhotoManifest):
        images = manifest.images
        source_status = manifest.source_status
    else:
        images = [PhotoObservation.model_validate(item) for item in manifest.get("images", [])]
        source_status = str(manifest.get("source_status") or "UNKNOWN")

    required = [str(group) for group in required_space_groups]
    usable = [item for item in images if not item.is_duplicate]
    classified = [item for item in usable if item.content_labels]
    covered = set()
    for item in classified:
        covered.update(_space_groups_for_labels(item.content_labels))
    covered_ordered = [group for group in required if group in covered]
    uncovered = [group for group in required if group not in covered]

    if not images or source_status == "PHOTO_SOURCE_UNAVAILABLE":
        coverage: float | None = None
        confidence: PhotoConfidence = "UNKNOWN"
    else:
        coverage = round(100.0 * len(covered_ordered) / len(required), 2) if required else 100.0
        if coverage >= 75:
            confidence = "HIGH"
        elif coverage >= 50:
            confidence = "MEDIUM"
        else:
            confidence = "LOW"

    return {
        "photo_count": len(images),
        "unique_photo_count": len(usable),
        "classified_photo_count": len(classified),
        "covered_space_groups": covered_ordered,
        "uncovered_space_groups": uncovered,
        "required_space_groups": required,
        "photo_evidence_coverage": coverage,
        "photo_confidence": confidence,
    }


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"[-+]?\d[\d,]*(?:\.\d+)?", str(value))
    if not match:
        return None
    try:
        return float(match.group(0).replace(",", ""))
    except ValueError:
        return None


def _first_value(card: Mapping[str, Any] | Any, names: Sequence[str]) -> Any:
    for name in names:
        value = _value(card, name)
        if value not in (None, "", [], {}):
            return value
    return None


def _normalise_signal_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        values: Sequence[Any] = [value]
    elif isinstance(value, Sequence):
        values = value
    else:
        values = [value]
    result: list[str] = []
    for item in values:
        text = str(item).strip()
        if text and text not in result:
            result.append(text)
    return result


def _canonical_defects(value: Any) -> list[str]:
    aliases = {
        "WATER_DAMAGE": "WATER_STAINING",
        "WATER_STAIN": "WATER_STAINING",
        "MOULD": "VISIBLE_MOLD",
        "MOLD": "VISIBLE_MOLD",
        "FOUNDATION_CRACK": "FOUNDATION_CRACKING",
        "UNFINISHED": "UNFINISHED_MAJOR_DEMOLITION",
        "FIRE": "FIRE_DAMAGE",
    }
    result: list[str] = []
    for item in _normalise_signal_list(value):
        token = re.sub(r"[^A-Za-z0-9]+", "_", item).strip("_").upper()
        token = aliases.get(token, token)
        if token not in result:
            result.append(token)
    return result


ONLINE_COST_PRIOR_VERSION = "online_cost_prior_v1"

ONLINE_SURVIVAL_PRIORS: dict[str, dict[str, tuple[float, float, float]]] = {
    "ROOF": {
        "LOCAL": (5_000, 10_000, 15_000),
        "SYSTEMIC": (15_000, 25_000, 35_000),
    },
    "WATER_DRAINAGE": {
        "LOCAL": (5_000, 12_500, 20_000),
        "SYSTEMIC": (20_000, 40_000, 60_000),
    },
    "FOUNDATION_STRUCTURE": {
        "LOCAL": (10_000, 20_000, 30_000),
        "SYSTEMIC": (30_000, 60_000, 100_000),
    },
    "HEATING": {
        "LOCAL": (5_000, 8_500, 12_000),
        "SYSTEMIC": (12_000, 18_000, 25_000),
    },
    "ELECTRICAL": {
        "LOCAL": (3_000, 8_000, 15_000),
        "SYSTEMIC": (15_000, 25_000, 40_000),
    },
    "PLUMBING": {
        "LOCAL": (3_000, 8_000, 15_000),
        "SYSTEMIC": (15_000, 25_000, 40_000),
    },
    "ENVELOPE": {
        "LOCAL": (3_000, 8_000, 15_000),
        "SYSTEMIC": (15_000, 30_000, 50_000),
    },
    "BASEMENT_UNFINISHED": {
        "LOCAL": (10_000, 20_000, 30_000),
        "SYSTEMIC": (30_000, 55_000, 80_000),
    },
}

ONLINE_COSMETIC_PRIORS: dict[str, tuple[float, float, float]] = {
    "PAINT_LIGHTING_CLOSET": (5_000, 12_500, 20_000),
    "FLOORING": (5_000, 12_500, 20_000),
    "KITCHEN": (15_000, 27_500, 40_000),
    "BATHROOM": (8_000, 14_000, 20_000),
}

ONLINE_SEVERE_PRIOR = (150_000, 225_000, 350_000)
MINOR_MAINTENANCE_PRIOR = (0, 0, 10_000)

CRITICAL_OBSERVATION_TOKENS = (
    "fire",
    "collapse",
    "uninhabitable",
    "severe mold",
    "severe mould",
)
MAJOR_OBSERVATION_TOKENS = (
    "foundation crack",
    "foundation failure",
    "major roof damage",
    "major unfinished demolition",
    "severe floor deformation",
    "structural damage",
    "major water damage",
)
MODERATE_OBSERVATION_TOKENS = (
    "water staining",
    "water stain",
    "water damage",
    "basement moisture",
    "localized roof damage",
    "siding damage",
    "floor deformation",
    "drainage issue",
    "threshold damage",
)
MINOR_OBSERVATION_TOKENS = (
    "dry grass",
    "dry and patchy grass",
    "patchy grass",
    "weathered fence",
    "peeling paint",
    "peeling",
    "light rust",
    "rust on",
    "surface wear",
    "surface aging",
    "overgrown",
    "staining on exterior",
    "staining",
    "weathered exterior",
    "discoloration",
)
COSMETIC_OBSERVATION_TOKENS = (
    "dated kitchen",
    "original kitchen",
    "old kitchen",
    "dated cabinet",
    "old cabinet",
    "dated bathroom",
    "original bathroom",
    "old flooring",
    "dated flooring",
    "dated finish",
    "personal items",
    "clutter",
)


def _observation_severity(
    value: str,
    canonical: str | None = None,
) -> tuple[EvidenceCategory, EvidenceScope]:
    """Classify evidence conservatively without treating arbitrary text as a defect."""

    text = str(value or "").strip().lower().replace("_", " ")
    if canonical in {"FIRE_DAMAGE", "COLLAPSE", "UNINHABITABLE"}:
        return "CRITICAL", "SYSTEMIC"
    if canonical in {"VISIBLE_MOLD", "FOUNDATION_CRACKING", "UNFINISHED_MAJOR_DEMOLITION"}:
        return "MAJOR", "SYSTEMIC"
    if canonical in {
        "WATER_STAINING",
        "BASEMENT_MOISTURE",
        "FLOOR_DEFORMATION",
        "SIDING_DAMAGE",
        "ROOF_DAMAGE",
    }:
        return "MODERATE", "LOCAL"
    if any(token in text for token in CRITICAL_OBSERVATION_TOKENS):
        return "CRITICAL", "SYSTEMIC"
    if any(token in text for token in MAJOR_OBSERVATION_TOKENS):
        return "MAJOR", "SYSTEMIC"
    if any(token in text for token in MODERATE_OBSERVATION_TOKENS):
        scope: EvidenceScope = (
            "SYSTEMIC"
            if any(token in text for token in ("widespread", "extensive", "throughout", "multiple"))
            else "LOCAL"
        )
        return "MODERATE", scope
    if any(token in text for token in MINOR_OBSERVATION_TOKENS):
        return "MINOR_MAINTENANCE", "LOCAL"
    if any(token in text for token in COSMETIC_OBSERVATION_TOKENS):
        return "COSMETIC", "LOCAL"
    return "UNKNOWN", "UNKNOWN"


def _project_group_for_observation(
    value: str,
    canonical: str | None,
    category: EvidenceCategory,
) -> str | None:
    text = str(value or "").strip().lower().replace("_", " ")
    if category in {"CRITICAL", "MAJOR"}:
        if canonical in {"FOUNDATION_CRACKING", "FLOOR_DEFORMATION"} or "foundation" in text or "floor deformation" in text or "structural" in text:
            return "FOUNDATION_STRUCTURE"
        if canonical == "UNFINISHED_MAJOR_DEMOLITION" or "unfinished" in text or "demolition" in text:
            return "BASEMENT_UNFINISHED"
        if canonical == "ROOF_DAMAGE" or "roof" in text:
            return "ROOF"
        return "WATER_DRAINAGE"
    if category == "MODERATE":
        if canonical in {"FOUNDATION_CRACKING", "FLOOR_DEFORMATION"} or "floor deformation" in text:
            return "FOUNDATION_STRUCTURE"
        if canonical == "SIDING_DAMAGE" or "siding" in text or "envelope" in text:
            return "ENVELOPE"
        if canonical == "ROOF_DAMAGE" or "roof" in text:
            return "ROOF"
        return "WATER_DRAINAGE"
    if category == "MINOR_MAINTENANCE":
        return "MINOR_EXTERIOR_MAINTENANCE"
    return None


def _cosmetic_project_group(value: str) -> str | None:
    text = str(value or "").strip().lower().replace("_", " ")
    if any(token in text for token in ("kitchen", "cabinet")):
        return "KITCHEN"
    if any(token in text for token in ("bath", "shower", "tub", "toilet")):
        return "BATHROOM"
    if any(token in text for token in ("floor", "flooring", "carpet")):
        return "FLOORING"
    if any(token in text for token in ("paint", "lighting", "closet", "finish", "clutter", "personal items")):
        return "PAINT_LIGHTING_CLOSET"
    return None


def _is_recent_update(value: str) -> bool:
    text = str(value or "").lower()
    return any(token in text for token in ("new ", "updated ", "replaced ", "renovated", "fully renovated", "recently renovated"))


def _system_from_update(value: str) -> str | None:
    text = str(value or "").lower()
    systems = (
        ("roof", "ROOF"),
        ("furnace", "FURNACE"),
        ("heating", "FURNACE"),
        ("electrical", "ELECTRICAL"),
        ("plumbing", "PLUMBING"),
        ("foundation", "FOUNDATION"),
    )
    if not _is_recent_update(text):
        return None
    for token, system in systems:
        if token in text:
            return system
    return None


def _confidence_for_cost(
    coverage_value: float | None,
    photo_confidence: PhotoConfidence,
    has_text_evidence: bool,
    has_conflict: bool,
) -> PhotoConfidence:
    if has_conflict:
        return "LOW" if coverage_value is None or coverage_value < 75 else "MEDIUM"
    if coverage_value is not None and coverage_value >= 75:
        return "HIGH"
    if coverage_value is not None and coverage_value >= 50:
        return "MEDIUM"
    if coverage_value is not None and coverage_value > 0 or has_text_evidence:
        return "LOW"
    if photo_confidence == "UNKNOWN":
        return "UNKNOWN"
    return "LOW"


def _unseen_systems(
    maintenance_signals: Sequence[str],
    coverage: Mapping[str, Any] | None,
) -> list[str]:
    default_systems = ["ROOF", "FURNACE", "ELECTRICAL", "PLUMBING", "FOUNDATION"]
    updated = {system for value in maintenance_signals if (system := _system_from_update(value))}
    covered_groups = set(coverage.get("covered_space_groups") or []) if coverage else set()
    if "exterior" in covered_groups:
        updated.add("ROOF") if any("roof" in str(value).lower() and "intact" in str(value).lower() for value in maintenance_signals) else None
    if "basement" in covered_groups:
        updated.add("FOUNDATION") if any("foundation" in str(value).lower() for value in maintenance_signals) else None
    return [system for system in default_systems if system not in updated]


def _unobserved_critical_areas(coverage: Mapping[str, Any] | None) -> list[str]:
    coverage = coverage or {}
    uncovered = set(str(value) for value in coverage.get("uncovered_space_groups") or [])
    if coverage.get("photo_evidence_coverage") is None and not coverage.get("covered_space_groups"):
        uncovered.update(REQUIRED_SPACE_GROUPS)
    labels = {
        "exterior": "EXTERIOR",
        "living": "LIVING",
        "basement": "BASEMENT_MECHANICAL",
        "site": "SITE",
    }
    return [labels[group] for group in REQUIRED_SPACE_GROUPS if group in uncovered]


def _sum_cost_items(items: Sequence[Mapping[str, Any]]) -> tuple[float, float, float]:
    return tuple(
        round(sum(float(item.get(key) or 0) for item in items), 2)
        for key in ("cost_low", "cost_base", "cost_high")
    )  # type: ignore[return-value]


_EVIDENCE_SEVERITY_RANK = {
    "UNKNOWN": 0,
    "COSMETIC": 1,
    "MINOR_MAINTENANCE": 2,
    "MODERATE": 3,
    "MAJOR": 4,
    "CRITICAL": 5,
}


def _cost_item(
    *,
    project_group: str,
    category: EvidenceCategory,
    scope: EvidenceScope,
    cost_type: CostType,
    costs: tuple[float, float, float],
    source: str,
    evidence: Sequence[str],
    confidence: PhotoConfidence,
) -> dict[str, Any]:
    return OnlineCostItem(
        project_group=project_group,
        category=category,
        scope=scope,
        cost_type=cost_type,
        cost_low=costs[0],
        cost_base=costs[1],
        cost_high=costs[2],
        source=source,
        evidence=list(dict.fromkeys(str(item) for item in evidence if str(item).strip())),
        confidence=confidence,
    ).model_dump(mode="json")


def _build_observed_cost_items(
    *,
    visual_defects: Sequence[str],
    text_defects: Sequence[str],
    visual_cosmetic: Sequence[str],
    text_cosmetic: Sequence[str],
    estimate_confidence: PhotoConfidence,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Build deduplicated survival, cosmetic, and minor-maintenance items."""

    survival_groups: dict[str, dict[str, Any]] = {}
    minor_evidence: list[str] = []

    records: list[tuple[str, str | None, str]] = []
    for value in _normalise_signal_list(visual_defects):
        canonical = _canonical_defects([value])
        records.append((value, canonical[0] if canonical else None, "PHOTO"))
    for value in _normalise_signal_list(text_defects):
        records.append((value, value, "DESCRIPTION"))

    for value, canonical, source in records:
        category, scope = _observation_severity(value, canonical)
        if category == "MINOR_MAINTENANCE":
            minor_evidence.append(value)
            continue
        if category not in {"CRITICAL", "MAJOR", "MODERATE"}:
            continue
        if category == "CRITICAL":
            group = "SEVERE_CONDITION"
        else:
            group = _project_group_for_observation(value, canonical, category)
        if not group:
            continue

        current = survival_groups.get(group)
        if current is None or _EVIDENCE_SEVERITY_RANK[category] > _EVIDENCE_SEVERITY_RANK[current["category"]]:
            survival_groups[group] = {
                "category": category,
                "scope": scope,
                "evidence": [value],
                "sources": {source},
            }
        else:
            current["evidence"].append(value)
            current["sources"].add(source)
            if scope == "SYSTEMIC":
                current["scope"] = "SYSTEMIC"

    survival_items: list[dict[str, Any]] = []
    for group, item in survival_groups.items():
        category = item["category"]
        scope = item["scope"]
        if category == "CRITICAL":
            costs = ONLINE_SEVERE_PRIOR
        else:
            costs = ONLINE_SURVIVAL_PRIORS[group][scope]
        survival_items.append(
            _cost_item(
                project_group=group,
                category=category,
                scope=scope,
                cost_type="SURVIVAL",
                costs=costs,
                source="+".join(sorted(item["sources"])),
                evidence=item["evidence"],
                confidence=estimate_confidence,
            )
        )

    cosmetic_groups: dict[str, dict[str, Any]] = {}
    cosmetic_records = [
        (value, "PHOTO") for value in _normalise_signal_list(visual_cosmetic)
    ] + [
        (value, "DESCRIPTION") for value in _normalise_signal_list(text_cosmetic)
    ]
    for value, source in cosmetic_records:
        category, _scope = _observation_severity(value)
        if category != "COSMETIC" or _is_recent_update(value):
            continue
        group = _cosmetic_project_group(value)
        if group is None and "original condition" in str(value).lower():
            group = "PAINT_LIGHTING_CLOSET"
        if group is None:
            continue
        current = cosmetic_groups.setdefault(group, {"evidence": [], "sources": set()})
        current["evidence"].append(value)
        current["sources"].add(source)

    cosmetic_items: list[dict[str, Any]] = []
    for group, item in cosmetic_groups.items():
        cosmetic_items.append(
            _cost_item(
                project_group=group,
                category="COSMETIC",
                scope="LOCAL",
                cost_type="COSMETIC",
                costs=ONLINE_COSMETIC_PRIORS[group],
                source="+".join(sorted(item["sources"])),
                evidence=item["evidence"],
                confidence=estimate_confidence,
            )
        )

    minor_items: list[dict[str, Any]] = []
    if minor_evidence:
        minor_items.append(
            _cost_item(
                project_group="MINOR_EXTERIOR_MAINTENANCE",
                category="MINOR_MAINTENANCE",
                scope="LOCAL",
                cost_type="MINOR_MAINTENANCE",
                costs=MINOR_MAINTENANCE_PRIOR,
                source="PHOTO",
                evidence=minor_evidence,
                confidence=estimate_confidence,
            )
        )
    return survival_items, cosmetic_items, minor_items


def _description_signals(description: str) -> tuple[list[str], list[str], list[str]]:
    """Extract only explicit text signals; absence is not evidence of absence."""

    maintenance_terms = (
        "clean",
        "well maintained",
        "well-maintained",
        "meticulously maintained",
        "updated roof",
        "new roof",
        "updated furnace",
        "new furnace",
        "updated windows",
        "new windows",
        "updated plumbing",
        "updated electrical",
        "new hot water tank",
        "new hot-water tank",
        "fire sprinkler",
        "fully renovated",
        "recently renovated",
    )
    cosmetic_terms = (
        "original kitchen",
        "dated kitchen",
        "old kitchen",
        "old cabinets",
        "dated cabinets",
        "original bathroom",
        "dated bathroom",
        "old flooring",
        "dated flooring",
        "needs updating",
        "original condition",
    )
    defect_terms = {
        "water staining": "WATER_STAINING",
        "water stain": "WATER_STAINING",
        "water damage": "WATER_STAINING",
        "mold": "VISIBLE_MOLD",
        "mould": "VISIBLE_MOLD",
        "foundation crack": "FOUNDATION_CRACKING",
        "sloping floor": "FLOOR_DEFORMATION",
        "uneven floor": "FLOOR_DEFORMATION",
        "siding damage": "SIDING_DAMAGE",
        "roof damage": "ROOF_DAMAGE",
        "basement moisture": "BASEMENT_MOISTURE",
        "fire damage": "FIRE_DAMAGE",
        "collapsed": "COLLAPSE",
        "uninhabitable": "UNINHABITABLE",
        "tear down": "UNINHABITABLE",
    }
    maintenance = [term for term in maintenance_terms if term in description]
    cosmetic = [term for term in cosmetic_terms if term in description]
    defects: list[str] = []
    for term, label in defect_terms.items():
        if term in description and label not in defects:
            defects.append(label)
    return maintenance, cosmetic, defects


def _kitchen_count(description: str, card: Mapping[str, Any], evidence: Mapping[str, Any]) -> int | None:
    explicit = _first_value(evidence, ("kitchen_count", "number_of_kitchens"))
    if explicit is None:
        explicit = _first_value(card, ("kitchen_count", "number_of_kitchens"))
    number = _number(explicit)
    if number is not None:
        return int(number)
    if re.search(r"\b(two|2|multiple|second|three|3) kitchens?\b", description):
        if re.search(r"\b(three|3) kitchens?\b", description):
            return 3
        return 2
    return 1 if "kitchen" in description else None


def _coverage_values(coverage: Mapping[str, Any] | None) -> tuple[float | None, PhotoConfidence]:
    coverage = coverage or {}
    numeric = _number(coverage.get("photo_evidence_coverage"))
    raw_confidence = str(coverage.get("photo_confidence") or "").upper()
    if raw_confidence in {"HIGH", "MEDIUM", "LOW", "UNKNOWN"}:
        confidence: PhotoConfidence = raw_confidence  # type: ignore[assignment]
    elif numeric is None:
        confidence = "UNKNOWN"
    elif numeric >= 75:
        confidence = "HIGH"
    elif numeric >= 50:
        confidence = "MEDIUM"
    else:
        confidence = "LOW"
    return numeric, confidence


def _score_for_band(band: str, scores: Mapping[str, float]) -> float | None:
    return scores.get(band)


def build_online_holding_assessment(
    listing_description: str | None,
    card: Mapping[str, Any] | Any,
    photo_evidence: Mapping[str, Any] | None,
    coverage: Mapping[str, Any] | None,
) -> OnlineHoldingAssessment:
    """Aggregate observable online evidence into three independent ledgers.

    A clean but dated kitchen affects only cosmetic modernisation.  Explicit
    visible defects affect maintenance and capex.  Missing photo coverage
    changes confidence, not the underlying condition band.
    """

    evidence = photo_evidence or {}
    description = _as_text(listing_description)
    text_maintenance, text_cosmetic, text_defects = _description_signals(description)

    maintenance_signals = _normalise_signal_list(evidence.get("maintenance_signals"))
    cosmetic_signals = _normalise_signal_list(evidence.get("cosmetic_signals"))
    visible_defects = _canonical_defects(evidence.get("visible_defects"))
    for defect in text_defects:
        if defect not in visible_defects:
            visible_defects.append(defect)
    for signal in text_maintenance:
        if signal not in maintenance_signals:
            maintenance_signals.append(signal)
    for signal in text_cosmetic:
        if signal not in cosmetic_signals:
            cosmetic_signals.append(signal)

    coverage_value, photo_confidence = _coverage_values(coverage)
    flags: list[str] = []
    if photo_confidence == "LOW":
        flags.append("PHOTO_COVERAGE_LOW")
    if photo_confidence == "UNKNOWN":
        flags.append("PHOTO_SOURCE_OR_CLASSIFICATION_UNKNOWN")

    severe_defects = {"FIRE_DAMAGE", "COLLAPSE", "UNINHABITABLE"}
    major_defects = {"VISIBLE_MOLD", "FOUNDATION_CRACKING", "UNFINISHED_MAJOR_DEMOLITION"}
    observed_categories = [
        _observation_severity(item, item)[0] for item in visible_defects
    ] + [
        _observation_severity(item, item)[0] for item in text_defects
    ]
    if "CRITICAL" in observed_categories:
        maintenance_condition: MaintenanceCondition = "SEVERE"
    elif "MAJOR" in observed_categories:
        maintenance_condition = "SEVERE"
    elif "MODERATE" in observed_categories:
        maintenance_condition = "ELEVATED_RISK"
    elif maintenance_signals:
        maintenance_condition = "GOOD"
    elif photo_confidence != "UNKNOWN" and (coverage_value or 0) >= 75:
        # Broad, clean, defect-free photo evidence supports an acceptable
        # observation, but not a claim that hidden systems are sound.
        maintenance_condition = "ACCEPTABLE"
    else:
        maintenance_condition = "UNKNOWN"

    cosmetic_blob = " ".join([description, " ".join(cosmetic_signals)]).lower()
    recent_terms = (
        "fully renovated",
        "recently renovated",
        "renovated",
        "new kitchen",
        "new bathroom",
        "new cabinets",
        "modernized",
    )
    dated_terms = (
        "original kitchen",
        "dated kitchen",
        "old kitchen",
        "old cabinets",
        "dated cabinets",
        "original bathroom",
        "dated bathroom",
        "old flooring",
        "dated flooring",
        "needs updating",
        "original condition",
    )
    if any(term in cosmetic_blob for term in recent_terms):
        cosmetic_modernization: CosmeticModernization = "RECENT"
    elif any(term in cosmetic_blob for term in dated_terms):
        cosmetic_modernization = "DATED"
    elif cosmetic_signals or any("updated" in signal or "new" in signal for signal in maintenance_signals):
        cosmetic_modernization = "MODERATE"
    else:
        cosmetic_modernization = "UNKNOWN"

    bedrooms = _number(_first_value(card, ("bedrooms", "beds", "bedroom_count")))
    bathrooms = _number(_first_value(card, ("bathrooms", "baths", "bathroom_count")))
    area = _number(
        _first_value(
            card,
            ("finished_sqft", "living_sqft", "living_area", "house_sqft", "building_area_sqft"),
        )
    )
    kitchen_count = _kitchen_count(description, card if isinstance(card, Mapping) else {}, evidence)
    if kitchen_count is not None and kitchen_count > 1:
        flags.append("MULTI_KITCHEN_LAYOUT_REVIEW")

    if bedrooms is None and bathrooms is None and area is None:
        usability: HoldingUsability = "UNKNOWN"
        usability_evidence: list[str] = []
    elif (bedrooms or 0) >= 3 and (bathrooms or 0) >= 2 and (area or 0) >= 1500:
        usability = "GOOD"
        usability_evidence = ["3_PLUS_BEDS_2_PLUS_BATHS_1500_PLUS_SQFT"]
    elif (bedrooms or 0) >= 3 and (bathrooms or 0) >= 2:
        usability = "ACCEPTABLE"
        usability_evidence = ["3_PLUS_BEDS_2_PLUS_BATHS_BUT_AREA_NOT_CONFIRMED"]
    elif (bedrooms or 0) >= 2 and (bathrooms or 0) >= 2 and (area or 0) >= 1200:
        usability = "ACCEPTABLE"
        usability_evidence = ["MINIMUM_TWO_BED_TWO_BATH_PROFILE"]
    elif (bedrooms or 0) >= 2 and (bathrooms or 0) >= 1:
        usability = "ACCEPTABLE"
        usability_evidence = ["BASIC_TWO_BED_ONE_BATH_PROFILE"]
    else:
        usability = "POOR"
        usability_evidence = ["LIMITED_FAMILY_LAYOUT_SIGNAL"]
    if kitchen_count is not None and kitchen_count > 1:
        usability_evidence.append("MULTI_KITCHEN_IS_LAYOUT_FLAG_NOT_ROOM_BONUS")
        if usability == "VERY_GOOD":
            usability = "GOOD"

    if not visible_defects and (maintenance_signals or cosmetic_signals):
        if "MAINTENANCE_EVIDENCE_ONLY" not in flags:
            flags.append("MAINTENANCE_EVIDENCE_ONLY")
    if photo_confidence in {"LOW", "UNKNOWN"}:
        flags.append("UNSEEN_SYSTEMS_NOT_ASSESSED")

    has_text_evidence = bool(text_maintenance or text_cosmetic or text_defects)
    has_conflict = bool(evidence.get("conflict") or evidence.get("conflicts"))
    estimate_confidence = _confidence_for_cost(
        coverage_value,
        photo_confidence,
        has_text_evidence,
        has_conflict,
    )
    visual_defect_signals = _normalise_signal_list(evidence.get("visible_defects"))
    visual_cosmetic_signals = _normalise_signal_list(evidence.get("cosmetic_signals"))
    observed_survival_items, observed_cosmetic_items, minor_maintenance_items = _build_observed_cost_items(
        visual_defects=visual_defect_signals,
        text_defects=text_defects,
        visual_cosmetic=visual_cosmetic_signals,
        text_cosmetic=text_cosmetic,
        estimate_confidence=estimate_confidence,
    )
    survival_low, survival_base, survival_high = _sum_cost_items(observed_survival_items)
    cosmetic_low, cosmetic_base, cosmetic_high = _sum_cost_items(observed_cosmetic_items)

    recent_system_updates = {
        system for value in maintenance_signals if (system := _system_from_update(value))
    }
    maintenance_blob = " ".join(maintenance_signals).lower()
    recent_complete_update = any(
        term in maintenance_blob
        for term in ("fully renovated", "recently renovated")
    )
    recent_named_updates = len(recent_system_updates) >= 2
    if "CRITICAL" in observed_categories:
        capex: OnlineCapexBand = "E"
    elif "MAJOR" in observed_categories:
        capex = "D"
    elif "MODERATE" in observed_categories:
        capex = "C"
    elif recent_complete_update or (recent_named_updates and not observed_cosmetic_items):
        capex = "A"
    elif observed_survival_items or observed_cosmetic_items or minor_maintenance_items or maintenance_signals or cosmetic_signals:
        capex = "B"
    elif photo_confidence != "UNKNOWN" and coverage_value is not None and coverage_value > 0:
        capex = "B"
    else:
        capex = "UNKNOWN"

    unseen_systems = _unseen_systems(maintenance_signals, coverage)
    unobserved_critical_areas = _unobserved_critical_areas(coverage)

    evidence_lines = []
    evidence_lines.extend(f"maintenance:{item}" for item in maintenance_signals)
    evidence_lines.extend(f"cosmetic:{item}" for item in cosmetic_signals)
    evidence_lines.extend(f"visible_defect:{item}" for item in visible_defects)
    evidence_lines.extend(f"layout:{item}" for item in usability_evidence)
    evidence_lines.extend(
        f"survival_cost:{item['project_group']}:{item['cost_base']}"
        for item in observed_survival_items
    )
    evidence_lines.extend(
        f"cosmetic_cost:{item['project_group']}:{item['cost_base']}"
        for item in observed_cosmetic_items
    )

    return OnlineHoldingAssessment(
        visible_maintenance_condition=maintenance_condition,
        visible_maintenance_score=_score_for_band(
            maintenance_condition,
            {"GOOD": 80, "ACCEPTABLE": 60, "ELEVATED_RISK": 35, "SEVERE": 10},
        ),
        cosmetic_modernization=cosmetic_modernization,
        cosmetic_modernization_score=_score_for_band(
            cosmetic_modernization,
            {"RECENT": 90, "MODERATE": 65, "DATED": 35},
        ),
        holding_usability=usability,
        holding_usability_score=_score_for_band(
            usability,
            {"VERY_GOOD": 90, "GOOD": 80, "ACCEPTABLE": 60, "POOR": 25},
        ),
        online_capex_band=capex,
        photo_evidence_coverage=coverage_value,
        photo_confidence=photo_confidence,
        kitchen_count=kitchen_count,
        visible_defects=visible_defects,
        maintenance_evidence=maintenance_signals,
        cosmetic_evidence=cosmetic_signals,
        usability_evidence=usability_evidence,
        flags=list(dict.fromkeys(flags)),
        evidence=evidence_lines,
        survival_capex_low=survival_low,
        survival_capex_base=survival_base,
        survival_capex_high=survival_high,
        cosmetic_reno_low=cosmetic_low,
        cosmetic_reno_base=cosmetic_base,
        cosmetic_reno_high=cosmetic_high,
        observed_survival_items=observed_survival_items,
        observed_cosmetic_items=observed_cosmetic_items,
        minor_maintenance_items=minor_maintenance_items,
        unseen_systems=unseen_systems,
        unobserved_critical_areas=unobserved_critical_areas,
        estimate_confidence=estimate_confidence,
        estimate_basis=ONLINE_COST_PRIOR_VERSION,
    )
