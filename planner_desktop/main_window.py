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
from planner_desktop.repositories.external_series_repository import (
    InMemoryExternalSeriesRepository,
)
from planner_desktop.repositories.series_repository import InMemorySeriesRepository
from planner_desktop.repositories.tag_repository import InMemoryTagRepository
from planner_desktop.repositories.template_repository import (
    InMemoryTemplateRepository,
)
from planner_desktop.storage.calendar_sync_store import CalendarSyncStore
from planner_desktop.storage.calendar_series_sync_store import (
    CalendarSeriesSyncStore,
)
from planner_desktop.storage.external_series_repository import (
    SQLiteExternalSeriesRepository,
)
from planner_desktop.storage.series_repository import SQLiteSeriesRepository
from planner_desktop.storage.sqlite_daily_task_repository import (
    SQLiteDailyTaskRepository,
)
from planner_desktop.storage.sqlite_task_repository import SQLiteTaskRepository
from planner_desktop.storage.tag_repository import SQLiteTagRepository
from planner_desktop.storage.template_repository import SQLiteTemplateRepository
from planner_desktop.usecases.daily_task_service import DailyTaskService
from planner_desktop.usecases.external_series_service import ExternalSeriesService
from planner_desktop.usecases.occurrence_materializer import OccurrenceMaterializer
from planner_desktop.usecases.recurrence_service import RecurrenceService
from planner_desktop.usecases.series_calendar_link_service import (
    SeriesCalendarLinkService,
)
from planner_desktop.usecases.task_service import DesktopTaskService
from planner_desktop.usecases.tag_service import TagService
from planner_desktop.usecases.template_service import TemplateService
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
        if isinstance(self.repository, SQLiteTaskRepository):
            self.tag_repository = SQLiteTagRepository(self.repository.db_path)
        else:
            self.tag_repository = InMemoryTagRepository()
        self.tag_service = TagService(self.tag_repository, self.repository)
        self.service.tag_service = self.tag_service

        # Локальные серии и шаблоны (Phase 3.2A). Строго локальны:
        # ни Calendar-очереди, ни Google. SQLite-режим делит ту же БД.
        if isinstance(self.repository, SQLiteTaskRepository):
            series_repository = SQLiteSeriesRepository(self.repository.db_path)
            template_repository = SQLiteTemplateRepository(self.repository.db_path)
            self.external_series_repository = SQLiteExternalSeriesRepository(
                self.repository.db_path
            )
            self.series_sync_store = CalendarSeriesSyncStore(
                self.repository.db_path
            )
        else:
            series_repository = InMemorySeriesRepository()
            template_repository = InMemoryTemplateRepository()
            self.external_series_repository = InMemoryExternalSeriesRepository(
                self.repository
            )
            self.series_sync_store = None
        self.external_series_service = ExternalSeriesService(
            self.external_series_repository
        )
        self.recurrence_service = RecurrenceService(
            series_repository, self.repository, tag_service=self.tag_service)
        self.series_link_service = (
            SeriesCalendarLinkService(
                series_repository,
                self.repository,
                self.series_sync_store,
            )
            if self.series_sync_store is not None
            else None
        )
        self.recurrence_service.series_link_service = self.series_link_service
        if self.series_sync_store is not None:
            from planner_desktop.usecases.series_conflict_service import (
                SeriesConflictService,
            )

            self.series_conflict_service = SeriesConflictService(
                series_repository,
                self.repository,
                self.series_sync_store,
            )
        else:
            self.series_conflict_service = None
        self.recurrence_service.series_conflict_service = (
            self.series_conflict_service
        )
        self.template_service = TemplateService(
            template_repository, tag_service=self.tag_service)
        self.materializer = OccurrenceMaterializer(self.recurrence_service)
        self.service.recurrence_service = self.recurrence_service
        self.service.template_service = self.template_service
        self.service.materializer = self.materializer

        self.today_viewmodel = TodayViewModel(
            service=self.service, daily_service=self.daily_service)
        self.calendar_viewmodel = CalendarViewModel(
            service=self.service, daily_service=self.daily_service)
        self.settings_viewmodel = SettingsViewModel(
            self.service, daily_service=self.daily_service,
            manual_sync_service=self._build_manual_sync_service(),
            tag_service=self.tag_service,
            external_series_service=self.external_series_service,
            series_link_service=self.series_link_service,
            series_sync_store=self.series_sync_store,
            series_conflict_service=self.series_conflict_service)
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
