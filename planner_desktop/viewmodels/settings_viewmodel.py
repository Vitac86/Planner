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

from planner_desktop.usecases.daily_task_service import DailyTaskService
from planner_desktop.usecases.manual_sync_service import (
    LAST_SYNC_AT_KEY,
    LAST_SYNC_ERROR_KEY,
    LAST_SYNC_SUMMARY_KEY,
    ManualSyncResult,
    ManualSyncService,
)
from planner_desktop.usecases.task_service import DesktopTaskService
from planner_desktop.usecases.tag_service import TagService

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

    def __init__(self, service: DesktopTaskService,
                 daily_service: DailyTaskService | None = None,
                 parent: QObject | None = None,
                 *,
                 manual_sync_service: ManualSyncService | None = None,
                 tag_service: TagService | None = None,
                 connection_checker: Callable[[], Any] | None = None,
                 connector: Callable[[], Any] | None = None,
                 executor: Any | None = None) -> None:
        super().__init__(parent)
        self._service = service
        self._daily = daily_service
        self._sync_service = manual_sync_service
        self._tags = tag_service or getattr(service, "tag_service", None)
        self._connection_checker = connection_checker or _default_connection_checker
        self._connector = connector or _default_connector
        self._executor = executor  # лениво: QtBackgroundExecutor при первом действии
        self._busy_kind = ""       # "" | "connect" | "sync"
        self._live_error = ""      # ошибка текущей сессии (поверх сохранённой)
        self._live_error_set = False
        self._tag_busy = False
        self._tag_error = ""

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
        self.tasksMutated.emit()   # pull мог создать/обновить/удалить задачи

    @Slot()
    def refresh(self) -> None:
        self.stateChanged.emit()
        self.syncStateChanged.emit()
        self.tagStateChanged.emit()

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
        ]
        return "\n".join(lines)
