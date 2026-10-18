from __future__ import annotations

import re


def parse_money(value: object | None) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).lower().replace(",", "").strip()
    match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*([mk])?", text)
    if not match:
        return None
    amount = float(match.group(1))
    suffix = match.group(2)
    if suffix == "m":
        amount *= 1_000_000
    elif suffix == "k":
        amount *= 1_000
    return int(amount)


def format_money(value: int | float | None) -> str:
    if value is None:
        return "unknown"
    return f"${int(value):,}"


def ratio(numerator: int | float | None, denominator: int | float | None) -> float | None:
    if numerator is None or denominator in (None, 0):
        return None
    return float(numerator) / float(denominator)
