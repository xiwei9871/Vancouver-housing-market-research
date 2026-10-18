from __future__ import annotations

import hashlib
from urllib.parse import urlsplit, urlunsplit

from .models import Listing
from .utils.text import slug_text


def normalized_address(address: str | None) -> str:
    text = slug_text(address)
    replacements = {
        " avenue": " ave",
        " street": " st",
        " road": " rd",
        " drive": " dr",
        " boulevard": " blvd",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return text


def listing_dedupe_key(listing: Listing) -> str:
    if listing.mls_number:
        return f"mls:{listing.mls_number.strip().upper()}"
    if listing.url:
        return f"url:{canonical_url(listing.url)}"
    return f"address:{normalized_address(listing.address)}|{slug_text(listing.city)}"


def canonical_url(url: str) -> str:
    parsed = urlsplit(url.strip())
    netloc = parsed.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    path = parsed.path.rstrip("/") or "/"
    return urlunsplit((parsed.scheme.lower(), netloc, path, parsed.query, ""))
