"""Общая база действий над задачами для Today/Calendar/History ViewModel.

До фазы 1 три ViewModel-я дублировали одинаковые слоты (saveEditor,
editorDataFor, deleteTask, toggleCompleted...). Теперь общий контракт живёт
здесь один раз:

- редактор: editorDataFor / saveEditor (тот же 10-аргументный слот) /
  editorError / clearEditorError / applyEditorPreset / newScheduledDefaults;
- действия: toggleCompleted / deleteTask / postponeTask (снуз) /
  unscheduleTask / restoreTask;
- выбор задачи: selectedUid / selectedTask / selectTask / clearSelection —
  выбор живёт в Python, QML только показывает;
- защита от дублей: busy на время операции (кнопки в QML выключаются)
  плюс окно подавления повторного идентичного действия (быстрый двойной
  клик по «перенести»/«удалить» не выполняется дважды);
- тосты: toastMessage — успех, toastError — ошибка (обе строки).

Подкласс обязан реализовать _emit_data_changed() — эмит своих *Changed
сигналов (расписание страниц у всех разное), больше ничего.

QML НИКОГДА не зовёт репозиторий напрямую: только эти слоты.
"""
from __future__ import annotations

import time as _time
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from PySide6.QtCore import Property, QObject, Signal, Slot

from planner_desktop.domain import scheduling
from planner_desktop.domain.task import Task
from planner_desktop.usecases.task_service import DesktopTaskService
from planner_desktop.usecases.bulk_task_service import (
    ACTION_ADD_TAG,
    ACTION_COMPLETE,
    ACTION_DELETE,
    ACTION_POSTPONE_TOMORROW,
    ACTION_PRIORITY,
    ACTION_REMOVE_TAG,
    ACTION_RESTORE,
    ACTION_UNSCHEDULE,
    BulkTaskService,
)
from planner_desktop.viewmodels.task_rows import (
    editor_payload,
    save_editor,
    task_to_row,
)
from planner_desktop.viewmodels.task_selection import TaskSelection


class TaskActionsViewModel(QObject):
    """База ViewModel-ей с задачами. Не используется из QML напрямую."""

    editorErrorChanged = Signal()
    toastMessage = Signal(str)
    toastError = Signal(str)
    tasksMutated = Signal()
    busyChanged = Signal()
    selectedTaskChanged = Signal()
    selectionChanged = Signal()

    #: Окно подавления повторного идентичного действия (секунды).
    DUPLICATE_WINDOW_S = 0.3

    def __init__(
        self,
        service: DesktopTaskService,
        parent: QObject | None = None,
        *,
        clock: Optional[Callable[[], float]] = None,
        now_provider: Optional[Callable[[], datetime]] = None,
    ) -> None:
        super().__init__(parent)
        self._service = service
        self._editor_error = ""
        self._busy = False
        self._selected_uid = ""
        self._selection = TaskSelection()
        self._last_op: tuple[str, str, float] = ("", "", float("-inf"))
        self._clock = clock or _time.monotonic
        self._now = now_provider or datetime.now
        self._bulk = BulkTaskService(service, getattr(service, "tag_service", None))

    # ---- хук подкласса -----------------------------------------------------------

    def _emit_data_changed(self) -> None:
        """Эмит собственных *Changed-сигналов страницы (перечитать списки)."""
        raise NotImplementedError

    def _notify_mutation(self, toast: str = "") -> None:
        self._emit_data_changed()
        self._prune_selection()
        self.selectedTaskChanged.emit()
        self.tasksMutated.emit()
        if toast:
            self.toastMessage.emit(toast)

    # ---- защита от дублей ----------------------------------------------------------

    def _begin(self, name: str, uid: str = "", *, dedupe: bool = False) -> bool:
        """True — операцию можно выполнять; False — занято или дубль клика."""
        if self._busy:
            return False
        if dedupe:
            now = self._clock()
            last_name, last_uid, last_at = self._last_op
            if (name == last_name and uid == last_uid
                    and now - last_at < self.DUPLICATE_WINDOW_S):
                return False
            self._last_op = (name, uid, now)
        self._busy = True
        self.busyChanged.emit()
        return True

    def _end(self) -> None:
        self._busy = False
        self.busyChanged.emit()

    @Property(bool, notify=busyChanged)
    def busy(self) -> bool:
        return self._busy

    # ---- выбор задачи ---------------------------------------------------------------

    @Property(str, notify=selectedTaskChanged)
    def selectedUid(self) -> str:
        return self._selected_uid

    @Property("QVariantList", notify=selectionChanged)
    def selectedUids(self) -> List[str]:
        self._sync_visible_selection()
        return list(self._selection.selected)

    @Property(int, notify=selectionChanged)
    def selectedCount(self) -> int:
        self._sync_visible_selection()
        return self._selection.count

    @Property(bool, notify=selectionChanged)
    def hasMultiSelection(self) -> bool:
        return self.selectedCount > 1

    @Property(str, notify=selectionChanged)
    def selectionStatus(self) -> str:
        count = self.selectedCount
        return "Нет выбранных задач" if count == 0 else f"Выбрано задач: {count}"

    @Property("QVariantList", notify=tasksMutated)
    def availableTags(self) -> List[Dict[str, Any]]:
        tag_service = getattr(self._service, "tag_service", None)
        if tag_service is None:
            return []
        return [
            {"id": tag.id, "name": tag.name}
            for tag in tag_service.list_tags()
        ]

    @Property("QVariant", notify=selectedTaskChanged)
    def selectedTask(self) -> Optional[Dict[str, Any]]:
        """Строка-словарь выбранной задачи (как в списках) или None."""
        task = self._selected_live_task()
        if task is None:
            return None
        return task_to_row(task, self._service.pending_task_uids())

    def _selected_live_task(self) -> Optional[Task]:
        if not self._selected_uid:
            return None
        return self._service.get_task(self._selected_uid)

    def _visible_task_uids(self) -> List[str]:
        return [task.uid for task in self._service.repository.list_all()]

    def _sync_visible_selection(self) -> bool:
        return self._selection.set_visible(self._visible_task_uids())

    def _prune_selection(self) -> None:
        before = self._selection.selected
        changed = self._sync_visible_selection()
        visible = set(self._selection.visible)
        if self._selected_uid and self._selected_uid not in visible:
            self._selected_uid = ""
            self.selectedTaskChanged.emit()
        if changed or before != self._selection.selected:
            self.selectionChanged.emit()

    @Slot(str)
    def selectTask(self, uid: str) -> None:
        self._sync_visible_selection()
        selection_changed = self._selection.select(uid)
        if uid != self._selected_uid:
            self._selected_uid = uid
            self.selectedTaskChanged.emit()
        if selection_changed:
            self.selectionChanged.emit()

    @Slot(str, bool, bool)
    def selectTaskWithModifiers(self, uid: str, ctrl: bool, shift: bool) -> None:
        self._sync_visible_selection()
        if not self._selection.select(uid, ctrl=bool(ctrl), shift=bool(shift)):
            return
        selected = self._selection.selected
        new_current = uid if uid in selected else (selected[-1] if selected else "")
        if new_current != self._selected_uid:
            self._selected_uid = new_current
            self.selectedTaskChanged.emit()
        self.selectionChanged.emit()

    @Slot(str, result=bool)
    def isTaskSelected(self, uid: str) -> bool:
        self._sync_visible_selection()
        return self._selection.contains(uid)

    @Slot()
    def selectAllVisible(self) -> None:
        self._sync_visible_selection()
        if self._selection.select_all_visible():
            selected = self._selection.selected
            if not self._selected_uid and selected:
                self._selected_uid = selected[0]
                self.selectedTaskChanged.emit()
            self.selectionChanged.emit()

    @Slot()
    def clearSelection(self) -> None:
        selection_changed = self._selection.clear()
        if self._selected_uid:
            self._selected_uid = ""
            self.selectedTaskChanged.emit()
        if selection_changed:
            self.selectionChanged.emit()

    # ---- общий контракт редактора ------------------------------------------------------

    @Property(str, notify=editorErrorChanged)
    def editorError(self) -> str:
        return self._editor_error

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
        dedupe_key = "\x1f".join((
            uid,
            title,
            notes,
            str(priority),
            str(scheduled),
            str(is_all_day),
            date_text,
            time_text,
            duration_text,
            str(completed),
        ))
        if not self._begin("saveEditor", dedupe_key, dedupe=True):
            return False
        try:
            result = save_editor(
                self._service, uid, title, notes, priority, scheduled,
                is_all_day, date_text, time_text, duration_text, completed,
            )
        except Exception as exc:  # страховка от зависания UI
            message = f"Не удалось сохранить задачу: {exc}"
            self._set_editor_error(message)
            self.toastError.emit(message)
            return False
        finally:
            self._end()

        if not result.ok:
            self._set_editor_error(" ".join(result.errors))
            return False

        self._set_editor_error("")
        self._notify_mutation("Сохранено")
        return True

    @Slot()
    def clearEditorError(self) -> None:
        self._set_editor_error("")

    def _set_editor_error(self, message: str) -> None:
        if self._editor_error != message:
            self._editor_error = message
            self.editorErrorChanged.emit()

    # ---- пресеты формы (чистые расчёты, ничего не сохраняют) ---------------------------

    @Slot(str, str, str, str, result="QVariantMap")
    def applyEditorPreset(self, preset: str, mode: str, date_text: str,
                          time_text: str) -> Dict[str, Any]:
        """Пересчёт полей формы пресетом («Сегодня»/«На вечер»/…).

        Возвращает {ok, mode, dateText, timeText, error}; при ok=False форма
        не меняется, error пригоден для инлайн-подсказки.
        """
        state = scheduling.EditorState(
            mode=mode, date_text=date_text, time_text=time_text)
        result = scheduling.apply_editor_preset(
            preset, state, today=self._now().date(), now=self._now())
        return {
            "ok": result.ok,
            "mode": result.mode,
            "dateText": result.date_text,
            "timeText": result.time_text,
            "error": result.error,
        }

    @Slot(result="QVariantMap")
    def newScheduledDefaults(self) -> Dict[str, Any]:
        """Заготовка «новой запланированной задачи» (Ctrl+Shift+N)."""
        result = scheduling.new_scheduled_defaults(self._now())
        return {
            "ok": result.ok,
            "mode": result.mode,
            "dateText": result.date_text,
            "timeText": result.time_text,
            "error": result.error,
        }

    @Property("QVariantList", constant=True)
    def editorPresets(self) -> List[dict]:
        return scheduling.editor_presets()

    @Property("QVariantList", constant=True)
    def durationPresets(self) -> List[dict]:
        return scheduling.duration_presets()

    # ---- quick scheduling выбранной/инспектируемой задачи ----------------------

    @Slot(str, result="QVariantList")
    def taskPresetsFor(self, uid: str) -> List[Dict[str, Any]]:
        """Editor presets как persistable actions для TaskInspector."""
        task = self._service.get_task(uid)
        if task is None:
            return []
        recurring = task.google_calendar_recurring_event_id is not None
        timed = task.start is not None and not task.is_all_day
        scheduled = task.start is not None
        actions: List[Dict[str, Any]] = []
        for item in scheduling.editor_presets():
            enabled = not recurring
            if item["id"] == scheduling.PRESET_PLUS_HOUR:
                enabled = enabled and timed
            elif item["id"] == scheduling.PRESET_UNSCHEDULE:
                enabled = enabled and scheduled
            actions.append({**item, "enabled": enabled})
        return actions

    @Slot(str, str, result=bool)
    def applyTaskPreset(self, uid: str, preset: str) -> bool:
        """Сохранить scheduling preset через use-case слой и Calendar queue."""
        if not self._begin(f"taskPreset:{preset}", uid, dedupe=True):
            return False
        try:
            result = self._service.apply_scheduling_preset(
                uid, preset, now=self._now()
            )
        except Exception as exc:
            self.toastError.emit(f"Не удалось изменить расписание: {exc}")
            return False
        finally:
            self._end()
        if not result.ok:
            self.toastError.emit(" ".join(result.errors))
            return False
        toast = (
            "Дата снята"
            if preset == scheduling.PRESET_UNSCHEDULE
            else "Расписание обновлено"
        )
        self._notify_mutation(toast)
        return True

    # ---- снуз / перенос -------------------------------------------------------------------

    @Slot(str, result="QVariantList")
    def snoozeActionsFor(self, uid: str) -> List[Dict[str, Any]]:
        """Пункты меню снуза для конкретной задачи с флагом enabled.

        Экземплярам повторяющихся серий перенос запрещён (безопасность
        синка), недатированной задаче нечего «снимать».
        """
        task = self._service.get_task(uid)
        if task is None:
            return []
        recurring = task.google_calendar_recurring_event_id is not None
        actions = []
        for item in scheduling.snooze_actions():
            enabled = True
            if recurring:
                enabled = False
            if item["id"] == scheduling.SNOOZE_UNSCHEDULE and task.start is None:
                enabled = False
            actions.append({**item, "enabled": enabled})
        return actions

    @Slot(str, str, result=bool)
    def postponeTask(self, uid: str, action: str) -> bool:
        """Снуз задачи. Все правила — в domain/scheduling.py и сервисе;
        двойной быстрый клик по тому же пункту подавляется."""
        if action == scheduling.SNOOZE_PICK:
            return False  # «Выбрать дату и время» открывает редактор в QML
        # Ключ дедупликации включает действие: подавляется именно повторный
        # клик по тому же пункту, а не два разных переноса подряд.
        if not self._begin(f"postpone:{action}", uid, dedupe=True):
            return False
        try:
            result = self._service.postpone_task(uid, action, now=self._now())
        except Exception as exc:
            self.toastError.emit(f"Не удалось перенести задачу: {exc}")
            return False
        finally:
            self._end()
        if not result.ok:
            self.toastError.emit(" ".join(result.errors))
            return False
        toast = ("Дата снята" if action == scheduling.SNOOZE_UNSCHEDULE
                 else "Задача перенесена")
        self._notify_mutation(toast)
        return True

    @Slot(str, result=bool)
    def unscheduleTask(self, uid: str) -> bool:
        return self.postponeTask(uid, scheduling.SNOOZE_UNSCHEDULE)

    # ---- базовые действия -------------------------------------------------------------------

    @Slot(str, result=bool)
    def toggleCompleted(self, uid: str) -> bool:
        if not self._begin("toggle", uid, dedupe=True):
            return False
        try:
            changed = self._service.toggle_completed(uid)
        except Exception as exc:
            self.toastError.emit(f"Не удалось изменить состояние задачи: {exc}")
            return False
        finally:
            self._end()
        if changed:
            task = self._service.get_task(uid)
            toast = (
                "Задача выполнена"
                if task is not None and task.completed
                else "Задача возвращена в работу"
            )
            self._notify_mutation(toast)
        else:
            self.toastError.emit("Не удалось изменить состояние задачи.")
        return changed

    @Slot(str, result=bool)
    def deleteTask(self, uid: str) -> bool:
        if not self._begin("delete", uid, dedupe=True):
            return False
        try:
            deleted = self._service.delete_task_by_uid(uid)
        except Exception as exc:
            self.toastError.emit(f"Не удалось удалить задачу: {exc}")
            return False
        finally:
            self._end()
        if deleted:
            if uid == self._selected_uid:
                self._selected_uid = ""
            self._notify_mutation("Задача удалена")
        else:
            self.toastError.emit("Не удалось удалить задачу.")
        return deleted

    @Slot(str, result=bool)
    def restoreTask(self, uid: str) -> bool:
        """Вернуть выполненную задачу в работу (страница «История» и др.)."""
        if not self._begin("restore", uid, dedupe=True):
            return False
        try:
            restored = self._service.restore_task(uid)
        except Exception as exc:
            self.toastError.emit(f"Не удалось восстановить задачу: {exc}")
            return False
        finally:
            self._end()
        if restored:
            self._notify_mutation("Задача возвращена в работу")
        else:
            self.toastError.emit("Не удалось восстановить задачу.")
        return restored

    @Slot(str, result=bool)
    def duplicateTask(self, uid: str) -> bool:
        if not self._begin("duplicate", uid, dedupe=True):
            return False
        try:
            result = self._service.duplicate_task(uid)
        except Exception as exc:
            self.toastError.emit(f"Не удалось дублировать задачу: {exc}")
            return False
        finally:
            self._end()
        if not result.ok:
            self.toastError.emit(" ".join(result.errors))
            return False
        self._notify_mutation("Копия задачи создана")
        self.selectTask(result.task.uid)
        return True

    def _bulk_result_map(self, result) -> Dict[str, Any]:
        return {
            "affected": result.affected_count,
            "skipped": result.skipped_count,
            "failed": result.failed_count,
            "summary": result.summary,
            "busyRejected": result.busy_rejected,
            "items": [
                {"uid": item.uid, "status": item.status, "message": item.message}
                for item in result.items
            ],
        }

    def _run_bulk(self, action: str, value=None) -> Dict[str, Any]:
        self._sync_visible_selection()
        uids = self._selection.selected
        if not uids:
            return {
                "affected": 0, "skipped": 0, "failed": 0,
                "summary": "Нет выбранных задач.", "busyRejected": False,
                "items": [],
            }
        if not self._begin(f"bulk:{action}"):
            return {
                "affected": 0, "skipped": 0, "failed": 0,
                "summary": "Другая операция уже выполняется.",
                "busyRejected": True, "items": [],
            }
        try:
            # The tag service can be attached after a VM is built in tests/demo.
            self._bulk.tag_service = getattr(self._service, "tag_service", None)
            result = self._bulk.execute(action, uids, value, now=self._now())
        finally:
            self._end()
        if result.affected_count:
            self._notify_mutation(result.summary)
        elif result.failed_count or result.busy_rejected:
            self.toastError.emit(result.summary)
        elif result.skipped_count:
            self.toastMessage.emit(result.summary)
        return self._bulk_result_map(result)

    @Slot(result="QVariantMap")
    def bulkComplete(self) -> Dict[str, Any]:
        return self._run_bulk(ACTION_COMPLETE)

    @Slot(result="QVariantMap")
    def bulkRestore(self) -> Dict[str, Any]:
        return self._run_bulk(ACTION_RESTORE)

    @Slot(int, result="QVariantMap")
    def bulkSetPriority(self, priority: int) -> Dict[str, Any]:
        return self._run_bulk(ACTION_PRIORITY, priority)

    @Slot(int, result="QVariantMap")
    def bulkAddTag(self, tag_id: int) -> Dict[str, Any]:
        return self._run_bulk(ACTION_ADD_TAG, tag_id)

    @Slot(int, result="QVariantMap")
    def bulkRemoveTag(self, tag_id: int) -> Dict[str, Any]:
        return self._run_bulk(ACTION_REMOVE_TAG, tag_id)

    @Slot(result="QVariantMap")
    def bulkPostponeTomorrow(self) -> Dict[str, Any]:
        return self._run_bulk(ACTION_POSTPONE_TOMORROW)

    @Slot(result="QVariantMap")
    def bulkUnschedule(self) -> Dict[str, Any]:
        return self._run_bulk(ACTION_UNSCHEDULE)

    @Slot(result="QVariantMap")
    def bulkDelete(self) -> Dict[str, Any]:
        return self._run_bulk(ACTION_DELETE)

    @Slot()
    def refresh(self) -> None:
        """Перечитать данные (после чужих мутаций); только *Changed-сигналы."""
        self._emit_data_changed()
        self._prune_selection()
        self.selectedTaskChanged.emit()

    # ---- доступ для тестов -----------------------------------------------------------------

    @property
    def service(self) -> DesktopTaskService:
        return self._service
