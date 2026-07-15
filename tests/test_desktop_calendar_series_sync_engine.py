from datetime import date, time

from planner_desktop.domain.recurrence import (
    RecurrenceFrequency,
    RecurrenceRule,
    SeriesSchedule,
    TaskSeries,
)
from planner_desktop.storage.calendar_series_sync_store import CalendarSeriesSyncStore
from planner_desktop.storage.external_series_repository import SQLiteExternalSeriesRepository
from planner_desktop.storage.series_repository import SQLiteSeriesRepository
from planner_desktop.storage.sqlite_task_repository import SQLiteTaskRepository
from planner_desktop.sync.calendar_series_sync_engine import CalendarSeriesSyncEngine
from planner_desktop.sync.fake_calendar_gateway import FakeCalendarGateway
from planner_desktop.usecases.recurrence_service import RecurrenceService
from planner_desktop.usecases.series_calendar_link_service import SeriesCalendarLinkService


def _stack(tmp_path):
    db = tmp_path / "desktop.db"
    tasks = SQLiteTaskRepository(db)
    series_repo = SQLiteSeriesRepository(db)
    store = CalendarSeriesSyncStore(db)
    catalog = SQLiteExternalSeriesRepository(db)
    gateway = FakeCalendarGateway()
    links = SeriesCalendarLinkService(series_repo, tasks, store)
    recurrence = RecurrenceService(series_repo, tasks)
    recurrence.series_link_service = links
    series = recurrence.create_series(TaskSeries(
        uid="series-1", title="Daily", notes="N",
        schedule=SeriesSchedule(date(2026, 7, 15), False, time(9), 30,
                                "Europe/Moscow"),
        rule=RecurrenceRule(RecurrenceFrequency.DAILY),
    )).series
    engine = CalendarSeriesSyncEngine(
        series_repo, tasks, store, catalog, gateway
    )
    return tasks, series_repo, store, catalog, gateway, links, recurrence, series, engine


def test_create_update_delete_success_and_catalog(tmp_path):
    tasks, series_repo, store, catalog, gateway, links, recurrence, series, engine = _stack(tmp_path)
    assert links.connect_to_google(series.uid).ok
    created = engine.push_pending()
    assert created.created == 1
    link = store.get_link(series.uid)
    assert link.link_status.value == "synced"
    assert link.remote_event_id == gateway.events[0].id
    catalog_row = catalog.get("google", "primary", link.remote_event_id)
    assert catalog_row.planner_owned and catalog_row.linked_series_uid == series.uid

    result = recurrence.update_series(series.uid, title="Daily changed")
    assert result.ok and store.get_pending_op(series.uid).op.value == "update"
    updated = engine.push_pending()
    assert updated.updated == 1
    assert gateway.get_recurring_master(link.remote_event_id).summary == "Daily changed"

    assert links.request_remote_delete_keep_local(series.uid).ok
    deleted = engine.push_pending()
    assert deleted.deleted == 1
    assert store.get_link(series.uid) is None
    assert series_repo.get_by_uid(series.uid).is_deleted is False
    assert catalog.get("google", "primary", link.remote_event_id).is_cancelled

    catalog.close(); store.close(); series_repo.close(); tasks.close()


def test_delete_local_and_remote_preserves_completed_history(tmp_path):
    tasks, series_repo, store, catalog, gateway, links, recurrence, series, engine = _stack(tmp_path)
    recurrence.ensure_occurrences(date(2026, 7, 15), date(2026, 7, 16), series_uid=series.uid)
    rows = tasks.list_by_series(series.uid)
    tasks.complete(rows[0].id, True)
    links.connect_to_google(series.uid)
    engine.push_pending()
    links.request_delete_local_and_remote(series.uid)
    engine.push_pending()
    assert series_repo.get_by_uid(series.uid).is_deleted
    remaining = tasks.list_by_series(series.uid)
    assert len(remaining) == 1 and remaining[0].completed
    catalog.close(); store.close(); series_repo.close(); tasks.close()
