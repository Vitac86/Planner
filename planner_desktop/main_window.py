"""Главное окно нового десктопа: движок QML + привязка ViewModel-ей."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from PySide6.QtQml import QQmlApplicationEngine

from planner_desktop.repositories import TaskRepository
from planner_desktop.repositories.fake_task_repository import FakeTaskRepository
from planner_desktop.storage.calendar_sync_store import CalendarSyncStore
from planner_desktop.storage.sqlite_task_repository import SQLiteTaskRepository
from planner_desktop.usecases.task_service import DesktopTaskService
from planner_desktop.viewmodels.calendar_viewmodel import CalendarViewModel
from planner_desktop.viewmodels.today_viewmodel import TodayViewModel

QML_DIR = Path(__file__).resolve().parent / "qml"


def _default_service() -> DesktopTaskService:
    """Сервис задач поверх SQLite-хранилища (PlannerDesktop/app_desktop.db).

    Календарная очередь живёт в той же изолированной БД: задачи с датой
    из Quick Add копят pending-операции для будущего движка синка —
    сетевых вызовов при этом нет, реальный Google-шлюз ещё не подключён.

    PLANNER_DESKTOP_DEMO=1 включает фейковый репозиторий с демо-данными —
    ничего не пишется на диск (и очередь не создаётся). Старый
    Planner/app.db не открывается ни в одном из режимов.
    """
    if os.environ.get("PLANNER_DESKTOP_DEMO") == "1":
        return DesktopTaskService(FakeTaskRepository())
    repository = SQLiteTaskRepository()
    queue = CalendarSyncStore(repository.db_path)
    return DesktopTaskService(repository, calendar_queue=queue)


class MainWindow:
    """Владеет QQmlApplicationEngine и ViewModel-ями.

    Один общий репозиторий на все страницы, чтобы задача, добавленная
    через Quick Add, сразу была видна в календарной сетке.
    """

    def __init__(self, repository: Optional[TaskRepository] = None) -> None:
        if repository is not None:
            self.service = DesktopTaskService(repository)  # тесты: без очереди
        else:
            self.service = _default_service()
        self.repository = self.service.repository
        self.today_viewmodel = TodayViewModel(service=self.service)
        self.calendar_viewmodel = CalendarViewModel(self.repository)

        self.engine = QQmlApplicationEngine()
        context = self.engine.rootContext()
        context.setContextProperty("todayVm", self.today_viewmodel)
        context.setContextProperty("calendarVm", self.calendar_viewmodel)

    def show(self) -> None:
        self.engine.load(str(QML_DIR / "Main.qml"))
        if not self.engine.rootObjects():
            raise RuntimeError("Не удалось загрузить qml/Main.qml")
