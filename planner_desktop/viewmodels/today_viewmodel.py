"""ViewModel страницы «Сегодня».

Правила валидации живут в domain/commands.py; здесь только адаптация
под QML: свойства-списки словарей, сигналы об изменениях и слоты.
QObject можно создавать без QApplication, поэтому тесты гоняют этот
класс без какого-либо окна.
"""
from __future__ import annotations

from typing import Any, Dict, List

from PySide6.QtCore import Property, QObject, Signal, Slot

from planner_desktop.domain.commands import QuickAddCommand, execute_quick_add
from planner_desktop.domain.task import Task
from planner_desktop.repositories import TaskRepository
from planner_desktop.repositories.fake_task_repository import FakeTaskRepository


def _task_to_row(task: Task) -> Dict[str, Any]:
    if task.is_all_day:
        time_label = "Весь день"
    elif task.start is not None:
        time_label = task.start.strftime("%H:%M")
        if task.end is not None:
            time_label += "–" + task.end.strftime("%H:%M")
    else:
        time_label = ""
    return {
        "uid": task.uid,
        "title": task.title,
        "notes": task.notes,
        "timeLabel": time_label,
        "isAllDay": task.is_all_day,
        "priority": task.priority,
        "completed": task.completed,
    }


class TodayViewModel(QObject):
    tasksChanged = Signal()
    dailyChanged = Signal()
    errorChanged = Signal()

    def __init__(self, repository: TaskRepository | None = None,
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._repository = repository or FakeTaskRepository()
        self._error = ""
        self._daily_done: Dict[str, bool] = {
            title: False for title in self._repository.daily_titles
        }

    # ---- свойства для QML -------------------------------------------------

    @Property("QVariantList", notify=tasksChanged)
    def todayTasks(self) -> List[Dict[str, Any]]:
        return [_task_to_row(t) for t in self._repository.list_today()]

    @Property("QVariantList", notify=tasksChanged)
    def undatedTasks(self) -> List[Dict[str, Any]]:
        return [_task_to_row(t) for t in self._repository.list_undated()]

    @Property("QVariantList", notify=dailyChanged)
    def dailyTasks(self) -> List[Dict[str, Any]]:
        return [
            {"title": title, "done": done}
            for title, done in self._daily_done.items()
        ]

    @Property(str, notify=errorChanged)
    def errorMessage(self) -> str:
        return self._error

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

        self._repository.add(result.task)
        self._set_error("")
        self.tasksChanged.emit()
        return True

    @Slot(str, result=bool)
    def toggleCompleted(self, uid: str) -> bool:
        changed = self._repository.toggle_completed(uid)
        if changed:
            self.tasksChanged.emit()
        return changed

    @Slot(str)
    def toggleDaily(self, title: str) -> None:
        if title in self._daily_done:
            self._daily_done[title] = not self._daily_done[title]
            self.dailyChanged.emit()

    @Slot()
    def clearError(self) -> None:
        self._set_error("")

    # ---- внутреннее -------------------------------------------------------

    def _set_error(self, message: str) -> None:
        if self._error != message:
            self._error = message
            self.errorChanged.emit()

    @property
    def repository(self) -> TaskRepository:
        return self._repository
