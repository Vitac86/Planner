"""Helpers for generating and storing a stable device identifier."""
from __future__ import annotations

import os
import uuid
from pathlib import Path

from core.settings import DATA_DIR


_DEVICE_ID_PATH = DATA_DIR / "device_id.txt"


def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _read_existing(path: Path) -> str | None:
    try:
        if path.exists():
            value = path.read_text(encoding="utf-8").strip()
            if value:
                return value
    except OSError:
        return None
    return None


def _write_value(path: Path, value: str) -> None:
    tmp = path.with_suffix(".tmp")
    _ensure_parent(path)
    try:
        tmp.write_text(value, encoding="utf-8")
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def get_device_id() -> str:
    """Return a deterministic identifier for the current installation."""

    existing = _read_existing(_DEVICE_ID_PATH)
    if existing:
        return existing

    new_id = uuid.uuid4().hex.upper()
    try:
        _write_value(_DEVICE_ID_PATH, new_id)
    except OSError:
        # Best effort: even if we fail to persist, still return the value so the
        # caller can continue working. A new identifier will be generated next
        # time.
        return new_id
    return new_id


__all__ = ["get_device_id"]

