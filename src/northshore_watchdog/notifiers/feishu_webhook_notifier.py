from __future__ import annotations

import base64
import hashlib
import hmac
import os
import time

import httpx

from northshore_watchdog.models import ScoredListing
from northshore_watchdog.utils.money import format_money, ratio

from .base import Notifier


def build_feishu_text_payload(text: str) -> dict:
    return {"msg_type": "text", "content": {"text": text}}


def build_feishu_post_payload(title: str, markdown: str) -> dict:
    return {
        "msg_type": "post",
        "content": {
            "post": {
                "zh_cn": {
                    "title": title,
                    "content": [[{"tag": "text", "text": markdown[:3900]}]],
                }
            }
        },
    }


def build_feishu_card_payload(title: str, scored: list[ScoredListing], report_path: str) -> dict:
    elements = [{"tag": "div", "text": {"tag": "lark_md", "content": f"**报告路径**: `{report_path}`"}}]
    for item in scored[:5]:
        listing = item.listing
        assessment_ratio = ratio(listing.list_price, listing.assessment_total)
        content = (
            f"**{listing.address}, {listing.city}**\n"
            f"MLS® Num {listing.mls_number or 'unknown'}\n"
            f"{format_money(listing.list_price)} | Score {item.score_total} / {item.grade} | "
            f"Assessment {_fmt_ratio(assessment_ratio)}\n"
            f"Lot {listing.lot_size_sqft or 'unknown'} sqft | Built {listing.year_built or 'unknown'} | "
            f"{listing.bedrooms or 'unknown'} bed/{listing.bathrooms or 'unknown'} bath\n"
            f"推荐: {', '.join(item.reasons[:2]) or 'none'}\n"
            f"风险: {', '.join((item.risks + item.exclusion_reasons)[:2]) or 'none'}\n"
            f"[Listing]({listing.url or '#'})\n"
            f"下一步: {'; '.join(item.next_actions[:2])}"
        )
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": content}})
    return {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {"title": {"tag": "plain_text", "content": title}},
            "elements": elements,
        },
    }


def sign_payload(payload: dict, secret: str) -> dict:
    timestamp = str(int(time.time()))
    string_to_sign = f"{timestamp}\n{secret}".encode("utf-8")
    signature = base64.b64encode(hmac.new(string_to_sign, b"", digestmod=hashlib.sha256).digest()).decode("utf-8")
    payload = dict(payload)
    payload["timestamp"] = timestamp
    payload["sign"] = signature
    return payload


class FeishuWebhookNotifier(Notifier):
    def send(self, payload: dict) -> bool:
        url = os.getenv(self.config.get("webhook_url_env", "FEISHU_WEBHOOK_URL"), "")
        if not url:
            return False
        secret = os.getenv(self.config.get("secret_env", "FEISHU_WEBHOOK_SECRET"), "")
        if secret:
            payload = sign_payload(payload, secret)
        try:
            response = httpx.post(url, json=payload, timeout=float(self.config.get("timeout_seconds", 10)))
            return response.status_code < 300
        except httpx.HTTPError:
            return False


def _fmt_ratio(value: float | None) -> str:
    return "unknown" if value is None else f"{value:.2f}"
