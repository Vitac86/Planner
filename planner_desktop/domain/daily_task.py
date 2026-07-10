"""Доменная модель ежедневной (повторяющейся) задачи нового десктопа.

Локальная сущность: в Google Calendar не уходит и никогда не порождает
Calendar-операций — это личный чек-лист «делаю по таким-то дням недели».
Отдельна от обычной :class:`~planner_desktop.domain.task.Task`, потому что
у неё нет одной даты: есть маска дней недели и отметки выполнения на
конкретную дату.

Модель — обычный dataclass без Qt/QML. Маска дней недели — 7 бит,
бит ``i`` (0 = понедельник, как у ``date.weekday()``) означает «активна
в этот день недели».
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import List, Optional

from planner_desktop.domain.task import utc_now

# Все семь дней (Пн..Вс) = биты 0..6.
ALL_WEEKDAYS_MASK = 0b1111111  # 127
WEEKDAYS_ONLY_MASK = 0b0011111  # Пн–Пт
WEEKEND_MASK = 0b1100000        # Сб, Вс

_WEEKDAY_SHORT = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]


def normalize_mask(value: object) -> int:
    """Приводит произвольное значение к валидной 7-битной маске дней недели."""
    try:
        mask = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0
    return mask & ALL_WEEKDAYS_MASK


def weekday_in_mask(mask: object, weekday: int) -> bool:
    """Активна ли маска в указанный день недели (0 = понедельник)."""
    if weekday < 0 or weekday > 6:
        return False
    return bool((normalize_mask(mask) >> weekday) & 1)


def mask_weekdays(mask: object) -> List[int]:
    """Список номеров дней недели (0..6), выставленных в маске."""
    normalized = normalize_mask(mask)
    return [i for i in range(7) if (normalized >> i) & 1]


def describe_mask(mask: object) -> str:
    """Человекочитаемое описание маски: «Каждый день» / «Будни» /
    «Выходные» / «Пн, Ср, Пт» / «Никогда» (пустая маска)."""
    normalized = normalize_mask(mask)
    if normalized == 0:
        return "Никогда"
    if normalized == ALL_WEEKDAYS_MASK:
        return "Каждый день"
    if normalized == WEEKDAYS_ONLY_MASK:
        return "По будням"
    if normalized == WEEKEND_MASK:
        return "По выходным"
    return ", ".join(_WEEKDAY_SHORT[i] for i in mask_weekdays(normalized))


@dataclass
class DailyTask:
    """Повторяющийся по дням недели локальный пункт чек-листа."""

    title: str
    id: Optional[int] = None
    uid: str = field(default_factory=lambda: str(uuid.uuid4()))
    notes: str = ""
    enabled: bool = True
    weekdays_mask: int = ALL_WEEKDAYS_MASK
    # Предпочтительное время в формате «ЧЧ:ММ» или пустая строка (без времени).
    preferred_time: str = ""
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    # Тумбстоун: удаление помечается, чтобы отметки выполнения не осиротели
    # мгновенно и список оставался консистентным.
    deleted_at: Optional[datetime] = None

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None

    def occurs_on(self, day: date) -> bool:
        """Должна ли задача появиться в списке на указанную дату."""
        return (
            self.enabled
            and not self.is_deleted
            and weekday_in_mask(self.weekdays_mask, day.weekday())
        )

    def touch(self) -> None:
        self.updated_at = utc_now()

    def mark_deleted(self, when: Optional[datetime] = None) -> None:
        self.deleted_at = when or utc_now()
        self.updated_at = self.deleted_at
