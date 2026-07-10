"""Главное окно нового десктопа: движок QML + привязка ViewModel-ей."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from PySide6.QtQml import QQmlApplicationEngine

from planner_desktop.repositories import TaskRepository
from planner_desktop.repositories.daily_task_repository import (
    InMemoryDailyTaskRepository,
)
from planner_desktop.repositories.fake_task_repository import FakeTaskRepository
from planner_desktop.storage.calendar_sync_store import CalendarSyncStore
from planner_desktop.storage.sqlite_daily_task_repository import (
    SQLiteDailyTaskRepository,
)
from planner_desktop.storage.sqlite_task_repository import SQLiteTaskRepository
from planner_desktop.usecases.daily_task_service import DailyTaskService
from planner_desktop.usecases.task_service import DesktopTaskService
from planner_desktop.viewmodels.calendar_viewmodel import CalendarViewModel
from planner_desktop.viewmodels.daily_tasks_viewmodel import DailyTasksViewModel
from planner_desktop.viewmodels.settings_viewmodel import SettingsViewModel
from planner_desktop.viewmodels.today_viewmodel import TodayViewModel

QML_DIR = Path(__file__).resolve().parent / "qml"


def _build_services() -> tuple[DesktopTaskService, DailyTaskService]:
    """Сервисы задач и ежедневных задач поверх SQLite (app_desktop.db).

    Календарная очередь и ежедневные задачи живут в той же изолированной
    БД: сетевых вызовов нет, реальный Google-шлюз ещё не подключён.

    PLANNER_DESKTOP_DEMO=1 включает фейковые in-memory репозитории с
    демо-данными — ничего не пишется на диск. Старый Planner/app.db не
    открывается ни в одном из режимов.
    """
    if os.environ.get("PLANNER_DESKTOP_DEMO") == "1":
        return (
            DesktopTaskService(FakeTaskRepository()),
            DailyTaskService(InMemoryDailyTaskRepository(seed=True)),
        )
    repository = SQLiteTaskRepository()
    queue = CalendarSyncStore(repository.db_path)
    daily_repo = SQLiteDailyTaskRepository(repository.db_path)
    return (
        DesktopTaskService(repository, calendar_queue=queue),
        DailyTaskService(daily_repo),
    )


class MainWindow:
    """Владеет QQmlApplicationEngine и ViewModel-ями.

    Один общий репозиторий на все страницы, чтобы задача, добавленная
    через Quick Add, сразу была видна в календарной сетке. Ежедневные
    задачи — общий DailyTaskService между «Сегодня» и диалогом управления.
    """

    def __init__(self, repository: Optional[TaskRepository] = None) -> None:
        if repository is not None:
            # тесты: инъекция репозитория задач, ежедневные — in-memory.
            self.service = DesktopTaskService(repository)  # без очереди
            self.daily_service = DailyTaskService(InMemoryDailyTaskRepository())
        else:
            self.service, self.daily_service = _build_services()
        self.repository = self.service.repository
        self.today_viewmodel = TodayViewModel(
            service=self.service, daily_service=self.daily_service)
        self.calendar_viewmodel = CalendarViewModel(service=self.service)
        self.settings_viewmodel = SettingsViewModel(self.service)
        self.daily_viewmodel = DailyTasksViewModel(self.daily_service)

        # Мутация на одной странице освежает остальные. Петли нет:
        # refresh() эмитит только *Changed-сигналы, а не tasksMutated.
        self.today_viewmodel.tasksMutated.connect(self.calendar_viewmodel.refresh)
        self.today_viewmodel.tasksMutated.connect(self.settings_viewmodel.refresh)
        self.calendar_viewmodel.tasksMutated.connect(self.today_viewmodel.refresh)
        self.calendar_viewmodel.tasksMutated.connect(self.settings_viewmodel.refresh)

        # Ежедневные: правки в диалоге управления освежают чек-лист «Сегодня»
        # и наоборот. refresh()/refreshDaily() эмитят только *Changed —
        # без mutated, поэтому петли нет.
        self.daily_viewmodel.mutated.connect(self.today_viewmodel.refreshDaily)
        self.today_viewmodel.dailyMutated.connect(self.daily_viewmodel.refresh)

        self.engine = QQmlApplicationEngine()
        context = self.engine.rootContext()
        context.setContextProperty("todayVm", self.today_viewmodel)
        context.setContextProperty("calendarVm", self.calendar_viewmodel)
        context.setContextProperty("settingsVm", self.settings_viewmodel)
        context.setContextProperty("dailyVm", self.daily_viewmodel)

    def show(self) -> None:
        self.engine.load(str(QML_DIR / "Main.qml"))
        if not self.engine.rootObjects():
            raise RuntimeError("Не удалось загрузить qml/Main.qml")
