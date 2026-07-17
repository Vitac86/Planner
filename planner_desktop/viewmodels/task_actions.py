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
from datetime import date, datetime, timedelta
from typing import Any, Callable, Dict, List, Optional

from PySide6.QtCore import Property, QObject, Signal, Slot

from planner_desktop.domain import scheduling
from planner_desktop.domain.commands import TaskEditorCommand
from planner_desktop.domain.recurrence import (
    SeriesEditScope,
    SeriesSchedule,
    TaskSeries,
    describe_rule,
    default_timezone_name,
    recurrence_presets,
    validate_rule,
)
from planner_desktop.domain.series_calendar_link import (
    readable_series_link_status,
)
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
from planner_desktop.viewmodels.series_rows import (
    rule_from_map,
    rule_to_map,
    series_to_row,
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
    taskSelectionChanged = Signal()
    templatesChanged = Signal()

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
        template_service = getattr(service, "template_service", None)
        if template_service is not None:
            template_service.add_change_listener(self.templatesChanged.emit)

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

    @Property("QVariantList", notify=taskSelectionChanged)
    def selectedUids(self) -> List[str]:
        self._sync_visible_selection()
        return list(self._selection.selected)

    @Property(int, notify=taskSelectionChanged)
    def selectedCount(self) -> int:
        self._sync_visible_selection()
        return self._selection.count

    @Property(bool, notify=taskSelectionChanged)
    def hasMultiSelection(self) -> bool:
        return self.selectedCount > 1

    @Property(str, notify=taskSelectionChanged)
    def selectionStatus(self) -> str:
        count = self.selectedCount
        return "Нет выбранных задач" if count == 0 else f"Выбрано задач: {count}"

    @Property("QVariantList", notify=selectedTaskChanged)
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
        # The multi-selection is scoped to the current visible collection,
        # but the inspector's single current task may remain valid after a
        # move changes its date/filter bucket (for example Calendar drag).
        # Clear that current task only when it actually disappeared. Search
        # has a stricter result-selection policy in its own recompute hook.
        if self._selected_uid and self._selected_live_task() is None:
            self._selected_uid = ""
            self.selectedTaskChanged.emit()
        if changed or before != self._selection.selected:
            self.taskSelectionChanged.emit()

    @Slot(str)
    def selectTask(self, uid: str) -> None:
        self._sync_visible_selection()
        selection_changed = self._selection.select(uid)
        if uid != self._selected_uid:
            self._selected_uid = uid
            self.selectedTaskChanged.emit()
        if selection_changed:
            self.taskSelectionChanged.emit()

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
        self.taskSelectionChanged.emit()

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
            self.taskSelectionChanged.emit()

    @Slot()
    def clearSelection(self) -> None:
        selection_changed = self._selection.clear()
        if self._selected_uid:
            self._selected_uid = ""
            self.selectedTaskChanged.emit()
        if selection_changed:
            self.taskSelectionChanged.emit()

    # ---- общий контракт редактора ------------------------------------------------------

    @Property(str, notify=editorErrorChanged)
    def editorError(self) -> str:
        return self._editor_error

    @Slot(str, result="QVariantMap")
    def editorDataFor(self, uid: str) -> Dict[str, Any]:
        data = editor_payload(self._service.get_task(uid))
        tag_service = getattr(self._service, "tag_service", None)
        tags = tag_service.tags_for_task(uid) if uid and tag_service is not None else []
        data["tagIds"] = [tag.id for tag in tags]
        data["tags"] = [{"id": tag.id, "name": tag.name} for tag in tags]
        data.setdefault("seriesSummary", "")
        data.setdefault("rule", rule_to_map(None))
        data.setdefault("timezoneName", "")
        data.setdefault("seriesLinkStatus", "")
        data.setdefault("seriesLinkedToGoogle", False)
        data.setdefault("occurrenceSyncStatus", "")
        data.setdefault("occurrenceSyncStatusText", "")
        data.setdefault("occurrenceOriginalSlot", data.get("occurrenceKey", ""))
        data.setdefault("occurrenceRemoteStatus", "")
        recurrence = getattr(self._service, "recurrence_service", None)
        if data.get("isSeriesOccurrence") and recurrence is not None:
            series = recurrence.get_series(data.get("seriesUid", ""))
            if series is not None:
                data["seriesSummary"] = series.summary()
                data["rule"] = rule_to_map(series.rule)
                data["timezoneName"] = series.schedule.timezone_name
                links = getattr(recurrence, "series_link_service", None)
                link = links.get_link(series.uid) if links is not None else None
                data["seriesLinkedToGoogle"] = link is not None
                data["seriesLinkStatus"] = (
                    readable_series_link_status(link.link_status)
                    if link is not None
                    else readable_series_link_status(None)
                )
                occurrence_store = getattr(
                    recurrence, "occurrence_sync_store", None
                )
                if link is not None and occurrence_store is not None:
                    occurrence_link = occurrence_store.get_occurrence_link(
                        series.uid, data.get("occurrenceKey", "")
                    )
                    if occurrence_link is not None:
                        status = occurrence_link.sync_status.value
                        data["occurrenceSyncStatus"] = status
                        data["occurrenceSyncStatusText"] = {
                            "local_only": "Локальное исключение",
                            "pending_update": "Ожидает обновления Google",
                            "synced_exception": "Исключение синхронизировано",
                            "pending_cancel": "Ожидает отмены Google",
                            "cancelled": "Экземпляр отменён",
                            "conflict": "Конфликт экземпляра",
                            "remote_changed": "Изменён в Google",
                            "remote_cancelled": "Отменён в Google",
                            "terminal_error": "Ошибка синхронизации экземпляра",
                        }.get(status, status)
                        data["occurrenceOriginalSlot"] = (
                            occurrence_link.original_start_value
                        )
                        data["occurrenceRemoteStatus"] = (
                            "cancelled"
                            if occurrence_link.is_cancelled_remote
                            else "confirmed"
                        )
        return data

    def _series_links(self):
        recurrence = getattr(self._service, "recurrence_service", None)
        return getattr(recurrence, "series_link_service", None)

    @Slot(str, result="QVariantMap")
    def seriesGoogleLinkData(self, series_uid: str) -> Dict[str, Any]:
        links = self._series_links()
        recurrence = getattr(self._service, "recurrence_service", None)
        series = recurrence.get_series(series_uid) if recurrence is not None else None
        if links is None or series is None:
            return {
                "seriesUid": series_uid,
                "available": False,
                "statusText": "Локальная серия",
                "validationErrors": ["Сервис связи с Google недоступен."],
            }
        link = links.get_link(series_uid)
        validation = (
            links.validate_connection(series_uid) if link is None else None
        )
        pending = links.store.get_pending_op(series_uid)
        return {
            "seriesUid": series_uid,
            "available": True,
            "title": series.title,
            "status": link.link_status.value if link is not None else "",
            "statusText": readable_series_link_status(
                link.link_status if link is not None else None
            ),
            "linked": link is not None,
            "canConnect": link is None and validation is not None and validation.ok,
            "validationErrors": list(validation.errors) if validation else [],
            "remoteEventId": link.remote_event_id if link is not None else "",
            "lastError": link.last_error or "" if link is not None else "",
            "pendingOperation": pending.op.value if pending is not None else "",
            "whatSent": "Название, заметки, расписание и правило повторения.",
            "whatLocal": "Теги, выполненность, приоритет и история.",
        }

    def _run_series_link_action(self, key: str, series_uid: str, action, toast: str) -> bool:
        if not self._begin(key, series_uid, dedupe=True):
            return False
        try:
            result = action(series_uid)
        except Exception as exc:
            self.toastError.emit(f"Операция связи Google не выполнена: {exc}")
            return False
        finally:
            self._end()
        if not result.ok:
            self.toastError.emit(result.error or "Операция связи Google не выполнена.")
            return False
        self._notify_mutation(toast)
        return True

    @Slot(str, result=bool)
    def connectSeriesToGoogle(self, series_uid: str) -> bool:
        links = self._series_links()
        if links is None:
            self.toastError.emit("Сервис связи с Google недоступен.")
            return False
        return self._run_series_link_action(
            "connectSeriesGoogle",
            series_uid,
            links.connect_to_google,
            "Создание серии Google поставлено в очередь ручной синхронизации",
        )

    @Slot(str, result=bool)
    def disconnectSeriesKeepGoogle(self, series_uid: str) -> bool:
        links = self._series_links()
        if links is None:
            return False
        return self._run_series_link_action(
            "disconnectSeriesGoogle",
            series_uid,
            links.disconnect_keep_remote,
            "Связь отключена; серия Google сохранена",
        )

    @Slot(str, result=bool)
    def deleteGoogleSeriesKeepLocal(self, series_uid: str) -> bool:
        links = self._series_links()
        if links is None:
            return False
        return self._run_series_link_action(
            "deleteGoogleSeries",
            series_uid,
            links.request_remote_delete_keep_local,
            "Удаление серии Google поставлено в очередь; локальная сохранена",
        )

    @Slot(str, result=bool)
    def deleteLocalAndGoogleSeries(self, series_uid: str) -> bool:
        links = self._series_links()
        if links is None:
            return False
        return self._run_series_link_action(
            "deleteLocalGoogleSeries",
            series_uid,
            links.request_delete_local_and_remote,
            "Удаление локальной и Google-серии подтверждено",
        )

    # ---- explicit conflict / remote-deleted resolution (Phase 3.2B3A) -------

    def _series_conflicts(self):
        recurrence = getattr(self._service, "recurrence_service", None)
        return getattr(recurrence, "series_conflict_service", None)

    @Slot(str, result="QVariantMap")
    def seriesConflictData(self, series_uid: str) -> Dict[str, Any]:
        """Local-only comparison data for SeriesConflictDialog (no network)."""
        conflicts = self._series_conflicts()
        if conflicts is None:
            return {
                "seriesUid": series_uid,
                "available": False,
                "statusText": "Сервис разрешения конфликтов недоступен.",
            }
        return conflicts.get_conflict(series_uid)

    @Slot(str, result="QVariantMap")
    def seriesRemoteDeletedData(self, series_uid: str) -> Dict[str, Any]:
        conflicts = self._series_conflicts()
        if conflicts is None:
            return {"seriesUid": series_uid, "available": False}
        return conflicts.get_remote_deleted(series_uid)

    def _run_conflict_action(self, key: str, series_uid: str, action, toast: str) -> bool:
        if not self._begin(key, series_uid, dedupe=True):
            return False
        try:
            result = action(series_uid)
        except Exception as exc:
            self.toastError.emit(f"Действие с конфликтом не выполнено: {exc}")
            return False
        finally:
            self._end()
        if not result.ok:
            self.toastError.emit(
                result.error or "Действие с конфликтом не выполнено."
            )
            return False
        self._notify_mutation(toast)
        return True

    @Slot(str, result=bool)
    def resolveConflictKeepPlanner(self, series_uid: str) -> bool:
        conflicts = self._series_conflicts()
        if conflicts is None:
            return False
        return self._run_conflict_action(
            "resolveKeepPlanner",
            series_uid,
            lambda uid: conflicts.resolve_keep_planner(uid, confirmed=True),
            "Перезапись мастера Google поставлена в очередь ручной синхронизации",
        )

    @Slot(str, result=bool)
    def resolveConflictUseGoogle(self, series_uid: str) -> bool:
        conflicts = self._series_conflicts()
        if conflicts is None:
            return False
        return self._run_conflict_action(
            "resolveUseGoogle",
            series_uid,
            lambda uid: conflicts.resolve_use_google(uid, confirmed=True),
            "Локальная серия обновлена по версии Google",
        )

    @Slot(str, result=bool)
    def resolveConflictDisconnect(self, series_uid: str) -> bool:
        conflicts = self._series_conflicts()
        if conflicts is None:
            return False
        return self._run_conflict_action(
            "resolveDisconnect",
            series_uid,
            conflicts.resolve_disconnect,
            "Связь отключена; обе версии сохранены",
        )

    @Slot(str, result=bool)
    def recoverRemoteDeletedKeepLocal(self, series_uid: str) -> bool:
        conflicts = self._series_conflicts()
        if conflicts is None:
            return False
        return self._run_conflict_action(
            "recoverKeepLocal",
            series_uid,
            conflicts.recover_remote_deleted_keep_local,
            "Серия осталась локальной; мёртвая связь отключена",
        )

    @Slot(str, result=bool)
    def recoverRemoteDeletedRecreate(self, series_uid: str) -> bool:
        conflicts = self._series_conflicts()
        if conflicts is None:
            return False
        return self._run_conflict_action(
            "recoverRecreate",
            series_uid,
            lambda uid: conflicts.recover_remote_deleted_recreate(
                uid, confirmed=True
            ),
            "Пересоздание серии Google поставлено в очередь ручной синхронизации",
        )

    @Slot(str, result=bool)
    def deleteRemoteDeletedLocalSeries(self, series_uid: str) -> bool:
        conflicts = self._series_conflicts()
        if conflicts is None:
            return False
        return self._run_conflict_action(
            "deleteRemoteDeletedLocal",
            series_uid,
            lambda uid: conflicts.delete_remote_deleted_local_series(
                uid, confirmed=True
            ),
            "Локальная серия удалена; выполненная история сохранена",
        )

    @Slot(str, str, str, int, bool, bool, str, str, str, bool, result=bool)
    def saveEditor(self, uid: str, title: str, notes: str, priority: int,
                   scheduled: bool, is_all_day: bool, date_text: str,
                   time_text: str, duration_text: str,
                   completed: bool) -> bool:
        return self._save_editor(
            uid, title, notes, priority, scheduled, is_all_day, date_text,
            time_text, duration_text, completed, None,
        )

    @Slot(str, str, str, int, bool, bool, str, str, str, bool,
          "QVariantList", result=bool)
    def saveEditorWithTags(
        self, uid: str, title: str, notes: str, priority: int,
        scheduled: bool, is_all_day: bool, date_text: str,
        time_text: str, duration_text: str, completed: bool, tag_ids,
    ) -> bool:
        return self._save_editor(
            uid, title, notes, priority, scheduled, is_all_day, date_text,
            time_text, duration_text, completed, list(tag_ids),
        )

    def _save_editor(
        self, uid: str, title: str, notes: str, priority: int,
        scheduled: bool, is_all_day: bool, date_text: str,
        time_text: str, duration_text: str, completed: bool,
        tag_ids: Optional[List[int]],
    ) -> bool:
        """Сохранение TaskEditorDialog (создание при пустом uid).

        Ошибки валидации не закрывают диалог: False + editorError.
        """
        tag_service = getattr(self._service, "tag_service", None)
        if tag_ids is not None:
            if tag_service is None and tag_ids:
                self._set_editor_error("Сервис тегов недоступен.")
                return False
            try:
                if tag_service is not None:
                    tag_service.resolve_tag_ids(tag_ids)
            except Exception as exc:
                self._set_editor_error(str(exc))
                return False

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
            ",".join(str(item) for item in (tag_ids or [])),
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

        if tag_ids is not None and tag_service is not None:
            try:
                tag_service.set_task_tags(result.task.uid, tag_ids)
            except Exception as exc:
                self._set_editor_error(f"Не удалось сохранить теги: {exc}")
                return False

        self._set_editor_error("")
        self._notify_mutation("Сохранено")
        return True

    @Slot(str, result="QVariantMap")
    def createTag(self, name: str) -> Dict[str, Any]:
        tag_service = getattr(self._service, "tag_service", None)
        if tag_service is None:
            return {"ok": False, "error": "Сервис тегов недоступен."}
        try:
            tag = tag_service.create(name)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        self.tasksMutated.emit()
        return {"ok": True, "id": tag.id, "name": tag.name, "error": ""}

    @Slot(str, "QVariantList", result=bool)
    def setTaskTags(self, uid: str, tag_ids) -> bool:
        tag_service = getattr(self._service, "tag_service", None)
        if tag_service is None:
            self.toastError.emit("Сервис тегов недоступен.")
            return False
        if not self._begin("setTags", uid, dedupe=True):
            return False
        try:
            tag_service.set_task_tags(uid, list(tag_ids))
        except Exception as exc:
            self.toastError.emit(str(exc))
            return False
        finally:
            self._end()
        self._notify_mutation("Теги обновлены")
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
        recurring = (
            task.google_calendar_recurring_event_id is not None
            or task.is_series_occurrence
        )
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
        recurring = (
            task.google_calendar_recurring_event_id is not None
            or task.is_series_occurrence
        )
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
            task = self._service.get_task(uid)
            recurrence = getattr(self._service, "recurrence_service", None)
            deleted = (
                recurrence.delete_occurrence(uid)
                if task is not None
                and task.is_series_occurrence
                and recurrence is not None
                else self._service.delete_task_by_uid(uid)
            )
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

    # ---- локальные повторяющиеся серии (Phase 3.2A) --------------------------

    @Property("QVariantList", constant=True)
    def recurrencePresets(self) -> List[dict]:
        return recurrence_presets()

    def _schedule_from_payload(
        self, payload: Dict[str, Any]
    ) -> Optional[SeriesSchedule]:
        """SeriesSchedule из полей формы; None при невалидной дате/времени."""
        date_text = str(payload.get("dateText") or "").strip()
        try:
            start_date = datetime.strptime(date_text, "%Y-%m-%d").date()
        except ValueError:
            return None
        is_all_day = bool(payload.get("isAllDay"))
        local_time = None
        if not is_all_day:
            try:
                local_time = datetime.strptime(
                    str(payload.get("timeText") or "").strip(), "%H:%M"
                ).time()
            except ValueError:
                return None
        duration_text = str(payload.get("durationText") or "").strip()
        duration = None
        if not is_all_day:
            try:
                duration = int(duration_text) if duration_text else 60
            except ValueError:
                return None
            if duration <= 0:
                return None
        return SeriesSchedule(
            start_date=start_date,
            all_day=is_all_day,
            local_time=local_time,
            duration_minutes=duration,
            timezone_name=default_timezone_name(),
        )

    @Slot("QVariantMap", result="QVariantMap")
    def recurrenceSummary(self, payload) -> Dict[str, Any]:
        """{ok, summary, error} для инлайн-сводки редактора правила."""
        payload = dict(payload or {})
        schedule = self._schedule_from_payload(payload)
        if schedule is None:
            return {"ok": False, "summary": "", "error": "Укажите дату начала."}
        rule = rule_from_map(payload.get("rule") or {}, schedule.start_date)
        validation = validate_rule(rule, schedule)
        if not validation.ok:
            return {
                "ok": False,
                "summary": "",
                "error": " ".join(validation.errors),
            }
        return {
            "ok": True,
            "summary": describe_rule(rule, schedule),
            "error": "",
        }

    @Property(str, constant=True)
    def localTimezoneName(self) -> str:
        return default_timezone_name()

    def _editor_command_from_payload(
        self, payload: Dict[str, Any]
    ) -> TaskEditorCommand:
        return TaskEditorCommand(
            title=str(payload.get("title") or ""),
            notes=str(payload.get("notes") or ""),
            add_to_calendar=bool(payload.get("scheduled", True)),
            is_all_day=bool(payload.get("isAllDay")),
            date_text=str(payload.get("dateText") or ""),
            time_text=str(payload.get("timeText") or ""),
            duration_text=str(payload.get("durationText") or ""),
            priority=int(payload.get("priority") or 0),
            completed=bool(payload.get("completed")),
        )

    def _payload_tag_ids(self, payload: Dict[str, Any]) -> Optional[List[int]]:
        raw = payload.get("tagIds")
        if raw is None:
            return None
        return [int(item) for item in raw]

    def _ensure_default_horizon(self) -> None:
        """Материализовать разумный горизонт после операции с серией."""
        materializer = getattr(self._service, "materializer", None)
        if materializer is not None:
            today = self._now().date()
            materializer.ensure_range(today, today + timedelta(days=30))

    @Slot("QVariantMap", result=bool)
    def saveEditorAsSeries(self, payload) -> bool:
        """Создание новой локальной серии из формы редактора.

        Никаких Calendar-операций: серия строго локальна в Phase 3.2A.
        """
        recurrence = getattr(self._service, "recurrence_service", None)
        if recurrence is None:
            self._set_editor_error("Сервис повторяющихся задач недоступен.")
            return False
        payload = dict(payload or {})
        schedule = self._schedule_from_payload(payload)
        if schedule is None:
            self._set_editor_error(
                "Для повторяющейся задачи укажите дату (и время)."
            )
            return False
        title = str(payload.get("title") or "").strip()
        if not title:
            self._set_editor_error("Название задачи не может быть пустым.")
            return False
        rule = rule_from_map(payload.get("rule") or {}, schedule.start_date)
        series = TaskSeries(
            title=title,
            schedule=schedule,
            rule=rule,
            notes=str(payload.get("notes") or "").strip(),
            priority=int(payload.get("priority") or 0),
        )
        if not self._begin("saveSeries", series.title, dedupe=True):
            return False
        try:
            result = recurrence.create_series(
                series, tag_ids=self._payload_tag_ids(payload)
            )
        except Exception as exc:
            self._set_editor_error(f"Не удалось создать серию: {exc}")
            return False
        finally:
            self._end()
        if not result.ok:
            self._set_editor_error(" ".join(result.errors))
            return False
        self._ensure_default_horizon()
        self._set_editor_error("")
        self._notify_mutation("Повторяющаяся задача создана")
        return True

    @Slot(str, str, "QVariantMap", result=bool)
    def saveOccurrenceScoped(self, uid: str, scope: str, payload) -> bool:
        """Сохранение экземпляра серии с ЯВНОЙ областью изменений.

        scope: this_occurrence — правка одной строки (exception);
        this_and_future — транзакционный split серии.
        """
        recurrence = getattr(self._service, "recurrence_service", None)
        if recurrence is None:
            self._set_editor_error("Сервис повторяющихся задач недоступен.")
            return False
        try:
            edit_scope = SeriesEditScope(scope)
        except ValueError:
            self._set_editor_error("Неизвестная область изменений.")
            return False
        payload = dict(payload or {})
        command = self._editor_command_from_payload(payload)
        tag_ids = self._payload_tag_ids(payload)
        tag_service = getattr(self._service, "tag_service", None)
        if tag_ids:
            if tag_service is None:
                self._set_editor_error("Сервис тегов недоступен.")
                return False
            try:
                tag_service.resolve_tag_ids(tag_ids)
            except Exception as exc:
                self._set_editor_error(str(exc))
                return False
        if not self._begin(f"saveOccurrence:{scope}", uid, dedupe=True):
            return False
        try:
            if edit_scope == SeriesEditScope.THIS_OCCURRENCE:
                result = recurrence.edit_occurrence(
                    uid, command, tag_ids=tag_ids
                )
                ok = result.ok
                errors = result.errors
                target_uid = result.task.uid if result.task is not None else ""
                toast = "Экземпляр изменён"
            elif edit_scope == SeriesEditScope.THIS_AND_FUTURE:
                rule = None
                if payload.get("rule") is not None:
                    schedule = self._schedule_from_payload(payload)
                    anchor = (
                        schedule.start_date if schedule is not None
                        else self._now().date()
                    )
                    rule = rule_from_map(payload.get("rule") or {}, anchor)
                split = recurrence.edit_this_and_future(
                    uid, command, rule=rule, tag_ids=tag_ids
                )
                ok = split.ok
                errors = split.errors
                target_uid = (
                    split.moved_task.uid if split.moved_task is not None else ""
                )
                toast = "Серия изменена с этого экземпляра"
            else:
                task = self._service.get_task(uid)
                if task is None or task.series_uid is None:
                    self._set_editor_error("Экземпляр серии не найден.")
                    return False
                schedule = self._schedule_from_payload(payload)
                if schedule is None:
                    self._set_editor_error(
                        "У повторяющейся серии должна быть дата начала."
                    )
                    return False
                rule = rule_from_map(
                    payload.get("rule") or {}, schedule.start_date
                )
                result = recurrence.update_series(
                    task.series_uid,
                    title=command.title,
                    notes=command.notes,
                    priority=command.priority,
                    schedule=schedule,
                    rule=rule,
                    tag_ids=tag_ids,
                )
                ok = result.ok
                errors = result.errors
                target_uid = uid
                toast = "Определение серии изменено"
        except Exception as exc:
            self._set_editor_error(f"Не удалось сохранить изменения: {exc}")
            return False
        finally:
            self._end()
        if not ok:
            self._set_editor_error(" ".join(errors))
            return False
        self._ensure_default_horizon()
        self._set_editor_error("")
        self._notify_mutation(toast)
        return True

    @Slot(str, result=bool)
    def stopSeriesFromOccurrence(self, uid: str) -> bool:
        """«Остановить этот и все будущие»: история сохраняется."""
        recurrence = getattr(self._service, "recurrence_service", None)
        if recurrence is None:
            self.toastError.emit("Сервис повторяющихся задач недоступен.")
            return False
        if not self._begin("stopSeries", uid, dedupe=True):
            return False
        try:
            result = recurrence.stop_this_and_future(uid)
        except Exception as exc:
            self.toastError.emit(f"Не удалось остановить серию: {exc}")
            return False
        finally:
            self._end()
        if not result.ok:
            self.toastError.emit(" ".join(result.errors))
            return False
        self._notify_mutation("Серия остановлена; история сохранена")
        return True

    @Slot(str, result=bool)
    def deleteSeries(self, series_uid: str) -> bool:
        """Удалить серию целиком; выполненная история остаётся."""
        recurrence = getattr(self._service, "recurrence_service", None)
        if recurrence is None:
            self.toastError.emit("Сервис повторяющихся задач недоступен.")
            return False
        if not self._begin("deleteSeries", series_uid, dedupe=True):
            return False
        try:
            result = recurrence.delete_series(series_uid)
        except Exception as exc:
            self.toastError.emit(f"Не удалось удалить серию: {exc}")
            return False
        finally:
            self._end()
        if not result.ok:
            self.toastError.emit(" ".join(result.errors))
            return False
        self._notify_mutation("Серия удалена; история сохранена")
        return True

    @Slot(str, result=bool)
    def duplicateSeries(self, series_uid: str) -> bool:
        """Явное «Создать копию серии» (определение, без экземпляров)."""
        recurrence = getattr(self._service, "recurrence_service", None)
        if recurrence is None:
            self.toastError.emit("Сервис повторяющихся задач недоступен.")
            return False
        if not self._begin("duplicateSeries", series_uid, dedupe=True):
            return False
        try:
            result = recurrence.duplicate_series(series_uid)
        except Exception as exc:
            self.toastError.emit(f"Не удалось создать копию серии: {exc}")
            return False
        finally:
            self._end()
        if not result.ok:
            self.toastError.emit(" ".join(result.errors))
            return False
        self._ensure_default_horizon()
        self._notify_mutation("Копия серии создана")
        return True

    # ---- шаблоны задач (Phase 3.2A) --------------------------------------------

    @Property("QVariantList", notify=templatesChanged)
    def taskTemplates(self) -> List[Dict[str, Any]]:
        templates = getattr(self._service, "template_service", None)
        if templates is None:
            return []
        return [
            {
                "uid": item.uid,
                "name": item.name,
                "kind": item.kind,
                "title": item.title,
                "isRecurring": item.is_recurring,
            }
            for item in templates.list_templates()
        ]

    @Slot(str, result=str)
    def seriesSummaryFor(self, uid: str) -> str:
        """Человекочитаемая сводка правила серии для экземпляра (или '')."""
        recurrence = getattr(self._service, "recurrence_service", None)
        if recurrence is None:
            return ""
        task = self._service.get_task(uid)
        if task is None or task.series_uid is None:
            return ""
        series = recurrence.get_series(task.series_uid)
        return series.summary() if series is not None else ""

    @Slot(str, result="QVariantMap")
    def templatePrefill(self, template_uid: str) -> Dict[str, Any]:
        """Предзаполнение редактора из шаблона; ничего не сохраняет."""
        templates = getattr(self._service, "template_service", None)
        if templates is None:
            return {}
        data = templates.editor_prefill(template_uid)
        if data and data.get("scheduled") and not data.get("dateText"):
            data["dateText"] = self._now().strftime("%Y-%m-%d")
        return data

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
