from datetime import date, datetime

import pytest

from planner_desktop.domain.recurrence import (
    RecurrenceFrequency,
    RecurrenceRule,
    SeriesSchedule,
    TaskSeries,
)
from planner_desktop.domain.series_calendar_link import LINKED_OCCURRENCE_CHANGE_ERROR
from planner_desktop.domain.task import Task
from planner_desktop.repositories.fake_task_repository import FakeTaskRepository
from planner_desktop.repositories.series_repository import InMemorySeriesRepository
from planner_desktop.repositories.tag_repository import InMemoryTagRepository
from planner_desktop.storage.calendar_series_sync_store import CalendarSeriesSyncStore
from planner_desktop.storage.calendar_sync_store import CalendarSyncStore
from planner_desktop.sync.fake_calendar_gateway import FakeCalendarGateway
from planner_desktop.usecases.recurrence_service import RecurrenceService
from planner_desktop.usecases.series_calendar_link_service import SeriesCalendarLinkService
from planner_desktop.usecases.tag_service import TagService
from planner_desktop.usecases.task_service import DesktopTaskService
from planner_desktop.viewmodels.settings_viewmodel import SettingsViewModel


def test_occurrences_never_enter_ordinary_queue_and_local_metadata_isolated(tmp_path):
    db = tmp_path / "desktop.db"
    tasks = FakeTaskRepository(seed=False)
    ordinary_queue = CalendarSyncStore(db)
    task_service = DesktopTaskService(tasks, ordinary_queue)
    series_repo = InMemorySeriesRepository()
    tags = TagService(InMemoryTagRepository(), tasks)
    recurrence = RecurrenceService(series_repo, tasks, tag_service=tags)
    link_store = CalendarSeriesSyncStore(db)
    links = SeriesCalendarLinkService(series_repo, tasks, link_store)
    recurrence.series_link_service = links
    task_service.recurrence_service = recurrence
    series = recurrence.create_series(TaskSeries(
        uid="s1", title="Daily",
        schedule=SeriesSchedule(date(2026, 7, 15), True, timezone_name="UTC"),
        rule=RecurrenceRule(RecurrenceFrequency.DAILY),
    )).series
    recurrence.ensure_occurrences(
        date(2026, 7, 15), date(2026, 7, 17), series_uid=series.uid
    )
    assert ordinary_queue.count_pending_ops() == 0

    assert links.connect_to_google(series.uid).ok
    assert link_store.count_pending_ops() == 1
    assert ordinary_queue.count_pending_ops() == 0
    tag = tags.create("Local tag")
    assert recurrence.update_series(series.uid, tag_ids=[tag.id]).ok
    assert link_store.count_pending_ops() == 1
    occurrence = tasks.list_by_series(series.uid)[0]
    assert task_service.toggle_completed(occurrence.uid)
    assert link_store.count_pending_ops() == 1
    assert ordinary_queue.count_pending_ops() == 0

    blocked = task_service.postpone_task(occurrence.uid, "tomorrow")
    assert blocked.errors == [LINKED_OCCURRENCE_CHANGE_ERROR]
    with pytest.raises(ValueError, match="следующем этапе"):
        task_service.delete_task_by_uid(occurrence.uid)

    gateway = FakeCalendarGateway()
    SettingsViewModel(task_service, series_link_service=links,
                      series_sync_store=link_store).refresh()
    assert gateway.write_call_count == gateway.list_call_count == 0

    ordinary = task_service.create_task(Task(
        title="Ordinary", start=datetime(2026, 7, 20, 10),
        end=datetime(2026, 7, 20, 11),
    ))
    assert ordinary.series_uid is None
    assert ordinary_queue.count_pending_ops() == 1
    link_store.close(); ordinary_queue.close()


def test_unlinked_phase3_2a_series_still_has_zero_queue_delta(tmp_path):
    tasks = FakeTaskRepository(seed=False)
    queue = CalendarSyncStore(tmp_path / "desktop.db")
    series_repo = InMemorySeriesRepository()
    recurrence = RecurrenceService(series_repo, tasks)
    series = recurrence.create_series(TaskSeries(
        title="Local only",
        schedule=SeriesSchedule(date(2026, 7, 15), True, timezone_name="UTC"),
        rule=RecurrenceRule(RecurrenceFrequency.DAILY),
    )).series
    recurrence.ensure_occurrences(
        date(2026, 7, 15), date(2026, 7, 20), series_uid=series.uid
    )
    recurrence.update_series(series.uid, notes="changed")
    assert queue.count_pending_ops() == 0
    queue.close()
