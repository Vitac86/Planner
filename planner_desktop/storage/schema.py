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

SCHEMA_VERSION = 6

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

# Локальные повторяющиеся серии (Phase 3.2A). Google-полей нет сознательно:
# серии и их экземпляры НЕ синхронизируются с Calendar в этой фазе.
# weekdays_csv — выбранные дни недели weekly-правила через запятую
# ("0,2,4", 0 = понедельник); даты — ISO 8601 текстом, как в tasks.
CREATE_TASK_SERIES_TABLE = """
CREATE TABLE IF NOT EXISTS task_series (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uid TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    notes TEXT NOT NULL DEFAULT '',
    priority INTEGER NOT NULL DEFAULT 0,
    start_date TEXT NOT NULL,
    all_day INTEGER NOT NULL DEFAULT 1,
    local_time TEXT,
    duration_minutes INTEGER,
    timezone_name TEXT NOT NULL DEFAULT 'UTC',
    frequency TEXT NOT NULL CHECK (frequency IN ('daily','weekly','monthly','yearly')),
    interval INTEGER NOT NULL DEFAULT 1,
    weekdays_csv TEXT NOT NULL DEFAULT '',
    month_day INTEGER,
    yearly_month INTEGER,
    yearly_day INTEGER,
    end_mode TEXT NOT NULL DEFAULT 'never' CHECK (end_mode IN ('never','until','count')),
    until_date TEXT,
    occurrence_count INTEGER,
    revision INTEGER NOT NULL DEFAULT 1,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    deleted_at TEXT
)
"""

CREATE_SERIES_ACTIVE_INDEX = """
CREATE INDEX IF NOT EXISTS idx_task_series_active
ON task_series (active, deleted_at)
"""

# Теги серии. FK на tags каскадирует только ассоциацию; исторические
# Task-строки серии никакими FK не задеваются.
CREATE_SERIES_TAGS_TABLE = """
CREATE TABLE IF NOT EXISTS series_tags (
    series_uid TEXT NOT NULL,
    tag_id INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (series_uid, tag_id),
    FOREIGN KEY (series_uid) REFERENCES task_series(uid) ON DELETE CASCADE,
    FOREIGN KEY (tag_id) REFERENCES tags(id) ON DELETE CASCADE
)
"""

CREATE_SERIES_TAGS_INDEX = """
CREATE INDEX IF NOT EXISTS idx_series_tags_series ON series_tags (series_uid)
"""

# Локальные шаблоны задач. rule_* заполняются только для kind='recurring'.
CREATE_TASK_TEMPLATES_TABLE = """
CREATE TABLE IF NOT EXISTS task_templates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uid TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    normalized_name TEXT NOT NULL,
    template_kind TEXT NOT NULL DEFAULT 'ordinary'
        CHECK (template_kind IN ('ordinary','recurring')),
    title TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    priority INTEGER NOT NULL DEFAULT 0,
    schedule_mode TEXT NOT NULL DEFAULT 'none'
        CHECK (schedule_mode IN ('none','allday','timed')),
    time_text TEXT NOT NULL DEFAULT '',
    duration_minutes INTEGER,
    rule_frequency TEXT,
    rule_interval INTEGER,
    rule_weekdays_csv TEXT,
    rule_month_day INTEGER,
    rule_yearly_month INTEGER,
    rule_yearly_day INTEGER,
    rule_end_mode TEXT,
    rule_until_date TEXT,
    rule_occurrence_count INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    deleted_at TEXT
)
"""

# Уникальность нормализованного имени — только среди живых шаблонов:
# удалённый шаблон не блокирует повторное использование имени.
CREATE_TEMPLATES_NAME_INDEX = """
CREATE UNIQUE INDEX IF NOT EXISTS idx_task_templates_active_name
ON task_templates (normalized_name) WHERE deleted_at IS NULL
"""

CREATE_TEMPLATE_TAGS_TABLE = """
CREATE TABLE IF NOT EXISTS template_tags (
    template_uid TEXT NOT NULL,
    tag_id INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (template_uid, tag_id),
    FOREIGN KEY (template_uid) REFERENCES task_templates(uid) ON DELETE CASCADE,
    FOREIGN KEY (tag_id) REFERENCES tags(id) ON DELETE CASCADE
)
"""

CREATE_TEMPLATE_TAGS_INDEX = """
CREATE INDEX IF NOT EXISTS idx_template_tags_template
ON template_tags (template_uid)
"""

# Идентичность экземпляра серии уникальна, включая тумбстоуны и exception:
# регенерация физически не может создать дубль слота.
CREATE_TASK_OCCURRENCE_INDEX = """
CREATE UNIQUE INDEX IF NOT EXISTS idx_tasks_series_occurrence
ON tasks (series_uid, occurrence_key)
WHERE series_uid IS NOT NULL AND occurrence_key IS NOT NULL
"""

CREATE_TASK_SERIES_INDEX = """
CREATE INDEX IF NOT EXISTS idx_tasks_series_uid
ON tasks (series_uid) WHERE series_uid IS NOT NULL
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


def _migrate_series_columns(connection: sqlite3.Connection) -> None:
    """Аддитивные колонки привязки задач к локальной серии (v5 -> v6).

    Идемпотентно: колонка добавляется, только если её нет. Существующие
    строки остаются обычными задачами (series_uid = NULL); Google
    recurring-метаданные не трогаются; TaskSeries из Google-повторений
    НЕ строится.
    """
    existing = _column_names(connection, "tasks")
    if "series_uid" not in existing:
        connection.execute("ALTER TABLE tasks ADD COLUMN series_uid TEXT")
    if "occurrence_key" not in existing:
        connection.execute("ALTER TABLE tasks ADD COLUMN occurrence_key TEXT")
    if "series_revision" not in existing:
        connection.execute("ALTER TABLE tasks ADD COLUMN series_revision INTEGER")
    if "is_series_exception" not in existing:
        connection.execute(
            "ALTER TABLE tasks ADD COLUMN is_series_exception INTEGER "
            "NOT NULL DEFAULT 0"
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
    connection.execute(CREATE_TASK_SERIES_TABLE)
    connection.execute(CREATE_SERIES_ACTIVE_INDEX)
    connection.execute(CREATE_SERIES_TAGS_TABLE)
    connection.execute(CREATE_SERIES_TAGS_INDEX)
    connection.execute(CREATE_TASK_TEMPLATES_TABLE)
    connection.execute(CREATE_TEMPLATES_NAME_INDEX)
    connection.execute(CREATE_TEMPLATE_TAGS_TABLE)
    connection.execute(CREATE_TEMPLATE_TAGS_INDEX)
    _migrate_completed_at(connection)
    _migrate_series_columns(connection)
    connection.execute(CREATE_TASK_OCCURRENCE_INDEX)
    connection.execute(CREATE_TASK_SERIES_INDEX)
    connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    connection.commit()
