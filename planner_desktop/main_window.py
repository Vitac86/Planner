"""Главное окно нового десктопа: движок QML + привязка ViewModel-ей."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtQml import QQmlApplicationEngine

from planner_desktop.repositories.fake_task_repository import FakeTaskRepository
from planner_desktop.viewmodels.calendar_viewmodel import CalendarViewModel
from planner_desktop.viewmodels.today_viewmodel import TodayViewModel

QML_DIR = Path(__file__).resolve().parent / "qml"


class MainWindow:
    """Владеет QQmlApplicationEngine и ViewModel-ями.

    Один общий FakeTaskRepository на все страницы, чтобы задача,
    добавленная через Quick Add, сразу была видна в календарной сетке.
    """

    def __init__(self) -> None:
        self.repository = FakeTaskRepository()
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
