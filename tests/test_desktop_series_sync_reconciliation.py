from datetime import date

import pytest

from planner_desktop.domain.recurrence import (
    RecurrenceFrequency,
    RecurrenceRule,
    SeriesSchedule,
    TaskSeries,
)
from planner_desktop.domain.series_calendar_link import SeriesLinkStatus
from planner_desktop.repositories.fake_task_repository import FakeTaskRepository
from planner_desktop.repositories.external_series_repository import InMemoryExternalSeriesRepository
from planner_desktop.repositories.series_repository import InMemorySeriesRepository
from planner_desktop.storage.calendar_series_sync_store import CalendarSeriesSyncStore
from planner_desktop.sync.calendar_series_sync_engine import CalendarSeriesSyncEngine
from planner_desktop.sync.fake_calendar_gateway import FakeCalendarGateway
from planner_desktop.sync.sync_types import TerminalGatewayError
from planner_desktop.usecases.series_calendar_link_service import SeriesCalendarLinkService


class FailOnceStore(CalendarSeriesSyncStore):
    fail_next_synced = False

    def set_link_status(self, series_uid, status, **kwargs):
        if self.fail_next_synced and status is SeriesLinkStatus.SYNCED:
            self.fail_next_synced = False
            raise RuntimeError("local persistence failed")
        return super().set_link_status(series_uid, status, **kwargs)


def _stack(tmp_path, store_cls=CalendarSeriesSyncStore):
    series_repo = InMemorySeriesRepository()
    tasks = FakeTaskRepository(seed=False)
    series = series_repo.add(TaskSeries(
        uid="s1", title="Daily",
        schedule=SeriesSchedule(date(2026, 7, 15), True, timezone_name="UTC"),
        rule=RecurrenceRule(RecurrenceFrequency.DAILY),
    ))
    store = store_cls(tmp_path / "desktop.db")
    catalog = InMemoryExternalSeriesRepository(tasks)
    gateway = FakeCalendarGateway()
    links = SeriesCalendarLinkService(series_repo, tasks, store)
    links.connect_to_google(series.uid)
    engine = CalendarSeriesSyncEngine(series_repo, tasks, store, catalog, gateway)
    return series, store, gateway, engine


def test_remote_success_local_failure_reconciles_without_second_master(tmp_path):
    series, store, gateway, engine = _stack(tmp_path, FailOnceStore)
    store.fail_next_synced = True
    with pytest.raises(RuntimeError, match="local persistence failed"):
        engine.push_pending()
    assert len(gateway.events) == 1
    assert gateway.write_call_count == 1
    assert store.get_pending_op(series.uid) is not None

    result = engine.push_pending()
    assert result.created == 1 and result.items[0].reconciled
    assert gateway.write_call_count == 1
    assert len(gateway.events) == 1
    assert store.get_pending_op(series.uid) is None
    assert store.get_link(series.uid).link_status is SeriesLinkStatus.SYNCED
    store.close()


def test_terminal_operation_remains_visible_and_does_not_retry(tmp_path):
    series, store, gateway, engine = _stack(tmp_path)
    gateway.fail_next(TerminalGatewayError("bad request"))
    result = engine.push_pending()
    assert result.terminal == 1
    assert store.count_terminal_ops() == 1
    writes = gateway.write_call_count
    assert engine.push_pending().pushed == 0
    assert gateway.write_call_count == writes
    store.close()
