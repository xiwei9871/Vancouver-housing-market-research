from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode

import httpx

from .base import ProviderResult
from .scoring import normalize_walk_score


def _valid_coordinates(latitude: float | None, longitude: float | None) -> bool:
    return (
        latitude is not None
        and longitude is not None
        and -90.0 <= float(latitude) <= 90.0
        and -180.0 <= float(longitude) <= 180.0
    )


class WalkScoreProvider:
    name = "walk_score"
    endpoint = "https://api.walkscore.com/score"

    def __init__(
        self,
        *,
        api_key: str | None,
        client: httpx.Client | None = None,
        endpoint: str | None = None,
        timeout: float = 20.0,
    ) -> None:
        self.api_key = api_key
        self.client = client
        self.endpoint = endpoint or self.endpoint
        self.timeout = timeout

    def get_location_scores(
        self,
        *,
        address: str,
        latitude: float | None,
        longitude: float | None,
    ) -> ProviderResult:
        now = datetime.now(timezone.utc)
        base = dict(
            provider=self.name,
            queried_at=now,
            latitude=latitude,
            longitude=longitude,
        )
        if not self.api_key:
            return ProviderResult(**base, status="NOT_CONFIGURED")
        if not _valid_coordinates(latitude, longitude):
            return ProviderResult(**base, status="INVALID_REQUEST", error="valid latitude/longitude required")
        params = {
            "format": "json",
            "address": address,
            "lat": latitude,
            "lon": longitude,
            "transit": 1,
            "bike": 1,
            "wsapikey": self.api_key,
        }
        owned = self.client is None
        client = self.client or httpx.Client(timeout=self.timeout)
        try:
            response = client.get(self.endpoint, params=params)
            raw = response.json()
            if not isinstance(raw, dict):
                raise ValueError("Walk Score response is not an object")
            status_code = raw.get("status")
            if status_code == 2:
                return ProviderResult(**base, status="NO_DATA", raw=raw, http_status=response.status_code, source_url=self.endpoint)
            if response.status_code != 200 or status_code != 1:
                return ProviderResult(
                    **base,
                    status="FAILED",
                    raw=raw,
                    http_status=response.status_code,
                    source_url=self.endpoint,
                    error=f"Walk Score API status {status_code!r}",
                )
            scores: dict[str, float] = {}
            if raw.get("walkscore") is not None:
                scores["walk"] = normalize_walk_score(raw["walkscore"])
            transit = raw.get("transit")
            if isinstance(transit, dict) and transit.get("score") is not None:
                scores["transit"] = normalize_walk_score(transit["score"])
            bike = raw.get("bike")
            if isinstance(bike, dict) and bike.get("score") is not None:
                scores["bike"] = normalize_walk_score(bike["score"])
            if not scores:
                return ProviderResult(**base, status="NO_DATA", raw=raw, http_status=response.status_code, source_url=self.endpoint)
            return ProviderResult(
                **base,
                status="OK",
                scores=scores,
                raw=raw,
                http_status=response.status_code,
                # The API key is a query parameter; never persist response.url.
                source_url=self.endpoint,
                provider_version=str(raw.get("updated") or "unknown"),
            )
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            return ProviderResult(**base, status="FAILED", error=f"{type(exc).__name__}: {exc}")
        finally:
            if owned:
                client.close()
