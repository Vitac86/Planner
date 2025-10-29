"""Utility helpers for task priorities."""
from __future__ import annotations

from typing import Dict

# Priority levels are intentionally limited to keep the UI compact and easy to scan.
# 0 — default/no priority, 1 — low, 2 — medium, 3 — high.
PRIORITY_META: Dict[int, Dict[str, str]] = {
    0: {
        "label": "Без приоритета",
        "short": "Без",
        "color": "#64748B",    # slate-500
        "bgcolor": "#E2E8F0",  # slate-200
    },
    1: {
        "label": "Низкий приоритет",
        "short": "Низкий",
        "color": "#0EA5E9",    # sky-500
        "bgcolor": "#E0F2FE",  # sky-100
    },
    2: {
        "label": "Средний приоритет",
        "short": "Средний",
        "color": "#F59E0B",    # amber-500
        "bgcolor": "#FEF3C7",  # amber-100
    },
    3: {
        "label": "Высокий приоритет",
        "short": "Высокий",
        "color": "#EF4444",    # red-500
        "bgcolor": "#FEE2E2",  # red-100
    },
}

DEFAULT_PRIORITY = 0


def normalize_priority(value: int | str | None) -> int:
    """Clamp external values to the supported priority range."""
    if value is None:
        return DEFAULT_PRIORITY
    try:
        ivalue = int(value)
    except (TypeError, ValueError):
        return DEFAULT_PRIORITY
    floor = min(PRIORITY_META.keys())
    ceil = max(PRIORITY_META.keys())
    return max(floor, min(ceil, ivalue))


def priority_label(value: int, *, short: bool = False) -> str:
    meta = PRIORITY_META.get(value, PRIORITY_META[DEFAULT_PRIORITY])
    return meta["short" if short else "label"]


def priority_color(value: int) -> str:
    meta = PRIORITY_META.get(value, PRIORITY_META[DEFAULT_PRIORITY])
    return meta["color"]


def priority_bgcolor(value: int) -> str:
    meta = PRIORITY_META.get(value, PRIORITY_META[DEFAULT_PRIORITY])
    return meta["bgcolor"]


def priority_options() -> Dict[str, str]:
    """Return mapping of dropdown values -> labels."""
    return {str(level): meta["label"] for level, meta in PRIORITY_META.items()}
