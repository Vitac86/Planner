"""Главное окно нового десктопа: движок QML + привязка ViewModel-ей."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from PySide6.QtQml import QQmlApplicationEngine

from planner_desktop.repositories import TaskRepository
from planner_desktop.repositories.fake_task_repository import FakeTaskRepository
from planner_desktop.storage.sqlite_task_repository import SQLiteTaskRepository
from planner_desktop.viewmodels.calendar_viewmodel import CalendarViewModel
from planner_desktop.viewmodels.today_viewmodel import TodayViewModel

QML_DIR = Path(__file__).resolve().parent / "qml"


def _default_repository() -> TaskRepository:
    """SQLite-хранилище нового десктопа (PlannerDesktop/app_desktop.db).

    PLANNER_DESKTOP_DEMO=1 включает фейковый репозиторий с демо-данными —
    ничего не пишется на диск. Старый Planner/app.db не открывается ни в
    одном из режимов.
    """
    if os.environ.get("PLANNER_DESKTOP_DEMO") == "1":
        return FakeTaskRepository()
    return SQLiteTaskRepository()


class MainWindow:
    """Владеет QQmlApplicationEngine и ViewModel-ями.

    Один общий репозиторий на все страницы, чтобы задача, добавленная
    через Quick Add, сразу была видна в календарной сетке.
    """

    def __init__(self, repository: Optional[TaskRepository] = None) -> None:
        self.repository = repository if repository is not None else _default_repository()
        self.today_viewmodel = TodayViewModel(self.repository)
        self.calendar_viewmodel = CalendarViewModel(self.repository)

        self.engine = QQmlApplicationEngine()
        context = self.engine.rootContext()
        context.setContextProperty("todayVm", self.today_viewmodel)
        context.setContextProperty("calendarVm", self.calendar_viewmodel)

    def show(self) -> None:
        self.engine.load(str(QML_DIR / "Main.qml"))
        if not self.engine.rootObjects():
            raise RuntimeError("Не удалось загрузить qml/Main.qml")
