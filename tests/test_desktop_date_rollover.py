"""Date-dependent pages stay current across midnight and resume, without IO."""
from datetime import datetime, timedelta

import pytest
from PySide6.QtCore import QCoreApplication, Qt

from planner_desktop.domain.recurrence import (
    RecurrenceFrequency, RecurrenceRule, SeriesSchedule, TaskSeries,
)
from planner_desktop.domain.task import Task
from planner_desktop.repositories.daily_task_repository import InMemoryDailyTaskRepository
from planner_desktop.repositories.fake_task_repository import FakeTaskRepository
from planner_desktop.repositories.series_repository import InMemorySeriesRepository
from planner_desktop.usecases.daily_task_service import DailyTaskService
from planner_desktop.usecases.occurrence_materializer import OccurrenceMaterializer
from planner_desktop.usecases.recurrence_service import RecurrenceService
from planner_desktop.usecases.task_service import DesktopTaskService
from planner_desktop.viewmodels.calendar_viewmodel import CalendarViewModel, DISPLAY_DAY
from planner_desktop.viewmodels.date_rollover import DateRolloverMonitor
from planner_desktop.viewmodels.history_viewmodel import HistoryViewModel
from planner_desktop.viewmodels.today_viewmodel import TodayViewModel


@pytest.fixture
def clock_monitor():
    app = QCoreApplication.instance() or QCoreApplication([])
    clock = [datetime(2026, 7, 15, 23, 59)]
    monitor = DateRolloverMonitor(today_provider=lambda: clock[0].date())
    yield clock, monitor
    monitor._timer.stop()
    monitor.deleteLater()
    app.processEvents()


def test_minute_timer_notifies_once_when_local_date_changes(clock_monitor):
    clock, monitor = clock_monitor
    days = []
    monitor.dayChanged.connect(lambda: days.append(clock[0].date()))
    monitor._timer.timeout.emit()
    assert days == []

    clock[0] += timedelta(minutes=2)
    monitor._timer.timeout.emit()
    monitor._timer.timeout.emit()
    assert days == [clock[0].date()]


def test_activation_after_sleep_catches_up_once_and_handles_clock_correction(clock_monitor):
    clock, monitor = clock_monitor
    days = []
    monitor.dayChanged.connect(lambda: days.append(clock[0].date()))
    clock[0] += timedelta(days=3)
    monitor._on_application_state_changed(Qt.ApplicationState.ApplicationInactive)
    assert days == []
    monitor._on_application_state_changed(Qt.ApplicationState.ApplicationActive)
    monitor._timer.timeout.emit()
    assert len(days) == 1

    clock[0] -= timedelta(days=1)
    monitor.checkDate()
    assert days[-1] == clock[0].date()
    assert len(days) == 2


def test_today_refresh_reloads_daily_checks_and_current_day_tasks(clock_monitor):
    clock, monitor = clock_monitor
    repo = FakeTaskRepository(seed=False)
    old_task = repo.add(Task(title="Yesterday", start=clock[0], completed=True))
    new_task = repo.add(Task(title="Today", start=clock[0] + timedelta(days=1)))
    daily = DailyTaskService(InMemoryDailyTaskRepository())
    habit = daily.create("Читать").task
    vm = TodayViewModel(repo, daily_service=daily, now_provider=lambda: clock[0])
    monitor.dayChanged.connect(vm.refresh)
    vm.toggleDaily(habit.uid)
    old_day = clock[0].date()
    assert vm.dailyDoneCount == 1
    assert vm.completedTodayCount == 1
    vm.selectTask(old_task.uid)
    daily_changes = []
    vm.dailyChanged.connect(lambda: daily_changes.append(vm.dailyDoneCount))

    clock[0] += timedelta(minutes=2)
    monitor.checkDate()

    assert daily_changes == [0]
    assert vm.dailyTasks[0]["done"] is False
    assert [row["uid"] for row in vm.todayTasks] == [new_task.uid]
    assert vm.todayCount == 1 and vm.completedTodayCount == 0
    assert vm.headerDateText == "четверг, 16 июля 2026"
    assert vm.selectedUids == []
    assert daily.occurrences_for(old_day)[0].done is True
    vm.toggleDaily(habit.uid)
    assert daily.occurrences_for(clock[0].date())[0].done is True
    assert daily.occurrences_for(old_day)[0].done is True


def test_rollover_refresh_preserves_calendar_navigation_and_updates_history(clock_monitor):
    clock, monitor = clock_monitor
    service = DesktopTaskService(FakeTaskRepository(seed=False))
    service.create_task(Task(title="Done", completed=True, completed_at=clock[0]))
    daily = DailyTaskService(InMemoryDailyTaskRepository())
    calendar = CalendarViewModel(service=service, now_provider=lambda: clock[0])
    history = HistoryViewModel(service, daily, now_provider=lambda: clock[0])
    calendar.setDisplayMode(DISPLAY_DAY)
    calendar.nextPeriod()
    calendar.nextPeriod()
    selected_date = calendar.selectedDateText
    monitor.dayChanged.connect(calendar.refresh)
    monitor.dayChanged.connect(history.refresh)
    labels = []
    history.historyChanged.connect(lambda: labels.append(history.groups[0]["relLabel"]))
    assert history.groups[0]["relLabel"] == "Сегодня"

    clock[0] += timedelta(minutes=2)
    monitor.checkDate()

    assert labels == ["Вчера"]
    assert calendar.selectedDateText == selected_date
    assert calendar.displayMode == DISPLAY_DAY


def test_rollover_materializes_todays_recurring_tasks_and_quick_add_uses_new_date(clock_monitor):
    clock, monitor = clock_monitor
    tasks = FakeTaskRepository(seed=False)
    recurrence = RecurrenceService(InMemorySeriesRepository(), tasks)
    series = recurrence.create_series(TaskSeries(
        title="Daily review",
        schedule=SeriesSchedule(start_date=clock[0].date()),
        rule=RecurrenceRule(RecurrenceFrequency.DAILY),
    )).series
    service = DesktopTaskService(tasks)
    service.recurrence_service = recurrence
    service.materializer = OccurrenceMaterializer(recurrence, buffer_days=0)
    vm = TodayViewModel(service=service, now_provider=lambda: clock[0])
    monitor.dayChanged.connect(vm.refresh)
    previous_uid = vm.todayTasks[0]["uid"]

    clock[0] += timedelta(days=2)
    monitor._on_application_state_changed(Qt.ApplicationState.ApplicationActive)

    assert vm.todayTasks[0]["seriesUid"] == series.uid
    assert vm.todayTasks[0]["uid"] != previous_uid
    assert tasks.get_by_uid(vm.todayTasks[0]["uid"]).start.date() == clock[0].date()
    assert vm.addQuick("Отчёт завтра", 0)
    added = next(task for task in tasks.list_all() if task.title == "Отчёт")
    assert added.start.date() == clock[0].date() + timedelta(days=1)
