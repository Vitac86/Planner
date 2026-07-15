from datetime import date
import sqlite3

import pytest

from planner_desktop.domain.recurrence import RecurrenceFrequency, RecurrenceRule
from planner_desktop.domain.tags import Tag
from planner_desktop.domain.templates import TaskTemplate
from planner_desktop.storage.tag_repository import SQLiteTagRepository
from planner_desktop.storage.template_repository import SQLiteTemplateRepository


def test_template_and_tags_survive_reopen(tmp_path):
    db_path = tmp_path / "desktop.db"
    tags = SQLiteTagRepository(db_path)
    tag = tags.add(Tag("Work", "work"))
    repository = SQLiteTemplateRepository(db_path)
    template = repository.add(TaskTemplate(
        name="Weekly planning",
        kind="recurring",
        title="Plan week",
        schedule_mode="allday",
        rule=RecurrenceRule(
            RecurrenceFrequency.WEEKLY, weekdays=(0,), interval=2
        ),
    ))
    repository.set_template_tags(template.uid, [tag.id])
    repository.close()

    reopened = SQLiteTemplateRepository(db_path)
    loaded = reopened.get_by_uid(template.uid)
    assert loaded.name == "Weekly planning"
    assert loaded.rule.weekdays == (0,)
    assert loaded.tags == ("Work",)
    assert reopened.tag_ids_for_template(template.uid) == [tag.id]
    reopened.close()
    tags.close()


def test_active_normalized_template_name_is_unique_but_reusable_after_delete(tmp_path):
    repository = SQLiteTemplateRepository(tmp_path / "desktop.db")
    first = repository.add(TaskTemplate(name="  Review  ", title="Review"))
    with pytest.raises(sqlite3.IntegrityError):
        repository.add(TaskTemplate(name="review", title="Other"))
    assert repository.delete(first.uid)
    second = repository.add(TaskTemplate(name="review", title="New"))
    assert second.uid != first.uid


def test_template_delete_does_not_delete_unrelated_tasks_or_series(tmp_path):
    from planner_desktop.domain.recurrence import SeriesSchedule, TaskSeries
    from planner_desktop.domain.task import Task
    from planner_desktop.storage.series_repository import SQLiteSeriesRepository
    from planner_desktop.storage.sqlite_task_repository import SQLiteTaskRepository

    db_path = tmp_path / "desktop.db"
    templates = SQLiteTemplateRepository(db_path)
    tasks = SQLiteTaskRepository(db_path)
    series_repository = SQLiteSeriesRepository(db_path)
    template = templates.add(TaskTemplate(name="Source", title="Task"))
    task = tasks.add(Task(title="Created independently"))
    series = series_repository.add(TaskSeries(
        title="Created series",
        schedule=SeriesSchedule(date(2026, 7, 1), True, timezone_name="UTC"),
        rule=RecurrenceRule(RecurrenceFrequency.DAILY),
    ))
    assert templates.delete(template.uid)
    assert tasks.get_by_uid(task.uid) is not None
    assert series_repository.get_by_uid(series.uid) is not None

