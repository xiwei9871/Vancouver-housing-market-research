from __future__ import annotations

import json
from pathlib import Path

from northshore_watchdog.config import resolve_path

from .feishu_webhook_notifier import FeishuWebhookNotifier
from .openclaw_notifier import OpenClawNotifier


def send_with_fallback(config: dict, openclaw_payload: dict, feishu_payload: dict) -> str:
    notifier_config = config.get("notifiers", {})
    if notifier_config.get("openclaw", {}).get("enabled", False):
        if OpenClawNotifier(notifier_config["openclaw"]).send(openclaw_payload):
            return "openclaw_sent"
        if notifier_config["openclaw"].get("fallback_to_feishu", True):
            if FeishuWebhookNotifier(notifier_config.get("feishu", {})).send(feishu_payload):
                return "feishu_sent_after_openclaw_failure"
    elif notifier_config.get("feishu", {}).get("enabled", False):
        if FeishuWebhookNotifier(notifier_config["feishu"]).send(feishu_payload):
            return "feishu_sent"

    path = resolve_path("outputs/failed_notifications.jsonl", config)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"openclaw": openclaw_payload, "feishu": feishu_payload}, ensure_ascii=False) + "\n")
    return "failed_written_to_jsonl"
