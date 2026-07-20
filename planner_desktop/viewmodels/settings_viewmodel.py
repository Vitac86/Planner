"""ViewModel страницы «Настройки»: локальный статус + ручной Google-синк.

Что здесь есть:

- чтение локального состояния (путь БД, счётчики очереди, диагностика) —
  как раньше, без сети;
- статус подключения Google Calendar (только файловая система
  изолированного профиля: есть ли client_secret.json / token.json;
  сами токены наружу не отдаются);
- два ЯВНЫХ действия пользователя: «Подключить Google Calendar»
  (браузерный OAuth, рекомендуется тестовый аккаунт) и «Синхронизировать
  сейчас» (ровно один цикл push+pull через ManualSyncService).

Чего здесь НЕТ и не появится в этой фазе: автоматического/фонового
синка — ни таймеров, ни запуска при старте. Сеть трогается только внутри
явно нажатых действий, и обе операции выполняются вне GUI-потока
(QtBackgroundExecutor), поэтому QML не замирает; результат возвращается
сигналами, кнопки восстанавливаются и при успехе, и при ошибке.

Для headless-тестов все внешние зависимости инъецируются:
manual_sync_service, connection_checker, connector, executor.
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

from PySide6.QtCore import Property, QObject, Signal, Slot

from planner_desktop.domain.templates import (
    SCHEDULE_MODE_NONE,
    TEMPLATE_KIND_ORDINARY,
    TEMPLATE_KIND_RECURRING,
    TaskTemplate,
)
from planner_desktop.domain.series_calendar_link import (
    SeriesLinkStatus,
    readable_series_link_status,
)
from planner_desktop.usecases.daily_task_service import DailyTaskService
from planner_desktop.usecases.external_series_service import (
    CATALOG_NOTE_RU,
    ExternalSeriesService,
)
from planner_desktop.usecases.manual_sync_service import (
    LAST_SYNC_AT_KEY,
    LAST_SYNC_ERROR_KEY,
    LAST_SYNC_SUMMARY_KEY,
    ManualSyncResult,
    ManualSyncService,
)
from planner_desktop.usecases.task_service import DesktopTaskService
from planner_desktop.usecases.tag_service import TagService
from planner_desktop.viewmodels.series_rows import rule_from_map, rule_to_map

logger = logging.getLogger(__name__)

APP_MODE_TEXT = (
    "Экспериментальный десктоп на PySide6 + Qt Quick/QML. "
    "Старое Flet-приложение (main.py) остаётся основным и не затронуто."
)
SYNC_NOTE_TEXT = (
    "Синхронизация с Google Calendar выполняется ТОЛЬКО вручную — кнопкой "
    "ниже или командой `python -m scripts.desktop_calendar_sync_once "
    "--real-google`. Автоматического и фонового синка нет: ни при старте, "
    "ни по таймеру."
)
MANUAL_SYNC_NOTE_TEXT = (
    "Ручной синк выполняет один цикл push+pull. Токен хранится в "
    "изолированном профиле PlannerDesktop; профиль старого приложения не "
    "используется. Для первого подключения используйте ТЕСТОВЫЙ "
    "Google-аккаунт (см. docs/GOOGLE_SYNC_SETUP.md)."
)
SYNC_UNAVAILABLE_TEXT = (
    "Синк недоступен в этом режиме (нет локальной очереди операций)."
)


def _format_local(stamp: datetime | None) -> str:
    if stamp is None:
        return "—"
    local = stamp.astimezone() if stamp.tzinfo is not None else stamp
    return local.strftime("%Y-%m-%d %H:%M")


def _default_connection_checker() -> Any:
    from planner_desktop.sync import google_auth

    return google_auth.get_connection_status()


def _default_connector() -> Any:
    from planner_desktop.sync import google_auth

    return google_auth.connect_interactive()


class SettingsViewModel(QObject):
    stateChanged = Signal()
    syncStateChanged = Signal()
    tasksMutated = Signal()  # pull мог создать/изменить задачи — освежить страницы
    toastMessage = Signal(str)
    tagStateChanged = Signal()
    templateStateChanged = Signal()
    externalSeriesStateChanged = Signal()
    seriesLinksStateChanged = Signal()

    def __init__(self, service: DesktopTaskService,
                 daily_service: DailyTaskService | None = None,
                 parent: QObject | None = None,
                 *,
                 manual_sync_service: ManualSyncService | None = None,
                 tag_service: TagService | None = None,
                 connection_checker: Callable[[], Any] | None = None,
                 connector: Callable[[], Any] | None = None,
                 executor: Any | None = None,
                 external_series_service: ExternalSeriesService | None = None,
                 series_link_service=None,
                 series_sync_store=None,
                 occurrence_sync_store=None,
                 occurrence_resolution_service=None,
                 series_conflict_service=None,
                 remote_split_service=None) -> None:
        super().__init__(parent)
        self._service = service
        self._daily = daily_service
        self._sync_service = manual_sync_service
        self._tags = tag_service or getattr(service, "tag_service", None)
        self._connection_checker = connection_checker or _default_connection_checker
        self._connector = connector or _default_connector
        self._executor = executor  # лениво: QtBackgroundExecutor при первом действии
        self._external_series = external_series_service
        self._series_links = series_link_service
        self._series_sync_store = series_sync_store
        self._occurrence_sync_store = occurrence_sync_store
        self._occurrence_resolutions = occurrence_resolution_service
        self._series_conflicts = series_conflict_service
        self._remote_splits = remote_split_service
        self._busy_kind = ""       # "" | "connect" | "sync"
        self._live_error = ""      # ошибка текущей сессии (поверх сохранённой)
        self._live_error_set = False
        self._tag_busy = False
        self._tag_error = ""
        self._template_busy = False
        self._template_error = ""

    # ---- общие сведения ---------------------------------------------------------

    @Property(str, constant=True)
    def appMode(self) -> str:
        return APP_MODE_TEXT

    @Property(str, constant=True)
    def syncNote(self) -> str:
        return SYNC_NOTE_TEXT

    @Property(str, constant=True)
    def manualSyncNote(self) -> str:
        return MANUAL_SYNC_NOTE_TEXT

    @Property(str, constant=True)
    def dbPath(self) -> str:
        db_path = getattr(self._service.repository, "db_path", None)
        if isinstance(db_path, (str, Path)):
            return str(db_path)
        return "в памяти процесса (демо-режим, на диск не пишется)"

    @Property(bool, constant=True)
    def hasSyncQueue(self) -> bool:
        return self._service.has_sync_queue

    # ---- подключение Google (только файловая система, без сети) -------------------

    def _status(self) -> Any:
        try:
            return self._connection_checker()
        except Exception:  # статус не должен ронять страницу
            logger.exception("Не удалось прочитать статус подключения")
            return None

    @Property(bool, notify=syncStateChanged)
    def googleConnected(self) -> bool:
        status = self._status()
        return bool(status is not None and status.connected)

    @Property(bool, notify=syncStateChanged)
    def hasClientSecret(self) -> bool:
        status = self._status()
        return bool(status is not None and status.has_client_secret)

    @Property(str, notify=syncStateChanged)
    def tokenPath(self) -> str:
        status = self._status()
        return status.token_path if status is not None else ""

    @Property(str, notify=syncStateChanged)
    def clientSecretPath(self) -> str:
        status = self._status()
        return status.client_secret_path if status is not None else ""

    @Property(str, notify=syncStateChanged)
    def connectionStatusText(self) -> str:
        status = self._status()
        if status is None:
            return "Статус подключения недоступен."
        if status.connected:
            return "Google Calendar подключён (токен в изолированном профиле)."
        if status.has_client_secret:
            return ("Google Calendar не подключён. Нажмите «Подключить» и "
                    "войдите ТЕСТОВЫМ аккаунтом.")
        return ("Нет client_secret.json. Положите OAuth-секрет в:\n"
                f"{status.client_secret_path}")

    # ---- состояние действий -------------------------------------------------------

    @Property(bool, notify=syncStateChanged)
    def syncBusy(self) -> bool:
        return self._busy_kind != ""

    @Property(bool, notify=syncStateChanged)
    def syncRunning(self) -> bool:
        return self._busy_kind == "sync"

    @Property(bool, notify=syncStateChanged)
    def connectRunning(self) -> bool:
        return self._busy_kind == "connect"

    @Property(bool, notify=syncStateChanged)
    def manualSyncEnabled(self) -> bool:
        """Кнопка «Синхронизировать сейчас»: есть очередь и сервис синка,
        десктоп подключён к Google и прямо сейчас ничего не выполняется."""
        return (self._sync_service is not None
                and self._busy_kind == ""
                and self.googleConnected)

    @Property(bool, notify=syncStateChanged)
    def connectEnabled(self) -> bool:
        return self.hasClientSecret and self._busy_kind == ""

    # ---- сводка последнего синка ----------------------------------------------------

    @Property(str, notify=syncStateChanged)
    def lastSyncAt(self) -> str:
        raw = self._service.get_sync_state(LAST_SYNC_AT_KEY)
        if not raw:
            return "—"
        try:
            return _format_local(datetime.fromisoformat(raw))
        except ValueError:
            return raw

    @Property(str, notify=syncStateChanged)
    def lastSyncSummary(self) -> str:
        return self._service.get_sync_state(LAST_SYNC_SUMMARY_KEY) or ""

    @Property(str, notify=syncStateChanged)
    def lastSyncError(self) -> str:
        if self._live_error_set:
            return self._live_error
        return self._service.get_sync_state(LAST_SYNC_ERROR_KEY) or ""

    # ---- действия пользователя -------------------------------------------------------

    @Slot()
    def connectGoogle(self) -> None:
        """Явный первый вход (браузерный OAuth) — вне GUI-потока."""
        if self._busy_kind:
            return
        if not self.hasClientSecret:
            self._set_error("Нет client_secret.json — подключение невозможно. "
                            f"Ожидаемый путь: {self.clientSecretPath}")
            return
        self._busy_kind = "connect"
        self._set_error("", emit_signal=False)
        self.syncStateChanged.emit()
        self._submit(self._connector, self._on_connect_done)

    def _on_connect_done(self, outcome: Any) -> None:
        self._busy_kind = ""
        if isinstance(outcome, Exception):
            self._set_error(f"Подключение не удалось: {outcome}",
                            emit_signal=False)
        else:
            self._set_error("", emit_signal=False)
            self.toastMessage.emit("Google Calendar подключён")
        self.syncStateChanged.emit()
        self.stateChanged.emit()

    @Slot()
    def syncNow(self) -> None:
        """Один ручной цикл push+pull — вне GUI-потока. Никакого автозапуска."""
        if self._busy_kind:
            return
        if self._sync_service is None:
            self._set_error(SYNC_UNAVAILABLE_TEXT)
            return
        if not self.googleConnected:
            self._set_error("Google Calendar не подключён — сначала нажмите "
                            "«Подключить Google Calendar».")
            return
        self._busy_kind = "sync"
        self._set_error("", emit_signal=False)
        self.syncStateChanged.emit()
        self._submit(self._sync_service.run_once, self._on_sync_done)

    def _on_sync_done(self, outcome: Any) -> None:
        self._busy_kind = ""
        if isinstance(outcome, Exception):
            # Страховка: ManualSyncService сам не бросает, но кнопка обязана
            # ожить даже при неожиданном.
            self._set_error(f"Синхронизация упала: {outcome}", emit_signal=False)
        elif isinstance(outcome, ManualSyncResult) and not outcome.ok:
            self._set_error(outcome.error, emit_signal=False)
        else:
            self._set_error("", emit_signal=False)
            if isinstance(outcome, ManualSyncResult):
                self.toastMessage.emit(outcome.summary)
        self.syncStateChanged.emit()
        self.stateChanged.emit()   # счётчики очереди/курсор изменились
        self.externalSeriesStateChanged.emit()
        self.seriesLinksStateChanged.emit()
        self.tasksMutated.emit()   # pull мог создать/обновить/удалить задачи

    @Slot()
    def refresh(self) -> None:
        self.stateChanged.emit()
        self.syncStateChanged.emit()
        self.tagStateChanged.emit()
        self.templateStateChanged.emit()
        self.externalSeriesStateChanged.emit()
        self.seriesLinksStateChanged.emit()

    # ---- обнаруженные повторяющиеся серии Google (Phase 3.2B1) ---------------

    @Property(str, constant=True)
    def externalSeriesNote(self) -> str:
        return CATALOG_NOTE_RU

    def _external_series_diagnostics(self) -> dict:
        if self._external_series is None:
            return {
                "active_master_count": 0,
                "unsupported_master_count": 0,
                "cancelled_master_count": 0,
                "possible_legacy_master_import_count": 0,
                "last_catalog_refresh_at": None,
            }
        try:
            return self._external_series.diagnostics()
        except Exception:
            logger.exception("Не удалось прочитать каталог Google-серий")
            return {
                "active_master_count": 0,
                "unsupported_master_count": 0,
                "cancelled_master_count": 0,
                "possible_legacy_master_import_count": 0,
                "last_catalog_refresh_at": None,
            }

    @Property("QVariantList", notify=externalSeriesStateChanged)
    def externalSeriesRows(self):
        if self._external_series is None:
            return []
        try:
            return self._external_series.rows()
        except Exception:
            logger.exception("Не удалось прочитать строки Google-серий")
            return []

    @Property(int, notify=externalSeriesStateChanged)
    def externalActiveSeriesCount(self) -> int:
        return int(self._external_series_diagnostics()["active_master_count"])

    @Property(int, notify=externalSeriesStateChanged)
    def externalUnsupportedSeriesCount(self) -> int:
        return int(self._external_series_diagnostics()["unsupported_master_count"])

    @Property(int, notify=externalSeriesStateChanged)
    def externalCancelledSeriesCount(self) -> int:
        return int(self._external_series_diagnostics()["cancelled_master_count"])

    @Property(int, notify=externalSeriesStateChanged)
    def possibleLegacyMasterImportCount(self) -> int:
        return int(self._external_series_diagnostics()[
            "possible_legacy_master_import_count"
        ])

    @Property(str, notify=externalSeriesStateChanged)
    def externalSeriesLastRefresh(self) -> str:
        return _format_local(
            self._external_series_diagnostics()["last_catalog_refresh_at"]
        )

    # ---- explicit local-series links (schema v8, local reads only) ---------

    @Property(str, constant=True)
    def linkedSeriesNote(self) -> str:
        return (
            "Здесь показаны локальные связи и очередь мастеров. Страница не "
            "обращается к Google; сеть вызывается только кнопкой ручного синка."
        )

    def _series_link_diagnostics(self) -> dict:
        if self._series_sync_store is None:
            return {
                **{status.value: 0 for status in SeriesLinkStatus},
                "quarantined": 0,
                "series_ops_terminal": 0,
            }
        try:
            return self._series_sync_store.diagnostics()
        except Exception:
            logger.exception("Не удалось прочитать диагностику связей серий")
            return {
                **{status.value: 0 for status in SeriesLinkStatus},
                "quarantined": 0,
                "series_ops_terminal": 0,
            }

    @Property("QVariantList", notify=seriesLinksStateChanged)
    def linkedSeriesRows(self):
        if self._series_links is None:
            return []
        rows = []
        for link in self._series_links.list_links(include_detached=True):
            series = self._series_links.series_repository.get_by_uid(link.series_uid)
            pending = self._series_links.store.get_pending_op(link.series_uid)
            rows.append({
                "seriesUid": link.series_uid,
                "title": series.title if series is not None else "(локальная серия удалена)",
                "status": link.link_status.value,
                "statusText": readable_series_link_status(link.link_status),
                "remoteEventId": link.remote_event_id,
                "pendingOperation": pending.op.value if pending is not None else "",
                "lastError": link.last_error or "",
                "detached": link.link_status is SeriesLinkStatus.DETACHED,
                "generation": int(getattr(link, "link_generation", 0)),
                "conflictReason": link.conflict_reason or "",
                "resolutionKind": link.resolution_kind or "",
            })
        return rows

    @Property(int, notify=seriesLinksStateChanged)
    def linkedSeriesCount(self) -> int:
        diag = self._series_link_diagnostics()
        return int(diag.get(SeriesLinkStatus.SYNCED.value, 0))

    @Property(int, notify=seriesLinksStateChanged)
    def pendingSeriesCreateCount(self) -> int:
        return int(self._series_link_diagnostics().get("pending_create", 0))

    @Property(int, notify=seriesLinksStateChanged)
    def pendingSeriesUpdateCount(self) -> int:
        return int(self._series_link_diagnostics().get("pending_update", 0))

    @Property(int, notify=seriesLinksStateChanged)
    def pendingSeriesDeleteCount(self) -> int:
        return int(self._series_link_diagnostics().get("pending_delete", 0))

    @Property(int, notify=seriesLinksStateChanged)
    def conflictedSeriesCount(self) -> int:
        return int(self._series_link_diagnostics().get("conflict", 0))

    @Property(int, notify=seriesLinksStateChanged)
    def remoteDeletedSeriesCount(self) -> int:
        return int(self._series_link_diagnostics().get("remote_deleted", 0))

    @Property(int, notify=seriesLinksStateChanged)
    def terminalSeriesOpsCount(self) -> int:
        return int(self._series_link_diagnostics().get("series_ops_terminal", 0))

    @Property(int, notify=seriesLinksStateChanged)
    def quarantinedSeriesInstanceCount(self) -> int:
        return int(self._series_link_diagnostics().get("quarantined", 0))

    # ---- linked occurrence diagnostics and explicit quarantine actions ----

    def _occurrence_diagnostics(self) -> dict:
        if self._occurrence_sync_store is None:
            return {
                "occurrence_pending_update": 0,
                "occurrence_pending_cancel": 0,
                "occurrence_terminal": 0,
                "occurrence_quarantined": 0,
                "occurrence_remote_cancelled": 0,
                "occurrence_resolved_history": 0,
                "occurrence_exceptions": 0,
            }
        try:
            return self._occurrence_sync_store.diagnostics()
        except Exception:
            logger.exception("Could not read occurrence sync diagnostics")
            return {}

    @Property(int, notify=seriesLinksStateChanged)
    def pendingOccurrenceUpdateCount(self) -> int:
        return int(self._occurrence_diagnostics().get(
            "occurrence_pending_update", 0
        ))

    @Property(int, notify=seriesLinksStateChanged)
    def pendingOccurrenceCancelCount(self) -> int:
        return int(self._occurrence_diagnostics().get(
            "occurrence_pending_cancel", 0
        ))

    @Property(int, notify=seriesLinksStateChanged)
    def terminalOccurrenceOpsCount(self) -> int:
        return int(self._occurrence_diagnostics().get("occurrence_terminal", 0))

    @Property(int, notify=seriesLinksStateChanged)
    def unresolvedOccurrenceQuarantineCount(self) -> int:
        return int(self._occurrence_diagnostics().get(
            "occurrence_quarantined", 0
        ))

    @Property(int, notify=seriesLinksStateChanged)
    def remoteCancelledOccurrenceCount(self) -> int:
        return int(self._occurrence_diagnostics().get(
            "occurrence_remote_cancelled", 0
        ))

    @Property(int, notify=seriesLinksStateChanged)
    def resolvedOccurrenceHistoryCount(self) -> int:
        return int(self._occurrence_diagnostics().get(
            "occurrence_resolved_history", 0
        ))

    @Property(int, notify=seriesLinksStateChanged)
    def linkedOccurrenceExceptionCount(self) -> int:
        return int(self._occurrence_diagnostics().get(
            "occurrence_exceptions", 0
        ))

    @Property("QVariantList", notify=seriesLinksStateChanged)
    def quarantinedOccurrenceRows(self):
        if self._occurrence_sync_store is None:
            return []
        rows = []
        for change in self._occurrence_sync_store.list_occurrence_changes(
            unresolved_only=True
        ):
            supported = False
            reason = change.resolution_error or ""
            if self._occurrence_resolutions is not None:
                supported, support_reason = (
                    self._occurrence_resolutions.use_google_support(change.id)
                )
                reason = support_reason or reason
            payload = change.payload
            local = {}
            if (
                self._occurrence_resolutions is not None
                and change.matched_series_uid
            ):
                repository = self._occurrence_resolutions.task_repository
                task = next(
                    (
                        item for item in repository.list_by_series(
                            change.matched_series_uid
                        )
                        if item.occurrence_key == change.matched_occurrence_key
                    ),
                    None,
                )
                if task is not None:
                    local = {
                        "title": task.title,
                        "schedule": (
                            f"{task.start} - {task.end}"
                            if task.start is not None else ""
                        ),
                    }
            rows.append({
                "id": int(change.id or 0),
                "seriesUid": change.matched_series_uid or "",
                "occurrenceKey": change.matched_occurrence_key or "",
                "remoteInstanceId": change.remote_instance_event_id,
                "status": change.status,
                "title": str(payload.get("summary") or ""),
                "remoteCancelled": change.status == "cancelled",
                "remoteEtag": change.remote_etag or "",
                "resolutionStatus": change.resolution_status,
                "canUseGoogle": bool(supported),
                "useGoogleDisabledReason": reason,
                "local": local,
                "google": {
                    "title": str(payload.get("summary") or ""),
                    "schedule": (
                        f"{payload.get('start') or ''} - "
                        f"{payload.get('end') or ''}"
                    ),
                },
            })
        return rows

    def _run_occurrence_resolution(self, result, *, mutates_tasks: bool) -> bool:
        if not result.ok:
            self.toastMessage.emit(result.error)
            return False
        self.seriesLinksStateChanged.emit()
        if mutates_tasks:
            self.tasksMutated.emit()
        return True

    @Slot(int, result=bool)
    def useGoogleOccurrence(self, change_id: int) -> bool:
        if self._occurrence_resolutions is None:
            return False
        return self._run_occurrence_resolution(
            self._occurrence_resolutions.use_google(change_id),
            mutates_tasks=True,
        )

    @Slot(int, bool, result=bool)
    def keepPlannerOccurrence(self, change_id: int, confirmed: bool) -> bool:
        if self._occurrence_resolutions is None:
            return False
        return self._run_occurrence_resolution(
            self._occurrence_resolutions.keep_planner(
                change_id, confirmed=confirmed
            ),
            mutates_tasks=False,
        )

    @Slot(int, result=bool)
    def keepBothOccurrence(self, change_id: int) -> bool:
        if self._occurrence_resolutions is None:
            return False
        return self._run_occurrence_resolution(
            self._occurrence_resolutions.keep_both_as_local_copy(change_id),
            mutates_tasks=True,
        )

    @Slot(int, result=bool)
    def ignoreOccurrenceForNow(self, change_id: int) -> bool:
        if self._occurrence_resolutions is None:
            return False
        return self._run_occurrence_resolution(
            self._occurrence_resolutions.ignore_for_now(change_id),
            mutates_tasks=False,
        )

    # ---- explicit conflict resolution diagnostics (Phase 3.2B3A) -----------

    @Property(str, constant=True)
    def conflictResolutionNote(self) -> str:
        return (
            "Разрешение конфликтов выполняется только явными действиями "
            "пользователя. «Оставить версию Planner» и пересоздание серии "
            "выполняются следующей ручной синхронизацией; «Использовать "
            "версию Google» и отключение — локальные операции без сети."
        )

    @Property(int, notify=seriesLinksStateChanged)
    def pendingResolutionCount(self) -> int:
        return int(self._series_link_diagnostics().get("resolutions_pending", 0))

    @Property(int, notify=seriesLinksStateChanged)
    def failedResolutionCount(self) -> int:
        return int(self._series_link_diagnostics().get("resolutions_failed", 0))

    @Property(int, notify=seriesLinksStateChanged)
    def supersededResolutionCount(self) -> int:
        return int(
            self._series_link_diagnostics().get("resolutions_superseded", 0)
        )

    @Property("QVariantList", notify=seriesLinksStateChanged)
    def resolutionHistoryRows(self):
        if self._series_conflicts is None:
            return []
        try:
            history = self._series_conflicts.list_resolution_history()
        except Exception:
            logger.exception("Не удалось прочитать историю разрешений")
            return []
        rows = []
        for item in history[:50]:
            rows.append({
                "id": item.id,
                "seriesUid": item.series_uid,
                "kind": item.resolution_kind,
                "kindText": item.kind_text,
                "status": item.status,
                "statusText": item.status_text,
                "createdAt": _format_local(item.created_at),
                "completedAt": _format_local(item.completed_at),
                "error": item.error or "",
            })
        return rows

    # ---- remote "this and future" split plans (Phase 3.2B3C1) ---------------

    @Property(str, constant=True)
    def remoteSplitNote(self) -> str:
        return (
            "Разделение «этот и будущие» связанной серии выполняется "
            "durable-планом: удалённые шаги происходят только при ручной "
            "синхронизации. После завершения в Google существуют два "
            "мастера: исходный (прошлые экземпляры) и преемник."
        )

    def _remote_split_counts(self) -> dict:
        if self._remote_splits is None:
            return {}
        try:
            return self._remote_splits.diagnostics()
        except Exception:
            logger.exception("Не удалось прочитать диагностику разделений")
            return {}

    @Property(int, notify=seriesLinksStateChanged)
    def activeRemoteSplitCount(self) -> int:
        counts = self._remote_split_counts()
        return sum(int(counts.get(state, 0)) for state in (
            "pending", "source_trimmed", "successor_created",
            "local_finalize_pending", "rollback_pending",
            "successor_removed_for_rollback",
        ))

    @Property(int, notify=seriesLinksStateChanged)
    def partialRemoteSplitCount(self) -> int:
        counts = self._remote_split_counts()
        return sum(int(counts.get(state, 0)) for state in (
            "source_trimmed", "successor_created", "local_finalize_pending",
            "rollback_pending", "successor_removed_for_rollback",
        ))

    @Property(int, notify=seriesLinksStateChanged)
    def conflictRemoteSplitCount(self) -> int:
        return int(self._remote_split_counts().get("conflict", 0))

    @Property(int, notify=seriesLinksStateChanged)
    def terminalRemoteSplitCount(self) -> int:
        return int(self._remote_split_counts().get("terminal_error", 0))

    @Property(int, notify=seriesLinksStateChanged)
    def completedRemoteSplitCount(self) -> int:
        counts = self._remote_split_counts()
        return int(counts.get("completed", 0)) + int(
            counts.get("rolled_back", 0)
        )

    @Property("QVariantList", notify=seriesLinksStateChanged)
    def remoteSplitRows(self):
        if self._remote_splits is None:
            return []
        try:
            records = self._remote_splits.list_split_history()
        except Exception:
            logger.exception("Не удалось прочитать планы разделения")
            return []
        recurrence = getattr(self._service, "recurrence_service", None)
        rows = []
        for record in records[:50]:
            title = ""
            if recurrence is not None:
                series = recurrence.get_series(record.source_series_uid)
                title = series.title if series is not None else ""
            state = record.state.value
            rows.append({
                "id": record.id,
                "seriesUid": record.source_series_uid,
                "seriesTitle": title,
                "targetSlot": record.target_occurrence_key,
                "state": state,
                "statusText": record.status_text,
                "attempts": record.attempts,
                "lastError": record.last_error or "",
                "createdAt": _format_local(record.created_at),
                "completedAt": _format_local(record.completed_at),
                "isActive": record.is_active,
                "canCancel": state == "pending",
                "canRetry": state in (
                    "pending", "source_trimmed", "successor_created",
                    "local_finalize_pending", "rollback_pending",
                    "successor_removed_for_rollback",
                ),
                "canRollback": state in (
                    "source_trimmed", "successor_created",
                    "local_finalize_pending", "conflict",
                ),
                "successorRemoteId": record.successor_remote_event_id,
            })
        return rows

    def _run_remote_split_action(self, result) -> bool:
        if not result.ok:
            self.toastMessage.emit(
                result.error or "Операция с планом разделения не выполнена."
            )
            return False
        self.seriesLinksStateChanged.emit()
        return True

    @Slot(int, result=bool)
    def retryRemoteSplit(self, plan_id: int) -> bool:
        if self._remote_splits is None:
            return False
        return self._run_remote_split_action(
            self._remote_splits.retry_split(plan_id)
        )

    @Slot(int, result=bool)
    def rollbackRemoteSplit(self, plan_id: int) -> bool:
        if self._remote_splits is None:
            return False
        return self._run_remote_split_action(
            self._remote_splits.request_split_rollback(plan_id)
        )

    @Slot(int, result=bool)
    def cancelRemoteSplit(self, plan_id: int) -> bool:
        if self._remote_splits is None:
            return False
        return self._run_remote_split_action(
            self._remote_splits.cancel_unstarted_split(plan_id)
        )

    # ---- локальные теги --------------------------------------------------------

    @Property(str, constant=True)
    def tagNote(self) -> str:
        return (
            "Теги хранятся только в Planner Desktop и не отправляются "
            "в Google Calendar."
        )

    @Property("QVariantList", notify=tagStateChanged)
    def tags(self):
        if self._tags is None:
            return []
        return [
            {"id": item.tag.id, "name": item.tag.name,
             "taskCount": item.task_count}
            for item in self._tags.list_with_counts()
        ]

    @Property(int, notify=tagStateChanged)
    def tagCount(self) -> int:
        return len(self.tags)

    @Property(bool, notify=tagStateChanged)
    def tagBusy(self) -> bool:
        return self._tag_busy

    @Property(str, notify=tagStateChanged)
    def tagError(self) -> str:
        return self._tag_error

    @Slot(str, result=bool)
    def createTag(self, name: str) -> bool:
        return self._tag_action(
            lambda: self._tags.create(name) if self._tags is not None else None,
            "Тег создан",
        )

    @Slot(int, str, result=bool)
    def renameTag(self, tag_id: int, name: str) -> bool:
        return self._tag_action(
            lambda: self._tags.rename(tag_id, name) if self._tags is not None else None,
            "Тег переименован",
        )

    @Slot(int, result=bool)
    def deleteTag(self, tag_id: int) -> bool:
        return self._tag_action(
            lambda: self._tags.delete(tag_id) if self._tags is not None else None,
            "Тег удалён; задачи сохранены",
        )

    @Slot()
    def clearTagError(self) -> None:
        if self._tag_error:
            self._tag_error = ""
            self.tagStateChanged.emit()

    def _tag_action(self, operation, success_message: str) -> bool:
        if self._tag_busy:
            return False
        if self._tags is None:
            self._tag_error = "Сервис тегов недоступен."
            self.tagStateChanged.emit()
            return False
        self._tag_busy = True
        self.tagStateChanged.emit()
        try:
            operation()
        except Exception as exc:
            self._tag_error = str(exc)
            return False
        finally:
            self._tag_busy = False
            self.tagStateChanged.emit()
        self._tag_error = ""
        self.tagStateChanged.emit()
        self.stateChanged.emit()
        self.tasksMutated.emit()
        self.toastMessage.emit(success_message)
        return True

    # ---- шаблоны задач (Phase 3.2A) ------------------------------------------------

    @Property(str, constant=True)
    def templateNote(self) -> str:
        return (
            "Шаблоны хранятся только в Planner Desktop и не отправляются "
            "в Google Calendar. Применение шаблона предзаполняет редактор; "
            "правка шаблона не меняет уже созданные задачи."
        )

    def _template_service(self):
        return getattr(self._service, "template_service", None)

    @Property("QVariantList", notify=templateStateChanged)
    def templates(self):
        service = self._template_service()
        if service is None:
            return []
        rows = []
        for item in service.list_templates():
            rows.append({
                "uid": item.uid,
                "name": item.name,
                "kind": item.kind,
                "kindLabel": (
                    "Повторяющаяся серия" if item.is_recurring else "Обычная задача"
                ),
                "title": item.title,
                "isRecurring": item.is_recurring,
            })
        return rows

    @Property(int, notify=templateStateChanged)
    def templateCount(self) -> int:
        return len(self.templates)

    @Property(bool, notify=templateStateChanged)
    def templateBusy(self) -> bool:
        return self._template_busy

    @Property(str, notify=templateStateChanged)
    def templateError(self) -> str:
        return self._template_error

    @Slot(str, result="QVariantMap")
    def templateDataFor(self, uid: str):
        """Данные шаблона для TemplateEditorDialog (пустая форма для '')."""
        service = self._template_service()
        template = service.get_template(uid) if service and uid else None
        if template is None:
            return {
                "exists": False,
                "uid": "",
                "name": "",
                "kind": TEMPLATE_KIND_ORDINARY,
                "title": "",
                "notes": "",
                "priority": 0,
                "scheduleMode": SCHEDULE_MODE_NONE,
                "timeText": "",
                "durationText": "",
                "tagIds": [],
                "rule": rule_to_map(None),
            }
        tag_ids = list(service.repository.tag_ids_for_template(uid))
        return {
            "exists": True,
            "uid": template.uid,
            "name": template.name,
            "kind": template.kind,
            "title": template.title,
            "notes": template.notes,
            "priority": template.priority,
            "scheduleMode": template.schedule_mode,
            "timeText": template.time_text,
            "durationText": (
                str(template.duration_minutes)
                if template.duration_minutes else ""
            ),
            "tagIds": tag_ids,
            "rule": rule_to_map(template.rule),
        }

    def _template_from_map(self, data) -> TaskTemplate:
        data = dict(data or {})
        kind = str(data.get("kind") or TEMPLATE_KIND_ORDINARY)
        duration_text = str(data.get("durationText") or "").strip()
        duration = int(duration_text) if duration_text.isdigit() else None
        rule = None
        if kind == TEMPLATE_KIND_RECURRING:
            rule = rule_from_map(data.get("rule") or {})
        return TaskTemplate(
            name=str(data.get("name") or ""),
            kind=kind,
            title=str(data.get("title") or "").strip(),
            notes=str(data.get("notes") or "").strip(),
            priority=int(data.get("priority") or 0),
            schedule_mode=str(data.get("scheduleMode") or SCHEDULE_MODE_NONE),
            time_text=str(data.get("timeText") or "").strip(),
            duration_minutes=duration,
            rule=rule,
        )

    @staticmethod
    def _template_tag_ids(data) -> Optional[list]:
        raw = dict(data or {}).get("tagIds")
        if raw is None:
            return None
        return [int(item) for item in raw]

    @Slot("QVariantMap", result=bool)
    def createTemplate(self, data) -> bool:
        return self._template_action(
            lambda service: service.create_template(
                self._template_from_map(data),
                tag_ids=self._template_tag_ids(data),
            ),
            "Шаблон создан",
        )

    @Slot(str, "QVariantMap", result=bool)
    def updateTemplate(self, uid: str, data) -> bool:
        return self._template_action(
            lambda service: service.update_template(
                uid,
                self._template_from_map(data),
                tag_ids=self._template_tag_ids(data),
            ),
            "Шаблон изменён",
        )

    @Slot(str, result=bool)
    def duplicateTemplate(self, uid: str) -> bool:
        return self._template_action(
            lambda service: service.duplicate_template(uid),
            "Копия шаблона создана",
        )

    @Slot(str, result=bool)
    def deleteTemplate(self, uid: str) -> bool:
        """Удаляет только шаблон: созданные из него задачи/серии остаются."""
        def operation(service):
            ok = service.delete_template(uid)
            if not ok:
                raise KeyError("Шаблон не найден.")
            return ok
        return self._template_action(
            operation, "Шаблон удалён; созданные задачи сохранены"
        )

    @Slot()
    def clearTemplateError(self) -> None:
        if self._template_error:
            self._template_error = ""
            self.templateStateChanged.emit()

    def _template_action(self, operation, success_message: str) -> bool:
        if self._template_busy:
            return False
        service = self._template_service()
        if service is None:
            self._template_error = "Сервис шаблонов недоступен."
            self.templateStateChanged.emit()
            return False
        self._template_busy = True
        self.templateStateChanged.emit()
        try:
            result = operation(service)
            errors = getattr(result, "errors", None)
            if errors:
                self._template_error = " ".join(errors)
                return False
        except Exception as exc:
            self._template_error = str(exc)
            return False
        finally:
            self._template_busy = False
            self.templateStateChanged.emit()
        self._template_error = ""
        self.templateStateChanged.emit()
        self.toastMessage.emit(success_message)
        return True

    # ---- диагностика локальных серий (Phase 3.2A) ----------------------------------

    @Property(str, constant=True)
    def seriesNote(self) -> str:
        return (
            "Локальные серии не синхронизируются с Google Calendar в этой "
            "фазе: экземпляры существуют только в Planner Desktop."
        )

    def _recurrence_service(self):
        return getattr(self._service, "recurrence_service", None)

    def _series_diagnostics(self) -> dict:
        service = self._recurrence_service()
        if service is None:
            return {"active_series": 0, "occurrences": 0, "exceptions": 0}
        try:
            return service.diagnostics()
        except Exception:
            logger.exception("Не удалось прочитать диагностику серий")
            return {"active_series": 0, "occurrences": 0, "exceptions": 0}

    @Property(int, notify=stateChanged)
    def activeSeriesCount(self) -> int:
        return int(self._series_diagnostics().get("active_series", 0))

    @Property(int, notify=stateChanged)
    def seriesOccurrenceCount(self) -> int:
        return int(self._series_diagnostics().get("occurrences", 0))

    @Property(int, notify=stateChanged)
    def seriesExceptionCount(self) -> int:
        return int(self._series_diagnostics().get("exceptions", 0))

    @Property(str, notify=stateChanged)
    def materializationHorizonText(self) -> str:
        materializer = getattr(self._service, "materializer", None)
        if materializer is None:
            return "—"
        covered_end = materializer.covered_end
        if covered_end is None:
            return "ещё не материализовано"
        return covered_end.strftime("%d.%m.%Y")

    # ---- внутреннее -------------------------------------------------------------------

    def _submit(self, fn: Callable[[], Any], callback: Callable[[Any], None]) -> None:
        if self._executor is None:
            from planner_desktop.viewmodels.background import QtBackgroundExecutor

            self._executor = QtBackgroundExecutor(self)
        self._executor.submit(fn, callback)

    def _set_error(self, message: str, *, emit_signal: bool = True) -> None:
        self._live_error = message
        self._live_error_set = True
        if emit_signal:
            self.syncStateChanged.emit()

    # ---- статус Calendar-очереди ------------------------------------------------

    @Property(int, notify=stateChanged)
    def pendingOpsCount(self) -> int:
        return self._service.count_pending_ops()

    @Property(int, notify=stateChanged)
    def pendingCreateCount(self) -> int:
        return self._service.pending_ops_breakdown().get("create", 0)

    @Property(int, notify=stateChanged)
    def pendingUpdateCount(self) -> int:
        return self._service.pending_ops_breakdown().get("update", 0)

    @Property(int, notify=stateChanged)
    def pendingDeleteCount(self) -> int:
        return self._service.pending_ops_breakdown().get("delete", 0)

    @Property(int, notify=stateChanged)
    def terminalOpsCount(self) -> int:
        return self._service.count_terminal_ops()

    @Property(str, notify=stateChanged)
    def lastLocalChange(self) -> str:
        return _format_local(self._service.last_local_change())

    @Property(str, notify=stateChanged)
    def syncCursor(self) -> str:
        cursor = self._service.sync_cursor()
        return cursor if cursor else "— (pull ещё не выполнялся)"

    # ---- диагностика ------------------------------------------------------------

    @Property(int, notify=stateChanged)
    def schemaVersion(self) -> int:
        return self._service.schema_version()

    @Property(int, notify=stateChanged)
    def taskCount(self) -> int:
        return self._service.count_active_tasks()

    @Property(int, notify=stateChanged)
    def dailyTaskCount(self) -> int:
        return len(self._daily.list_all()) if self._daily is not None else 0

    @Property(str, notify=stateChanged)
    def diagnosticsText(self) -> str:
        """Готовая к копированию сводка. Токены/личные данные не включаются."""
        breakdown = self._service.pending_ops_breakdown()
        lines = [
            "Planner Desktop — диагностика",
            f"Путь БД: {self.dbPath}",
            f"Версия схемы: {self.schemaVersion}",
            f"Задач (активных): {self.taskCount}",
            f"Ежедневных задач: {self.dailyTaskCount}",
            f"Операций в очереди: {self.pendingOpsCount} "
            f"(create {breakdown.get('create', 0)}, "
            f"update {breakdown.get('update', 0)}, "
            f"delete {breakdown.get('delete', 0)})",
            f"Dead-letter: {self.terminalOpsCount}",
            f"Последнее локальное изменение: {self.lastLocalChange}",
            f"Курсор pull-а: {self.syncCursor}",
            f"Google подключён: {'да' if self.googleConnected else 'нет'}",
            f"Последний синк: {self.lastSyncAt}",
            f"Google-серий (активных): {self.externalActiveSeriesCount}",
            f"Google-серий (неподдерживаемых): {self.externalUnsupportedSeriesCount}",
            f"Google-серий (отменённых): {self.externalCancelledSeriesCount}",
            f"Возможных старых импортов мастера: {self.possibleLegacyMasterImportCount}",
            f"Обновление каталога Google-серий: {self.externalSeriesLastRefresh}",
        ]
        return "\n".join(lines)
