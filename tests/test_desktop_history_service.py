"""Тесты HistoryService и семантики Task.completed_at: журнал выполненного
(разовые задачи + отметки ежедневных), группировка по датам, фильтр
диапазона, миграция схемы v3 -> v4. Без окна и без сети.
"""
import sqlite3
from datetime import date, datetime, timedelta, timezone

import pytest

from planner_desktop.domain.daily_task import DailyTask
from planner_desktop.domain.task import Task, utc_now
from planner_desktop.repositories.daily_task_repository import (
    InMemoryDailyTaskRepository,
)
from planner_desktop.repositories.fake_task_repository import FakeTaskRepository
from planner_desktop.storage.schema import SCHEMA_VERSION, create_schema
from planner_desktop.storage.sqlite_task_repository import SQLiteTaskRepository
from planner_desktop.usecases.history_service import (
    RANGE_7_DAYS,
    RANGE_30_DAYS,
    RANGE_ALL,
    HistoryService,
)


# ---- Task.set_completed: метка времени выполнения -----------------------------------

def test_set_completed_stamps_transition_to_done():
    task = Task(title="a")
    assert task.completed_at is None
    task.set_completed(True)
    assert task.completed is True
    assert task.completed_at is not None


def test_set_completed_keeps_stamp_on_repeated_save():
    """Повторное сохранение уже выполненной задачи не сдвигает её в истории."""
    task = Task(title="a")
    first = datetime(2026, 7, 1, 10, 0, tzinfo=timezone.utc)
    task.set_completed(True, when=first)
    task.set_completed(True)
    assert task.completed_at == first


def test_set_completed_false_clears_stamp():
    task = Task(title="a")
    task.set_completed(True)
    task.set_completed(False)
    assert task.completed is False
    assert task.completed_at is None


def test_sqlite_toggle_persists_completed_at(tmp_path):
    repo = SQLiteTaskRepository(tmp_path / "app_desktop.db")
    try:
        task = repo.add(Task(title="a"))
        repo.toggle_completed(task.uid)
        stored = repo.get_by_uid(task.uid)
        assert stored.completed is True
        assert stored.completed_at is not None

        repo.toggle_completed(task.uid)
        stored = repo.get_by_uid(task.uid)
        assert stored.completed is False
        assert stored.completed_at is None
    finally:
        repo.close()


# ---- миграция схемы v3 -> v4 ---------------------------------------------------------

V3_TASKS_TABLE = """
CREATE TABLE tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uid TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    notes TEXT NOT NULL DEFAULT '',
    start TEXT,
    "end" TEXT,
    duration_minutes INTEGER,
    is_all_day INTEGER NOT NULL DEFAULT 0,
    priority INTEGER NOT NULL DEFAULT 0,
    completed INTEGER NOT NULL DEFAULT 0,
    google_calendar_event_id TEXT,
    google_calendar_etag TEXT,
    google_calendar_recurring_event_id TEXT,
    google_calendar_original_start TEXT,
    updated_at TEXT NOT NULL,
    deleted_at TEXT
)
"""


def test_migration_adds_and_backfills_completed_at(tmp_path):
    """Старой БД (v3, без completed_at) create_schema аддитивно добавляет
    колонку и датирует уже выполненные задачи их updated_at."""
    db_path = tmp_path / "app_desktop.db"
    done_stamp = datetime(2026, 7, 5, 9, 30, tzinfo=timezone.utc)

    old = sqlite3.connect(db_path)
    old.execute(V3_TASKS_TABLE)
    old.execute(
        "INSERT INTO tasks (uid, title, completed, updated_at) VALUES (?, ?, 1, ?)",
        ("uid-done", "Выполненная до миграции", done_stamp.isoformat()),
    )
    old.execute(
        "INSERT INTO tasks (uid, title, completed, updated_at) VALUES (?, ?, 0, ?)",
        ("uid-open", "Невыполненная", utc_now().isoformat()),
    )
    old.execute("PRAGMA user_version = 3")
    old.commit()
    old.close()

    repo = SQLiteTaskRepository(db_path)  # create_schema внутри
    try:
        assert repo.schema_version() == SCHEMA_VERSION
        done = repo.get_by_uid("uid-done")
        assert done.completed_at == done_stamp
        assert repo.get_by_uid("uid-open").completed_at is None
    finally:
        repo.close()


def test_migration_is_idempotent(tmp_path):
    db_path = tmp_path / "app_desktop.db"
    for _ in range(2):
        connection = sqlite3.connect(db_path)
        connection.row_factory = sqlite3.Row
        create_schema(connection)
        connection.close()


# ---- HistoryService: сборка журнала -------------------------------------------------

@pytest.fixture()
def tasks_repo():
    return FakeTaskRepository()


@pytest.fixture()
def daily_repo():
    return InMemoryDailyTaskRepository(seed=False)


@pytest.fixture()
def history(tasks_repo, daily_repo):
    return HistoryService(tasks_repo, daily_repo)


def add_completed(repo, title, done_on: date, hour=12):
    task = repo.add(Task(title=title))
    task.set_completed(
        True,
        when=datetime(done_on.year, done_on.month, done_on.day, hour,
                      0, tzinfo=timezone.utc),
    )
    repo.update(task)
    return task


def test_groups_completed_tasks_by_date(history, tasks_repo):
    today = date.today()
    add_completed(tasks_repo, "Сегодняшняя", today)
    add_completed(tasks_repo, "Вчерашняя", today - timedelta(days=1))
    tasks_repo.add(Task(title="Не выполнена"))

    groups = history.groups(range_days=RANGE_ALL, today=today)
    assert [g.day for g in groups] == [today, today - timedelta(days=1)]
    assert [g.entries[0].title for g in groups] == ["Сегодняшняя", "Вчерашняя"]
    assert all(e.can_reopen for g in groups for e in g.entries)


def test_deleted_tasks_are_excluded(history, tasks_repo):
    today = date.today()
    task = add_completed(tasks_repo, "Удалённая", today)
    tasks_repo.delete(task.id)
    assert history.groups(today=today) == []


def test_daily_completions_appear_as_view_only_entries(history, daily_repo):
    today = date.today()
    daily = DailyTask(title="Зарядка", preferred_time="08:00")
    daily_repo.add(daily)
    daily_repo.set_completed(daily.uid, today, True)

    groups = history.groups(today=today)
    assert len(groups) == 1
    entry = groups[0].entries[0]
    assert entry.is_daily is True
    assert entry.can_reopen is False
    assert entry.title == "Зарядка"
    assert entry.time_label == "08:00"


def test_range_filter_excludes_old_entries(history, tasks_repo):
    today = date.today()
    add_completed(tasks_repo, "Свежая", today)
    add_completed(tasks_repo, "Старая", today - timedelta(days=40))

    week = history.groups(range_days=RANGE_7_DAYS, today=today)
    month = history.groups(range_days=RANGE_30_DAYS, today=today)
    everything = history.groups(range_days=RANGE_ALL, today=today)

    assert sum(g.count for g in week) == 1
    assert sum(g.count for g in month) == 1
    assert sum(g.count for g in everything) == 2


def test_entries_within_day_sorted_latest_first(history, tasks_repo):
    today = date.today()
    add_completed(tasks_repo, "Утро", today, hour=8)
    add_completed(tasks_repo, "Вечер", today, hour=20)

    groups = history.groups(today=today)
    assert [e.title for e in groups[0].entries] == ["Вечер", "Утро"]


def test_total_completed_counts_both_sources(history, tasks_repo, daily_repo):
    today = date.today()
    add_completed(tasks_repo, "Разовая", today)
    daily = DailyTask(title="Ежедневная")
    daily_repo.add(daily)
    daily_repo.set_completed(daily.uid, today, True)

    assert history.total_completed(range_days=RANGE_ALL, today=today) == 2
