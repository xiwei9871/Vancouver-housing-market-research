from __future__ import annotations

from datetime import datetime, timezone


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
