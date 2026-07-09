"""ViewModel страницы «Сегодня».

Правила валидации живут в domain/commands.py; здесь только адаптация
под QML: свойства-списки словарей, сигналы об изменениях и слоты.
QObject можно создавать без QApplication, поэтому тесты гоняют этот
класс без какого-либо окна.

Сигналы:

- tasksChanged — данные списков/статистики устарели, QML перечитает свойства;
- tasksMutated — эта ViewModel сама изменила задачи; MainWindow подписывает
  на него refresh() других ViewModel-ей (календарь, настройки), чтобы
  страницы не расходились. refresh() эмитит только tasksChanged, поэтому
  петля исключена;
- toastMessage — короткое «Сохранено»/«Удалено» для всплывашки в QML.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Dict, List

from PySide6.QtCore import Property, QObject, Signal, Slot

from planner_desktop.domain.commands import QuickAddCommand, execute_quick_add
from planner_desktop.repositories import TaskRepository
from planner_desktop.repositories.fake_task_repository import FakeTaskRepository
from planner_desktop.usecases.task_service import DesktopTaskService
from planner_desktop.viewmodels.task_rows import (
    editor_payload,
    save_editor,
    task_to_row,
)

_MONTHS_GENITIVE = [
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]
_WEEKDAYS = [
    "понедельник", "вторник", "среда", "четверг",
    "пятница", "суббота", "воскресенье",
]


def _header_date_text(day: date) -> str:
    return (
        f"{_WEEKDAYS[day.weekday()]}, {day.day} "
        f"{_MONTHS_GENITIVE[day.month - 1]} {day.year}"
    )


class TodayViewModel(QObject):
    tasksChanged = Signal()
    dailyChanged = Signal()
    errorChanged = Signal()
    editorErrorChanged = Signal()
    toastMessage = Signal(str)
    tasksMutated = Signal()

    def __init__(self, repository: TaskRepository | None = None,
                 parent: QObject | None = None,
                 service: DesktopTaskService | None = None) -> None:
        """CRUD идёт через DesktopTaskService: он же ставит Calendar-операции
        в очередь, когда сервис создан с CalendarSyncStore (см. main_window).
        Старый вызов TodayViewModel(repository) работает как раньше —
        сервис без очереди собирается автоматически."""
        super().__init__(parent)
        if service is not None:
            self._service = service
        else:
            self._service = DesktopTaskService(repository or FakeTaskRepository())
        self._repository = self._service.repository
        self._error = ""
        self._editor_error = ""
        self._daily_done: Dict[str, bool] = {
            title: False for title in self._repository.daily_titles
        }

    # ---- свойства для QML -------------------------------------------------

    @Property(str, notify=tasksChanged)
    def headerDateText(self) -> str:
        return _header_date_text(date.today())

    @Property("QVariantList", notify=tasksChanged)
    def todayTasks(self) -> List[Dict[str, Any]]:
        pending = self._service.pending_task_uids()
        return [task_to_row(t, pending) for t in self._repository.list_today()]

    @Property("QVariantList", notify=tasksChanged)
    def undatedTasks(self) -> List[Dict[str, Any]]:
        pending = self._service.pending_task_uids()
        return [task_to_row(t, pending) for t in self._repository.list_undated()]

    @Property("QVariantList", notify=dailyChanged)
    def dailyTasks(self) -> List[Dict[str, Any]]:
        return [
            {"title": title, "done": done}
            for title, done in self._daily_done.items()
        ]

    # ---- статистика шапки ---------------------------------------------------

    @Property(int, notify=tasksChanged)
    def todayCount(self) -> int:
        return len(self._repository.list_today())

    @Property(int, notify=tasksChanged)
    def undatedCount(self) -> int:
        return len(self._repository.list_undated())

    @Property(int, notify=tasksChanged)
    def completedTodayCount(self) -> int:
        return sum(1 for t in self._repository.list_today() if t.completed)

    @Property(int, notify=tasksChanged)
    def pendingSyncCount(self) -> int:
        return self._service.count_pending_ops()

    @Property(bool, constant=True)
    def hasSyncQueue(self) -> bool:
        return self._service.has_sync_queue

    @Property(str, notify=errorChanged)
    def errorMessage(self) -> str:
        return self._error

    @Property(str, notify=editorErrorChanged)
    def editorError(self) -> str:
        return self._editor_error

    # ---- слоты ------------------------------------------------------------

    @Slot(str, str, bool, bool, str, str, str, result=bool)
    def addTask(self, title: str, notes: str, add_to_calendar: bool,
                is_all_day: bool, date_text: str, time_text: str,
                duration_text: str) -> bool:
        """Quick Add. Любой невалидный ввод даёт False + errorMessage,
        исключения наружу не выпускаются — UI не зависает."""
        command = QuickAddCommand(
            title=title,
            notes=notes,
            add_to_calendar=add_to_calendar,
            is_all_day=is_all_day,
            date_text=date_text,
            time_text=time_text,
            duration_text=duration_text,
        )
        try:
            result = execute_quick_add(command)
        except Exception as exc:  # страховка: битый ввод не должен ронять UI
            self._set_error(f"Не удалось добавить задачу: {exc}")
            return False

        if not result.ok:
            self._set_error(" ".join(result.errors))
            return False

        self._service.create_task(result.task)
        self._set_error("")
        self._notify_mutation("Задача добавлена")
        return True

    @Slot(str, result=bool)
    def toggleCompleted(self, uid: str) -> bool:
        changed = self._service.toggle_completed(uid)
        if changed:
            self._notify_mutation()
        return changed

    @Slot(str, result=bool)
    def deleteTask(self, uid: str) -> bool:
        deleted = self._service.delete_task_by_uid(uid)
        if deleted:
            self._notify_mutation("Задача удалена")
        return deleted

    @Slot(str, result="QVariantMap")
    def editorDataFor(self, uid: str) -> Dict[str, Any]:
        return editor_payload(self._service.get_task(uid))

    @Slot(str, str, str, int, bool, bool, str, str, str, bool, result=bool)
    def saveEditor(self, uid: str, title: str, notes: str, priority: int,
                   scheduled: bool, is_all_day: bool, date_text: str,
                   time_text: str, duration_text: str,
                   completed: bool) -> bool:
        """Сохранение TaskEditorDialog (создание при пустом uid).

        Ошибки валидации не закрывают диалог: False + editorError.
        """
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
    def refresh(self) -> None:
        """Перечитать данные (вызывается извне после чужих мутаций)."""
        self.tasksChanged.emit()

    @Slot(str)
    def toggleDaily(self, title: str) -> None:
        if title in self._daily_done:
            self._daily_done[title] = not self._daily_done[title]
            self.dailyChanged.emit()

    @Slot()
    def clearError(self) -> None:
        self._set_error("")

    @Slot()
    def clearEditorError(self) -> None:
        self._set_editor_error("")

    # ---- внутреннее -------------------------------------------------------

    def _notify_mutation(self, toast: str = "") -> None:
        self.tasksChanged.emit()
        self.tasksMutated.emit()
        if toast:
            self.toastMessage.emit(toast)

    def _set_error(self, message: str) -> None:
        if self._error != message:
            self._error = message
            self.errorChanged.emit()

    def _set_editor_error(self, message: str) -> None:
        if self._editor_error != message:
            self._editor_error = message
            self.editorErrorChanged.emit()

    @property
    def repository(self) -> TaskRepository:
        return self._repository
