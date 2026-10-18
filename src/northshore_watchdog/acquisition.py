from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from .config import resolve_path


MANIFEST_SCHEMA = "weekly-acquisition-v1"
REQUIRED_ENTRIES: tuple[tuple[str, str], ...] = (
    ("purchase", "realtor.ca"),
    ("purchase", "zolo.ca"),
    ("rental", "realtor.ca"),
    ("rental", "zolo.ca"),
)


@dataclass(frozen=True)
class AcquisitionEntryValidation:
    radar: str
    domain: str
    status: str
    snapshot_path: str | None
    result_count: int | None
    card_count: int | None
    age_days: float | None
    reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "radar": self.radar,
            "domain": self.domain,
            "status": self.status,
            "snapshot_path": self.snapshot_path,
            "result_count": self.result_count,
            "card_count": self.card_count,
            "age_days": self.age_days,
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True)
class AcquisitionValidationResult:
    status: str
    manifest_path: str
    observed_at: datetime | None
    entries_validated: int
    reasons: list[str] = field(default_factory=list)
    entries: list[AcquisitionEntryValidation] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "manifest_path": self.manifest_path,
            "observed_at": self.observed_at.isoformat() if self.observed_at else None,
            "entries_validated": self.entries_validated,
            "reasons": list(self.reasons),
            "entries": [entry.to_dict() for entry in self.entries],
        }


def validate_acquisition_manifest(
    manifest_path: str | Path,
    purchase_config: dict[str, Any],
    rental_config: dict[str, Any],
    *,
    now: datetime | None = None,
    min_observed_at: datetime | None = None,
) -> AcquisitionValidationResult:
    """Validate the hand-off produced by the weekly web-search agent.

    This function deliberately validates an explicit manifest instead of
    inferring success from the presence of a recently modified file. A fresh
    but malformed or partial search must never turn into a normal weekly
    report.
    """

    path = Path(manifest_path)
    checked_at = _aware(now or datetime.now().astimezone())
    minimum_observed_at = _aware(min_observed_at) if min_observed_at is not None else None
    reasons: list[str] = []
    entries: list[AcquisitionEntryValidation] = []

    if not path.is_file():
        return _result(path, None, entries, [f"manifest missing: {path}"])

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return _result(path, None, entries, [f"manifest unreadable: {exc}"])
    if not isinstance(payload, dict):
        return _result(path, None, entries, ["manifest must be a JSON object"])

    if payload.get("schema_version") != MANIFEST_SCHEMA:
        reasons.append(f"unsupported manifest schema: {payload.get('schema_version')!r}")
    if str(payload.get("status", "")).lower() != "success":
        reasons.append(f"manifest status is not success: {payload.get('status')!r}")

    observed_at = _parse_datetime(payload.get("observed_at"))
    if observed_at is None:
        reasons.append("manifest observed_at is missing or invalid")
    else:
        max_age_days = max(
            _max_age_days(purchase_config),
            _max_age_days(rental_config),
        )
        age_days = (checked_at - observed_at).total_seconds() / 86400
        if age_days < -0.25:
            reasons.append("manifest observed_at is too far in the future")
        elif age_days > max_age_days:
            reasons.append(f"manifest is too old: {age_days:.3f} days")
        if minimum_observed_at is not None and observed_at < minimum_observed_at:
            reasons.append(
                "manifest observed_at predates acquisition start: "
                f"{observed_at.isoformat()} < {minimum_observed_at.isoformat()}"
            )

    for radar, domain in REQUIRED_ENTRIES:
        entry_payload = _nested_entry(payload, radar, domain)
        if not isinstance(entry_payload, dict):
            reasons.append(f"missing manifest entry: {radar}.{domain}")
            continue
        entry = _validate_entry(
            radar,
            domain,
            entry_payload,
            purchase_config if radar == "purchase" else rental_config,
            checked_at,
            minimum_observed_at,
        )
        entries.append(entry)
        reasons.extend(f"{radar}.{domain}: {reason}" for reason in entry.reasons)

    return _result(path, observed_at, entries, reasons)


def _result(
    path: Path,
    observed_at: datetime | None,
    entries: list[AcquisitionEntryValidation],
    reasons: Iterable[str],
) -> AcquisitionValidationResult:
    reason_list = list(dict.fromkeys(reasons))
    return AcquisitionValidationResult(
        status="fresh" if not reason_list and len(entries) == len(REQUIRED_ENTRIES) else "error",
        manifest_path=str(path),
        observed_at=observed_at,
        entries_validated=len(entries),
        reasons=reason_list,
        entries=entries,
    )


def _nested_entry(payload: dict[str, Any], radar: str, domain: str) -> Any:
    group = payload.get(radar)
    return group.get(domain) if isinstance(group, dict) else None


def _validate_entry(
    radar: str,
    domain: str,
    payload: dict[str, Any],
    config: dict[str, Any],
    checked_at: datetime,
    min_observed_at: datetime | None,
) -> AcquisitionEntryValidation:
    reasons: list[str] = []
    entry_status = str(payload.get("status", "")).lower()
    if entry_status != "success":
        reasons.append(f"source status is not success: {payload.get('status')!r}")

    declared_domain = _domain(payload.get("source_domain"))
    if declared_domain != domain:
        reasons.append(f"source_domain is {payload.get('source_domain')!r}")

    raw_path = payload.get("snapshot_path")
    snapshot_path: Path | None = None
    if not isinstance(raw_path, str) or not raw_path.strip():
        reasons.append("snapshot_path is missing")
    else:
        snapshot_path = resolve_path(raw_path, config)
        if not _under_configured_directory(snapshot_path, config, radar):
            reasons.append(f"snapshot is outside configured directory: {snapshot_path}")
        if snapshot_path.suffix.lower() not in {".html", ".htm"}:
            reasons.append(f"snapshot is not HTML: {snapshot_path}")
        if not snapshot_path.is_file():
            reasons.append(f"snapshot missing: {snapshot_path}")

    age_days: float | None = None
    card_count: int | None = None
    if snapshot_path is not None and snapshot_path.is_file():
        observed = datetime.fromtimestamp(snapshot_path.stat().st_mtime).astimezone()
        age_days = max(0.0, (checked_at - observed).total_seconds() / 86400)
        if age_days > _max_age_days(config):
            reasons.append(f"snapshot is too old: {age_days:.3f} days")
        if min_observed_at is not None and observed < min_observed_at:
            reasons.append(
                "snapshot predates acquisition start: "
                f"{observed.isoformat()} < {min_observed_at.isoformat()}"
            )
        try:
            html = snapshot_path.read_text(encoding="utf-8", errors="ignore")
            soup = BeautifulSoup(html, "html.parser")
            if _meta_value(soup, "acquisition-status").lower() != "success":
                reasons.append("snapshot acquisition-status marker is not success")
            if _domain(_meta_value(soup, "source-domain")) != domain:
                reasons.append("snapshot source-domain marker does not match manifest")
            card_selector = ".rental-card, [data-rental-card]" if radar == "rental" else ".listing-card, [data-listing-card]"
            cards = soup.select(card_selector)
            card_count = len(cards)
            allowed_domains = _allowed_domains(config, radar)
            for index, card in enumerate(cards):
                url = card.get("data-url") or ((card.find("a") or {}).get("href"))
                if not _url_allowed(url, allowed_domains):
                    reasons.append(f"card {index} has disallowed source URL: {url!r}")
        except OSError as exc:
            reasons.append(f"snapshot unreadable: {exc}")

    result_count = _integer(payload.get("result_count"))
    if result_count is None:
        reasons.append("result_count is missing or invalid")
    elif card_count is not None and result_count != card_count:
        reasons.append(f"result_count={result_count} does not match card_count={card_count}")

    return AcquisitionEntryValidation(
        radar=radar,
        domain=domain,
        status=entry_status,
        snapshot_path=str(snapshot_path) if snapshot_path else None,
        result_count=result_count,
        card_count=card_count,
        age_days=round(age_days, 6) if age_days is not None else None,
        reasons=tuple(dict.fromkeys(reasons)),
    )


def _configured_directories(config: dict[str, Any], radar: str) -> list[Path]:
    collector_name = "search_page_snapshot" if radar == "purchase" else "snapshot"
    collector = config.get("collectors", {}).get(collector_name, {})
    values = collector.get("input_dirs")
    if values is None:
        value = collector.get("input_dir")
        values = [value] if value else []
    return [resolve_path(value, config).resolve() for value in values if value]


def _under_configured_directory(path: Path, config: dict[str, Any], radar: str) -> bool:
    candidate = path.resolve()
    for directory in _configured_directories(config, radar):
        try:
            candidate.relative_to(directory)
            return True
        except ValueError:
            continue
    return False


def _allowed_domains(config: dict[str, Any], radar: str) -> list[str]:
    collector_name = "search_page_snapshot" if radar == "purchase" else "snapshot"
    collector = config.get("collectors", {}).get(collector_name, {})
    domains = collector.get("allowed_domains")
    if domains is None:
        domains = config.get("allowed_domains")
    if not domains:
        domains = ["realtor.ca", "zolo.ca"]
    return [str(item) for item in domains]


def _url_allowed(url: Any, allowed_domains: Iterable[str]) -> bool:
    if not isinstance(url, str) or not url.strip():
        return False
    host = _domain(urlparse(url).netloc)
    return bool(host) and any(
        host == _domain(domain) or host.endswith(f".{_domain(domain)}")
        for domain in allowed_domains
        if _domain(domain)
    )


def _meta_value(soup: BeautifulSoup, name: str) -> str:
    tag = soup.find("meta", attrs={"name": name})
    return str(tag.get("content", "")) if tag else ""


def _domain(value: Any) -> str:
    text = str(value or "").strip().lower()
    if "://" in text:
        text = urlparse(text).netloc.lower()
    return text.removeprefix("www.").split(":", 1)[0]


def _integer(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _max_age_days(config: dict[str, Any]) -> float:
    return float(config.get("monitoring", {}).get("max_age_days", 7))


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return _aware(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except ValueError:
        return None


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value
