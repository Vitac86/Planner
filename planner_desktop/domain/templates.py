"""Доменная модель локальных шаблонов задач (Phase 3.2A).

Шаблон — заготовка для предзаполнения общего редактора: применение
шаблона НИЧЕГО не сохраняет, пока пользователь не нажмёт «Создать».
Ordinary-шаблон порождает обычную задачу, recurring-шаблон — новую
независимую TaskSeries. Google-метаданные в шаблоны не попадают.

Чистый Python без Qt/SQLite/Google.
"""
from __future__ import annotations

import unicodedata
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, time
from typing import List, Optional, Tuple

from planner_desktop.domain.recurrence import (
    RecurrenceRule,
    SeriesSchedule,
    validate_rule,
)
from planner_desktop.domain.task import utc_now

TEMPLATE_KIND_ORDINARY = "ordinary"
TEMPLATE_KIND_RECURRING = "recurring"
VALID_TEMPLATE_KINDS = (TEMPLATE_KIND_ORDINARY, TEMPLATE_KIND_RECURRING)

#: Детерминированные лимиты (см. docs/RECURRENCE_ARCHITECTURE.md).
MAX_TEMPLATE_NAME_LENGTH = 60
MAX_TEMPLATES = 100

#: Дефолт планирования шаблона: none / allday / timed
SCHEDULE_MODE_NONE = "none"
SCHEDULE_MODE_ALL_DAY = "allday"
SCHEDULE_MODE_TIMED = "timed"
VALID_SCHEDULE_MODES = (
    SCHEDULE_MODE_NONE, SCHEDULE_MODE_ALL_DAY, SCHEDULE_MODE_TIMED,
)

EMPTY_NAME_ERROR = "Название шаблона не может быть пустым."
NAME_TOO_LONG_ERROR = (
    f"Название шаблона не может быть длиннее {MAX_TEMPLATE_NAME_LENGTH} символов."
)
NAME_CONFLICT_ERROR = "Шаблон с таким названием уже существует."
TOO_MANY_TEMPLATES_ERROR = (
    f"Достигнут предел {MAX_TEMPLATES} шаблонов — удалите неиспользуемые."
)
UNKNOWN_KIND_ERROR = "Неизвестный тип шаблона."
EMPTY_TITLE_ERROR = "Заголовок задачи в шаблоне не может быть пустым."
RECURRING_RULE_REQUIRED_ERROR = "Для повторяющегося шаблона укажите правило повторения."
RECURRING_SCHEDULE_REQUIRED_ERROR = (
    "Для повторяющегося шаблона выберите «Весь день» или «Со временем»."
)
BAD_TEMPLATE_TIME_ERROR = "Время шаблона должно быть в формате ЧЧ:ММ."


def clean_template_name(name: str) -> str:
    return " ".join(str(name or "").split())


def normalized_template_name(name: str) -> str:
    return unicodedata.normalize("NFKC", clean_template_name(name)).casefold()


@dataclass
class TaskTemplate:
    """Локальный шаблон обычной задачи или повторяющейся серии."""

    name: str
    kind: str = TEMPLATE_KIND_ORDINARY
    id: Optional[int] = None
    uid: str = field(default_factory=lambda: str(uuid.uuid4()))
    title: str = ""
    notes: str = ""
    priority: int = 0
    tags: Tuple[str, ...] = field(default_factory=tuple)
    #: Дефолты планирования: режим + время/длительность (дата подставляется
    #: при применении — «сегодня», сам шаблон дату не хранит).
    schedule_mode: str = SCHEDULE_MODE_NONE
    time_text: str = ""
    duration_minutes: Optional[int] = None
    #: Дефолты правила повторения (только для recurring-шаблона).
    rule: Optional[RecurrenceRule] = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    deleted_at: Optional[datetime] = None

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None

    @property
    def is_recurring(self) -> bool:
        return self.kind == TEMPLATE_KIND_RECURRING

    def touch(self) -> None:
        self.updated_at = utc_now()

    def mark_deleted(self, when: Optional[datetime] = None) -> None:
        self.deleted_at = when or utc_now()
        self.updated_at = self.deleted_at


def validate_template(template: TaskTemplate) -> List[str]:
    """Человекочитаемые ошибки формы шаблона; пусто — валидно."""
    errors: List[str] = []
    if not clean_template_name(template.name):
        errors.append(EMPTY_NAME_ERROR)
    elif len(clean_template_name(template.name)) > MAX_TEMPLATE_NAME_LENGTH:
        errors.append(NAME_TOO_LONG_ERROR)
    if template.kind not in VALID_TEMPLATE_KINDS:
        errors.append(UNKNOWN_KIND_ERROR)
    if not (template.title or "").strip():
        errors.append(EMPTY_TITLE_ERROR)
    if template.schedule_mode not in VALID_SCHEDULE_MODES:
        errors.append("Неизвестный режим планирования шаблона.")
    if template.priority not in (0, 1, 2, 3):
        errors.append("Приоритет шаблона должен быть от 0 до 3.")
    if template.duration_minutes is not None and template.duration_minutes <= 0:
        errors.append("Длительность должна быть больше нуля.")
    parsed_time: Optional[time] = None
    if template.schedule_mode == SCHEDULE_MODE_TIMED:
        try:
            parsed_time = datetime.strptime(template.time_text.strip(), "%H:%M").time()
        except ValueError:
            errors.append(BAD_TEMPLATE_TIME_ERROR)
    if template.kind == TEMPLATE_KIND_RECURRING:
        if template.schedule_mode == SCHEDULE_MODE_NONE:
            errors.append(RECURRING_SCHEDULE_REQUIRED_ERROR)
        if template.rule is None:
            errors.append(RECURRING_RULE_REQUIRED_ERROR)
        elif template.schedule_mode in (SCHEDULE_MODE_ALL_DAY, SCHEDULE_MODE_TIMED):
            schedule = SeriesSchedule(
                start_date=date(2000, 1, 1),
                all_day=template.schedule_mode == SCHEDULE_MODE_ALL_DAY,
                local_time=parsed_time,
                duration_minutes=template.duration_minutes,
                timezone_name="UTC",
            )
            errors.extend(validate_rule(template.rule, schedule).errors)
    return errors


__all__ = [
    "EMPTY_NAME_ERROR",
    "EMPTY_TITLE_ERROR",
    "BAD_TEMPLATE_TIME_ERROR",
    "MAX_TEMPLATES",
    "MAX_TEMPLATE_NAME_LENGTH",
    "NAME_CONFLICT_ERROR",
    "NAME_TOO_LONG_ERROR",
    "RECURRING_RULE_REQUIRED_ERROR",
    "RECURRING_SCHEDULE_REQUIRED_ERROR",
    "SCHEDULE_MODE_ALL_DAY",
    "SCHEDULE_MODE_NONE",
    "SCHEDULE_MODE_TIMED",
    "TEMPLATE_KIND_ORDINARY",
    "TEMPLATE_KIND_RECURRING",
    "TOO_MANY_TEMPLATES_ERROR",
    "TaskTemplate",
    "UNKNOWN_KIND_ERROR",
    "VALID_SCHEDULE_MODES",
    "VALID_TEMPLATE_KINDS",
    "clean_template_name",
    "normalized_template_name",
    "validate_template",
]
