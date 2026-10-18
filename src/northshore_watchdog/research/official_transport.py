from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import time
from typing import Any, Callable, Mapping
from urllib.parse import urlparse

import httpx

from .models import SourceType
from .sources import SourceRunResult, SourceRunStatus, SourceSnapshot


_CHALLENGE_MARKERS = (
    "captcha",
    "incapsula",
    "verify you are human",
    "just a moment",
    "request unsuccessful",
    "access denied",
    "enable javascript and cookies to continue",
    "cf_chl_opt",
    "challenge-platform",
    "cf-mitigated",
)


@dataclass(frozen=True)
class OfficialHttpResponse:
    url: str
    status_code: int
    headers: Mapping[str, str]
    content: bytes
    method: str = "GET"
    request: Mapping[str, Any] | None = None


@dataclass
class OfficialFetchResult:
    status: SourceRunStatus
    responses: list[OfficialHttpResponse]
    snapshots: list[SourceSnapshot]
    errors: list[str]
    warnings: list[str]
    source_run: SourceRunResult


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _header(headers: Mapping[str, str], name: str) -> str:
    wanted = name.lower()
    return next((str(value) for key, value in headers.items() if str(key).lower() == wanted), "")


def _host_allowed(url: str, allowed_domains: list[str]) -> bool:
    parsed = urlparse(url)
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        return False
    host = parsed.hostname.lower().rstrip(".")
    return any(host == domain.lower().rstrip(".") or host.endswith(f".{domain.lower().rstrip('.')}") for domain in allowed_domains)


def _has_challenge(content: bytes) -> bool:
    text = content.decode("utf-8", errors="ignore").lower()
    return any(marker in text for marker in _CHALLENGE_MARKERS)


class OfficialHttpTransport:
    """Bounded read-only HTTP transport for official enrichment sources."""

    def __init__(
        self,
        *,
        name: str,
        source_registry_id: str,
        source_type: SourceType | str,
        source_url: str,
        raw_snapshot_dir: str | Path,
        config: Mapping[str, Any] | None = None,
        transport: Callable[[Mapping[str, Any]], OfficialHttpResponse | httpx.Response] | None = None,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        self.name = name
        self.source_registry_id = source_registry_id
        self.source_type = source_type if isinstance(source_type, SourceType) else SourceType(str(source_type))
        self.source_url = source_url
        self.raw_snapshot_dir = Path(raw_snapshot_dir)
        self.config = dict(config or {})
        self.transport = transport
        self.sleep_fn = sleep_fn

    def _coerce_response(
        self,
        response: OfficialHttpResponse | httpx.Response,
        request: Mapping[str, Any],
    ) -> OfficialHttpResponse:
        if isinstance(response, OfficialHttpResponse):
            return OfficialHttpResponse(
                url=response.url,
                status_code=response.status_code,
                headers=response.headers,
                content=response.content,
                method=response.method or str(request.get("method") or "GET").upper(),
                request=dict(request),
            )
        return OfficialHttpResponse(
            url=str(response.url),
            status_code=response.status_code,
            headers=dict(response.headers),
            content=response.content,
            method=str(request.get("method") or response.request.method or "GET").upper(),
            request=dict(request),
        )

    def _request(self, request: Mapping[str, Any]) -> OfficialHttpResponse:
        if self.transport is not None:
            return self._coerce_response(self.transport(request), request)
        method = str(request.get("method") or "GET").upper()
        kwargs: dict[str, Any] = {
            "params": request.get("params"),
            "data": request.get("data"),
            "json": request.get("json"),
            "headers": request.get("headers"),
            "follow_redirects": True,
        }
        kwargs = {key: value for key, value in kwargs.items() if value is not None}
        with httpx.Client(
            timeout=float(self.config.get("timeout_seconds", 20)),
            trust_env=bool(self.config.get("trust_env", True)),
        ) as client:
            response = client.request(method, str(request["url"]), **kwargs)
        return self._coerce_response(response, request)

    def _write_snapshot(
        self,
        response: OfficialHttpResponse,
        *,
        observed_at: datetime,
        retrieved_at: datetime,
        request_index: int,
    ) -> SourceSnapshot:
        digest = sha256(response.content).hexdigest()
        target_dir = self.raw_snapshot_dir / self.name
        target_dir.mkdir(parents=True, exist_ok=True)
        stamp = _aware(retrieved_at).strftime("%Y%m%dT%H%M%S%fZ")
        path = target_dir / f"{stamp}-{request_index:03d}-{digest[:16]}.bin"
        path.write_bytes(response.content)
        return SourceSnapshot(
            source_registry_id=self.source_registry_id,
            url=response.request.get("url", response.url) if response.request else response.url,
            final_url=response.url,
            method=response.method,
            request_kind="target",
            status_code=response.status_code,
            content_type=_header(response.headers, "content-type") or None,
            raw_path=str(path),
            sha256=digest,
            size_bytes=len(response.content),
            observed_at=_aware(observed_at),
            retrieved_at=_aware(retrieved_at),
            source_version=str(self.config.get("source_version")) if self.config.get("source_version") else None,
        )

    def fetch_all(
        self,
        requests: list[Mapping[str, Any]],
        *,
        observed_at: datetime,
    ) -> OfficialFetchResult:
        started_at = datetime.now(timezone.utc)
        observed_at = _aware(observed_at)
        source_role = str(self.config.get("source_role") or "OFFICIAL_ENRICHMENT")
        priority = self.config.get("priority")
        priority = int(priority) if priority not in (None, "") else None
        coverage_complete = self.config.get("coverage_complete")
        if self.config.get("enabled") is False:
            source_run = SourceRunResult(
                source_registry_id=self.source_registry_id,
                source_name=self.name,
                status=SourceRunStatus.SKIPPED_DISABLED,
                source_role=source_role,
                priority=priority,
                coverage_complete=coverage_complete,
                started_at=started_at,
                finished_at=datetime.now(timezone.utc),
            )
            return OfficialFetchResult(SourceRunStatus.SKIPPED_DISABLED, [], [], [], [], source_run)

        allowed_domains = [str(item) for item in self.config.get("allowed_domains", []) if str(item).strip()]
        max_requests = max(1, int(self.config.get("max_requests_per_run", len(requests) or 1)))
        responses: list[OfficialHttpResponse] = []
        snapshots: list[SourceSnapshot] = []
        errors: list[str] = []
        warnings: list[str] = []
        attempts = 0
        saw_policy_block = False
        saw_failure = False
        retry_attempts = max(0, int(self.config.get("retry_attempts", 0) or 0))
        retry_delay = float(
            self.config.get(
                "retry_delay_seconds",
                self.config.get("delay_seconds", 0),
            )
        )
        for request_index, request in enumerate(requests):
            if attempts >= max_requests:
                warnings.append(f"request cap reached: {max_requests}")
                break
            url = str(request.get("url") or "")
            if not _host_allowed(url, allowed_domains):
                errors.append(f"official target URL is not allowed: {url}")
                saw_policy_block = True
                continue
            request_retry = 0
            response: OfficialHttpResponse | None = None
            while response is None:
                if attempts:
                    self.sleep_fn(
                        retry_delay
                        if request_retry
                        else float(self.config.get("delay_seconds", 0))
                    )
                try:
                    response = self._request(request)
                except (httpx.HTTPError, OSError, TypeError, ValueError) as exc:
                    attempts += 1
                    if request_retry < retry_attempts and attempts < max_requests:
                        request_retry += 1
                        continue
                    errors.append(f"request {url}: {type(exc).__name__}: {exc}")
                    saw_failure = True
                    break
                attempts += 1
            if response is None:
                continue
            retrieved_at = datetime.now(timezone.utc)
            snapshot = self._write_snapshot(
                response,
                observed_at=observed_at,
                retrieved_at=retrieved_at,
                request_index=request_index,
            )
            snapshots.append(snapshot)
            if not _host_allowed(response.url, allowed_domains):
                errors.append(f"official redirect is not allowed: {response.url}")
                saw_policy_block = True
                continue
            if response.status_code in {401, 403, 429}:
                errors.append(f"official source blocked: HTTP {response.status_code} at {url}")
                saw_policy_block = True
                continue
            if _has_challenge(response.content):
                errors.append(f"official source challenge detected at {url}")
                saw_policy_block = True
                continue
            if not 200 <= response.status_code < 300:
                errors.append(f"official source failed: HTTP {response.status_code} at {url}")
                saw_failure = True
                continue
            responses.append(response)

        if responses and (errors or len(requests) > attempts):
            status = SourceRunStatus.PARTIAL
        elif responses:
            status = SourceRunStatus.SUCCESS
        elif saw_policy_block:
            status = SourceRunStatus.BLOCKED_POLICY
        elif saw_failure or errors:
            status = SourceRunStatus.FAILED
        else:
            status = SourceRunStatus.FAILED
            errors.append("official source received no requests")
        source_run = SourceRunResult(
            source_registry_id=self.source_registry_id,
            source_name=self.name,
            status=status,
            source_role=source_role,
            priority=priority,
            coverage_complete=coverage_complete,
            started_at=started_at,
            finished_at=datetime.now(timezone.utc),
            records=[],
            snapshots=snapshots,
            errors=errors,
            warnings=warnings,
        )
        return OfficialFetchResult(status, responses, snapshots, errors, warnings, source_run)
