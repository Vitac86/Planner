"""SQLite-репозиторий локальных серий + миграция схемы v6.

Проверяется: аддитивность/идемпотентность миграции, выживание старых
данных, персистентность серий/тегов после reopen, уникальность
идентичности экземпляра (series_uid, occurrence_key) и тумбстоуны.
"""
import sqlite3
from datetime import date, datetime, time

import pytest

from planner_desktop.domain.recurrence import (
    RecurrenceEndMode,
    RecurrenceFrequency,
    RecurrenceRule,
    SeriesSchedule,
    TaskSeries,
    replace_series,
)
from planner_desktop.domain.tags import Tag
from planner_desktop.domain.task import Task, utc_now
from planner_desktop.storage.schema import SCHEMA_VERSION
from planner_desktop.storage.series_repository import (
    SQLiteSeriesRepository,
    csv_to_weekdays,
    weekdays_to_csv,
)
from planner_desktop.storage.sqlite_task_repository import SQLiteTaskRepository
from planner_desktop.storage.tag_repository import SQLiteTagRepository

MOSCOW = "Europe/Moscow"


def make_series(**kwargs):
    defaults = dict(
        title="Планёрка",
        schedule=SeriesSchedule(
            start_date=date(2026, 7, 6),
            all_day=False,
            local_time=time(10, 0),
            duration_minutes=30,
            timezone_name=MOSCOW,
        ),
        rule=RecurrenceRule(
            RecurrenceFrequency.WEEKLY,
            weekdays=(0, 2, 4),
            end_mode=RecurrenceEndMode.COUNT,
            occurrence_count=20,
        ),
        notes="повестка",
        priority=2,
    )
    defaults.update(kwargs)
    return TaskSeries(**defaults)


def test_schema_v6_migration_is_additive_and_idempotent(tmp_path):
    db_path = tmp_path / "desktop.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute("CREATE TABLE preserved (value TEXT)")
        connection.execute("INSERT INTO preserved VALUES ('ok')")
        connection.commit()

    # Старый Task переживает миграцию и остаётся обычной задачей.
    tasks = SQLiteTaskRepository(db_path)
    plain = tasks.add(Task(title="Старая задача"))
    tasks.close()

    for _ in range(3):  # многократный запуск безопасен
        repo = SQLiteSeriesRepository(db_path)
        repo.close()

    with sqlite3.connect(db_path) as connection:
        tables = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        assert {
            "preserved", "tasks", "task_series", "series_tags",
            "task_templates", "template_tags",
        } <= tables
        assert connection.execute(
            "SELECT value FROM preserved").fetchone()[0] == "ok"
        assert connection.execute(
            "PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        row = connection.execute(
            "SELECT series_uid, occurrence_key, is_series_exception "
            "FROM tasks WHERE uid = ?", (plain.uid,)
        ).fetchone()
        assert row == (None, None, 0)
        # Ни одна TaskSeries не построена из существующих задач автоматически.
        assert connection.execute(
            "SELECT COUNT(*) FROM task_series").fetchone()[0] == 0


def test_series_roundtrip_and_reopen(tmp_path):
    db_path = tmp_path / "desktop.db"
    repo = SQLiteSeriesRepository(db_path)
    series = repo.add(make_series())
    repo.close()

    reopened = SQLiteSeriesRepository(db_path)
    loaded = reopened.get_by_uid(series.uid)
    assert loaded is not None
    assert loaded.title == "Планёрка"
    assert loaded.schedule == series.schedule
    assert loaded.rule == series.rule
    assert loaded.revision == 1
    assert loaded.active
    reopened.close()


def test_series_update_and_tombstone(tmp_path):
    repo = SQLiteSeriesRepository(tmp_path / "d.db")
    series = repo.add(make_series())
    updated = replace_series(series, title="Летучка", revision=2)
    repo.update(updated)
    assert repo.get_by_uid(series.uid).title == "Летучка"
    assert repo.get_by_uid(series.uid).revision == 2

    assert repo.delete(series.uid)
    assert not repo.delete(series.uid)  # повторное удаление — False
    tombstone = repo.get_by_uid(series.uid)
    assert tombstone.is_deleted
    assert repo.list_all() == []
    assert repo.count_active() == 0


def test_series_tags_survive_reopen(tmp_path):
    db_path = tmp_path / "desktop.db"
    tags = SQLiteTagRepository(db_path)
    work = tags.add(Tag("Работа", "работа"))
    repo = SQLiteSeriesRepository(db_path)
    series = repo.add(make_series())
    repo.set_series_tags(series.uid, [work.id])
    repo.close()

    reopened = SQLiteSeriesRepository(db_path)
    assert reopened.tag_ids_for_series(series.uid) == [work.id]
    assert reopened.get_by_uid(series.uid).tags == ("Работа",)
    reopened.close()
    tags.close()


def test_unique_series_uid(tmp_path):
    repo = SQLiteSeriesRepository(tmp_path / "d.db")
    series = repo.add(make_series())
    with pytest.raises(sqlite3.IntegrityError):
        repo.add(make_series(uid=series.uid))


def test_unique_occurrence_identity_on_tasks(tmp_path):
    db_path = tmp_path / "desktop.db"
    tasks = SQLiteTaskRepository(db_path)
    tasks.add(Task(
        title="Экз.",
        start=datetime(2026, 7, 6, 10, 0),
        series_uid="s-1",
        occurrence_key="2026-07-06T10:00@Europe/Moscow",
    ))
    with pytest.raises(sqlite3.IntegrityError):
        tasks.add(Task(
            title="Дубль",
            start=datetime(2026, 7, 6, 10, 0),
            series_uid="s-1",
            occurrence_key="2026-07-06T10:00@Europe/Moscow",
        ))
    # Другая серия с тем же ключом — допустимо.
    tasks.add(Task(
        title="Другая серия",
        series_uid="s-2",
        occurrence_key="2026-07-06T10:00@Europe/Moscow",
    ))
    # Обычные задачи (NULL, NULL) индекс не ограничивает.
    tasks.add(Task(title="Обычная 1"))
    tasks.add(Task(title="Обычная 2"))
    tasks.close()


def test_task_series_fields_roundtrip_including_tombstone(tmp_path):
    tasks = SQLiteTaskRepository(tmp_path / "d.db")
    occurrence = tasks.add(Task(
        title="Экз.",
        start=datetime(2026, 7, 6, 10, 0),
        series_uid="s-1",
        occurrence_key="2026-07-06",
        series_revision=3,
        is_series_exception=True,
    ))
    loaded = tasks.get_by_uid(occurrence.uid)
    assert loaded.series_uid == "s-1"
    assert loaded.occurrence_key == "2026-07-06"
    assert loaded.series_revision == 3
    assert loaded.is_series_exception
    assert loaded.is_series_occurrence

    # Тумбстоун сохраняет привязку (иначе слот регенерировался бы).
    tasks.delete(occurrence.id)
    tombstone = tasks.get_by_uid(occurrence.uid)
    assert tombstone.is_deleted
    assert tombstone.series_uid == "s-1"
    assert tasks.list_by_series("s-1")[0].uid == occurrence.uid


def test_hard_delete_by_uid_removes_row(tmp_path):
    tasks = SQLiteTaskRepository(tmp_path / "d.db")
    occurrence = tasks.add(Task(
        title="Экз.", series_uid="s-1", occurrence_key="2026-07-06",
    ))
    assert tasks.hard_delete_by_uid(occurrence.uid)
    assert tasks.get_by_uid(occurrence.uid) is None
    assert tasks.list_by_series("s-1") == []
    assert not tasks.hard_delete_by_uid(occurrence.uid)


def test_weekdays_csv_helpers():
    assert weekdays_to_csv((0, 2, 4)) == "0,2,4"
    assert csv_to_weekdays("0,2,4") == (0, 2, 4)
    assert csv_to_weekdays("") == ()
    assert csv_to_weekdays(None) == ()
