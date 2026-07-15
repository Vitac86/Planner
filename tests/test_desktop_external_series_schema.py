"""Schema v7 is additive/idempotent and preserves every v6 data family."""
import sqlite3
from datetime import date, datetime, time

from planner_desktop.domain.recurrence import (
    RecurrenceFrequency,
    RecurrenceRule,
    SeriesSchedule,
    TaskSeries,
)
from planner_desktop.domain.task import Task
from planner_desktop.domain.templates import TaskTemplate
from planner_desktop.storage.schema import SCHEMA_VERSION, create_schema
from planner_desktop.storage.series_repository import SQLiteSeriesRepository
from planner_desktop.storage.sqlite_task_repository import SQLiteTaskRepository
from planner_desktop.storage.tag_repository import SQLiteTagRepository
from planner_desktop.storage.template_repository import SQLiteTemplateRepository
from planner_desktop.usecases.tag_service import TagService


def _seed_v6_families(db_path):
    tasks = SQLiteTaskRepository(db_path)
    tags_repo = SQLiteTagRepository(db_path)
    tags = TagService(tags_repo, tasks)
    tag = tags.get_or_create("Сохранить")
    task = tasks.add(Task(title="Старая задача"))
    tags.set_task_tags(task.uid, [tag.id])

    series_repo = SQLiteSeriesRepository(db_path)
    series_repo.add(TaskSeries(
        title="Локальная серия",
        schedule=SeriesSchedule(
            start_date=date(2026, 7, 1), all_day=False,
            local_time=time(9), duration_minutes=30, timezone_name="Europe/Moscow",
        ),
        rule=RecurrenceRule(RecurrenceFrequency.DAILY),
    ))
    template_repo = SQLiteTemplateRepository(db_path)
    template_repo.add(TaskTemplate(name="Старый шаблон", title="Шаблон"))
    template_repo.close()
    series_repo.close()
    tags_repo.close()
    tasks.close()


def test_schema_v7_migration_is_additive_and_idempotent(tmp_path):
    db_path = tmp_path / "desktop.db"
    _seed_v6_families(db_path)

    # Reconstruct the exact migration boundary: v6 data exists but the new
    # catalog/indexes do not.
    connection = sqlite3.connect(db_path)
    connection.execute("DROP TABLE external_calendar_series")
    connection.execute("PRAGMA user_version = 6")
    connection.commit()

    create_schema(connection)
    create_schema(connection)

    assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION == 7
    columns = {
        row[1] for row in connection.execute(
            "PRAGMA table_info(external_calendar_series)"
        ).fetchall()
    }
    assert {
        "provider", "calendar_id", "remote_event_id", "recurrence_lines_json",
        "parsed_rule_json", "support_status", "remote_status", "deleted_at",
    } <= columns
    indexes = {
        row[1] for row in connection.execute(
            "PRAGMA index_list(external_calendar_series)"
        ).fetchall()
    }
    assert {
        "idx_external_series_remote_event",
        "idx_external_series_status",
        "idx_external_series_last_seen",
    } <= indexes
    assert connection.execute("SELECT title FROM tasks").fetchone()[0] == "Старая задача"
    assert connection.execute("SELECT name FROM tags").fetchone()[0] == "Сохранить"
    assert connection.execute("SELECT title FROM task_series").fetchone()[0] == "Локальная серия"
    assert connection.execute("SELECT name FROM task_templates").fetchone()[0] == "Старый шаблон"
    connection.close()


def test_external_catalog_has_no_foreign_key_to_tasks_or_local_series(tmp_path):
    connection = sqlite3.connect(tmp_path / "desktop.db")
    create_schema(connection)
    assert connection.execute(
        "PRAGMA foreign_key_list(external_calendar_series)"
    ).fetchall() == []
    connection.close()

