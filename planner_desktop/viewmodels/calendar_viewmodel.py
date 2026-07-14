"""ViewModel страницы «Календарь»: недельная навигация + список дня.

Данные — тот же репозиторий, что и у «Сегодня» (через общий
DesktopTaskService); общие действия над задачами (редактор, удаление,
снуз, выбор задачи, busy-защита) — в базе TaskActionsViewModel, поэтому
поведение на всех страницах одинаковое.

Сигналы:

- weekChanged/selectionChanged — QML перечитывает сетку и список дня;
- tasksMutated (база) — эта ViewModel изменила задачи; MainWindow дёргает
  refresh() остальных ViewModel-ей;
- toastMessage/toastError (база) — всплывашки успеха/ошибки.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Dict, List

from PySide6.QtCore import Property, Signal, Slot

from planner_desktop.repositories import TaskRepository
from planner_desktop.repositories.daily_task_repository import (
    InMemoryDailyTaskRepository,
)
from planner_desktop.repositories.fake_task_repository import FakeTaskRepository
from planner_desktop.usecases.daily_task_service import DailyTaskService
from planner_desktop.usecases.task_service import DesktopTaskService
from planner_desktop.viewmodels.task_actions import TaskActionsViewModel
from planner_desktop.viewmodels.task_rows import task_to_row

_WEEKDAY_LABELS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]

# Режимы фильтра агенды выбранного дня.
FILTER_ALL = "all"
FILTER_ACTIVE = "active"
FILTER_COMPLETED = "completed"
FILTER_DAILY = "daily"
_VALID_FILTERS = (FILTER_ALL, FILTER_ACTIVE, FILTER_COMPLETED, FILTER_DAILY)


def _monday_of(day: date) -> date:
    return day - timedelta(days=day.weekday())


class CalendarViewModel(TaskActionsViewModel):
    weekChanged = Signal()
    selectionChanged = Signal()
    filterChanged = Signal()
    dailyMutated = Signal()

    def __init__(self, repository: TaskRepository | None = None,
                 parent=None,
                 service: DesktopTaskService | None = None,
                 daily_service: DailyTaskService | None = None,
                 **kwargs) -> None:
        if service is None:
            service = DesktopTaskService(repository or FakeTaskRepository())
        super().__init__(service, parent, **kwargs)
        self._repository = self._service.repository
        self._daily = daily_service or DailyTaskService(InMemoryDailyTaskRepository())
        self._filter = FILTER_ALL
        today = date.today()
        self._week_start = _monday_of(today)
        self._selected_index = today.weekday()

    def _emit_data_changed(self) -> None:
        self.weekChanged.emit()
        self.selectionChanged.emit()

    # ---- свойства недели ------------------------------------------------------

    @Property(str, notify=weekChanged)
    def weekTitle(self) -> str:
        week_end = self._week_start + timedelta(days=6)
        return (
            f"Неделя {self._week_start.strftime('%d.%m')} — "
            f"{week_end.strftime('%d.%m.%Y')}"
        )

    @Property(bool, notify=weekChanged)
    def isCurrentWeek(self) -> bool:
        return self._week_start == _monday_of(date.today())

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

    # ---- выбранный день --------------------------------------------------------

    @Property(int, notify=selectionChanged)
    def selectedIndex(self) -> int:
        return self._selected_index

    @Property(str, notify=selectionChanged)
    def selectedDayTitle(self) -> str:
        day = self._selected_day()
        return f"{_WEEKDAY_LABELS[self._selected_index]}, {day.strftime('%d.%m.%Y')}"

    @Property(str, notify=selectionChanged)
    def selectedDateText(self) -> str:
        """Дата выбранного дня в формате формы редактора (ГГГГ-ММ-ДД)."""
        return self._selected_day().strftime("%Y-%m-%d")

    @Property("QVariantList", notify=selectionChanged)
    def selectedDayTasks(self) -> List[Dict[str, Any]]:
        """Задачи выбранного дня с учётом фильтра (all/active/completed).
        В режиме «daily» агенда задач пуста — QML показывает чек-лист."""
        if self._filter == FILTER_DAILY:
            return []
        pending = self._service.pending_task_uids()
        tasks = self._tasks_for(self._selected_day())
        if self._filter == FILTER_ACTIVE:
            tasks = [t for t in tasks if not t.completed]
        elif self._filter == FILTER_COMPLETED:
            tasks = [t for t in tasks if t.completed]
        return [task_to_row(t, pending) for t in tasks]

    @Property("QVariantList", notify=selectionChanged)
    def selectedDayDailyTasks(self) -> List[Dict[str, Any]]:
        """Пункты ежедневного чек-листа на выбранный день с отметкой."""
        return [
            {
                "uid": occ.task.uid,
                "title": occ.task.title,
                "timeLabel": occ.task.preferred_time,
                "notes": occ.task.notes,
                "done": occ.done,
            }
            for occ in self._daily.occurrences_for(self._selected_day())
        ]

    # ---- сводка выбранного дня (не зависит от фильтра) --------------------------

    @Property(int, notify=selectionChanged)
    def selectedTaskTotal(self) -> int:
        return len(self._tasks_for(self._selected_day()))

    @Property(int, notify=selectionChanged)
    def selectedCompletedCount(self) -> int:
        return sum(1 for t in self._tasks_for(self._selected_day()) if t.completed)

    @Property(int, notify=selectionChanged)
    def selectedActiveCount(self) -> int:
        return sum(1 for t in self._tasks_for(self._selected_day()) if not t.completed)

    @Property(int, notify=selectionChanged)
    def selectedDailyCount(self) -> int:
        return len(self._daily.occurrences_for(self._selected_day()))

    @Property(str, notify=filterChanged)
    def filterMode(self) -> str:
        return self._filter

    # ---- навигация --------------------------------------------------------------

    @Slot(int)
    def selectDay(self, index: int) -> None:
        if 0 <= index < 7 and index != self._selected_index:
            self.clearSelection()
            self._selected_index = index
            self.selectionChanged.emit()
            self.weekChanged.emit()

    @Slot()
    def previousWeek(self) -> None:
        self._shift_week(-1)

    @Slot()
    def nextWeek(self) -> None:
        self._shift_week(1)

    @Slot()
    def goToToday(self) -> None:
        self.clearSelection()
        today = date.today()
        self._week_start = _monday_of(today)
        self._selected_index = today.weekday()
        self.weekChanged.emit()
        self.selectionChanged.emit()

    @Slot(str)
    def setFilter(self, mode: str) -> None:
        if mode in _VALID_FILTERS and mode != self._filter:
            self._filter = mode
            self.filterChanged.emit()
            self.selectionChanged.emit()

    @Slot(str, result=bool)
    def toggleDailyCompleted(self, uid: str) -> bool:
        """Отметить/снять выполнение ежедневной задачи на ВЫБРАННЫЙ день
        (не обязательно сегодня)."""
        result = self._daily.toggle_completed(uid, self._selected_day())
        if result is None:
            return False
        self.selectionChanged.emit()
        self.dailyMutated.emit()
        return True

    @Slot()
    def refreshDaily(self) -> None:
        """Перечитать ежедневный чек-лист (после мутаций на других страницах)."""
        self.selectionChanged.emit()

    # ---- внутреннее ---------------------------------------------------------------

    def _shift_week(self, delta_weeks: int) -> None:
        self.clearSelection()
        self._week_start += timedelta(days=7 * delta_weeks)
        self.weekChanged.emit()
        self.selectionChanged.emit()

    def _selected_day(self) -> date:
        return self._week_start + timedelta(days=self._selected_index)

    def _tasks_for(self, day: date) -> List:
        """Задачи выбранного дня, отсортированные для агенды: сначала «весь
        день», затем по времени начала."""
        tasks = [
            t for t in self._repository.all()
            if t.start is not None and t.start.date() == day
        ]
        tasks.sort(key=lambda t: (0 if t.is_all_day else 1, t.start))
        return tasks

    @property
    def repository(self) -> TaskRepository:
        return self._repository
