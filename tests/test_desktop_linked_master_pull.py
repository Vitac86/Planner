from datetime import date

from planner_desktop.domain.recurrence import (
    RecurrenceFrequency,
    RecurrenceRule,
    SeriesSchedule,
    TaskSeries,
)
from planner_desktop.repositories.fake_task_repository import FakeTaskRepository
from planner_desktop.repositories.external_series_repository import InMemoryExternalSeriesRepository
from planner_desktop.repositories.series_repository import InMemorySeriesRepository
from planner_desktop.storage.calendar_series_sync_store import CalendarSeriesSyncStore
from planner_desktop.storage.calendar_sync_store import CalendarSyncStore
from planner_desktop.sync.calendar_series_sync_engine import CalendarSeriesSyncEngine
from planner_desktop.sync.calendar_sync_engine import CalendarSyncEngine
from planner_desktop.sync.fake_calendar_gateway import FakeCalendarGateway
from planner_desktop.usecases.series_calendar_link_service import SeriesCalendarLinkService


def _stack(tmp_path):
    db = tmp_path / "desktop.db"
    series_repo = InMemorySeriesRepository()
    tasks = FakeTaskRepository(seed=False)
    series = series_repo.add(TaskSeries(
        uid="s1", title="Local authoritative",
        schedule=SeriesSchedule(date(2026, 7, 15), True, timezone_name="UTC"),
        rule=RecurrenceRule(RecurrenceFrequency.DAILY),
    ))
    link_store = CalendarSeriesSyncStore(db)
    ordinary_store = CalendarSyncStore(db)
    catalog = InMemoryExternalSeriesRepository(tasks)
    gateway = FakeCalendarGateway()
    links = SeriesCalendarLinkService(series_repo, tasks, link_store)
    links.connect_to_google(series.uid)
    CalendarSeriesSyncEngine(
        series_repo, tasks, link_store, catalog, gateway
    ).push_pending()
    pull = CalendarSyncEngine(
        tasks, ordinary_store, gateway, catalog, series_link_store=link_store
    )
    return series_repo, tasks, link_store, ordinary_store, catalog, gateway, series, pull


def test_linked_master_echo_refreshes_without_conflict(tmp_path):
    series_repo, tasks, links, ordinary, catalog, gateway, series, pull = _stack(tmp_path)
    assert pull.pull_remote_changes() == 1
    link = links.get_link(series.uid)
    assert link.link_status.value == "synced"
    assert tasks.list_all() == []
    assert ordinary.count_pending_ops() == 0
    links.close(); ordinary.close()


def test_unexpected_master_edit_marks_conflict_and_keeps_local(tmp_path):
    series_repo, tasks, links, ordinary, catalog, gateway, series, pull = _stack(tmp_path)
    pull.pull_remote_changes()
    remote_id = links.get_link(series.uid).remote_event_id
    gateway.patch_event(remote_id, {"summary": "Changed in Google"})
    pull.pull_remote_changes()
    link = links.get_link(series.uid)
    assert link.link_status.value == "conflict"
    assert series_repo.get_by_uid(series.uid).title == "Local authoritative"
    assert ordinary.count_pending_ops() == 0
    assert catalog.get("google", "primary", remote_id).title == "Changed in Google"
    links.close(); ordinary.close()


def test_remote_master_cancellation_keeps_local_series(tmp_path):
    series_repo, tasks, links, ordinary, catalog, gateway, series, pull = _stack(tmp_path)
    pull.pull_remote_changes()
    remote_id = links.get_link(series.uid).remote_event_id
    gateway.delete_recurring_master(remote_id)
    pull.pull_remote_changes()
    assert links.get_link(series.uid).link_status.value == "remote_deleted"
    assert series_repo.get_by_uid(series.uid).is_deleted is False
    assert tasks.list_all() == []
    assert catalog.get("google", "primary", remote_id).is_cancelled
    links.close(); ordinary.close()
