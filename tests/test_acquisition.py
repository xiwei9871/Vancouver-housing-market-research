import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from northshore_watchdog.acquisition import validate_acquisition_manifest


NOW = datetime(2026, 8, 30, 12, tzinfo=timezone.utc)


def _set_mtime(path: Path, value: datetime) -> None:
    timestamp = value.timestamp()
    os.utime(path, (timestamp, timestamp))


def _configs(tmp_path: Path) -> tuple[dict, dict, dict[str, Path]]:
    realtor_dir = tmp_path / "realtor"
    zolo_dir = tmp_path / "zolo"
    rental_dir = tmp_path / "rentals"
    for directory in (realtor_dir, zolo_dir, rental_dir):
        directory.mkdir()
    purchase_config = {
        "monitoring": {"max_age_days": 7},
        "collectors": {
            "search_page_snapshot": {
                "input_dirs": [str(realtor_dir), str(zolo_dir)],
            }
        },
    }
    rental_config = {
        "monitoring": {"max_age_days": 7},
        "collectors": {"snapshot": {"input_dirs": [str(rental_dir)]}},
    }
    return purchase_config, rental_config, {
        "purchase_realtor": realtor_dir,
        "purchase_zolo": zolo_dir,
        "rental": rental_dir,
    }


def _snapshot(path: Path, *, domain: str, rental: bool = False, card_url: str | None = None) -> None:
    card = ""
    if card_url:
        class_name = "rental-card" if rental else "listing-card"
        price_attr = 'data-rent="$2,800"' if rental else 'data-price="$1,500,000"'
        card = f'<article class="{class_name}" data-address="1 Main Street" data-city="Richmond" data-url="{card_url}" {price_attr}></article>'
    path.write_text(
        "\n".join(
            [
                "<!doctype html>",
                f'<meta name="acquisition-status" content="success">',
                f'<meta name="source-domain" content="{domain}">',
                card,
                "",
            ]
        ),
        encoding="utf-8",
    )
    _set_mtime(path, NOW - timedelta(hours=1))


def _manifest(paths: dict[str, Path], *, status: str = "success") -> dict:
    return {
        "schema_version": "weekly-acquisition-v1",
        "status": status,
        "observed_at": (NOW - timedelta(hours=1)).isoformat(),
        "purchase": {
            "realtor.ca": {
                "status": "success",
                "source_domain": "realtor.ca",
                "snapshot_path": str(paths["purchase_realtor"]),
                "result_count": 1,
            },
            "zolo.ca": {
                "status": "success",
                "source_domain": "zolo.ca",
                "snapshot_path": str(paths["purchase_zolo"]),
                "result_count": 1,
            },
        },
        "rental": {
            "realtor.ca": {
                "status": "success",
                "source_domain": "realtor.ca",
                "snapshot_path": str(paths["rental_realtor"]),
                "result_count": 1,
            },
            "zolo.ca": {
                "status": "success",
                "source_domain": "zolo.ca",
                "snapshot_path": str(paths["rental_zolo"]),
                "result_count": 1,
            },
        },
    }


def _write_valid_inputs(tmp_path: Path) -> tuple[Path, dict, dict]:
    purchase_config, rental_config, directories = _configs(tmp_path)
    purchase_realtor = directories["purchase_realtor"] / "auto-realtor.html"
    purchase_zolo = directories["purchase_zolo"] / "auto-zolo.html"
    rental_realtor = directories["rental"] / "auto-realtor-rentals.html"
    rental_zolo = directories["rental"] / "auto-zolo-rentals.html"
    _snapshot(
        purchase_realtor,
        domain="realtor.ca",
        card_url="https://www.realtor.ca/real-estate/1/1-main-street-north-vancouver",
    )
    _snapshot(
        purchase_zolo,
        domain="zolo.ca",
        card_url="https://www.zolo.ca/north-vancouver-real-estate/1-main-street",
    )
    _snapshot(
        rental_realtor,
        domain="realtor.ca",
        rental=True,
        card_url="https://www.realtor.ca/real-estate/2/2-main-street-richmond",
    )
    _snapshot(
        rental_zolo,
        domain="zolo.ca",
        rental=True,
        card_url="https://www.zolo.ca/richmond-real-estate/2-main-street/101",
    )
    paths = {
        "purchase_realtor": purchase_realtor,
        "purchase_zolo": purchase_zolo,
        "rental_realtor": rental_realtor,
        "rental_zolo": rental_zolo,
    }
    manifest = tmp_path / "latest.json"
    manifest.write_text(json.dumps(_manifest(paths)), encoding="utf-8")
    _set_mtime(manifest, NOW - timedelta(hours=1))
    return manifest, purchase_config, rental_config


def test_validates_four_fresh_source_snapshots(tmp_path):
    manifest, purchase_config, rental_config = _write_valid_inputs(tmp_path)

    result = validate_acquisition_manifest(manifest, purchase_config, rental_config, now=NOW)

    assert result.status == "fresh"
    assert result.reasons == []
    assert result.entries_validated == 4


def test_accepts_a_successful_empty_source_result(tmp_path):
    manifest, purchase_config, rental_config = _write_valid_inputs(tmp_path)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    empty_snapshot = tmp_path / "realtor" / "empty.html"
    _snapshot(empty_snapshot, domain="realtor.ca")
    payload["purchase"]["realtor.ca"].update(
        snapshot_path=str(empty_snapshot),
        result_count=0,
    )
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    result = validate_acquisition_manifest(manifest, purchase_config, rental_config, now=NOW)

    assert result.status == "fresh"


def test_rejects_old_manifest(tmp_path):
    manifest, purchase_config, rental_config = _write_valid_inputs(tmp_path)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["observed_at"] = (NOW - timedelta(days=8)).isoformat()
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    result = validate_acquisition_manifest(manifest, purchase_config, rental_config, now=NOW)

    assert result.status == "error"
    assert any("old" in reason for reason in result.reasons)


def test_rejects_inputs_older_than_current_acquisition_start(tmp_path):
    manifest, purchase_config, rental_config = _write_valid_inputs(tmp_path)

    result = validate_acquisition_manifest(
        manifest,
        purchase_config,
        rental_config,
        now=NOW,
        min_observed_at=NOW - timedelta(minutes=45),
    )

    assert result.status == "error"
    assert any("predates acquisition start" in reason for reason in result.reasons)
    assert sum("predates acquisition start" in reason for reason in result.reasons) >= 5


def test_rejects_partial_source_status(tmp_path):
    manifest, purchase_config, rental_config = _write_valid_inputs(tmp_path)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["rental"]["zolo.ca"]["status"] = "blocked"
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    result = validate_acquisition_manifest(manifest, purchase_config, rental_config, now=NOW)

    assert result.status == "error"
    assert any("rental.zolo.ca" in reason for reason in result.reasons)


def test_rejects_snapshot_outside_configured_directory(tmp_path):
    manifest, purchase_config, rental_config = _write_valid_inputs(tmp_path)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    outside = tmp_path / "outside.html"
    _snapshot(outside, domain="realtor.ca", card_url="https://www.realtor.ca/real-estate/1/1-main-street")
    payload["purchase"]["realtor.ca"]["snapshot_path"] = str(outside)
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    result = validate_acquisition_manifest(manifest, purchase_config, rental_config, now=NOW)

    assert result.status == "error"
    assert any("configured directory" in reason for reason in result.reasons)


def test_rejects_card_from_disallowed_domain(tmp_path):
    manifest, purchase_config, rental_config = _write_valid_inputs(tmp_path)
    bad = tmp_path / "zolo" / "auto-zolo.html"
    _snapshot(bad, domain="zolo.ca", card_url="https://example.com/for-sale")

    result = validate_acquisition_manifest(manifest, purchase_config, rental_config, now=NOW)

    assert result.status == "error"
    assert any("disallowed" in reason for reason in result.reasons)
