"""Команды и валидация ввода для нового десктопа.

Вся логика Quick Add живёт здесь, в чистом Python: ViewModel лишь
оборачивает её для QML. Благодаря этому правила валидации тестируются
без Qt и без окна.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import List, Optional

from .task import Task

DATE_FORMAT = "%Y-%m-%d"
TIME_FORMAT = "%H:%M"
DEFAULT_DURATION_MINUTES = 60
MAX_DURATION_MINUTES = 24 * 60


@dataclass
class QuickAddCommand:
    """Сырые значения формы Quick Add (строки — как их отдаёт QML)."""

    title: str = ""
    notes: str = ""
    add_to_calendar: bool = False
    is_all_day: bool = False
    date_text: str = ""      # "ГГГГ-ММ-ДД"
    time_text: str = ""      # "ЧЧ:ММ"
    duration_text: str = ""  # минуты, пусто = длительность по умолчанию


@dataclass
class QuickAddResult:
    task: Optional[Task] = None
    errors: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.task is not None and not self.errors


def _parse_date(text: str) -> Optional[date]:
    try:
        return datetime.strptime(text.strip(), DATE_FORMAT).date()
    except ValueError:
        return None


def _parse_time(text: str) -> Optional[time]:
    try:
        return datetime.strptime(text.strip(), TIME_FORMAT).time()
    except ValueError:
        return None


def validate_quick_add(command: QuickAddCommand) -> List[str]:
    """Возвращает список человекочитаемых ошибок; пустой список — ввод валиден."""
    errors: List[str] = []

    if not command.title.strip():
        errors.append("Название задачи не может быть пустым.")

    if command.add_to_calendar:
        parsed_date = _parse_date(command.date_text)
        if parsed_date is None:
            errors.append("Для задачи в календаре нужна дата в формате ГГГГ-ММ-ДД.")
        if not command.is_all_day:
            if _parse_time(command.time_text) is None:
                errors.append(
                    "Для задачи со временем укажите время в формате ЧЧ:ММ "
                    "(или отметьте «Весь день»)."
                )

    duration_text = command.duration_text.strip()
    if duration_text:
        try:
            minutes = int(duration_text)
        except ValueError:
            errors.append("Длительность должна быть целым числом минут.")
        else:
            if minutes <= 0:
                errors.append("Длительность должна быть больше нуля.")
            elif minutes > MAX_DURATION_MINUTES:
                errors.append("Длительность не может превышать 24 часа.")

    return errors


def build_task(command: QuickAddCommand) -> Task:
    """Строит Task из уже провалидированной команды.

    Вызывающий обязан сначала получить пустой validate_quick_add();
    иначе поведение не определено.
    """
    task = Task(title=command.title.strip(), notes=command.notes.strip())

    if not command.add_to_calendar:
        return task  # задача без даты — в телефонный календарь не попадает

    parsed_date = _parse_date(command.date_text)
    assert parsed_date is not None

    if command.is_all_day:
        # Семантика Google Calendar: только дата, конец — эксклюзивный.
        task.is_all_day = True
        task.start = datetime.combine(parsed_date, time.min)
        task.end = task.start + timedelta(days=1)
        return task

    parsed_time = _parse_time(command.time_text)
    assert parsed_time is not None

    duration_text = command.duration_text.strip()
    minutes = int(duration_text) if duration_text else DEFAULT_DURATION_MINUTES
    task.start = datetime.combine(parsed_date, parsed_time)
    task.duration_minutes = minutes
    task.end = task.start + timedelta(minutes=minutes)
    return task


def execute_quick_add(command: QuickAddCommand) -> QuickAddResult:
    """Валидация + сборка задачи одним вызовом; никогда не бросает исключений
    на плохом вводе — ошибки возвращаются списком."""
    errors = validate_quick_add(command)
    if errors:
        return QuickAddResult(errors=errors)
    return QuickAddResult(task=build_task(command))
