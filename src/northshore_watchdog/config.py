from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(merged.get(key), dict) and isinstance(value, dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _extends_path(value: str | Path, *, config_path: Path) -> Path:
    candidate = Path(value)
    if candidate.is_absolute():
        return candidate
    project_candidate = project_root() / candidate
    if project_candidate.exists():
        return project_candidate
    return config_path.parent / candidate


def _load_config_file(config_path: Path, seen: set[Path]) -> dict[str, Any]:
    resolved = config_path.resolve()
    if resolved in seen:
        raise ValueError(f"config extends cycle detected at {resolved}")
    seen.add(resolved)
    with resolved.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    if not isinstance(raw, dict):
        raise ValueError("config root must be a mapping")
    parent = raw.pop("extends", None)
    if parent:
        inherited = _load_config_file(
            _extends_path(parent, config_path=resolved),
            seen,
        )
        raw = _deep_merge(inherited, raw)
    raw["_config_path"] = str(resolved)
    raw["_project_root"] = str(project_root())
    return raw


def load_config(path: str | Path = "config/default.yaml") -> dict[str, Any]:
    load_dotenv()
    config_path = Path(path)
    if not config_path.is_absolute():
        config_path = project_root() / config_path
    return _load_config_file(config_path, set())


def resolve_path(path: str | Path, config: dict[str, Any] | None = None) -> Path:
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate
    root = Path(config.get("_project_root", project_root())) if config else project_root()
    return root / candidate
