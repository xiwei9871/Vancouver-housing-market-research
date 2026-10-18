#!/usr/bin/env python3
"""Build photo manifests + download photo bytes for the 2026-10-05 new batch.

Primary source: cached REALTOR.ca detail HTML (imageGallerySidebarPhotoN
URLs) via ``extract_photo_records_from_html`` + ``acquire_photo_manifest``.

Fallback: broker IDX mirror ``{host}/showlisting/mls-{MLS}/`` which serves
the same cloudfront CDN images, for properties whose detail snapshot has no
gallery (missing/failed fetch).

Writes per-property manifests under ``data/research/raw/photo-assets/`` and a
``strategy-v2-online-photo-evidence-newbatch-2026-10-05.json`` artifact.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from northshore_watchdog.research.strategy_v2_photo_intelligence import (  # noqa: E402
    build_photo_manifest,
    extract_photo_records_from_html,
)
from northshore_watchdog.research.strategy_v2_photo_acquisition import (  # noqa: E402
    acquire_photo_manifest,
)

ENRICHMENT = ROOT / "data/research/normalized/regional-official-enrichment-newbatch-2026-10-05.json"
DETAIL_DIR = ROOT / "data/research/raw/discovery/realtor-ca-native-details"
ASSET_ROOT = ROOT / "data/research/raw/photo-assets"
EVIDENCE_OUT = ROOT / "data/research/normalized/strategy-v2-online-photo-evidence-newbatch-2026-10-05.json"

IDX_HOSTS = (
    "https://www.pacificwestre.ca",
    "https://www.brianrhys.com",
    "https://www.anthonytrinetti.com",
)
CDN_IMG = re.compile(r"https?://[^\"'\s]*cloudfront[^\"'\s]*?\.(?:jpg|jpeg)", re.IGNORECASE)


def _fetch(url: str, timeout: float = 25.0) -> str | None:
    try:
        req = Request(url, headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"})
        return urlopen(req, timeout=timeout).read().decode("utf-8", "replace")
    except Exception:
        return None


def _idx_photos(mls: str) -> list[str]:
    """Photo CDN URLs from a broker IDX mirror page."""
    for host in IDX_HOSTS:
        html = _fetch(f"{host}/showlisting/mls-{mls.lower()}/")
        if not html or mls.lower() not in html.lower():
            continue
        urls = CDN_IMG.findall(html)
        # dedupe, keep order
        seen: list[str] = []
        for u in urls:
            u = u.replace("&amp;", "&")
            if u not in seen:
                seen.append(u)
        if seen:
            return seen
    return []


def main() -> int:
    payload = json.loads(ENRICHMENT.read_text(encoding="utf-8"))
    cards = payload["official_enrichment"]["cards"]
    retrieved_at = datetime.now(timezone.utc)
    evidence_rows: list[dict] = []

    for card in cards:
        mls = str(card.get("mls_number") or "").strip()
        key = f"mls:{mls}"
        safe = re.sub(r"[^a-z0-9]+", "-", mls.casefold()).strip("-") or "listing"
        detail_path = DETAIL_DIR / f"{safe}.html"
        photos: list[dict] = []
        source = "realtor-detail-html"
        if detail_path.exists():
            photos = extract_photo_records_from_html(
                detail_path.read_text(encoding="utf-8", errors="replace"), key
            )
        if not photos:
            urls = _idx_photos(mls)
            photos = [
                {"property_key": key, "source_url": u, "original_order": i,
                 "source_photo_id": f"idx-{i+1}", "content_labels": []}
                for i, u in enumerate(urls)
            ]
            source = "idx-mirror" if urls else "none"

        manifest = build_photo_manifest(key, photos, retrieved_at)
        outdir = ASSET_ROOT / f"mls-{safe}"
        manifest, summary = acquire_photo_manifest(
            manifest, outdir, retrieved_at=retrieved_at.isoformat(), timeout=25.0
        )
        (outdir / "manifest.json").write_text(
            json.dumps(manifest.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        n = sum(1 for im in manifest.images if im.local_path)
        evidence_rows.append({
            "property_key": key,
            "photo_count": n,
            "photo_manifest_path": str(outdir / "manifest.json"),
            "photo_source": source,
        })
        print(f"{key:18s} photos={n:3d} source={source}")

    EVIDENCE_OUT.write_text(json.dumps({
        "schema_version": "strategy-v2-online-photo-evidence-v1",
        "property_count": len(evidence_rows),
        "properties": evidence_rows,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    zero = [r["property_key"] for r in evidence_rows if r["photo_count"] == 0]
    print(f"\nwrote {EVIDENCE_OUT}; zero-photo: {zero}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
