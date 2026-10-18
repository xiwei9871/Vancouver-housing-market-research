from __future__ import annotations

import os
from typing import Any

import httpx

from .base import Notifier


def build_openclaw_payload(
    route: str,
    action: str,
    summary: dict[str, Any],
    top_candidates: list[dict[str, Any]],
    markdown_report: str,
    html_report_path: str | None,
    generated_at: str,
    review_candidates: list[dict[str, Any]] | None = None,
) -> dict:
    return {
        "route": route,
        "action": action,
        "payload": {
            "summary": summary,
            "top_candidates": top_candidates,
            "review_candidates": review_candidates or [],
            "markdown_report": markdown_report,
            "html_report_path": html_report_path,
            "generated_at": generated_at,
        },
    }


class OpenClawNotifier(Notifier):
    def send(self, payload: dict) -> bool:
        base_url = os.getenv(self.config.get("base_url_env", "OPENCLAW_BASE_URL"), "").rstrip("/")
        if not base_url:
            return False
        api_key = os.getenv(self.config.get("api_key_env", "OPENCLAW_API_KEY"), "")
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        endpoint = self.config.get("endpoint", "/api/routes/dispatch")
        try:
            response = httpx.post(f"{base_url}{endpoint}", json=payload, headers=headers, timeout=float(self.config.get("timeout_seconds", 15)))
            return response.status_code < 300
        except httpx.HTTPError:
            return False
