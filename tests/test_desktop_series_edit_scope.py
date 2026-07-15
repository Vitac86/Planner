from datetime import date, time

from planner_desktop.domain.commands import TaskEditorCommand
from planner_desktop.domain.recurrence import (
    RecurrenceFrequency,
    RecurrenceRule,
    SeriesSchedule,
    TaskSeries,
)
from planner_desktop.repositories.fake_task_repository import FakeTaskRepository
from planner_desktop.repositories.series_repository import InMemorySeriesRepository
from planner_desktop.storage.series_repository import SQLiteSeriesRepository
from planner_desktop.storage.sqlite_task_repository import SQLiteTaskRepository
from planner_desktop.usecases.recurrence_service import RecurrenceService


def series():
    return TaskSeries(
        title="Standup",
        schedule=SeriesSchedule(
            date(2026, 7, 1), False, time(9), 30, "Europe/Moscow"
        ),
        rule=RecurrenceRule(RecurrenceFrequency.DAILY),
    )


def command(day: int, hour: int = 9, title: str = "Standup"):
    return TaskEditorCommand(
        title=title,
        notes="edited",
        add_to_calendar=True,
        is_all_day=False,
        date_text=f"2026-07-{day:02d}",
        time_text=f"{hour:02d}:00",
        duration_text="45",
        priority=2,
    )


def in_memory_service():
    tasks = FakeTaskRepository(seed=False)
    series_repository = InMemorySeriesRepository()
    service = RecurrenceService(series_repository, tasks)
    created = service.create_series(series()).series
    service.ensure_occurrences(date(2026, 7, 1), date(2026, 7, 6))
    return tasks, series_repository, service, created


def test_this_occurrence_keeps_key_and_exception_survives_regeneration():
    tasks, _repo, service, created = in_memory_service()
    occurrence = tasks.list_by_series(created.uid)[1]
    original_key = occurrence.occurrence_key
    result = service.edit_occurrence(occurrence.uid, command(2, 14, "One-off"))
    assert result.ok
    assert result.task.occurrence_key == original_key
    assert result.task.is_series_exception
    assert result.task.start.hour == 14

    service.ensure_occurrences(date(2026, 7, 1), date(2026, 7, 6))
    stored = tasks.get_by_uid(occurrence.uid)
    assert (stored.title, stored.start.hour, stored.occurrence_key) == (
        "One-off", 14, original_key
    )


def test_this_and_future_splits_and_preserves_past_and_completed_history():
    tasks, repository, service, created = in_memory_service()
    rows = tasks.list_by_series(created.uid)
    tasks.complete(rows[0].id, True)
    selected = rows[2]

    result = service.edit_this_and_future(
        selected.uid,
        command(3, 11, "New standup"),
        rule=RecurrenceRule(RecurrenceFrequency.WEEKLY, weekdays=(4,)),
    )
    assert result.ok
    assert result.old_series.rule.until_date == date(2026, 7, 2)
    assert result.new_series.schedule.start_date == date(2026, 7, 3)
    assert result.moved_task.series_uid == result.new_series.uid
    assert tasks.get_by_uid(rows[0].uid).completed
    assert repository.get_by_uid(created.uid).rule.until_date == date(2026, 7, 2)
    assert all(
        row.completed or row.occurrence_key[:10] < "2026-07-03"
        for row in tasks.list_by_series(created.uid)
    )


def test_stop_this_and_future_and_delete_one_preserve_past():
    tasks, repository, service, created = in_memory_service()
    rows = tasks.list_by_series(created.uid)
    assert service.delete_occurrence(rows[1].uid)
    result = service.stop_this_and_future(rows[3].uid)
    assert result.ok
    assert repository.get_by_uid(created.uid).rule.until_date == date(2026, 7, 3)
    assert tasks.get_by_uid(rows[1].uid).is_deleted
    assert tasks.get_by_uid(rows[0].uid) is not None


def test_sqlite_split_failure_rolls_back_series_tasks_and_associations(
    tmp_path, monkeypatch
):
    db_path = tmp_path / "desktop.db"
    tasks = SQLiteTaskRepository(db_path)
    series_repository = SQLiteSeriesRepository(db_path)
    service = RecurrenceService(series_repository, tasks)
    created = service.create_series(series()).series
    service.ensure_occurrences(date(2026, 7, 1), date(2026, 7, 5))
    before = [
        (row.uid, row.series_uid, row.occurrence_key)
        for row in tasks.list_by_series(created.uid)
    ]
    selected = tasks.list_by_series(created.uid)[2]

    def fail(_task):
        raise RuntimeError("injected split failure")

    monkeypatch.setattr(series_repository, "_update_split_task_no_commit", fail)
    result = service.edit_this_and_future(selected.uid, command(3, 12))
    assert not result.ok
    assert "injected split failure" in result.errors[0]
    assert series_repository.get_by_uid(created.uid).rule.end_mode.value == "never"
    assert series_repository.list_all(include_inactive=True) == [
        series_repository.get_by_uid(created.uid)
    ]
    after = [
        (row.uid, row.series_uid, row.occurrence_key)
        for row in tasks.list_by_series(created.uid)
    ]
    assert after == before
    series_repository.close()
    tasks.close()

