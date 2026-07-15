from datetime import date, datetime

import pytest

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
from planner_desktop.sync.sync_types import CalendarEvent
from planner_desktop.usecases.series_calendar_link_service import SeriesCalendarLinkService


def _stack(tmp_path, link_store_cls=CalendarSeriesSyncStore):
    db = tmp_path / "desktop.db"
    series_repo = InMemorySeriesRepository()
    tasks = FakeTaskRepository(seed=False)
    series = series_repo.add(TaskSeries(
        uid="s1", title="Local",
        schedule=SeriesSchedule(date(2026, 7, 15), True, timezone_name="UTC"),
        rule=RecurrenceRule(RecurrenceFrequency.DAILY),
    ))
    links = link_store_cls(db)
    ordinary = CalendarSyncStore(db)
    catalog = InMemoryExternalSeriesRepository(tasks)
    gateway = FakeCalendarGateway()
    service = SeriesCalendarLinkService(series_repo, tasks, links)
    service.connect_to_google(series.uid)
    CalendarSeriesSyncEngine(
        series_repo, tasks, links, catalog, gateway
    ).push_pending()
    pull = CalendarSyncEngine(
        tasks, ordinary, gateway, catalog, series_link_store=links
    )
    pull.pull_remote_changes()  # consume own master echo
    return tasks, links, ordinary, gateway, series, pull


def test_linked_instance_is_quarantined_not_imported_and_unlinked_is_unchanged(tmp_path):
    tasks, links, ordinary, gateway, series, pull = _stack(tmp_path)
    master_id = links.get_link(series.uid).remote_event_id
    linked_instance = gateway.insert_event(CalendarEvent(
        summary="Moved", start=date(2026, 7, 16), end=date(2026, 7, 17),
        is_all_day=True, recurring_event_id=master_id,
        original_start=datetime(2026, 7, 16),
    ))
    pull.pull_remote_changes()
    changes = links.list_occurrence_changes()
    assert len(changes) == 1
    assert changes[0].remote_instance_event_id == linked_instance.id
    assert tasks.get_by_google_event_id(linked_instance.id) is None
    assert ordinary.count_pending_ops() == 0

    external_instance = gateway.insert_event(CalendarEvent(
        summary="External instance", start=date(2026, 7, 18),
        end=date(2026, 7, 19), is_all_day=True,
        recurring_event_id="external-master",
        original_start=datetime(2026, 7, 18),
    ))
    pull.pull_remote_changes()
    assert tasks.get_by_google_event_id(external_instance.id) is not None
    links.close(); ordinary.close()


class FailingQuarantineStore(CalendarSeriesSyncStore):
    fail = False

    def upsert_occurrence_change(self, change):
        if self.fail:
            raise RuntimeError("quarantine persistence failed")
        return super().upsert_occurrence_change(change)


def test_quarantine_failure_does_not_advance_pull_cursor(tmp_path):
    tasks, links, ordinary, gateway, series, pull = _stack(tmp_path, FailingQuarantineStore)
    master_id = links.get_link(series.uid).remote_event_id
    gateway.insert_event(CalendarEvent(
        summary="Moved", start=date(2026, 7, 16), end=date(2026, 7, 17),
        is_all_day=True, recurring_event_id=master_id,
        original_start=datetime(2026, 7, 16),
    ))
    before = ordinary.get_sync_cursor()
    links.fail = True
    with pytest.raises(RuntimeError, match="quarantine persistence failed"):
        pull.pull_remote_changes()
    assert ordinary.get_sync_cursor() == before
    links.close(); ordinary.close()
