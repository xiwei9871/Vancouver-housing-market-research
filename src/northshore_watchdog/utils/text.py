from __future__ import annotations

import re
import unicodedata


def clean_text(value: object | None) -> str:
    if value is None:
        return ""
    text = unicodedata.normalize("NFKC", str(value))
    return re.sub(r"\s+", " ", text).strip()


def slug_text(value: object | None) -> str:
    text = clean_text(value).lower()
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def contains_any(text: str, keywords: list[str]) -> list[str]:
    haystack = slug_text(text)
    matches: list[str] = []
    for keyword in keywords:
        needle = slug_text(keyword)
        if needle and re.search(rf"\b{re.escape(needle)}\b", haystack):
            matches.append(keyword)
    return matches
