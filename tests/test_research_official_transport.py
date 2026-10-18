from datetime import datetime, timezone
from pathlib import Path

import httpx

from northshore_watchdog.research.models import SourceType
from northshore_watchdog.research.official_transport import (
    OfficialHttpResponse,
    OfficialHttpTransport,
)
from northshore_watchdog.research.sources import SourceRunStatus


OBSERVED_AT = datetime(2026, 9, 1, tzinfo=timezone.utc)


def _transport(tmp_path: Path, responder):
    return OfficialHttpTransport(
        name="official-test",
        source_registry_id="source:official-test",
        source_type=SourceType.OFFICIAL_GOV,
        source_url="https://official.example",
        raw_snapshot_dir=tmp_path / "raw",
        config={
            "enabled": True,
            "allowed_domains": ["official.example"],
            "max_requests_per_run": 5,
            "source_version": "official-test-v1",
        },
        transport=responder,
        sleep_fn=lambda seconds: None,
    )


def test_official_transport_fetches_and_snapshots_allowed_response(tmp_path):
    def responder(request):
        return OfficialHttpResponse(
            url=request["url"],
            status_code=200,
            headers={"content-type": "application/json"},
            content=b'{"ok": true}',
        )

    result = _transport(tmp_path, responder).fetch_all(
        [{"url": "https://official.example/property/1"}],
        observed_at=OBSERVED_AT,
    )

    assert result.status is SourceRunStatus.SUCCESS
    assert len(result.responses) == 1
    assert len(result.snapshots) == 1
    assert Path(result.snapshots[0].raw_path).read_bytes() == b'{"ok": true}'
    assert result.snapshots[0].source_version == "official-test-v1"


def test_official_transport_blocks_disallowed_domain_without_request(tmp_path):
    calls = []

    def responder(request):
        calls.append(request)
        raise AssertionError("disallowed request must not be sent")

    result = _transport(tmp_path, responder).fetch_all(
        [{"url": "https://not-official.example/property/1"}],
        observed_at=OBSERVED_AT,
    )

    assert result.status is SourceRunStatus.BLOCKED_POLICY
    assert calls == []
    assert "not allowed" in result.errors[0]


def test_official_transport_records_http_block_and_challenge(tmp_path):
    responses = iter(
        [
            OfficialHttpResponse(
                url="https://official.example/blocked",
                status_code=403,
                headers={"content-type": "text/html"},
                content=b"access denied",
            ),
            OfficialHttpResponse(
                url="https://official.example/challenge",
                status_code=200,
                headers={"content-type": "text/html"},
                content=b"Please verify you are human",
            ),
        ]
    )

    result = _transport(tmp_path, lambda request: next(responses)).fetch_all(
        [
            {"url": "https://official.example/blocked"},
            {"url": "https://official.example/challenge"},
        ],
        observed_at=OBSERVED_AT,
    )

    assert result.status is SourceRunStatus.BLOCKED_POLICY
    assert len(result.snapshots) == 2
    assert len(result.responses) == 0
    assert any("HTTP 403" in item for item in result.errors)
    assert any("challenge" in item for item in result.errors)


def test_official_transport_honours_request_cap(tmp_path):
    calls = []

    def responder(request):
        calls.append(request["url"])
        return OfficialHttpResponse(
            url=request["url"],
            status_code=200,
            headers={"content-type": "application/json"},
            content=b"{}",
        )

    transport = _transport(tmp_path, responder)
    transport.config["max_requests_per_run"] = 1
    result = transport.fetch_all(
        [
            {"url": "https://official.example/one"},
            {"url": "https://official.example/two"},
        ],
        observed_at=OBSERVED_AT,
    )

    assert calls == ["https://official.example/one"]
    assert result.status is SourceRunStatus.PARTIAL
    assert len(result.snapshots) == 1
    assert "request cap" in " ".join(result.warnings)


def test_official_transport_retries_transient_network_error(tmp_path):
    calls = []

    def responder(request):
        calls.append(request["url"])
        if len(calls) == 1:
            raise httpx.ReadTimeout("temporary network timeout")
        return OfficialHttpResponse(
            url=request["url"],
            status_code=200,
            headers={"content-type": "application/json"},
            content=b'{"ok": true}',
        )

    transport = _transport(tmp_path, responder)
    transport.config["retry_attempts"] = 1
    transport.config["retry_delay_seconds"] = 0

    result = transport.fetch_all(
        [{"url": "https://official.example/property/1"}],
        observed_at=OBSERVED_AT,
    )

    assert calls == [
        "https://official.example/property/1",
        "https://official.example/property/1",
    ]
    assert result.status is SourceRunStatus.SUCCESS
    assert len(result.responses) == 1
    assert result.errors == []
