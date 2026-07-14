"""ViewModel страницы «История».

Локальный журнал выполненного (разовые задачи + отметки ежедневных),
сгруппированный по датам, с фильтром диапазона (7 дней / 30 дней / всё).
Безопасное действие «вернуть в работу» доступно только разовым задачам;
ежедневные отметки — только просмотр. Деструктивных массовых действий нет.

Общий контракт действий (editorDataFor/saveEditor/editorError, deleteTask
с подтверждением в QML, restoreTask, busy-защита, тосты) — в базе
TaskActionsViewModel, поэтому «Подробнее» открывает общий TaskEditorDialog,
а удаление ведёт себя как на «Сегодня». Реопен и правки эмитят tasksMutated —
MainWindow освежает остальные страницы.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional

from PySide6.QtCore import Property, Signal, Slot

from planner_desktop.domain.commands import priority_label
from planner_desktop.usecases.daily_task_service import DailyTaskService
from planner_desktop.usecases.history_service import (
    RANGE_7_DAYS,
    VALID_RANGES,
    HistoryEntry,
    HistoryService,
)
from planner_desktop.usecases.task_service import DesktopTaskService
from planner_desktop.viewmodels.task_actions import TaskActionsViewModel

_MONTHS_GENITIVE = [
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]
_WEEKDAYS = [
    "понедельник", "вторник", "среда", "четверг",
    "пятница", "суббота", "воскресенье",
]


def _relative_label(day: date, today: date) -> str:
    delta = (today - day).days
    if delta == 0:
        return "Сегодня"
    if delta == 1:
        return "Вчера"
    return ""


def _date_label(day: date) -> str:
    return (
        f"{day.day} {_MONTHS_GENITIVE[day.month - 1]} {day.year}, "
        f"{_WEEKDAYS[day.weekday()]}"
    )


def _entry_to_row(entry: HistoryEntry) -> Dict[str, Any]:
    return {
        "kind": entry.kind,
        "uid": entry.uid,
        "title": entry.title,
        "notes": entry.notes,
        "timeLabel": entry.time_label,
        "isAllDay": entry.is_all_day,
        "priority": entry.priority,
        "priorityLabel": priority_label(entry.priority),
        "isDaily": entry.is_daily,
        "canReopen": entry.can_reopen,
        "doneAt": entry.completed_at.astimezone().strftime("%H:%M")
        if entry.completed_at is not None and entry.completed_at.tzinfo is not None
        else (entry.completed_at.strftime("%H:%M")
              if entry.completed_at is not None else ""),
    }


class HistoryViewModel(TaskActionsViewModel):
    historyChanged = Signal()
    rangeChanged = Signal()

    def __init__(
        self,
        service: DesktopTaskService,
        daily_service: DailyTaskService,
        parent=None,
        history_service: Optional[HistoryService] = None,
        **kwargs,
    ) -> None:
        super().__init__(service, parent, **kwargs)
        self._daily = daily_service
        self._history = history_service or HistoryService(
            service.repository, daily_service.repository
        )
        self._range_days = RANGE_7_DAYS

    def _emit_data_changed(self) -> None:
        self.historyChanged.emit()

    def _visible_task_uids(self) -> List[str]:
        return [
            entry.uid
            for group in self._groups()
            for entry in group.entries
            if not entry.is_daily
        ]

    # ---- свойства для QML -------------------------------------------------------

    @Property(int, notify=rangeChanged)
    def rangeDays(self) -> int:
        return self._range_days

    def _groups(self) -> List:
        return self._history.groups(range_days=self._range_days, today=date.today())

    @Property("QVariantList", notify=historyChanged)
    def groups(self) -> List[Dict[str, Any]]:
        today = date.today()
        result: List[Dict[str, Any]] = []
        for group in self._groups():
            rows = []
            for entry in group.entries:
                row = _entry_to_row(entry)
                task = None if entry.is_daily else self._service.get_task(entry.uid)
                row["tags"] = list(task.tags[:3]) if task is not None else []
                row["tagOverflow"] = max(0, len(task.tags) - 3) if task is not None else 0
                rows.append(row)
            result.append({
                "dateISO": group.day.isoformat(),
                "dateLabel": _date_label(group.day),
                "relLabel": _relative_label(group.day, today),
                "count": group.count,
                "entries": rows,
            })
        return result

    @Property(int, notify=historyChanged)
    def totalCount(self) -> int:
        return sum(g.count for g in self._groups())

    @Property(bool, notify=historyChanged)
    def isEmpty(self) -> bool:
        return self.totalCount == 0

    # ---- слоты ------------------------------------------------------------------

    @Slot(int)
    def setRange(self, days: int) -> None:
        if days not in VALID_RANGES:
            return
        if days != self._range_days:
            self._range_days = days
            self.rangeChanged.emit()
            self.historyChanged.emit()
            self._prune_selection()

    @Slot(str, result=bool)
    def reopenTask(self, uid: str) -> bool:
        """Вернуть разовую задачу в работу (снять отметку выполнения).
        Ежедневные отметки так не трогаем — они только для просмотра.
        Синоним restoreTask из базы, сохранён для обратной совместимости."""
        return self.restoreTask(uid)
