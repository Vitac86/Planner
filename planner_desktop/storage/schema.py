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

SCHEMA_VERSION = 2

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


# Очередь отложенных операций Calendar-синка (push из десктопа).
# status: pending — ждёт push-а; terminal — dead-letter, в push не выбирается.
# Даты — текст ISO 8601 (UTC с оффсетом), как и в tasks.
CREATE_PENDING_CALENDAR_OPS_TABLE = """
CREATE TABLE IF NOT EXISTS desktop_pending_calendar_ops (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    op TEXT NOT NULL CHECK (op IN ('create', 'update', 'delete')),
    task_uid TEXT NOT NULL,
    payload_json TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'terminal')),
    created_at TEXT NOT NULL,
    next_try_at TEXT NOT NULL
)
"""

CREATE_PENDING_OPS_DUE_INDEX = """
CREATE INDEX IF NOT EXISTS idx_pending_calendar_ops_due
ON desktop_pending_calendar_ops (status, next_try_at)
"""

# Ключ-значение состояния синка (курсор pull-а и т.п.).
CREATE_SYNC_STATE_TABLE = """
CREATE TABLE IF NOT EXISTS desktop_sync_state (
    key TEXT PRIMARY KEY,
    value TEXT,
    updated_at TEXT NOT NULL
)
"""


def create_schema(connection: sqlite3.Connection) -> None:
    """Создаёт таблицы, если их ещё нет (безопасно вызывать повторно).

    Единственный механизм «миграции» нового десктопа: только идемпотентные
    CREATE IF NOT EXISTS; старый storage/migrations.py не используется.
    """
    connection.execute(CREATE_TASKS_TABLE)
    connection.execute(CREATE_PENDING_CALENDAR_OPS_TABLE)
    connection.execute(CREATE_PENDING_OPS_DUE_INDEX)
    connection.execute(CREATE_SYNC_STATE_TABLE)
    connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    connection.commit()
