from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Literal, Mapping

from pydantic import BaseModel

from .models import CrawlArtifact
from .policies import canonical_url, page_fingerprint


RequestKind = Literal["ROBOTS", "SEARCH", "DETAIL"]


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _header(headers: Mapping[str, Any], name: str) -> str:
    wanted = name.lower()
    for key, value in headers.items():
        key_text = key.decode("latin-1") if isinstance(key, bytes) else str(key)
        if key_text.lower() != wanted:
            continue
        if isinstance(value, (list, tuple)):
            value = value[0] if value else ""
        if isinstance(value, bytes):
            return value.decode("latin-1")
        return str(value)
    return ""


def _content_type(headers: Mapping[str, Any], explicit: str | None) -> str | None:
    value = explicit or _header(headers, "content-type")
    value = value.split(";", 1)[0].strip().lower()
    return value or None


def _suffix(content_type: str | None) -> str:
    value = (content_type or "").lower()
    if "json" in value:
        return ".json"
    if "html" in value:
        return ".html"
    if "xml" in value:
        return ".xml"
    if value.startswith("text/") or "text" in value:
        return ".txt"
    if "png" in value:
        return ".png"
    return ".bin"


def _body_bytes(body: bytes | bytearray | memoryview | None) -> bytes:
    if body is None:
        return b""
    if isinstance(body, bytes):
        return body
    if isinstance(body, (bytearray, memoryview)):
        return bytes(body)
    raise TypeError("body must be bytes-like")


def _rows(rows: Iterable[Mapping[str, Any] | BaseModel] | None) -> list[dict[str, Any]]:
    if rows is None:
        return []
    result: list[dict[str, Any]] = []
    for row in rows:
        if isinstance(row, BaseModel):
            result.append(row.model_dump(mode="python"))
        elif isinstance(row, Mapping):
            result.append(dict(row))
        else:
            raise TypeError(f"row must be a mapping, got {type(row).__name__}")
    return result


class ArtifactWriter:
    """Persist artifacts in the caller-provided directory for one crawl run.

    ``root_dir`` is already the current run directory.  The caller (the
    future runner) is responsible for choosing a distinct directory for each
    source/run; this writer never adds a source or run subdirectory and never
    truncates an existing manifest.
    """

    def __init__(
        self,
        root_dir: str | Path,
        *,
        source_name: str,
        run_id: str,
        source_registry_id: str | None = None,
    ) -> None:
        self.root_dir = Path(root_dir)
        self.source_name = source_name
        self.run_id = run_id
        self.source_registry_id = source_registry_id or f"source:{source_name}"
        self.manifest_path = self.root_dir / "manifest.jsonl"
        self._raw_search_dir = self.root_dir / "raw" / "search"
        self._raw_detail_dir = self.root_dir / "raw" / "detail"
        self._screenshot_dir = self.root_dir / "screenshots" / "entry-and-errors"
        for directory in (self._raw_search_dir, self._raw_detail_dir, self._screenshot_dir):
            directory.mkdir(parents=True, exist_ok=True)
        self._request_index = self._existing_manifest_rows()

    def _existing_manifest_rows(self) -> int:
        if not self.manifest_path.exists():
            return 0
        with self.manifest_path.open("rb") as manifest:
            return sum(1 for line in manifest if line.strip())

    def _raw_directory(self, request_kind: str) -> Path:
        if request_kind == "DETAIL":
            return self._raw_detail_dir
        if request_kind in {"SEARCH", "ROBOTS"}:
            return self._raw_search_dir
        raise ValueError(f"unsupported crawl request kind: {request_kind}")

    def _append_manifest(self, artifact: CrawlArtifact) -> None:
        line = (artifact.model_dump_json() + "\n").encode("utf-8")
        with self.manifest_path.open("a+b") as manifest:
            if manifest.tell():
                manifest.seek(-1, 2)
                if manifest.read(1) != b"\n":
                    manifest.write(b"\n")
                manifest.seek(0, 2)
            manifest.write(line)
            manifest.flush()

    @staticmethod
    def _write_exclusive(path: Path, payload: bytes) -> None:
        with path.open("xb") as output:
            output.write(payload)
            output.flush()

    @staticmethod
    def _remove_created(paths: Iterable[Path]) -> None:
        for path in paths:
            try:
                path.unlink()
            except FileNotFoundError:
                pass

    def write(
        self,
        *,
        request_kind: RequestKind | str,
        requested_url: str,
        final_url: str | None = None,
        entrypoint_url: str | None = None,
        detail_url: str | None = None,
        body: bytes | bytearray | memoryview | None = b"",
        headers: Mapping[str, Any] | None = None,
        status_code: int | None = None,
        status: int | None = None,
        rows: Iterable[Mapping[str, Any] | BaseModel] | None = None,
        page_number: int | None = None,
        capture_screenshot: bool = False,
        screenshot_bytes: bytes | bytearray | memoryview | None = None,
        screenshot: bytes | bytearray | memoryview | None = None,
        retrieved_at: datetime | None = None,
        source_registry_id: str | None = None,
        content_type: str | None = None,
        parser: str | None = None,
        parse_status: str = "NOT_RUN",
        next_url: str | None = None,
        card_count: int | None = None,
        extracted_row_count: int | None = None,
        coverage_state: str = "IN_PROGRESS",
        error_type: str | None = None,
        retry_count: int = 0,
        skip_reason: str | None = None,
    ) -> CrawlArtifact:
        kind = str(request_kind).upper()
        if kind not in {"ROBOTS", "SEARCH", "DETAIL"}:
            raise ValueError(f"unsupported crawl request kind: {request_kind}")
        payload = _body_bytes(body)
        row_values = _rows(rows)
        response_type = _content_type(headers or {}, content_type)
        retrieved = _aware(retrieved_at or datetime.now(timezone.utc))
        digest = page_fingerprint(payload)
        screenshot_payload = screenshot_bytes if screenshot_bytes is not None else screenshot
        screenshot_payload_bytes = (
            _body_bytes(screenshot_payload)
            if capture_screenshot and screenshot_payload is not None
            else None
        )
        request_index = self._request_index + 1
        timestamp = retrieved.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        while True:
            stem = f"{timestamp}_{request_index:03d}_{digest[:12]}"
            raw_path = self._raw_directory(kind) / f"{stem}{_suffix(response_type)}"
            screenshot_path = (
                self._screenshot_dir / f"{stem}.png"
                if screenshot_payload_bytes is not None
                else None
            )
            artifact = CrawlArtifact.model_validate(
                {
                    "run_id": self.run_id,
                    "source_name": self.source_name,
                    "source_registry_id": source_registry_id or self.source_registry_id,
                    "entrypoint_url": entrypoint_url or requested_url,
                    "request_kind": kind,
                    "requested_url": requested_url,
                    "final_url": final_url,
                    "canonical_url": canonical_url(final_url or requested_url),
                    "page_number": page_number,
                    "detail_url": detail_url,
                    "status_code": status_code if status_code is not None else status,
                    "content_type": response_type,
                    "raw_path": str(raw_path),
                    "screenshot_path": str(screenshot_path) if screenshot_path else None,
                    "sha256": digest,
                    "size_bytes": len(payload),
                    "retrieved_at": retrieved,
                    "parser": parser,
                    "parse_status": parse_status,
                    "rows": row_values,
                    "next_url": next_url,
                    "card_count": len(row_values) if card_count is None and kind == "SEARCH" else card_count or 0,
                    "extracted_row_count": len(row_values) if extracted_row_count is None else extracted_row_count,
                    "coverage_state": coverage_state,
                    "skip_reason": skip_reason.strip() if skip_reason else None,
                    "error_type": error_type.strip() if error_type else None,
                    "retry_count": retry_count,
                }
            )
            created_paths: list[Path] = []
            try:
                self._write_exclusive(raw_path, payload)
                created_paths.append(raw_path)
                if screenshot_path is not None and screenshot_payload_bytes is not None:
                    self._write_exclusive(screenshot_path, screenshot_payload_bytes)
                    created_paths.append(screenshot_path)
                self._append_manifest(artifact)
            except FileExistsError:
                self._remove_created(created_paths)
                request_index += 1
                continue
            except Exception:
                self._remove_created(created_paths)
                raise
            self._request_index = request_index
            return artifact
