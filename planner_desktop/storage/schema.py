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

SCHEMA_VERSION = 5

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
    completed_at TEXT,
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

# Ежедневные (повторяющиеся по дням недели) локальные задачи.
# Полностью локальны: в Google Calendar не уходят, Calendar-операций
# не порождают. weekdays_mask — 7 бит (Пн..Вс), см. domain/daily_task.py.
CREATE_DAILY_TASKS_TABLE = """
CREATE TABLE IF NOT EXISTS desktop_daily_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uid TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    notes TEXT NOT NULL DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 1,
    weekdays_mask INTEGER NOT NULL DEFAULT 127,
    preferred_time TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    deleted_at TEXT
)
"""

# Отметки выполнения ежедневной задачи на конкретную дату.
# Наличие строки = задача выполнена в этот день. Ключ (uid, дата) —
# отметка идемпотентна и хранится по-дневно.
CREATE_DAILY_COMPLETIONS_TABLE = """
CREATE TABLE IF NOT EXISTS desktop_daily_completions (
    daily_uid TEXT NOT NULL,
    done_date TEXT NOT NULL,
    completed_at TEXT NOT NULL,
    PRIMARY KEY (daily_uid, done_date)
)
"""

CREATE_TAGS_TABLE = """
CREATE TABLE IF NOT EXISTS tags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    normalized_name TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""

CREATE_TAGS_NORMALIZED_INDEX = """
CREATE UNIQUE INDEX IF NOT EXISTS idx_tags_normalized_name
ON tags (normalized_name)
"""

CREATE_TASK_TAGS_TABLE = """
CREATE TABLE IF NOT EXISTS task_tags (
    task_uid TEXT NOT NULL,
    tag_id INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (task_uid, tag_id),
    FOREIGN KEY (task_uid) REFERENCES tasks(uid) ON DELETE CASCADE,
    FOREIGN KEY (tag_id) REFERENCES tags(id) ON DELETE CASCADE
)
"""

CREATE_TASK_TAGS_TASK_INDEX = """
CREATE INDEX IF NOT EXISTS idx_task_tags_task_uid ON task_tags (task_uid)
"""

CREATE_TASK_TAGS_TAG_INDEX = """
CREATE INDEX IF NOT EXISTS idx_task_tags_tag_id ON task_tags (tag_id)
"""


def _column_names(connection: sqlite3.Connection, table: str) -> set:
    rows = connection.execute(f"PRAGMA table_info({table})").fetchall()
    # PRAGMA table_info: (cid, name, type, notnull, dflt_value, pk)
    return {row[1] for row in rows}


def _migrate_completed_at(connection: sqlite3.Connection) -> None:
    """Аддитивно добавляет tasks.completed_at и заполняет его для уже
    выполненных задач (v3 -> v4). Идемпотентно: колонка добавляется только
    если её нет, backfill трогает лишь строки с NULL-меткой."""
    if "completed_at" not in _column_names(connection, "tasks"):
        connection.execute("ALTER TABLE tasks ADD COLUMN completed_at TEXT")
    # Историю выполненных до миграции задач приблизительно датируем их
    # последним изменением — лучшая доступная оценка момента выполнения.
    connection.execute(
        "UPDATE tasks SET completed_at = updated_at "
        "WHERE completed = 1 AND completed_at IS NULL"
    )


def create_schema(connection: sqlite3.Connection) -> None:
    """Создаёт таблицы, если их ещё нет (безопасно вызывать повторно).

    «Миграции» нового десктопа: идемпотентные CREATE IF NOT EXISTS плюс
    аддитивные ALTER-шаги (например, tasks.completed_at); старый
    storage/migrations.py не используется, данные не переписываются
    деструктивно.
    """
    connection.execute(CREATE_TASKS_TABLE)
    connection.execute(CREATE_PENDING_CALENDAR_OPS_TABLE)
    connection.execute(CREATE_PENDING_OPS_DUE_INDEX)
    connection.execute(CREATE_SYNC_STATE_TABLE)
    connection.execute(CREATE_DAILY_TASKS_TABLE)
    connection.execute(CREATE_DAILY_COMPLETIONS_TABLE)
    connection.execute(CREATE_TAGS_TABLE)
    connection.execute(CREATE_TAGS_NORMALIZED_INDEX)
    connection.execute(CREATE_TASK_TAGS_TABLE)
    connection.execute(CREATE_TASK_TAGS_TASK_INDEX)
    connection.execute(CREATE_TASK_TAGS_TAG_INDEX)
    _migrate_completed_at(connection)
    connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    connection.commit()
