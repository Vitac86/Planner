"""ViewModel страницы «История».

Локальный журнал выполненного (разовые задачи + отметки ежедневных),
сгруппированный по датам, с фильтром диапазона (7 дней / 30 дней / всё).
Безопасное действие «вернуть в работу» доступно только разовым задачам;
ежедневные отметки — только просмотр. Деструктивных массовых действий нет.

Тот же контракт редактора (editorDataFor/saveEditor/editorError/
clearEditorError), что у Today/Calendar, чтобы «Подробнее» открывало общий
TaskEditorDialog. Реопен и правки эмитят tasksMutated — MainWindow освежает
остальные страницы.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional

from PySide6.QtCore import Property, QObject, Signal, Slot

from planner_desktop.domain.commands import priority_label
from planner_desktop.usecases.daily_task_service import DailyTaskService
from planner_desktop.usecases.history_service import (
    RANGE_7_DAYS,
    VALID_RANGES,
    HistoryEntry,
    HistoryService,
)
from planner_desktop.usecases.task_service import DesktopTaskService
from planner_desktop.viewmodels.task_rows import editor_payload, save_editor

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


class HistoryViewModel(QObject):
    historyChanged = Signal()
    rangeChanged = Signal()
    editorErrorChanged = Signal()
    toastMessage = Signal(str)
    tasksMutated = Signal()

    def __init__(
        self,
        service: DesktopTaskService,
        daily_service: DailyTaskService,
        parent: QObject | None = None,
        history_service: Optional[HistoryService] = None,
    ) -> None:
        super().__init__(parent)
        self._service = service
        self._daily = daily_service
        self._history = history_service or HistoryService(
            service.repository, daily_service.repository
        )
        self._range_days = RANGE_7_DAYS
        self._editor_error = ""

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
            result.append({
                "dateISO": group.day.isoformat(),
                "dateLabel": _date_label(group.day),
                "relLabel": _relative_label(group.day, today),
                "count": group.count,
                "entries": [_entry_to_row(e) for e in group.entries],
            })
        return result

    @Property(int, notify=historyChanged)
    def totalCount(self) -> int:
        return sum(g.count for g in self._groups())

    @Property(bool, notify=historyChanged)
    def isEmpty(self) -> bool:
        return self.totalCount == 0

    @Property(str, notify=editorErrorChanged)
    def editorError(self) -> str:
        return self._editor_error

    # ---- слоты ------------------------------------------------------------------

    @Slot(int)
    def setRange(self, days: int) -> None:
        if days not in VALID_RANGES:
            return
        if days != self._range_days:
            self._range_days = days
            self.rangeChanged.emit()
            self.historyChanged.emit()

    @Slot(str, result=bool)
    def reopenTask(self, uid: str) -> bool:
        """Вернуть разовую задачу в работу (снять отметку выполнения).
        Ежедневные отметки так не трогаем — они только для просмотра."""
        task = self._service.get_task(uid)
        if task is None or not task.completed:
            return False
        if not self._service.toggle_completed(uid):
            return False
        self._notify_mutation("Задача возвращена в работу")
        return True

    @Slot(str, result="QVariantMap")
    def editorDataFor(self, uid: str) -> Dict[str, Any]:
        return editor_payload(self._service.get_task(uid))

    @Slot(str, str, str, int, bool, bool, str, str, str, bool, result=bool)
    def saveEditor(self, uid: str, title: str, notes: str, priority: int,
                   scheduled: bool, is_all_day: bool, date_text: str,
                   time_text: str, duration_text: str,
                   completed: bool) -> bool:
        """Тот же контракт, что у Today/Calendar (общий TaskEditorDialog)."""
        try:
            result = save_editor(
                self._service, uid, title, notes, priority, scheduled,
                is_all_day, date_text, time_text, duration_text, completed,
            )
        except Exception as exc:  # страховка от зависания UI
            self._set_editor_error(f"Не удалось сохранить задачу: {exc}")
            return False

        if not result.ok:
            self._set_editor_error(" ".join(result.errors))
            return False

        self._set_editor_error("")
        self._notify_mutation("Сохранено")
        return True

    @Slot()
    def clearEditorError(self) -> None:
        self._set_editor_error("")

    @Slot()
    def refresh(self) -> None:
        """Перечитать журнал (после мутаций на других страницах)."""
        self.historyChanged.emit()

    # ---- внутреннее -------------------------------------------------------------

    def _notify_mutation(self, toast: str = "") -> None:
        self.historyChanged.emit()
        self.tasksMutated.emit()
        if toast:
            self.toastMessage.emit(toast)

    def _set_editor_error(self, message: str) -> None:
        if self._editor_error != message:
            self._editor_error = message
            self.editorErrorChanged.emit()
