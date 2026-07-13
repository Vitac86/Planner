"""Команды и валидация ввода для нового десктопа.

Вся логика Quick Add и диалога редактирования живёт здесь, в чистом
Python: ViewModel лишь оборачивает её для QML. Благодаря этому правила
валидации тестируются без Qt и без окна.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import List, Optional, Tuple

from .task import Task

DATE_FORMAT = "%Y-%m-%d"
TIME_FORMAT = "%H:%M"
DEFAULT_DURATION_MINUTES = 60
MAX_DURATION_MINUTES = 24 * 60

# Приоритеты — те же уровни и подписи, что в старом приложении
# (core/priorities.py): 0 — без приоритета, 1 — низкий, 2 — средний, 3 — высокий.
PRIORITY_LABELS = {
    0: "Без приоритета",
    1: "Низкий",
    2: "Средний",
    3: "Высокий",
}
MAX_PRIORITY = max(PRIORITY_LABELS)


def normalize_priority(value: object) -> int:
    """Приводит произвольное значение к диапазону приоритетов 0..3."""
    try:
        priority = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0
    return max(0, min(MAX_PRIORITY, priority))


def priority_label(priority: int) -> str:
    return PRIORITY_LABELS.get(normalize_priority(priority), PRIORITY_LABELS[0])


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


# ---- диалог редактирования (создание и правка одной формой) --------------------

@dataclass
class TaskEditorCommand(QuickAddCommand):
    """Сырые значения формы TaskEditorDialog.

    Наследует поля Quick Add (title/notes/add_to_calendar/is_all_day/
    date_text/time_text/duration_text); add_to_calendar здесь означает
    «задача запланирована» (есть дата, попадает в календарную очередь).
    """

    priority: int = 0
    completed: bool = False


def validate_editor(command: TaskEditorCommand) -> List[str]:
    """Правила формы редактора совпадают с Quick Add; приоритет и галочка
    «выполнено» приходят из ComboBox/CheckBox и невалидными быть не могут
    (приоритет на всякий случай зажимается в 0..3)."""
    return validate_quick_add(command)


def schedule_from_command(
    command: TaskEditorCommand,
) -> Tuple[Optional[datetime], Optional[datetime], Optional[int], bool]:
    """(start, end, duration_minutes, is_all_day) из провалидированной команды.

    Для незапланированной задачи все поля расписания пустые.
    Вызывать только после пустого validate_editor().
    """
    if not command.add_to_calendar:
        return None, None, None, False

    parsed_date = _parse_date(command.date_text)
    assert parsed_date is not None

    if command.is_all_day:
        start = datetime.combine(parsed_date, time.min)
        return start, start + timedelta(days=1), None, True

    parsed_time = _parse_time(command.time_text)
    assert parsed_time is not None
    duration_text = command.duration_text.strip()
    minutes = int(duration_text) if duration_text else DEFAULT_DURATION_MINUTES
    start = datetime.combine(parsed_date, parsed_time)
    return start, start + timedelta(minutes=minutes), minutes, False


def build_task_from_editor(command: TaskEditorCommand) -> Task:
    """Новая задача из формы редактора (создание). Команда уже провалидирована."""
    task = Task(title=command.title.strip(), notes=command.notes.strip())
    task.priority = normalize_priority(command.priority)
    task.set_completed(command.completed)
    start, end, duration, is_all_day = schedule_from_command(command)
    task.start = start
    task.end = end
    task.duration_minutes = duration
    task.is_all_day = is_all_day
    return task


def apply_editor_fields(command: TaskEditorCommand, task: Task) -> None:
    """Накатывает на задачу текстовые поля формы (без расписания).

    Расписание (start/end/duration/is_all_day) — отдельная ответственность
    сервиса: переходы «запланирована ↔ без даты» требуют работы с
    Calendar-очередью и не решаются в домене.
    """
    task.title = command.title.strip()
    task.notes = command.notes.strip()
    task.priority = normalize_priority(command.priority)
    task.set_completed(command.completed)
