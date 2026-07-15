import sys
from datetime import date

from PySide6.QtCore import QCoreApplication

from planner_desktop.domain.recurrence import (
    RecurrenceFrequency,
    RecurrenceRule,
    SeriesSchedule,
    TaskSeries,
)
from planner_desktop.repositories.fake_task_repository import FakeTaskRepository
from planner_desktop.repositories.series_repository import InMemorySeriesRepository
from planner_desktop.storage.calendar_series_sync_store import CalendarSeriesSyncStore
from planner_desktop.usecases.recurrence_service import RecurrenceService
from planner_desktop.usecases.series_calendar_link_service import SeriesCalendarLinkService
from planner_desktop.usecases.task_service import DesktopTaskService
from planner_desktop.viewmodels.settings_viewmodel import SettingsViewModel
from planner_desktop.viewmodels.today_viewmodel import TodayViewModel


def _app():
    return QCoreApplication.instance() or QCoreApplication(sys.argv)


def test_series_editor_preflight_connect_and_settings_rows_are_local(tmp_path):
    _app()
    tasks = FakeTaskRepository(seed=False)
    service = DesktopTaskService(tasks)
    series_repo = InMemorySeriesRepository()
    series = series_repo.add(TaskSeries(
        uid="s1", title="Daily",
        schedule=SeriesSchedule(date.today(), True, timezone_name="UTC"),
        rule=RecurrenceRule(RecurrenceFrequency.DAILY),
    ))
    recurrence = RecurrenceService(series_repo, tasks)
    store = CalendarSeriesSyncStore(tmp_path / "desktop.db")
    links = SeriesCalendarLinkService(series_repo, tasks, store)
    recurrence.series_link_service = links
    service.recurrence_service = recurrence
    recurrence.ensure_occurrences(date.today(), date.today(), series_uid=series.uid)
    occurrence = tasks.list_by_series(series.uid)[0]

    vm = TodayViewModel(service=service)
    before = vm.seriesGoogleLinkData(series.uid)
    assert before["statusText"] == "Локальная серия"
    assert before["canConnect"]
    assert "Название" in before["whatSent"]
    assert "Теги" in before["whatLocal"]
    assert vm.connectSeriesToGoogle(series.uid)
    after = vm.seriesGoogleLinkData(series.uid)
    assert after["statusText"] == "Ожидает создания в Google"
    editor = vm.editorDataFor(occurrence.uid)
    assert editor["seriesLinkedToGoogle"] is True

    settings = SettingsViewModel(
        service,
        series_link_service=links,
        series_sync_store=store,
    )
    assert settings.pendingSeriesCreateCount == 1
    assert settings.linkedSeriesRows[0]["title"] == "Daily"
    assert settings.quarantinedSeriesInstanceCount == 0
    store.close()
