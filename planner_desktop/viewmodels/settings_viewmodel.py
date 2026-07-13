"""ViewModel страницы «Настройки»: статус нового десктопа без каких-либо
сетевых вызовов — только чтение локального состояния (путь БД, счётчики
Calendar-очереди, разбивка ожидающих операций, диагностика).

Никакого автосинка здесь нет и не появится: реальный Google-шлюз ещё
не подключён, push/pull выполняются вручную отдельной фазой. Кнопка
«Синхронизировать сейчас» намеренно отключена — она лишь честно сообщает,
что настоящего шлюза пока нет.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Property, QObject, Signal, Slot

from planner_desktop.usecases.daily_task_service import DailyTaskService
from planner_desktop.usecases.task_service import DesktopTaskService

APP_MODE_TEXT = (
    "Экспериментальный десктоп на PySide6 + Qt Quick/QML. "
    "Старое Flet-приложение (main.py) остаётся основным и не затронуто."
)
SYNC_NOTE_TEXT = (
    "Автоматической синхронизации с Google нет. Операции копятся в локальной "
    "очереди; реальный Google-шлюз и ручной запуск синка появятся в следующей "
    "фазе. Никакие Google API из нового приложения не вызываются."
)
MANUAL_SYNC_DISABLED_TEXT = (
    "Ручной синк недоступен: реальный шлюз Google Calendar ещё не подключён "
    "(появится в следующей фазе). Операции ниже уже готовятся локально."
)


def _format_local(stamp: datetime | None) -> str:
    if stamp is None:
        return "—"
    local = stamp.astimezone() if stamp.tzinfo is not None else stamp
    return local.strftime("%Y-%m-%d %H:%M")


class SettingsViewModel(QObject):
    stateChanged = Signal()

    def __init__(self, service: DesktopTaskService,
                 daily_service: DailyTaskService | None = None,
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._service = service
        self._daily = daily_service

    # ---- общие сведения ---------------------------------------------------------

    @Property(str, constant=True)
    def appMode(self) -> str:
        return APP_MODE_TEXT

    @Property(str, constant=True)
    def syncNote(self) -> str:
        return SYNC_NOTE_TEXT

    @Property(str, constant=True)
    def manualSyncNote(self) -> str:
        return MANUAL_SYNC_DISABLED_TEXT

    @Property(bool, constant=True)
    def manualSyncEnabled(self) -> bool:
        # Реального шлюза нет — кнопка всегда выключена (см. hard-constraints).
        return False

    @Property(str, constant=True)
    def dbPath(self) -> str:
        db_path = getattr(self._service.repository, "db_path", None)
        if isinstance(db_path, (str, Path)):
            return str(db_path)
        return "в памяти процесса (демо-режим, на диск не пишется)"

    @Property(bool, constant=True)
    def hasSyncQueue(self) -> bool:
        return self._service.has_sync_queue

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
        ]
        return "\n".join(lines)

    @Slot()
    def refresh(self) -> None:
        self.stateChanged.emit()
