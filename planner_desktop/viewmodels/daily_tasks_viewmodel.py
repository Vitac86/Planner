"""ViewModel управления ежедневными задачами (страница «Сегодня»).

Тонкая обёртка над DailyTaskService для QML: список всех ежедневных задач
для диалога управления, создание/правка/удаление и включение/выключение.
Отметки выполнения на сегодня живут в TodayViewModel (тот же сервис),
поэтому обе ViewModel-и подписаны на мутации друг друга в MainWindow.

Полностью локально: ежедневные задачи в Google Calendar не уходят.
"""
from __future__ import annotations

from typing import Any, Dict, List

from PySide6.QtCore import Property, QObject, Signal, Slot

from planner_desktop.domain.daily_task import DailyTask, describe_mask
from planner_desktop.usecases.daily_task_service import DailyTaskService


def daily_to_row(task: DailyTask) -> Dict[str, Any]:
    return {
        "uid": task.uid,
        "title": task.title,
        "notes": task.notes,
        "enabled": task.enabled,
        "weekdaysMask": int(task.weekdays_mask),
        "weekdaysText": describe_mask(task.weekdays_mask),
        "timeText": task.preferred_time,
    }


class DailyTasksViewModel(QObject):
    itemsChanged = Signal()
    editorErrorChanged = Signal()
    toastMessage = Signal(str)
    mutated = Signal()

    def __init__(self, service: DailyTaskService,
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._service = service
        self._editor_error = ""

    # ---- свойства ------------------------------------------------------------

    @Property("QVariantList", notify=itemsChanged)
    def items(self) -> List[Dict[str, Any]]:
        return [daily_to_row(t) for t in self._service.list_all()]

    @Property(int, notify=itemsChanged)
    def count(self) -> int:
        return len(self._service.list_all())

    @Property(str, notify=editorErrorChanged)
    def editorError(self) -> str:
        return self._editor_error

    # ---- слоты ---------------------------------------------------------------

    @Slot(str, result="QVariantMap")
    def editorDataFor(self, uid: str) -> Dict[str, Any]:
        task = self._service.get(uid)
        if task is None:
            return {
                "exists": False,
                "uid": "",
                "title": "",
                "notes": "",
                "enabled": True,
                "weekdaysMask": 0b1111111,
                "timeText": "",
            }
        row = daily_to_row(task)
        row["exists"] = True
        return row

    @Slot(str, str, str, bool, int, str, result=bool)
    def save(self, uid: str, title: str, notes: str, enabled: bool,
             weekdays_mask: int, time_text: str) -> bool:
        """Пустой uid — создание, иначе правка. Ошибки — в editorError,
        диалог остаётся открытым (возвращаем False)."""
        try:
            if uid:
                result = self._service.edit(
                    uid, title, notes=notes, enabled=enabled,
                    weekdays_mask=weekdays_mask, preferred_time=time_text,
                )
            else:
                result = self._service.create(
                    title, notes=notes, enabled=enabled,
                    weekdays_mask=weekdays_mask, preferred_time=time_text,
                )
        except Exception as exc:  # страховка от зависания UI
            self._set_editor_error(f"Не удалось сохранить: {exc}")
            return False

        if not result.ok:
            self._set_editor_error(" ".join(result.errors))
            return False

        self._set_editor_error("")
        self._notify_mutation("Ежедневная задача сохранена")
        return True

    @Slot(str, bool, result=bool)
    def setEnabled(self, uid: str, enabled: bool) -> bool:
        changed = self._service.set_enabled(uid, enabled) is not None
        if changed:
            self._notify_mutation()
        return changed

    @Slot(str, result=bool)
    def remove(self, uid: str) -> bool:
        deleted = self._service.delete(uid)
        if deleted:
            self._notify_mutation("Ежедневная задача удалена")
        return deleted

    @Slot()
    def refresh(self) -> None:
        self.itemsChanged.emit()

    @Slot()
    def clearEditorError(self) -> None:
        self._set_editor_error("")

    # ---- внутреннее ----------------------------------------------------------

    def _notify_mutation(self, toast: str = "") -> None:
        self.itemsChanged.emit()
        self.mutated.emit()
        if toast:
            self.toastMessage.emit(toast)

    def _set_editor_error(self, message: str) -> None:
        if self._editor_error != message:
            self._editor_error = message
            self.editorErrorChanged.emit()
