from datetime import date, datetime, time, timedelta

from planner_desktop.domain.commands import TaskEditorCommand
from planner_desktop.domain.recurrence import (
    RecurrenceFrequency,
    RecurrenceRule,
    SeriesSchedule,
    TaskSeries,
)
from planner_desktop.domain.task import Task
from planner_desktop.storage.calendar_sync_store import CalendarSyncStore
from planner_desktop.storage.series_repository import SQLiteSeriesRepository
from planner_desktop.storage.sqlite_task_repository import SQLiteTaskRepository
from planner_desktop.usecases.recurrence_service import RecurrenceService
from planner_desktop.usecases.task_service import (
    DesktopTaskService,
    SERIES_SCOPE_REQUIRED_ERROR,
)


def command(day=2, hour=11):
    return TaskEditorCommand(
        title="Edited",
        notes="local",
        add_to_calendar=True,
        is_all_day=False,
        date_text=f"2026-07-{day:02d}",
        time_text=f"{hour:02d}:00",
        duration_text="30",
        priority=1,
    )


def test_every_local_series_operation_keeps_calendar_queue_at_zero(tmp_path):
    db_path = tmp_path / "desktop.db"
    tasks = SQLiteTaskRepository(db_path)
    queue = CalendarSyncStore(db_path)
    series_repository = SQLiteSeriesRepository(db_path)
    recurrence = RecurrenceService(series_repository, tasks)
    ordinary = DesktopTaskService(tasks, calendar_queue=queue)

    created = recurrence.create_series(TaskSeries(
        title="Local",
        schedule=SeriesSchedule(
            date(2026, 7, 1), False, time(9), 30, "Europe/Moscow"
        ),
        rule=RecurrenceRule(RecurrenceFrequency.DAILY),
    )).series
    assert queue.count_pending_ops() == 0
    recurrence.update_series(created.uid, notes="changed")
    recurrence.ensure_occurrences(date(2026, 7, 1), date(2026, 7, 5))
    assert queue.count_pending_ops() == 0

    rows = tasks.list_by_series(created.uid)
    assert recurrence.edit_occurrence(rows[1].uid, command()).ok
    assert queue.count_pending_ops() == 0
    assert recurrence.edit_this_and_future(rows[2].uid, command(3, 12)).ok
    assert queue.count_pending_ops() == 0
    moved = tasks.get_by_uid(rows[2].uid)
    assert recurrence.delete_occurrence(moved.uid)
    assert queue.count_pending_ops() == 0

    # Even if a local occurrence reaches the ordinary task service, queue
    # guards keep completion/update/delete local.
    survivor = next(
        row for row in tasks.list_all() if row.series_uid and not row.is_deleted
    )
    ordinary.toggle_completed(survivor.uid)
    assert queue.count_pending_ops() == 0
    rejected = ordinary.edit_task(survivor.uid, command())
    assert rejected.errors == [SERIES_SCOPE_REQUIRED_ERROR]
    assert queue.count_pending_ops() == 0

    series_repository.close()
    queue.close()
    tasks.close()


def test_ordinary_task_sync_and_google_recurring_safety_are_unchanged(tmp_path):
    db_path = tmp_path / "desktop.db"
    tasks = SQLiteTaskRepository(db_path)
    queue = CalendarSyncStore(db_path)
    service = DesktopTaskService(tasks, calendar_queue=queue)
    start = datetime(2026, 7, 1, 9)
    ordinary = service.create_task(Task(
        title="Ordinary", start=start, end=start + timedelta(hours=1)
    ))
    assert queue.count_pending_ops() == 1

    google = tasks.add(Task(
        title="Google occurrence",
        start=start,
        end=start + timedelta(hours=1),
        google_calendar_event_id="event",
        google_calendar_recurring_event_id="remote-series",
    ))
    before = queue.count_pending_ops()
    result = service.move_timed_task(
        google.uid,
        start + timedelta(days=1),
        end=start + timedelta(days=1, hours=1),
    )
    assert not result.ok
    assert queue.count_pending_ops() == before
    assert tasks.get_by_uid(ordinary.uid).google_calendar_event_id is None
    queue.close()
    tasks.close()
