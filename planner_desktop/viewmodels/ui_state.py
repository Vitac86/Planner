"""UiStateViewModel — чистые UI-правила для QML (без данных задач).

Единая точка, откуда QML берёт:

- режим раскладки по ширине (domain/layout.py) и минимальный размер окна;
- политику клавиатурных сокращений (domain/keyboard.py);
- человекочитаемые русские подписи дат для DatePickerField;
- варианты времени для списка TimePickerField.

Ни репозиториев, ни сети, ни состояния задач: только детерминированные
функции, поэтому класс тестируется без QApplication и окна.
"""
from __future__ import annotations

from datetime import datetime
from typing import List

from PySide6.QtCore import Property, QObject, Slot

from planner_desktop.domain import keyboard, layout

_MONTHS_GENITIVE = [
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]
_WEEKDAYS_SHORT = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]

#: Шаг списка выбора времени (минуты).
TIME_OPTION_STEP_MINUTES = 30


def human_date(date_text: str) -> str:
    """«2026-07-14» → «вт, 14 июля 2026»; пустая/битая строка → ""."""
    try:
        day = datetime.strptime(date_text.strip(), "%Y-%m-%d").date()
    except ValueError:
        return ""
    return (
        f"{_WEEKDAYS_SHORT[day.weekday()]}, {day.day} "
        f"{_MONTHS_GENITIVE[day.month - 1]} {day.year}"
    )


def time_options(step_minutes: int = TIME_OPTION_STEP_MINUTES) -> List[str]:
    """Варианты «ЧЧ:ММ» для выпадающего списка времени (00:00…23:30)."""
    total = 24 * 60
    return [
        f"{m // 60:02d}:{m % 60:02d}" for m in range(0, total, step_minutes)
    ]


class UiStateViewModel(QObject):
    """Контекст-свойство uiVm в QML."""

    # ---- раскладка -----------------------------------------------------------

    @Slot(float, result=str)
    def layoutModeFor(self, content_width: float) -> str:
        return layout.layout_mode(content_width)

    @Slot(str, result=str)
    def inspectorPlacement(self, mode: str) -> str:
        return layout.inspector_placement(mode)

    @Property(int, constant=True)
    def minWindowWidth(self) -> int:
        return layout.MIN_WINDOW_WIDTH

    @Property(int, constant=True)
    def minWindowHeight(self) -> int:
        return layout.MIN_WINDOW_HEIGHT

    # ---- клавиатура -----------------------------------------------------------

    @Slot(str, bool, bool, result=bool)
    def allowShortcut(self, name: str, typing: bool, dialog_open: bool) -> bool:
        return keyboard.allow_shortcut(
            name, typing=typing, dialog_open=dialog_open)

    # ---- подписи и списки для пикеров -------------------------------------------

    @Slot(str, result=str)
    def humanDate(self, date_text: str) -> str:
        return human_date(date_text)

    @Property("QVariantList", constant=True)
    def timeOptions(self) -> List[str]:
        return time_options()
