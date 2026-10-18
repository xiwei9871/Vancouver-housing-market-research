from __future__ import annotations

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8))
def get_with_timeout(url: str, timeout: float = 10, headers: dict | None = None) -> httpx.Response:
    response = httpx.get(url, timeout=timeout, headers=headers)
    response.raise_for_status()
    return response
