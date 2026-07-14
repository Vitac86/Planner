"""ViewModel for the Phase 2.1 day/week Calendar time grid.

Calendar geometry is calculated in :mod:`planner_desktop.domain.calendar_layout`;
QML receives normalized ratios and never performs overlap calculations.  The
existing agenda, daily checklist, shared task actions, editor, and inspector
contracts remain available alongside the grid.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any, Dict, Iterable, List, Optional

from PySide6.QtCore import Property, Signal, Slot

from planner_desktop.domain.calendar_layout import (
    CalendarEventBlock,
    CalendarGridConfig,
    CalendarLayout,
    layout_calendar_events,
)
from planner_desktop.domain.task import Task
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

FILTER_ALL = "all"
FILTER_ACTIVE = "active"
FILTER_COMPLETED = "completed"
FILTER_DAILY = "daily"
_VALID_FILTERS = (FILTER_ALL, FILTER_ACTIVE, FILTER_COMPLETED, FILTER_DAILY)

DISPLAY_DAY = "day"
DISPLAY_WORK_WEEK = "work_week"
DISPLAY_WEEK = "week"
_VALID_DISPLAY_MODES = (DISPLAY_DAY, DISPLAY_WORK_WEEK, DISPLAY_WEEK)
_DISPLAY_MODE_OPTIONS = (
    {"label": "День", "value": DISPLAY_DAY},
    {"label": "Рабочая неделя", "value": DISPLAY_WORK_WEEK},
    {"label": "Неделя", "value": DISPLAY_WEEK},
)

DEFAULT_VISIBLE_START_HOUR = 6
DEFAULT_VISIBLE_END_HOUR = 23
DEFAULT_WORKDAY_SCROLL_HOUR = 8


def _monday_of(day: date) -> date:
    return day - timedelta(days=day.weekday())


def _format_minute(minute: int) -> str:
    minute = max(0, min(24 * 60, int(minute)))
    return f"{minute // 60:02d}:{minute % 60:02d}"


def _task_end(task: Task) -> datetime:
    start = task.start
    assert start is not None
    end = task.end
    if end is not None:
        if start.tzinfo is None and end.tzinfo is not None:
            end = end.replace(tzinfo=None)
        elif start.tzinfo is not None and end.tzinfo is None:
            end = end.replace(tzinfo=start.tzinfo)
        elif start.tzinfo is not None and end.tzinfo is not None:
            end = end.astimezone(start.tzinfo)
        if end > start:
            return end
    duration = task.duration_minutes
    try:
        duration = int(duration) if duration is not None else 0
    except (TypeError, ValueError, OverflowError):
        duration = 0
    return start + timedelta(minutes=duration if duration > 0 else 15)


class CalendarViewModel(TaskActionsViewModel):
    weekChanged = Signal()
    selectionChanged = Signal()
    filterChanged = Signal()
    dailyMutated = Signal()
    displayModeChanged = Signal()
    gridChanged = Signal()
    editEventRequested = Signal(str)

    def __init__(self, repository: TaskRepository | None = None,
                 parent=None,
                 service: DesktopTaskService | None = None,
                 daily_service: DailyTaskService | None = None,
                 grid_config: CalendarGridConfig | None = None,
                 **kwargs) -> None:
        if service is None:
            service = DesktopTaskService(repository or FakeTaskRepository())
        super().__init__(service, parent, **kwargs)
        self._repository = self._service.repository
        self._daily = daily_service or DailyTaskService(InMemoryDailyTaskRepository())
        self._filter = FILTER_ALL
        self._display_mode = DISPLAY_WEEK
        self._responsive_mode = "normal"
        self._grid_config = grid_config or CalendarGridConfig(
            visible_start_hour=DEFAULT_VISIBLE_START_HOUR,
            visible_end_hour=DEFAULT_VISIBLE_END_HOUR,
        )
        today = self._now().date()
        self._week_start = _monday_of(today)
        self._selected_index = today.weekday()

    def _emit_data_changed(self) -> None:
        self.weekChanged.emit()
        self.selectionChanged.emit()
        self.gridChanged.emit()

    # ---- display mode and visible period ----------------------------------------

    @Property(str, notify=displayModeChanged)
    def displayMode(self) -> str:
        return self._display_mode

    @Property("QVariantList", constant=True)
    def displayModeOptions(self) -> List[Dict[str, str]]:
        return [dict(item) for item in _DISPLAY_MODE_OPTIONS]

    @Slot(str)
    def setDisplayMode(self, mode: str) -> None:
        if mode not in _VALID_DISPLAY_MODES or mode == self._display_mode:
            return
        self._display_mode = mode
        if mode == DISPLAY_WORK_WEEK and self._selected_index > 4:
            self.clearSelection()
            self._selected_index = 4
        self.displayModeChanged.emit()
        self.weekChanged.emit()
        self.selectionChanged.emit()
        self.gridChanged.emit()

    @Slot(str)
    def setResponsiveMode(self, mode: str) -> None:
        """Compact Calendar intentionally defaults to one readable day.

        Expanding the window does not jump the user back to another mode;
        multi-day modes remain directly available from the switch.
        """
        if mode == self._responsive_mode:
            return
        self._responsive_mode = mode
        if mode == "compact" and self._display_mode != DISPLAY_DAY:
            self.setDisplayMode(DISPLAY_DAY)

    @Property(str, notify=weekChanged)
    def periodTitle(self) -> str:
        visible = self._visible_dates()
        first, last = visible[0], visible[-1]
        if self._display_mode == DISPLAY_DAY:
            return f"{_WEEKDAY_LABELS[first.weekday()]}, {first.strftime('%d.%m.%Y')}"
        prefix = "Рабочая неделя" if self._display_mode == DISPLAY_WORK_WEEK else "Неделя"
        return f"{prefix} {first.strftime('%d.%m')} — {last.strftime('%d.%m.%Y')}"

    @Property(str, notify=weekChanged)
    def weekTitle(self) -> str:
        """Backward-compatible header property used by Phase 1 QML/tests."""
        return self.periodTitle

    @Property(str, notify=weekChanged)
    def weekStartText(self) -> str:
        return self._week_start.strftime("%Y-%m-%d")

    @Property(str, notify=weekChanged)
    def weekEndText(self) -> str:
        return (self._week_start + timedelta(days=6)).strftime("%Y-%m-%d")

    @Property(bool, notify=weekChanged)
    def isCurrentWeek(self) -> bool:
        return self._week_start == _monday_of(self._now().date())

    @Property("QVariantList", notify=gridChanged)
    def visibleDates(self) -> List[Dict[str, Any]]:
        today = self._now().date()
        selected = self._selected_day()
        return [
            {
                "dayIndex": index,
                "weekIndex": day.weekday(),
                "dateText": day.strftime("%Y-%m-%d"),
                "shortDate": day.strftime("%d.%m"),
                "label": _WEEKDAY_LABELS[day.weekday()],
                "isToday": day == today,
                "isSelected": day == selected,
                "taskCount": len(self._tasks_for(day)),
            }
            for index, day in enumerate(self._visible_dates())
        ]

    @Property("QVariantList", notify=weekChanged)
    def weekDays(self) -> List[Dict[str, Any]]:
        """The original seven-day strip contract, retained for compatibility."""
        today = self._now().date()
        return [
            {
                "label": _WEEKDAY_LABELS[offset],
                "dateText": day.strftime("%d.%m"),
                "isToday": day == today,
                "isSelected": offset == self._selected_index,
                "taskCount": len(self._tasks_for(day)),
            }
            for offset in range(7)
            for day in (self._week_start + timedelta(days=offset),)
        ]

    # ---- selected day -----------------------------------------------------------

    @Property(int, notify=selectionChanged)
    def selectedIndex(self) -> int:
        return self._selected_index

    @Property(str, notify=selectionChanged)
    def selectedDayTitle(self) -> str:
        day = self._selected_day()
        return f"{_WEEKDAY_LABELS[day.weekday()]}, {day.strftime('%d.%m.%Y')}"

    @Property(str, notify=selectionChanged)
    def selectedDateText(self) -> str:
        return self._selected_day().strftime("%Y-%m-%d")

    @Property("QVariantList", notify=selectionChanged)
    def selectedDayTasks(self) -> List[Dict[str, Any]]:
        if self._filter == FILTER_DAILY:
            return []
        pending = self._service.pending_task_uids()
        tasks = self._tasks_for(self._selected_day())
        if self._filter == FILTER_ACTIVE:
            tasks = [task for task in tasks if not task.completed]
        elif self._filter == FILTER_COMPLETED:
            tasks = [task for task in tasks if task.completed]
        return [task_to_row(task, pending) for task in tasks]

    @Property("QVariantList", notify=selectionChanged)
    def selectedDayDailyTasks(self) -> List[Dict[str, Any]]:
        return [
            {
                "uid": occurrence.task.uid,
                "title": occurrence.task.title,
                "timeLabel": occurrence.task.preferred_time,
                "notes": occurrence.task.notes,
                "done": occurrence.done,
            }
            for occurrence in self._daily.occurrences_for(self._selected_day())
        ]

    @Property(int, notify=selectionChanged)
    def selectedTaskTotal(self) -> int:
        return len(self._tasks_for(self._selected_day()))

    @Property(int, notify=selectionChanged)
    def selectedCompletedCount(self) -> int:
        return sum(task.completed for task in self._tasks_for(self._selected_day()))

    @Property(int, notify=selectionChanged)
    def selectedActiveCount(self) -> int:
        return sum(not task.completed for task in self._tasks_for(self._selected_day()))

    @Property(int, notify=selectionChanged)
    def selectedDailyCount(self) -> int:
        return len(self._daily.occurrences_for(self._selected_day()))

    @Property(str, notify=filterChanged)
    def filterMode(self) -> str:
        return self._filter

    # ---- normalized grid data ---------------------------------------------------

    @Property(int, constant=True)
    def visibleStartHour(self) -> int:
        return self._grid_config.visible_start_hour

    @Property(int, constant=True)
    def visibleEndHour(self) -> int:
        return self._grid_config.visible_end_hour

    def _layout(self) -> CalendarLayout:
        return layout_calendar_events(
            self._repository.all(), self._visible_dates(), self._grid_config)

    def _sync_sets(self) -> tuple[set[str], set[str]]:
        pending = self._service.pending_task_uids()
        dead: set[str] = set()
        queue = self._service.calendar_queue
        if queue is not None:
            dead = {op.task_uid for op in queue.list_terminal_ops()}
        return pending, dead

    def _block_row(self, block: CalendarEventBlock,
                   tasks: Dict[str, Task], pending: set[str],
                   dead: set[str]) -> Dict[str, Any]:
        task = tasks[block.task_uid]
        row = task_to_row(task, pending)
        time_text = "Весь день" if block.all_day else (
            f"{_format_minute(block.start_minute)}–{_format_minute(block.end_minute)}"
        )
        state = "выполнено" if task.completed else "не выполнено"
        row.update({
            "dayIndex": block.day_index,
            "dateText": block.day.strftime("%Y-%m-%d"),
            "startMinute": block.start_minute,
            "endMinute": block.end_minute,
            "topRatio": block.top_ratio,
            "heightRatio": block.height_ratio,
            "overlapColumnIndex": block.overlap_column_index,
            "overlapColumnCount": block.overlap_column_count,
            "clippedAtStart": block.clipped_at_start,
            "clippedAtEnd": block.clipped_at_end,
            "allDay": block.all_day,
            "gridTimeLabel": time_text,
            "hasDeadLetter": task.uid in dead,
            "accessibleDescription": (
                f"{task.title}, {block.day.strftime('%d.%m.%Y')}, "
                f"{time_text}, {state}"
            ),
        })
        return row

    def _grid_rows(self) -> List[Dict[str, Any]]:
        layout = self._layout()
        tasks = {task.uid: task for task in self._repository.all()}
        pending, dead = self._sync_sets()
        today = self._now().date()
        selected = self._selected_day()
        rows: List[Dict[str, Any]] = []
        for column in layout.day_columns:
            rows.append({
                "dayIndex": column.day_index,
                "dateText": column.day.strftime("%Y-%m-%d"),
                "shortDate": column.day.strftime("%d.%m"),
                "label": _WEEKDAY_LABELS[column.day.weekday()],
                "isToday": column.day == today,
                "isSelected": column.day == selected,
                "timedEvents": [
                    self._block_row(block, tasks, pending, dead)
                    for block in column.timed_blocks
                ],
                "allDayEvents": [
                    self._block_row(block, tasks, pending, dead)
                    for block in column.all_day_blocks
                ],
            })
        return rows

    @Property("QVariantList", notify=gridChanged)
    def gridDays(self) -> List[Dict[str, Any]]:
        return self._grid_rows()

    @Property("QVariantList", notify=gridChanged)
    def timedEventBlocksByDate(self) -> List[Dict[str, Any]]:
        return [
            {"dateText": row["dateText"], "events": row["timedEvents"]}
            for row in self._grid_rows()
        ]

    @Property("QVariantList", notify=gridChanged)
    def allDayEventsByDate(self) -> List[Dict[str, Any]]:
        return [
            {"dateText": row["dateText"], "events": row["allDayEvents"]}
            for row in self._grid_rows()
        ]

    @Property("QVariantMap", notify=gridChanged)
    def currentTimeIndicator(self) -> Dict[str, Any]:
        now = self._now()
        dates = self._visible_dates()
        minute = now.hour * 60 + now.minute
        visible = (
            now.date() in dates
            and self._grid_config.visible_start_minute <= minute
            < self._grid_config.visible_end_minute
        )
        index = dates.index(now.date()) if now.date() in dates else -1
        ratio = (
            (minute - self._grid_config.visible_start_minute)
            / self._grid_config.visible_minutes
            if visible else 0.0
        )
        return {
            "visible": visible,
            "dayIndex": index,
            "minute": minute,
            "topRatio": ratio,
            "timeLabel": now.strftime("%H:%M"),
        }

    @Property(int, notify=gridChanged)
    def initialScrollMinute(self) -> int:
        now = self._now()
        if now.date() in self._visible_dates():
            minute = now.hour * 60 + now.minute - 60
        else:
            minute = DEFAULT_WORKDAY_SCROLL_HOUR * 60
        return max(
            self._grid_config.visible_start_minute,
            min(self._grid_config.visible_end_minute - 60, minute),
        )

    # ---- period and day navigation ---------------------------------------------

    @Slot(int)
    def selectDay(self, index: int) -> None:
        visible = self._visible_dates()
        if not 0 <= index < len(visible):
            return
        target = visible[index]
        if target != self._selected_day():
            self.clearSelection()
            self._week_start = _monday_of(target)
            self._selected_index = target.weekday()
            self.selectionChanged.emit()
            self.weekChanged.emit()
            self.gridChanged.emit()

    @Slot(str)
    def selectDate(self, date_text: str) -> None:
        try:
            target = datetime.strptime(date_text, "%Y-%m-%d").date()
        except ValueError:
            return
        if target == self._selected_day():
            return
        self.clearSelection()
        self._week_start = _monday_of(target)
        self._selected_index = target.weekday()
        if self._display_mode == DISPLAY_WORK_WEEK and self._selected_index > 4:
            self._selected_index = 4
        self.selectionChanged.emit()
        self.weekChanged.emit()
        self.gridChanged.emit()

    @Slot()
    def previousPeriod(self) -> None:
        self._shift_period(-1)

    @Slot()
    def nextPeriod(self) -> None:
        self._shift_period(1)

    @Slot()
    def previousWeek(self) -> None:
        self._shift_week(-1)

    @Slot()
    def nextWeek(self) -> None:
        self._shift_week(1)

    @Slot()
    def previousDay(self) -> None:
        self._select_adjacent_day(-1)

    @Slot()
    def nextDay(self) -> None:
        self._select_adjacent_day(1)

    @Slot()
    def goToToday(self) -> None:
        self.clearSelection()
        today = self._now().date()
        self._week_start = _monday_of(today)
        self._selected_index = today.weekday()
        if self._display_mode == DISPLAY_WORK_WEEK and self._selected_index > 4:
            self._selected_index = 4
        self.weekChanged.emit()
        self.selectionChanged.emit()
        self.gridChanged.emit()

    # ---- grid event selection and editor ---------------------------------------

    @Slot(str)
    def selectEvent(self, uid: str) -> None:
        self.selectTask(uid)

    @Slot(str)
    def openEventEditor(self, uid: str) -> None:
        if self._service.get_task(uid) is None:
            return
        self.selectTask(uid)
        self.editEventRequested.emit(uid)

    def _visible_event_uids(self) -> List[str]:
        result: List[str] = []
        for day in self._grid_rows():
            for event in day["allDayEvents"] + day["timedEvents"]:
                if event["uid"] not in result:
                    result.append(event["uid"])
        return result

    @Slot()
    def selectPreviousEvent(self) -> None:
        self._move_event_selection(-1)

    @Slot()
    def selectNextEvent(self) -> None:
        self._move_event_selection(1)

    # ---- existing agenda/daily actions -----------------------------------------

    @Slot(str)
    def setFilter(self, mode: str) -> None:
        if mode in _VALID_FILTERS and mode != self._filter:
            self._filter = mode
            self.filterChanged.emit()
            self.selectionChanged.emit()

    @Slot(str, result=bool)
    def toggleDailyCompleted(self, uid: str) -> bool:
        result = self._daily.toggle_completed(uid, self._selected_day())
        if result is None:
            return False
        self.selectionChanged.emit()
        self.dailyMutated.emit()
        return True

    @Slot()
    def refreshDaily(self) -> None:
        self.selectionChanged.emit()

    @Slot()
    def refreshCurrentTime(self) -> None:
        """Refresh only clock-derived grid data; never scrolls or syncs."""
        self.gridChanged.emit()

    @Slot()
    def refresh(self) -> None:
        """Preserve a live selection, clear a task that disappeared."""
        if self._selected_uid and self._service.get_task(self._selected_uid) is None:
            self._selected_uid = ""
            self.selectedTaskChanged.emit()
        self._emit_data_changed()
        self.selectedTaskChanged.emit()

    # ---- internals --------------------------------------------------------------

    def _visible_dates(self) -> List[date]:
        if self._display_mode == DISPLAY_DAY:
            return [self._selected_day()]
        count = 5 if self._display_mode == DISPLAY_WORK_WEEK else 7
        return [self._week_start + timedelta(days=offset) for offset in range(count)]

    def _shift_period(self, direction: int) -> None:
        if self._display_mode == DISPLAY_DAY:
            self._select_adjacent_day(direction)
        else:
            self._shift_week(direction)

    def _shift_week(self, delta_weeks: int) -> None:
        self.clearSelection()
        self._week_start += timedelta(days=7 * delta_weeks)
        self.weekChanged.emit()
        self.selectionChanged.emit()
        self.gridChanged.emit()

    def _select_adjacent_day(self, delta_days: int) -> None:
        target = self._selected_day() + timedelta(days=delta_days)
        if self._display_mode == DISPLAY_WORK_WEEK:
            if target.weekday() == 5 and delta_days > 0:
                target += timedelta(days=2)
            elif target.weekday() == 6 and delta_days < 0:
                target -= timedelta(days=2)
        self.selectDate(target.strftime("%Y-%m-%d"))

    def _move_event_selection(self, direction: int) -> None:
        uids = self._visible_event_uids()
        if not uids:
            self.clearSelection()
            return
        if self._selected_uid not in uids:
            self.selectTask(uids[0] if direction >= 0 else uids[-1])
            return
        index = uids.index(self._selected_uid)
        index = max(0, min(len(uids) - 1, index + direction))
        self.selectTask(uids[index])

    def _selected_day(self) -> date:
        return self._week_start + timedelta(days=self._selected_index)

    def _task_occurs_on(self, task: Task, day: date) -> bool:
        if task.start is None:
            return False
        if task.is_all_day:
            first = task.start.date()
            exclusive = (
                task.end.date() if task.end is not None
                else first + timedelta(days=1)
            )
            if exclusive <= first:
                exclusive = first + timedelta(days=1)
            return first <= day < exclusive
        start = task.start
        end = _task_end(task)
        day_start = datetime.combine(day, time.min, tzinfo=start.tzinfo)
        return start < day_start + timedelta(days=1) and end > day_start

    def _tasks_for(self, day: date) -> List[Task]:
        tasks = [
            task for task in self._repository.all()
            if self._task_occurs_on(task, day)
        ]
        tasks.sort(key=lambda task: (
            0 if task.is_all_day else 1,
            task.start or datetime.min,
            task.uid,
        ))
        return tasks

    @property
    def repository(self) -> TaskRepository:
        return self._repository


__all__ = [
    "CalendarViewModel",
    "DEFAULT_VISIBLE_END_HOUR",
    "DEFAULT_VISIBLE_START_HOUR",
    "DISPLAY_DAY",
    "DISPLAY_WEEK",
    "DISPLAY_WORK_WEEK",
    "FILTER_ACTIVE",
    "FILTER_ALL",
    "FILTER_COMPLETED",
    "FILTER_DAILY",
]
