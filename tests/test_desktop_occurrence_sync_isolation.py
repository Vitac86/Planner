from datetime import date, datetime

from planner_desktop.domain.commands import TaskEditorCommand
from planner_desktop.domain.series_calendar_link import SeriesSyncResult
from planner_desktop.domain.task import Task
from planner_desktop.repositories.fake_task_repository import FakeTaskRepository
from planner_desktop.repositories.series_repository import (
    InMemorySeriesRepository,
)
from planner_desktop.storage.calendar_sync_store import CalendarSyncStore
from planner_desktop.sync.calendar_series_occurrence_sync_engine import (
    CalendarSeriesOccurrenceSyncEngine,
    OccurrenceSyncResult,
)
from planner_desktop.sync.calendar_series_sync_engine import (
    CalendarSeriesSyncEngine,
)
from planner_desktop.sync.calendar_sync_engine import CalendarSyncEngine
from planner_desktop.sync.fake_calendar_gateway import FakeCalendarGateway
from planner_desktop.usecases.manual_sync_service import ManualSyncService
from planner_desktop.usecases.recurrence_service import RecurrenceService
from planner_desktop.usecases.series_calendar_link_service import (
    SeriesCalendarLinkService,
)
from tests.occurrence_sync_testkit import linked_occurrence_store, timed_series


def make_local_edit_stack(tmp_path):
    db = tmp_path / "desktop.db"
    series = timed_series()
    series_repo = InMemorySeriesRepository()
    series_repo.add(series)
    tasks = FakeTaskRepository(seed=False)
    key = "2026-07-20T09:00@Europe/Moscow"
    task = tasks.add(Task(
        title="Original",
        notes="Original notes",
        start=datetime(2026, 7, 20, 9),
        end=datetime(2026, 7, 20, 9, 30),
        duration_minutes=30,
        series_uid=series.uid,
        occurrence_key=key,
    ))
    master, occurrence, _link = linked_occurrence_store(db, series)
    links = SeriesCalendarLinkService(series_repo, tasks, master)
    recurrence = RecurrenceService(series_repo, tasks)
    recurrence.series_link_service = links
    recurrence.occurrence_sync_store = occurrence
    ordinary = CalendarSyncStore(db)
    return series, task, tasks, master, occurrence, ordinary, recurrence


def command(
    *,
    title="Original",
    notes="Original notes",
    day="2026-07-20",
    at="09:00",
    duration="30",
    priority=0,
    completed=False,
):
    return TaskEditorCommand(
        title=title,
        notes=notes,
        priority=priority,
        completed=completed,
        add_to_calendar=True,
        is_all_day=False,
        date_text=day,
        time_text=at,
        duration_text=duration,
    )


def test_linked_occurrence_edit_coalesces_instance_only(tmp_path):
    (
        series, task, tasks, master, occurrence, ordinary, recurrence
    ) = make_local_edit_stack(tmp_path)
    original_key = task.occurrence_key
    assert recurrence.edit_occurrence(
        task.uid,
        command(title="Changed title", notes="Changed notes"),
    ).ok
    assert recurrence.edit_occurrence(
        task.uid,
        command(
            title="Changed title",
            notes="Changed notes",
            at="10:00",
            duration="45",
        ),
    ).ok
    updated = tasks.get_by_uid(task.uid)
    assert updated.occurrence_key == original_key
    assert updated.start == datetime(2026, 7, 20, 10)
    assert updated.duration_minutes == 45
    op = occurrence.get_pending_op(series.uid, original_key)
    assert op.payload["summary"] == "Changed title"
    assert op.payload["start"]["dateTime"].startswith("2026-07-20T10:00")
    assert occurrence.count_pending_ops() == 1
    assert master.get_pending_op(series.uid) is None
    assert ordinary.count_pending_ops() == 0
    ordinary.close()
    occurrence.close()
    master.close()


def test_local_only_fields_create_zero_instance_operations(tmp_path):
    (
        series, task, tasks, master, occurrence, ordinary, recurrence
    ) = make_local_edit_stack(tmp_path)
    result = recurrence.edit_occurrence(
        task.uid, command(priority=3, completed=True)
    )
    assert result.ok
    assert tasks.get_by_uid(task.uid).priority == 3
    assert tasks.get_by_uid(task.uid).completed
    assert occurrence.count_pending_ops() == 0
    assert ordinary.count_pending_ops() == 0
    assert master.get_pending_op(series.uid) is None
    ordinary.close()
    occurrence.close()
    master.close()


def test_delete_occurrence_is_tombstone_and_instance_cancel_only(tmp_path):
    (
        series, task, tasks, master, occurrence, ordinary, recurrence
    ) = make_local_edit_stack(tmp_path)
    assert recurrence.delete_occurrence(task.uid)
    tombstone = tasks.get_by_uid(task.uid)
    assert tombstone.is_deleted
    assert tombstone.series_uid == series.uid
    assert tombstone.occurrence_key == task.occurrence_key
    assert occurrence.get_pending_op(
        series.uid, task.occurrence_key
    ).op.value == "cancel"
    assert master.get_pending_op(series.uid) is None
    assert ordinary.count_pending_ops() == 0
    ordinary.close()
    occurrence.close()
    master.close()


def test_materialized_occurrences_never_enter_ordinary_queue(tmp_path):
    (
        series, task, tasks, master, occurrence, ordinary, recurrence
    ) = make_local_edit_stack(tmp_path)
    recurrence.ensure_occurrences(date(2026, 7, 20), date(2026, 7, 25))
    assert len(tasks.list_by_series(series.uid)) >= 6
    assert ordinary.count_pending_ops() == 0
    assert occurrence.count_pending_ops() == 0
    ordinary.close()
    occurrence.close()
    master.close()


def test_manual_sync_push_order_is_master_occurrence_ordinary_then_pull(
    tmp_path, monkeypatch
):
    order = []

    def push_master(_engine):
        order.append("master")
        return SeriesSyncResult(updated=1)

    def push_occurrence(_engine):
        order.append("occurrence")
        return OccurrenceSyncResult(updates_pushed=1)

    def push_ordinary(_engine):
        order.append("ordinary")
        return 1

    def pull(_engine):
        order.append("pull")
        return 0

    monkeypatch.setattr(CalendarSeriesSyncEngine, "push_pending", push_master)
    monkeypatch.setattr(
        CalendarSeriesOccurrenceSyncEngine, "push_pending", push_occurrence
    )
    monkeypatch.setattr(CalendarSyncEngine, "push_pending", push_ordinary)
    monkeypatch.setattr(CalendarSyncEngine, "pull_remote_changes", pull)

    class PhaseStore:
        def count_terminal_ops(self):
            return 0

        def count_resolutions_completed_after(self, *_args):
            return 0

    ordinary = CalendarSyncStore(tmp_path / "manual-order.db")
    result = ManualSyncService(
        FakeTaskRepository(seed=False),
        ordinary,
        gateway_provider=FakeCalendarGateway,
        series_store=PhaseStore(),
        series_repository=InMemorySeriesRepository(),
        occurrence_store=PhaseStore(),
    ).run_once()
    assert result.ok
    assert order == ["master", "occurrence", "ordinary", "pull"]
    assert result.series_masters_updated == 1
    assert result.occurrence_updates_pushed == 1
    assert result.pushed == 3
    ordinary.close()
