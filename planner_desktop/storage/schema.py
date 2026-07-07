"""Схема SQLite нового десктопа.

Минимальная таблица под ``planner_desktop.domain.task.Task`` — один в один
поля dataclass-а, включая заготовки под будущую синхронизацию с Google
Calendar (``google_calendar_*``) и тумбстоун ``deleted_at``.

Схема независима от старых ``models/`` (SQLModel) и старого ``app.db``:
никакие их таблицы здесь не воспроизводятся и не читаются.

Даты хранятся текстом в ISO 8601 (naive — как ввёл пользователь,
``updated_at``/``deleted_at`` — UTC с оффсетом); булевы поля — 0/1.
"""
from __future__ import annotations

import sqlite3

SCHEMA_VERSION = 1

# "end" — зарезервированное слово SQL, поэтому в кавычках.
CREATE_TASKS_TABLE = """
CREATE TABLE IF NOT EXISTS tasks (
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


def create_schema(connection: sqlite3.Connection) -> None:
    """Создаёт таблицы, если их ещё нет (безопасно вызывать повторно)."""
    connection.execute(CREATE_TASKS_TABLE)
    connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    connection.commit()
