from datetime import date, time

from planner_desktop.domain.recurrence import (
    RecurrenceFrequency,
    RecurrenceRule,
    SeriesSchedule,
    TaskSeries,
)
from planner_desktop.repositories.fake_task_repository import FakeTaskRepository
from planner_desktop.repositories.series_repository import InMemorySeriesRepository
from planner_desktop.repositories.tag_repository import InMemoryTagRepository
from planner_desktop.usecases.recurrence_service import RecurrenceService
from planner_desktop.usecases.tag_service import TagService


def build():
    tasks = FakeTaskRepository(seed=False)
    series = InMemorySeriesRepository()
    tag_repository = InMemoryTagRepository()
    tags = TagService(tag_repository, tasks)
    return tasks, series, tags, RecurrenceService(series, tasks, tags)


def daily_series(**changes):
    values = dict(
        title="Daily review",
        schedule=SeriesSchedule(
            start_date=date(2026, 7, 1),
            all_day=False,
            local_time=time(9),
            duration_minutes=30,
            timezone_name="Europe/Moscow",
        ),
        rule=RecurrenceRule(RecurrenceFrequency.DAILY),
    )
    values.update(changes)
    return TaskSeries(**values)


def test_materialization_is_idempotent_and_preserves_completed_rows():
    tasks, _series, _tags, service = build()
    created = service.create_series(daily_series()).series
    first = service.ensure_occurrences(date(2026, 7, 1), date(2026, 7, 3))
    assert (first.created, first.existing, first.skipped) == (3, 0, 0)

    middle = tasks.list_by_series(created.uid)[1]
    tasks.complete(middle.id, True)
    second = service.ensure_occurrences(date(2026, 7, 1), date(2026, 7, 3))
    assert (second.created, second.existing) == (0, 3)
    assert tasks.get_by_uid(middle.uid).completed


def test_series_tags_are_applied_to_new_occurrences_without_overwriting_edits():
    tasks, series_repository, tags, service = build()
    work = tags.create("Work")
    series = service.create_series(daily_series(), tag_ids=[work.id]).series
    service.ensure_occurrences(date(2026, 7, 1), date(2026, 7, 1))
    first = tasks.list_by_series(series.uid)[0]
    assert tags.tags_for_task(first.uid)[0].name == "Work"

    personal = tags.create("Personal")
    tags.set_task_tags(first.uid, [personal.id])
    service.ensure_occurrences(date(2026, 7, 1), date(2026, 7, 2))
    rows = tasks.list_by_series(series.uid)
    assert [tag.name for tag in tags.tags_for_task(rows[0].uid)] == ["Personal"]
    assert [tag.name for tag in tags.tags_for_task(rows[1].uid)] == ["Work"]
    assert series_repository.tag_ids_for_series(series.uid) == [work.id]


def test_deleted_occurrence_is_a_permanent_materialization_tombstone():
    tasks, _series, _tags, service = build()
    series = service.create_series(daily_series()).series
    service.ensure_occurrences(date(2026, 7, 1), date(2026, 7, 1))
    occurrence = tasks.list_by_series(series.uid)[0]
    assert service.delete_occurrence(occurrence.uid)
    result = service.ensure_occurrences(date(2026, 7, 1), date(2026, 7, 1))
    assert (result.created, result.skipped) == (0, 1)
    assert len(tasks.list_by_series(series.uid)) == 1
