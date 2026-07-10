"""Сценарии работы с ежедневными задачами (use-case-слой десктопа).

Логика ежедневного чек-листа: какие пункты появляются на дату (маска дней
недели + отметки выполнения), создание/правка/удаление и переключение
отметки на конкретную дату. Полностью локально: ни сети, ни Google,
ни Calendar-очереди — ежедневные задачи в календарь не уходят.

Валидация здесь же, чтобы её можно было проверить без Qt.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import List, Optional, Tuple

from planner_desktop.domain.daily_task import (
    DailyTask,
    describe_mask,
    normalize_mask,
)
from planner_desktop.repositories.daily_task_repository import DailyTaskRepository

TIME_FORMAT = "%H:%M"

DAILY_NOT_FOUND_ERROR = "Ежедневная задача не найдена."
EMPTY_TITLE_ERROR = "Название ежедневной задачи не может быть пустым."
EMPTY_MASK_ERROR = "Выберите хотя бы один день недели."
BAD_TIME_ERROR = "Время должно быть в формате ЧЧ:ММ (или пустым)."


def _valid_time(text: str) -> bool:
    text = (text or "").strip()
    if not text:
        return True
    try:
        datetime.strptime(text, TIME_FORMAT)
        return True
    except ValueError:
        return False


def validate_daily(title: str, weekdays_mask: object, preferred_time: str) -> List[str]:
    """Человекочитаемые ошибки формы ежедневной задачи; пусто — ввод валиден."""
    errors: List[str] = []
    if not (title or "").strip():
        errors.append(EMPTY_TITLE_ERROR)
    if normalize_mask(weekdays_mask) == 0:
        errors.append(EMPTY_MASK_ERROR)
    if not _valid_time(preferred_time):
        errors.append(BAD_TIME_ERROR)
    return errors


@dataclass
class DailyOperationResult:
    task: Optional[DailyTask] = None
    errors: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.task is not None and not self.errors


@dataclass
class DailyOccurrence:
    """Ежедневная задача, показанная на конкретную дату, + её отметка."""

    task: DailyTask
    done: bool


class DailyTaskService:
    """CRUD ежедневных задач + выдача пунктов на дату и отметки выполнения."""

    def __init__(self, repository: DailyTaskRepository) -> None:
        self.repository = repository

    # ---- CRUD ----------------------------------------------------------------

    def list_all(self) -> List[DailyTask]:
        return self.repository.list_all()

    def get(self, uid: str) -> Optional[DailyTask]:
        task = self.repository.get_by_uid(uid)
        if task is None or task.is_deleted:
            return None
        return task

    def create(
        self,
        title: str,
        *,
        notes: str = "",
        enabled: bool = True,
        weekdays_mask: object = None,
        preferred_time: str = "",
    ) -> DailyOperationResult:
        mask = normalize_mask(weekdays_mask if weekdays_mask is not None else 0b1111111)
        errors = validate_daily(title, mask, preferred_time)
        if errors:
            return DailyOperationResult(errors=errors)
        task = DailyTask(
            title=title.strip(),
            notes=(notes or "").strip(),
            enabled=bool(enabled),
            weekdays_mask=mask,
            preferred_time=(preferred_time or "").strip(),
        )
        return DailyOperationResult(task=self.repository.add(task))

    def edit(
        self,
        uid: str,
        title: str,
        *,
        notes: str = "",
        enabled: bool = True,
        weekdays_mask: object = None,
        preferred_time: str = "",
    ) -> DailyOperationResult:
        task = self.get(uid)
        if task is None:
            return DailyOperationResult(errors=[DAILY_NOT_FOUND_ERROR])
        mask = normalize_mask(weekdays_mask if weekdays_mask is not None else task.weekdays_mask)
        errors = validate_daily(title, mask, preferred_time)
        if errors:
            return DailyOperationResult(errors=errors)
        task.title = title.strip()
        task.notes = (notes or "").strip()
        task.enabled = bool(enabled)
        task.weekdays_mask = mask
        task.preferred_time = (preferred_time or "").strip()
        return DailyOperationResult(task=self.repository.update(task))

    def set_enabled(self, uid: str, enabled: bool) -> Optional[DailyTask]:
        task = self.get(uid)
        if task is None:
            return None
        task.enabled = bool(enabled)
        return self.repository.update(task)

    def delete(self, uid: str) -> bool:
        return self.repository.delete(uid)

    # ---- вхождения на дату + отметки -----------------------------------------

    def occurrences_for(self, day: date) -> List[DailyOccurrence]:
        """Пункты чек-листа на указанную дату, отсортированные по времени
        (пункты без времени — в конце), затем по названию."""
        completed = self.repository.completed_uids_for(day)
        occurring = [t for t in self.repository.list_all() if t.occurs_on(day)]
        occurring.sort(key=_occurrence_sort_key)
        return [DailyOccurrence(task=t, done=t.uid in completed) for t in occurring]

    def is_completed(self, uid: str, day: date) -> bool:
        return self.repository.is_completed(uid, day)

    def set_completed(self, uid: str, day: date, completed: bool) -> bool:
        task = self.get(uid)
        if task is None:
            return False
        self.repository.set_completed(uid, day, completed)
        return True

    def toggle_completed(self, uid: str, day: date) -> Optional[bool]:
        """Инвертирует отметку выполнения на дату; возвращает новое значение
        (или None, если задачи нет)."""
        task = self.get(uid)
        if task is None:
            return None
        new_value = not self.repository.is_completed(uid, day)
        self.repository.set_completed(uid, day, new_value)
        return new_value


def _occurrence_sort_key(task: DailyTask) -> Tuple[int, str, str]:
    # Задачи со временем идут раньше; пустое время — в конец (флаг 1).
    has_time = 0 if task.preferred_time.strip() else 1
    return (has_time, task.preferred_time, task.title.lower())


__all__ = [
    "DailyTaskService",
    "DailyOperationResult",
    "DailyOccurrence",
    "validate_daily",
    "describe_mask",
]
