"""ViewModel страницы «Календарь»: недельная навигация + список дня.

Данные — тот же репозиторий, что и у «Сегодня» (через общий
DesktopTaskService); карточки задач и диалог редактирования на странице
календаря используют те же слоты (toggleCompleted/deleteTask/saveEditor),
что и TodayViewModel, поэтому поведение везде одинаковое.

Сигналы (та же схема, что у TodayViewModel):

- weekChanged/selectionChanged — QML перечитывает сетку и список дня;
- tasksMutated — эта ViewModel изменила задачи; MainWindow дёргает
  refresh() остальных ViewModel-ей;
- toastMessage — всплывашка «Сохранено»/«Удалено».
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Dict, List

from PySide6.QtCore import Property, QObject, Signal, Slot

from planner_desktop.repositories import TaskRepository
from planner_desktop.repositories.fake_task_repository import FakeTaskRepository
from planner_desktop.usecases.task_service import DesktopTaskService
from planner_desktop.viewmodels.task_rows import (
    editor_payload,
    save_editor,
    task_to_row,
)

_WEEKDAY_LABELS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]


def _monday_of(day: date) -> date:
    return day - timedelta(days=day.weekday())


class CalendarViewModel(QObject):
    weekChanged = Signal()
    selectionChanged = Signal()
    editorErrorChanged = Signal()
    toastMessage = Signal(str)
    tasksMutated = Signal()

    def __init__(self, repository: TaskRepository | None = None,
                 parent: QObject | None = None,
                 service: DesktopTaskService | None = None) -> None:
        super().__init__(parent)
        if service is not None:
            self._service = service
        else:
            self._service = DesktopTaskService(repository or FakeTaskRepository())
        self._repository = self._service.repository
        self._editor_error = ""
        today = date.today()
        self._week_start = _monday_of(today)
        self._selected_index = today.weekday()

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
        pending = self._service.pending_task_uids()
        return [task_to_row(t, pending) for t in self._tasks_for(self._selected_day())]

    @Property(str, notify=editorErrorChanged)
    def editorError(self) -> str:
        return self._editor_error

    # ---- навигация --------------------------------------------------------------

    @Slot(int)
    def selectDay(self, index: int) -> None:
        if 0 <= index < 7 and index != self._selected_index:
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
        today = date.today()
        self._week_start = _monday_of(today)
        self._selected_index = today.weekday()
        self.weekChanged.emit()
        self.selectionChanged.emit()

    @Slot()
    def refresh(self) -> None:
        """Перечитать данные (вызывается извне после чужих мутаций)."""
        self.weekChanged.emit()
        self.selectionChanged.emit()

    # ---- действия над задачами (те же, что на «Сегодня») --------------------------

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
        """Тот же контракт, что у TodayViewModel.saveEditor (общий диалог)."""
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

    # ---- внутреннее ---------------------------------------------------------------

    def _notify_mutation(self, toast: str = "") -> None:
        self.weekChanged.emit()
        self.selectionChanged.emit()
        self.tasksMutated.emit()
        if toast:
            self.toastMessage.emit(toast)

    def _set_editor_error(self, message: str) -> None:
        if self._editor_error != message:
            self._editor_error = message
            self.editorErrorChanged.emit()

    def _shift_week(self, delta_weeks: int) -> None:
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
