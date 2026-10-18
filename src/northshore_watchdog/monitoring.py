from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any

from .config import resolve_path


SUPPORTED_SUFFIXES: dict[str, set[str]] = {
    "search_page_snapshot": {".html", ".htm"},
    "email_alert": {".eml", ".html", ".htm", ".txt"},
    "snapshot": {".html", ".htm", ".txt", ".eml"},
}


def _as_local(value: datetime | None) -> datetime:
    current = value or datetime.now().astimezone()
    if current.tzinfo is None:
        return current.replace(tzinfo=timezone.utc).astimezone()
    return current.astimezone()


@dataclass(frozen=True)
class SnapshotMetadata:
    collector: str
    path: str
    observed_at: datetime
    age_days: float
    sha256: str
    size_bytes: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "collector": self.collector,
            "path": self.path,
            "observed_at": self.observed_at.isoformat(),
            "age_days": self.age_days,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
        }


@dataclass
class FreshnessResult:
    status: str
    checked_at: datetime
    max_age_days: float
    required_collectors: list[str]
    files: dict[str, list[SnapshotMetadata]]
    selected_files: dict[str, list[SnapshotMetadata]]
    newest: SnapshotMetadata | None
    stale_directories: list[str]
    reasons: list[str]
    warnings: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "checked_at": self.checked_at.isoformat(),
            "max_age_days": self.max_age_days,
            "required_collectors": self.required_collectors,
            "files": {
                name: [item.to_dict() for item in items]
                for name, items in self.files.items()
            },
            "selected_files": {
                name: [item.to_dict() for item in items]
                for name, items in self.selected_files.items()
            },
            "newest": self.newest.to_dict() if self.newest else None,
            "stale_directories": self.stale_directories,
            "reasons": self.reasons,
            "warnings": self.warnings,
        }


def snapshot_metadata(
    path: str | Path,
    collector: str,
    now: datetime | None = None,
) -> SnapshotMetadata:
    file_path = Path(path)
    observed_at = datetime.fromtimestamp(file_path.stat().st_mtime).astimezone()
    checked_at = _as_local(now)
    age_days = max(0.0, (checked_at - observed_at).total_seconds() / 86400)
    digest = sha256(file_path.read_bytes()).hexdigest()
    return SnapshotMetadata(
        collector=collector,
        path=str(file_path),
        observed_at=observed_at,
        age_days=round(age_days, 6),
        sha256=digest,
        size_bytes=file_path.stat().st_size,
    )


def _input_dirs(collector_config: dict[str, Any], config: dict[str, Any]) -> list[Path]:
    values = collector_config.get("input_dirs")
    if values is None:
        value = collector_config.get("input_dir")
        values = [value] if value else []
    return [resolve_path(value, config) for value in values]


def _files_in(directory: Path, suffixes: set[str]) -> list[Path]:
    if not directory.exists() or not directory.is_dir():
        return []
    return sorted(
        path for path in directory.iterdir()
        if path.is_file() and path.suffix.lower() in suffixes
    )


def inspect_input_freshness(
    config: dict[str, Any],
    now: datetime | None = None,
) -> FreshnessResult:
    monitoring = config.get("monitoring", {})
    checked_at = _as_local(now)
    max_age_days = float(monitoring.get("max_age_days", 7))
    required_collectors = [str(item) for item in monitoring.get("required_collectors", [])]
    require_each = bool(monitoring.get("require_each_input_dir", True))

    collector_configs = config.get("collectors", {})
    collector_names = list(dict.fromkeys(
        required_collectors
        + [
            name for name, collector_config in collector_configs.items()
            if collector_config.get("enabled", False) and name in SUPPORTED_SUFFIXES
        ]
    ))
    files: dict[str, list[SnapshotMetadata]] = {}
    selected_files: dict[str, list[SnapshotMetadata]] = {}
    stale_directories: list[str] = []
    reasons: list[str] = []
    warnings: list[str] = []

    for name in collector_names:
        collector_config = collector_configs.get(name, {})
        enabled = bool(collector_config.get("enabled", False))
        directories = _input_dirs(collector_config, config)
        suffixes = SUPPORTED_SUFFIXES.get(name, set())
        discovered: list[SnapshotMetadata] = []
        fresh_by_directory: dict[str, list[SnapshotMetadata]] = {}
        collector_reasons: list[str] = []

        if not enabled:
            message = f"required collector disabled: {name}"
            if name in required_collectors:
                reasons.append(message)
            else:
                warnings.append(message)
        if not directories:
            message = f"no input directory configured: {name}"
            if name in required_collectors:
                reasons.append(message)
            else:
                warnings.append(message)

        for directory in directories:
            directory_key = str(directory)
            paths = _files_in(directory, suffixes)
            metadata = [snapshot_metadata(path, name, checked_at) for path in paths]
            discovered.extend(metadata)
            fresh_by_directory[directory_key] = [
                item for item in metadata if item.age_days <= max_age_days
            ]
            if name in required_collectors and not fresh_by_directory[directory_key]:
                if not directory.exists():
                    collector_reasons.append(f"required input directory missing: {directory_key}")
                elif not directory.is_dir():
                    collector_reasons.append(f"required input path is not a directory: {directory_key}")
                elif not paths:
                    collector_reasons.append(f"no supported input files: {directory_key}")
                else:
                    collector_reasons.append(f"no fresh input file within {max_age_days:g} days: {directory_key}")
                stale_directories.append(directory_key)

        files[name] = sorted(discovered, key=lambda item: item.path)
        selected_files[name] = sorted(
            [item for items in fresh_by_directory.values() for item in items],
            key=lambda item: item.path,
        )

        if name in required_collectors and not require_each:
            if directories and not selected_files[name]:
                reasons.extend(collector_reasons)
                reasons.append(f"no fresh input file for required collector: {name}")
            elif selected_files[name]:
                warnings.extend(
                    f"no fresh input file in optional required directory: {directory}"
                    for directory, items in fresh_by_directory.items()
                    if not items
                )
        elif name in required_collectors:
            reasons.extend(collector_reasons)

        if name not in required_collectors and directories and not discovered:
            warnings.append(f"no optional input files: {name}")

    all_files = [item for items in files.values() for item in items]
    newest = max(all_files, key=lambda item: item.observed_at) if all_files else None
    stale_directories = sorted(set(stale_directories))
    reasons = list(dict.fromkeys(reasons))
    warnings = list(dict.fromkeys(warnings))
    return FreshnessResult(
        status="fresh" if not reasons else "stale",
        checked_at=checked_at,
        max_age_days=max_age_days,
        required_collectors=required_collectors,
        files=files,
        selected_files=selected_files,
        newest=newest,
        stale_directories=stale_directories,
        reasons=reasons,
        warnings=warnings,
    )


def flatten_selected_files(result: FreshnessResult) -> list[SnapshotMetadata]:
    return sorted(
        [item for items in result.selected_files.values() for item in items],
        key=lambda item: (item.collector, item.path),
    )


def input_fingerprint(result: FreshnessResult) -> str:
    stable = [
        {
            "collector": item.collector,
            "path": item.path,
            "observed_at": item.observed_at.isoformat(),
            "sha256": item.sha256,
            "size_bytes": item.size_bytes,
        }
        for item in flatten_selected_files(result)
    ]
    return sha256(json.dumps(stable, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def selected_paths(result: FreshnessResult) -> dict[str, list[str]]:
    return {
        name: [item.path for item in items]
        for name, items in result.selected_files.items()
    }


def write_status(path: str | Path, payload: dict[str, Any]) -> Path:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return output


def write_stale_alert(
    alert_dir: str | Path,
    radar: str,
    result: FreshnessResult,
) -> Path:
    directory = Path(alert_dir)
    directory.mkdir(parents=True, exist_ok=True)
    stamp = result.checked_at.strftime("%Y%m%d_%H%M%S")
    path = directory / f"{radar}_stale_{stamp}.md"
    newest = result.newest
    lines = [
        f"# {radar} radar stale-data alert",
        "",
        "正常周报未生成，原因是必需的本地输入数据已过期或缺失。",
        "",
        f"- Checked at: {result.checked_at.isoformat()}",
        f"- Max age: {result.max_age_days:g} days",
        f"- Newest source: {newest.path if newest else 'none'}",
        f"- Newest observation: {newest.observed_at.isoformat() if newest else 'none'}",
        f"- Newest age: {newest.age_days:g} days" if newest else "- Newest age: unknown",
        "- Stale directories: " + (", ".join(result.stale_directories) or "none"),
        "- Reasons: " + ("; ".join(result.reasons) or "none"),
        "",
        "请先放入新的授权快照或 saved-search 输入，再重新运行雷达。",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
