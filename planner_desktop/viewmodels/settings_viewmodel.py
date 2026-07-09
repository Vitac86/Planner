"""ViewModel страницы «Настройки»: статус нового десктопа без каких-либо
сетевых вызовов — только чтение локального состояния (путь БД, счётчики
Calendar-очереди, курсор синка).

Никакого автосинка здесь нет и не появится: реальный Google-шлюз ещё
не подключён, push/pull выполняются вручную отдельной фазой.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Property, QObject, Signal, Slot

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


class SettingsViewModel(QObject):
    stateChanged = Signal()

    def __init__(self, service: DesktopTaskService,
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._service = service

    @Property(str, constant=True)
    def appMode(self) -> str:
        return APP_MODE_TEXT

    @Property(str, constant=True)
    def syncNote(self) -> str:
        return SYNC_NOTE_TEXT

    @Property(str, constant=True)
    def dbPath(self) -> str:
        db_path = getattr(self._service.repository, "db_path", None)
        if isinstance(db_path, (str, Path)):
            return str(db_path)
        return "в памяти процесса (демо-режим, на диск не пишется)"

    @Property(bool, constant=True)
    def hasSyncQueue(self) -> bool:
        return self._service.has_sync_queue

    @Property(int, notify=stateChanged)
    def pendingOpsCount(self) -> int:
        return self._service.count_pending_ops()

    @Property(int, notify=stateChanged)
    def terminalOpsCount(self) -> int:
        return self._service.count_terminal_ops()

    @Property(str, notify=stateChanged)
    def syncCursor(self) -> str:
        cursor = self._service.sync_cursor()
        return cursor if cursor else "— (pull ещё не выполнялся)"

    @Slot()
    def refresh(self) -> None:
        self.stateChanged.emit()
