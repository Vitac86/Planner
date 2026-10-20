"""Validated local preferences; no database, task data or network access."""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

from planner_desktop.domain.layout import MIN_WINDOW_HEIGHT, MIN_WINDOW_WIDTH


class PreferencesError(ValueError):
    """A preference is invalid or could not be saved."""


@dataclass(frozen=True)
class Preferences:
    show_completion_stats: bool = True
    startup_page: str = "today"
    last_page_index: int = 0
    calendar_mode: str = "week"
    calendar_filter: str = "all"
    work_start_hour: int = 6
    work_end_hour: int = 23
    default_duration_minutes: int = 60
    window_width: int = 1240
    window_height: int = 800


_CHOICES = {
    "startup_page": {"today", "calendar", "last"},
    "calendar_mode": {"day", "work_week", "week"},
    "calendar_filter": {"all", "active", "completed", "daily"},
}
_RANGES = {
    "last_page_index": (0, 3),
    "work_start_hour": (0, 23),
    "work_end_hour": (1, 24),
    "default_duration_minutes": (5, 1440),
    "window_width": (MIN_WINDOW_WIDTH, 7680),
    "window_height": (MIN_WINDOW_HEIGHT, 4320),
}
_LABELS = {
    "show_completion_stats": "Показ статистики выполнения",
    "startup_page": "Стартовая страница",
    "last_page_index": "Последняя страница",
    "calendar_mode": "Вид календаря",
    "calendar_filter": "Фильтр календаря",
    "work_start_hour": "Начало рабочего дня",
    "work_end_hour": "Конец рабочего дня",
    "default_duration_minutes": "Длительность новой задачи",
    "window_width": "Ширина окна",
    "window_height": "Высота окна",
}


def _validate_value(name: str, value: Any) -> None:
    if name not in _LABELS:
        raise PreferencesError("Неизвестная настройка.")
    label = _LABELS[name]
    if name == "show_completion_stats":
        if type(value) is not bool:
            raise PreferencesError(f"{label}: выберите включено или выключено.")
    elif name in _CHOICES:
        if not isinstance(value, str) or value not in _CHOICES[name]:
            raise PreferencesError(f"{label}: недопустимое значение.")
    else:
        lower, upper = _RANGES[name]
        if type(value) is not int or not lower <= value <= upper:
            raise PreferencesError(f"{label}: введите целое число от {lower} до {upper}.")


class PreferencesStore:
    """None path is explicitly in-memory, for demo mode and injected tests.

    The caller chooses a path beside its own profile database. Reading never
    creates a directory; saving uses a temporary sibling and atomic replacement.
    A failed write leaves both the previous file and in-memory values intact.
    """

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path is not None else None
        self.settings = Preferences()
        self.load_error = ""
        if self.path is not None:
            self._load()

    def _load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("Preferences must be an object")
            if type(raw.get("version", 1)) is not int or raw.get("version", 1) != 1:
                raise ValueError("Unsupported preferences version")
        except FileNotFoundError:
            return
        except (OSError, ValueError, UnicodeError):
            self.load_error = (
                "Не удалось прочитать настройки. Используются стандартные значения; "
                "задачи не затронуты."
            )
            return
        valid, invalid = {}, []
        for name in _LABELS:
            if name not in raw:
                continue
            try:
                _validate_value(name, raw[name])
            except PreferencesError:
                invalid.append(_LABELS[name])
            else:
                valid[name] = raw[name]
        candidate = replace(self.settings, **valid)
        if candidate.work_start_hour >= candidate.work_end_hour:
            candidate = replace(candidate, work_start_hour=6, work_end_hour=23)
            invalid.append("Рабочие часы")
        self.settings = candidate
        if invalid:
            self.load_error = "Восстановлены стандартные значения: " + ", ".join(invalid) + "."

    def update(self, **changes: Any) -> bool:
        for name, value in changes.items():
            _validate_value(name, value)
        candidate = replace(self.settings, **changes)
        if candidate.work_start_hour >= candidate.work_end_hour:
            raise PreferencesError("Конец рабочего дня должен быть позже начала.")
        if candidate == self.settings:
            return False
        if self.path is not None:
            self._write(candidate)
        self.settings = candidate
        self.load_error = ""
        return True

    def _write(self, settings: Preferences) -> None:
        temporary: str | None = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self.path.parent,
                prefix=self.path.name + ".", suffix=".tmp", delete=False,
            ) as stream:
                temporary = stream.name
                json.dump({"version": 1, **asdict(settings)}, stream,
                          ensure_ascii=False, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        except OSError as exc:
            raise PreferencesError(f"Не удалось сохранить настройки: {exc}") from exc
        finally:
            if temporary is not None:
                try:
                    os.unlink(temporary)
                except FileNotFoundError:
                    pass
                except OSError:
                    pass
