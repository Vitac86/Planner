"""ViewModel страницы «Календарь» (пока заглушка недельной сетки).

Будущий источник данных — двусторонняя синхронизация с Google Calendar
(см. sync/calendar_contract.py); сейчас сетка строится из того же
фейкового репозитория.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Dict, List

from PySide6.QtCore import Property, QObject, Signal, Slot

from planner_desktop.repositories import TaskRepository
from planner_desktop.repositories.fake_task_repository import FakeTaskRepository

_WEEKDAY_LABELS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]

SYNC_SOURCE_NOTE = (
    "Будущий источник данных: Google Calendar (двусторонняя синхронизация). "
    "В этом скелете показаны фейковые данные."
)


class CalendarViewModel(QObject):
    weekChanged = Signal()
    selectionChanged = Signal()

    def __init__(self, repository: TaskRepository | None = None,
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._repository = repository or FakeTaskRepository()
        today = date.today()
        self._week_start = today - timedelta(days=today.weekday())
        self._selected_index = today.weekday()

    @Property(str, constant=True)
    def syncSourceNote(self) -> str:
        return SYNC_SOURCE_NOTE

    @Property("QVariantList", notify=weekChanged)
    def weekDays(self) -> List[Dict[str, Any]]:
        days = []
        for offset in range(7):
            day = self._week_start + timedelta(days=offset)
            days.append({
                "label": _WEEKDAY_LABELS[offset],
                "dateText": day.strftime("%d.%m"),
                "isToday": day == date.today(),
                "isSelected": offset == self._selected_index,
                "taskCount": len(self._tasks_for(day)),
            })
        return days

    @Property(int, notify=selectionChanged)
    def selectedIndex(self) -> int:
        return self._selected_index

    @Property(str, notify=selectionChanged)
    def selectedDayTitle(self) -> str:
        day = self._week_start + timedelta(days=self._selected_index)
        return f"{_WEEKDAY_LABELS[self._selected_index]}, {day.strftime('%d.%m.%Y')}"

    @Property("QVariantList", notify=selectionChanged)
    def selectedDayTasks(self) -> List[Dict[str, Any]]:
        day = self._week_start + timedelta(days=self._selected_index)
        rows = []
        for task in self._tasks_for(day):
            rows.append({
                "title": task.title,
                "timeLabel": "Весь день" if task.is_all_day
                else (task.start.strftime("%H:%M") if task.start else ""),
            })
        return rows

    @Slot(int)
    def selectDay(self, index: int) -> None:
        if 0 <= index < 7 and index != self._selected_index:
            self._selected_index = index
            self.selectionChanged.emit()
            self.weekChanged.emit()

    def _tasks_for(self, day: date):
        return [
            t for t in self._repository.all()
            if t.start is not None and t.start.date() == day
        ]
