"""Simple JSON-backed configuration store."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from core.settings import CONFIG_PATH


@dataclass
class AppConfig:
    """Lightweight configuration persisted to ``config.json``."""

    default_list_id: Optional[str] = None
    last_used_list_id: Optional[str] = None


def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _load_raw(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def load_config(path: Optional[Path] = None) -> AppConfig:
    target = path or CONFIG_PATH
    data = _load_raw(target)
    return AppConfig(
        default_list_id=data.get("default_list_id"),
        last_used_list_id=data.get("last_used_list_id"),
    )


def save_config(config: AppConfig, path: Optional[Path] = None) -> None:
    target = path or CONFIG_PATH
    _ensure_parent(target)
    payload = json.dumps(asdict(config), ensure_ascii=False, indent=2, sort_keys=True)
    tmp = target.with_suffix(".tmp")
    try:
        tmp.write_text(payload, encoding="utf-8")
        tmp.replace(target)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def update_config(path: Optional[Path] = None, **changes: Any) -> AppConfig:
    target = path or CONFIG_PATH
    cfg = load_config(target)
    for key, value in changes.items():
        if hasattr(cfg, key):
            setattr(cfg, key, value)
    save_config(cfg, target)
    return cfg


__all__ = ["AppConfig", "load_config", "save_config", "update_config"]

