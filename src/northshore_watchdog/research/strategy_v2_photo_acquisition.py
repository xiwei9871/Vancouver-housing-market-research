"""Auditable image-byte acquisition for Strategy V2 Phase 2A.

This module only fetches image URLs already present in a saved photo
manifest.  It does not discover new listing pages, bypass access controls, or
silently replace a failed download with a guessed image.  Every source is
kept in the manifest, while only the first copy of duplicate content is
written to disk.
"""

from __future__ import annotations

import hashlib
import mimetypes
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from .strategy_v2_photo_intelligence import (
    PhotoManifest,
    _image_metadata,
    build_photo_manifest,
)


DEFAULT_USER_AGENT = "northshore-property-watchdog/strategy-v2-photo-acquisition"
DEFAULT_MAX_BYTES = 25 * 1024 * 1024
DEFAULT_PERCEPTUAL_DISTANCE = 5


@dataclass(frozen=True)
class FetchedPhoto:
    """Fetched bytes and the server's optional content type."""

    content: bytes
    content_type: str | None = None
    status_code: int = 200


class PhotoFetchError(RuntimeError):
    """A recoverable image acquisition error with an auditable status."""

    def __init__(self, status: str, *, status_code: int | None = None) -> None:
        super().__init__(status)
        self.status = status
        self.status_code = status_code


PhotoFetcher = Callable[[str], FetchedPhoto | bytes]


def fetch_photo_bytes(
    source_url: str,
    *,
    timeout: float = 20.0,
    user_agent: str = DEFAULT_USER_AGENT,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> FetchedPhoto:
    """Fetch one already-known image URL using a normal HTTP request."""

    parsed = urlparse(source_url)
    if parsed.scheme.lower() not in {"http", "https"}:
        raise PhotoFetchError("UNSUPPORTED_SCHEME")
    request = Request(
        source_url,
        headers={
            "User-Agent": user_agent,
            "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
        },
    )
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 - URL was already in a saved manifest
            status_code = int(getattr(response, "status", 200) or 200)
            content = response.read(max_bytes + 1)
            if len(content) > max_bytes:
                raise PhotoFetchError("TOO_LARGE")
            if status_code >= 400:
                raise PhotoFetchError(f"HTTP_{status_code}", status_code=status_code)
            return FetchedPhoto(
                content=content,
                content_type=response.headers.get("Content-Type"),
                status_code=status_code,
            )
    except HTTPError as exc:
        raise PhotoFetchError(f"HTTP_{exc.code}", status_code=exc.code) from exc
    except URLError as exc:
        raise PhotoFetchError("NETWORK_ERROR") from exc
    except TimeoutError as exc:
        raise PhotoFetchError("TIMEOUT") from exc


def _magic_type(content: bytes) -> str | None:
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if content.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if content.startswith(b"RIFF") and content[8:12] == b"WEBP":
        return "image/webp"
    if content.startswith(b"BM"):
        return "image/bmp"
    if len(content) >= 4 and content[:4] in {b"II*\x00", b"MM\x00*"}:
        return "image/tiff"
    return None


def _header_dimensions(content: bytes) -> tuple[int | None, int | None]:
    """Read common image dimensions without requiring a decoder."""

    kind = _magic_type(content)
    if kind == "image/png" and len(content) >= 24:
        return int.from_bytes(content[16:20], "big"), int.from_bytes(content[20:24], "big")
    if kind == "image/gif" and len(content) >= 10:
        return int.from_bytes(content[6:8], "little"), int.from_bytes(content[8:10], "little")
    if kind == "image/jpeg":
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
            segment_length = int.from_bytes(content[offset:offset + 2], "big")
            if segment_length < 2 or offset + segment_length > len(content):
                break
            if marker in set(range(0xC0, 0xC4)) | set(range(0xC5, 0xC8)) | set(range(0xC9, 0xCC)) | set(range(0xCD, 0xD0)):
                if offset + 7 <= len(content):
                    return int.from_bytes(content[offset + 5:offset + 7], "big"), int.from_bytes(content[offset + 3:offset + 5], "big")
            offset += segment_length
    return None, None


def _validate_image(content: bytes, content_type: str | None) -> str:
    magic = _magic_type(content)
    if magic is None:
        raise PhotoFetchError("INVALID_CONTENT")
    # If Pillow is present, verify the complete image.  The magic/header
    # fallback keeps acquisition useful in minimal environments, while still
    # rejecting obvious HTML/JSON responses from a failed CDN request.
    try:
        import io
        from PIL import Image  # type: ignore[import-not-found]

        with Image.open(io.BytesIO(content)) as image:
            image.verify()
    except ImportError:
        pass
    except (OSError, ValueError) as exc:
        raise PhotoFetchError("INVALID_CONTENT") from exc
    return magic or str(content_type or "application/octet-stream").split(";", 1)[0].lower()


def _extension(content_type: str | None, content: bytes) -> str:
    kind = _magic_type(content) or str(content_type or "").split(";", 1)[0].lower()
    by_kind = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/gif": ".gif",
        "image/webp": ".webp",
        "image/bmp": ".bmp",
        "image/tiff": ".tiff",
    }
    if kind in by_kind:
        return by_kind[kind]
    return mimetypes.guess_extension(kind) or ".img"


def _as_datetime(value: datetime | str | None) -> datetime:
    if isinstance(value, datetime):
        result = value
    elif value:
        try:
            result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            result = datetime.now(timezone.utc)
    else:
        result = datetime.now(timezone.utc)
    if result.tzinfo is None:
        return result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def _distance(left: str, right: str) -> int | None:
    try:
        if len(left) != len(right):
            return None
        return (int(left, 16) ^ int(right, 16)).bit_count()
    except (TypeError, ValueError):
        return None


def _record_base(image: Any, *, property_key: str, retrieved_at: datetime) -> dict[str, Any]:
    record = image.model_dump(mode="python") if hasattr(image, "model_dump") else dict(image)
    record["property_key"] = property_key
    record["retrieved_at"] = retrieved_at
    record.pop("content", None)
    record.pop("content_bytes", None)
    record.pop("image_bytes", None)
    record.pop("bytes", None)
    return record


def acquire_photo_manifest(
    manifest: PhotoManifest,
    output_dir: str | Path,
    *,
    fetcher: PhotoFetcher | None = None,
    retrieved_at: datetime | str | None = None,
    timeout: float = 20.0,
    user_agent: str = DEFAULT_USER_AGENT,
    max_bytes: int = DEFAULT_MAX_BYTES,
    perceptual_distance: int = DEFAULT_PERCEPTUAL_DISTANCE,
) -> tuple[PhotoManifest, dict[str, Any]]:
    """Download and audit the known images in one manifest.

    The returned manifest preserves gallery order.  Duplicate URLs/content
    point to the canonical local asset and are never written twice.
    """

    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    observed = _as_datetime(retrieved_at)
    get_photo = fetcher or (
        lambda url: fetch_photo_bytes(
            url,
            timeout=timeout,
            user_agent=user_agent,
            max_bytes=max_bytes,
        )
    )
    records: list[dict[str, Any]] = []
    successful_by_url: dict[str, tuple[int, str, str, int, int, str | None]] = {}
    successful_by_sha: dict[str, tuple[int, str]] = {}
    successful_by_phash: list[tuple[str, int, str]] = []
    unique_content: set[str] = set()
    counters = {
        "total_count": len(manifest.images),
        "downloaded_count": 0,
        "local_cache_count": 0,
        "failed_count": 0,
        "invalid_content_count": 0,
        "duplicate_url_count": 0,
        "duplicate_sha256_count": 0,
        "duplicate_perceptual_count": 0,
    }

    for image in sorted(manifest.images, key=lambda item: item.original_order):
        record = _record_base(image, property_key=manifest.property_key, retrieved_at=observed)
        source_url = str(image.source_url or "").strip() or None
        original_order = int(image.original_order)
        record["source_url"] = source_url
        record["original_order"] = original_order

        cached = successful_by_url.get(source_url) if source_url else None
        if cached is not None:
            canonical_order, local_path, digest, width, height, phash = cached
            record.update(
                {
                    "sha256": digest,
                    "hash_basis": "CONTENT",
                    "local_path": local_path,
                    "width": width,
                    "height": height,
                    "perceptual_hash": phash,
                    "perceptual_hash_algorithm": "dhash64-v1" if phash else None,
                    "source_status": "DUPLICATE_URL",
                    "duplicate_of_order": canonical_order,
                    "duplicate_reason": "URL",
                }
            )
            counters["duplicate_url_count"] += 1
            records.append(record)
            continue

        content: bytes | None = None
        content_type: str | None = image.content_type
        local_path_value = str(image.local_path or "").strip() or None
        if local_path_value:
            try:
                content = Path(local_path_value).read_bytes()
                counters["local_cache_count"] += 1
                source_status = "LOCAL_CACHE"
            except OSError:
                content = None
                source_status = "LOCAL_CACHE_MISS"
        else:
            source_status = "NOT_ACQUIRED"

        if content is None and source_url:
            try:
                fetched = get_photo(source_url)
                fetched_photo = fetched if isinstance(fetched, FetchedPhoto) else FetchedPhoto(bytes(fetched))
                content = fetched_photo.content
                content_type = fetched_photo.content_type or content_type
                source_status = "DOWNLOADED"
                counters["downloaded_count"] += 1
            except PhotoFetchError as exc:
                source_status = exc.status
                counters["failed_count"] += 1
                if exc.status == "INVALID_CONTENT":
                    counters["invalid_content_count"] += 1
            except (OSError, TypeError, ValueError) as exc:
                source_status = f"FETCH_FAILED_{type(exc).__name__.upper()}"
                counters["failed_count"] += 1

        if content is None:
            record.update(
                {
                    "source_status": source_status if source_url or local_path_value else "NO_SOURCE_URL",
                    "sha256": image.sha256,
                    "hash_basis": image.hash_basis,
                    "local_path": local_path_value,
                }
            )
            records.append(record)
            continue

        try:
            canonical_type = _validate_image(content, content_type)
        except PhotoFetchError as exc:
            record.update(
                {
                    "source_status": exc.status,
                    "sha256": None,
                    "hash_basis": "NONE",
                    "local_path": None,
                    "content_type": content_type,
                    "file_size_bytes": len(content),
                }
            )
            counters["failed_count"] += 1
            if exc.status == "INVALID_CONTENT":
                counters["invalid_content_count"] += 1
            records.append(record)
            continue

        digest = hashlib.sha256(content).hexdigest()
        width, height, phash = _image_metadata(content)
        if width is None or height is None:
            header_width, header_height = _header_dimensions(content)
            width = width if width is not None else header_width
            height = height if height is not None else header_height

        duplicate_order: int | None = None
        duplicate_reason: str | None = None
        if digest in successful_by_sha:
            duplicate_order = successful_by_sha[digest][0]
            duplicate_reason = "SHA256"
            counters["duplicate_sha256_count"] += 1
        elif phash:
            for previous_hash, previous_order, _previous_path in successful_by_phash:
                distance = _distance(phash, previous_hash)
                if distance is not None and distance <= perceptual_distance:
                    duplicate_order = previous_order
                    duplicate_reason = "PERCEPTUAL"
                    counters["duplicate_perceptual_count"] += 1
                    break

        if duplicate_order is not None:
            canonical_record = next(item for item in records if int(item["original_order"]) == duplicate_order)
            canonical_path = str(canonical_record.get("local_path") or "") or None
            record.update(
                {
                    "sha256": digest,
                    "hash_basis": "CONTENT",
                    "perceptual_hash": phash,
                    "perceptual_hash_algorithm": "dhash64-v1" if phash else None,
                    "width": width,
                    "height": height,
                    "local_path": canonical_path,
                    "source_status": "DUPLICATE_PERCEPTUAL" if duplicate_reason == "PERCEPTUAL" else "DUPLICATE_SHA256",
                    "duplicate_of_order": duplicate_order,
                    "duplicate_reason": duplicate_reason,
                    "content_type": canonical_type,
                    "file_size_bytes": len(content),
                }
            )
            records.append(record)
            continue

        local_path = output / f"photo-{original_order:04d}-{digest[:16]}{_extension(canonical_type, content)}"
        if not local_path.exists():
            local_path.write_bytes(content)
        path_string = str(local_path)
        successful_by_sha[digest] = (original_order, path_string)
        if phash:
            successful_by_phash.append((phash, original_order, path_string))
        if source_url:
            successful_by_url[source_url] = (original_order, path_string, digest, width or 0, height or 0, phash)
        unique_content.add(digest)
        record.update(
            {
                "sha256": digest,
                "hash_basis": "CONTENT",
                "perceptual_hash": phash,
                "perceptual_hash_algorithm": "dhash64-v1" if phash else None,
                "width": width,
                "height": height,
                "local_path": path_string,
                "source_status": source_status,
                "content_type": canonical_type,
                "file_size_bytes": len(content),
            }
        )
        records.append(record)

    enriched = build_photo_manifest(
        property_key=manifest.property_key,
        photos=records,
        retrieved_at=observed,
    )
    if counters["failed_count"] == 0:
        acquisition_status = "COMPLETE"
    elif counters["downloaded_count"] or counters["local_cache_count"]:
        acquisition_status = "PARTIAL"
    elif counters["total_count"] == 0:
        acquisition_status = "NO_IMAGES"
    else:
        acquisition_status = "FAILED"
    counters["unique_content_count"] = len(unique_content)
    counters["status"] = acquisition_status
    enriched = enriched.model_copy(
        update={
            "schema_version": "strategy-v2-phase-2a-photo-manifest-v2",
            "acquisition_status": acquisition_status,
            "acquisition_summary": counters,
        }
    )
    return enriched, counters


def acquire_manifest_file(
    manifest_path: str | Path,
    output_dir: str | Path,
    **kwargs: Any,
) -> tuple[PhotoManifest, dict[str, Any]]:
    """Convenience wrapper for a persisted manifest JSON file."""

    import json

    payload = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    manifest = PhotoManifest.model_validate(payload)
    return acquire_photo_manifest(manifest, output_dir, **kwargs)
