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
from planner_desktop.viewmodels.history_viewmodel import HistoryViewModel
from planner_desktop.viewmodels.search_viewmodel import SearchViewModel
from planner_desktop.viewmodels.settings_viewmodel import SettingsViewModel
from planner_desktop.viewmodels.today_viewmodel import TodayViewModel
from planner_desktop.viewmodels.ui_state import UiStateViewModel

QML_DIR = Path(__file__).resolve().parent / "qml"


def _build_services() -> tuple[DesktopTaskService, DailyTaskService]:
    """Сервисы задач и ежедневных задач поверх SQLite (app_desktop.db).

    Календарная очередь и ежедневные задачи живут в той же изолированной
    БД. Сетевых вызовов при сборке сервисов нет: реальный Google-шлюз
    строится лениво внутри ManualSyncService и только по явному действию
    пользователя (кнопка синка в настройках).

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
        self.calendar_viewmodel = CalendarViewModel(
            service=self.service, daily_service=self.daily_service)
        self.settings_viewmodel = SettingsViewModel(
            self.service, daily_service=self.daily_service,
            manual_sync_service=self._build_manual_sync_service())
        self.daily_viewmodel = DailyTasksViewModel(self.daily_service)
        self.history_viewmodel = HistoryViewModel(self.service, self.daily_service)
        self.search_viewmodel = SearchViewModel(self.service)
        self.ui_state_viewmodel = UiStateViewModel()

        # Мутация задач на одной странице освежает остальные. Петли нет:
        # refresh() эмитит только *Changed-сигналы, а не tasksMutated.
        # Настройки — тоже мутатор: pull ручного синка меняет задачи.
        task_mutators = (self.today_viewmodel, self.calendar_viewmodel,
                         self.history_viewmodel, self.search_viewmodel,
                         self.settings_viewmodel)
        task_listeners = (self.today_viewmodel, self.calendar_viewmodel,
                          self.settings_viewmodel, self.history_viewmodel,
                          self.search_viewmodel)
        for mutator in task_mutators:
            for listener in task_listeners:
                if listener is not mutator:
                    mutator.tasksMutated.connect(listener.refresh)

        # Ежедневные: правки в диалоге управления освежают чек-лист «Сегодня»
        # и «Календаря» и наоборот. refresh()/refreshDaily()/setRange() эмитят
        # только *Changed — без mutated, поэтому петли нет.
        self.daily_viewmodel.mutated.connect(self.today_viewmodel.refreshDaily)
        self.daily_viewmodel.mutated.connect(self.calendar_viewmodel.refreshDaily)
        self.today_viewmodel.dailyMutated.connect(self.daily_viewmodel.refresh)
        self.today_viewmodel.dailyMutated.connect(self.calendar_viewmodel.refreshDaily)
        self.today_viewmodel.dailyMutated.connect(self.history_viewmodel.refresh)
        self.calendar_viewmodel.dailyMutated.connect(self.today_viewmodel.refreshDaily)
        self.calendar_viewmodel.dailyMutated.connect(self.daily_viewmodel.refresh)
        self.calendar_viewmodel.dailyMutated.connect(self.history_viewmodel.refresh)

        self.engine = QQmlApplicationEngine()
        context = self.engine.rootContext()
        context.setContextProperty("todayVm", self.today_viewmodel)
        context.setContextProperty("calendarVm", self.calendar_viewmodel)
        context.setContextProperty("settingsVm", self.settings_viewmodel)
        context.setContextProperty("dailyVm", self.daily_viewmodel)
        context.setContextProperty("historyVm", self.history_viewmodel)
        context.setContextProperty("searchVm", self.search_viewmodel)
        context.setContextProperty("uiVm", self.ui_state_viewmodel)

    def _build_manual_sync_service(self):
        """ManualSyncService для кнопки «Синхронизировать сейчас».

        Провайдер шлюза ленивый (google_auth.build_real_gateway): ни OAuth,
        ни сети при создании окна — только при явном нажатии кнопки.
        for_db_path обязателен: run_once() выполняется в фоновом Qt-потоке,
        а SQLite-соединения GUI-потока туда переносить нельзя — сервис
        открывает свои на время цикла. В режимах без очереди (демо/
        тестовая инъекция) синк недоступен.
        """
        if not self.service.has_sync_queue:
            return None

        from planner_desktop.sync.google_auth import build_real_gateway
        from planner_desktop.usecases.manual_sync_service import ManualSyncService

        return ManualSyncService.for_db_path(
            self.service.calendar_queue.db_path,
            gateway_provider=build_real_gateway,
        )

    def show(self) -> None:
        self.engine.load(str(QML_DIR / "Main.qml"))
        if not self.engine.rootObjects():
            raise RuntimeError("Не удалось загрузить qml/Main.qml")
