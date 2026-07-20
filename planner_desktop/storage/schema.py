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

SCHEMA_VERSION = 11

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

# Read-only catalog of recurring masters discovered during an explicit Google
# pull (Phase 3.2B1).  It is deliberately separate from local task_series:
# discovery must not adopt/link a local series and there is no FK cascade to
# tasks or task_series.  recurrence_lines_json is the exact ordered transport
# array; parsed_rule_json is present only for the lossless Planner subset.
CREATE_EXTERNAL_CALENDAR_SERIES_TABLE = """
CREATE TABLE IF NOT EXISTS external_calendar_series (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL,
    calendar_id TEXT NOT NULL,
    remote_event_id TEXT NOT NULL,
    etag TEXT,
    title TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    start_kind TEXT NOT NULL CHECK (start_kind IN ('timed', 'all_day')),
    start_value TEXT,
    end_value TEXT,
    timezone_name TEXT,
    recurrence_lines_json TEXT NOT NULL DEFAULT '[]',
    parsed_rule_json TEXT,
    support_status TEXT NOT NULL CHECK (support_status IN ('supported', 'unsupported')),
    unsupported_reason TEXT,
    remote_status TEXT NOT NULL DEFAULT 'confirmed',
    remote_updated_at TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    deleted_at TEXT,
    UNIQUE (provider, calendar_id, remote_event_id)
)
"""

CREATE_EXTERNAL_SERIES_REMOTE_INDEX = """
CREATE INDEX IF NOT EXISTS idx_external_series_remote_event
ON external_calendar_series (remote_event_id)
"""

CREATE_EXTERNAL_SERIES_STATUS_INDEX = """
CREATE INDEX IF NOT EXISTS idx_external_series_status
ON external_calendar_series (support_status, remote_status, deleted_at)
"""

CREATE_EXTERNAL_SERIES_LAST_SEEN_INDEX = """
CREATE INDEX IF NOT EXISTS idx_external_series_last_seen
ON external_calendar_series (last_seen_at)
"""


# Explicit local TaskSeries <-> Google recurring-master links (Phase 3.2B2).
# Rows are retained after detach for diagnostics/history.  There is deliberately
# no cascading foreign key: deleting a link or tombstoning a series must never
# remove materialized Task history.  Schema v9 (Phase 3.2B3A) adds the durable
# conflict base (etag/hash/snapshot), resolution metadata and link generations;
# v8 databases receive the same columns additively below.
CREATE_TASK_SERIES_CALENDAR_LINKS_TABLE = """
CREATE TABLE IF NOT EXISTS task_series_calendar_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    series_uid TEXT NOT NULL,
    provider TEXT NOT NULL,
    calendar_id TEXT NOT NULL,
    remote_event_id TEXT NOT NULL,
    remote_etag TEXT,
    remote_updated_at TEXT,
    link_status TEXT NOT NULL CHECK (link_status IN (
        'pending_create','synced','pending_update','pending_delete',
        'conflict','remote_deleted','detached','terminal_error'
    )),
    last_synced_series_revision INTEGER,
    last_synced_payload_hash TEXT,
    linked_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    detached_at TEXT,
    last_error TEXT,
    link_generation INTEGER NOT NULL DEFAULT 0,
    conflict_detected_at TEXT,
    conflict_reason TEXT,
    conflict_remote_etag TEXT,
    conflict_remote_payload_hash TEXT,
    conflict_remote_snapshot_json TEXT,
    resolved_at TEXT,
    resolution_kind TEXT
)
"""

CREATE_ACTIVE_SERIES_LINK_INDEX = """
CREATE UNIQUE INDEX IF NOT EXISTS idx_series_calendar_links_active_series
ON task_series_calendar_links (series_uid)
WHERE link_status <> 'detached'
"""

CREATE_ACTIVE_REMOTE_LINK_INDEX = """
CREATE UNIQUE INDEX IF NOT EXISTS idx_series_calendar_links_active_remote
ON task_series_calendar_links (provider, calendar_id, remote_event_id)
WHERE link_status <> 'detached'
"""

CREATE_SERIES_LINK_STATUS_INDEX = """
CREATE INDEX IF NOT EXISTS idx_series_calendar_links_status
ON task_series_calendar_links (link_status, updated_at)
"""


# Independent dead-letter queue for recurring-master writes.  At most one
# pending row exists per series; terminal rows remain visible and are never
# selected automatically again.  v9: a non-null resolution_id marks an explicit
# conflict-resolution/recovery operation (op values stay within the v8 CHECK).
CREATE_PENDING_CALENDAR_SERIES_OPS_TABLE = """
CREATE TABLE IF NOT EXISTS pending_calendar_series_ops (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    series_uid TEXT NOT NULL,
    op TEXT NOT NULL CHECK (op IN ('create','update','delete')),
    remote_event_id TEXT,
    desired_revision INTEGER,
    desired_payload_hash TEXT,
    payload_json TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending','terminal')),
    created_at TEXT NOT NULL,
    next_try_at TEXT NOT NULL,
    resolution_id INTEGER,
    acknowledged_remote_etag TEXT
)
"""

CREATE_PENDING_SERIES_OP_UNIQUE_INDEX = """
CREATE UNIQUE INDEX IF NOT EXISTS idx_pending_series_ops_one_per_series
ON pending_calendar_series_ops (series_uid)
WHERE status = 'pending'
"""

CREATE_PENDING_SERIES_OP_DUE_INDEX = """
CREATE INDEX IF NOT EXISTS idx_pending_series_ops_due
ON pending_calendar_series_ops (status, next_try_at, id)
"""


# Explicit per-occurrence Calendar state for Planner-owned linked masters
# (Phase 3.2B3B, schema v10).  No cascading foreign keys: Task tombstones and
# occurrence history survive detach/relink.
CREATE_TASK_SERIES_OCCURRENCE_CALENDAR_LINKS_TABLE = """
CREATE TABLE IF NOT EXISTS task_series_occurrence_calendar_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    series_uid TEXT NOT NULL,
    occurrence_key TEXT NOT NULL,
    series_link_id INTEGER NOT NULL,
    link_generation INTEGER NOT NULL,
    remote_master_event_id TEXT NOT NULL,
    remote_instance_event_id TEXT,
    original_start_kind TEXT NOT NULL
        CHECK (original_start_kind IN ('date','datetime')),
    original_start_value TEXT NOT NULL,
    original_start_timezone TEXT,
    remote_etag TEXT,
    remote_updated_at TEXT,
    sync_status TEXT NOT NULL DEFAULT 'local_only' CHECK (sync_status IN (
        'local_only','pending_update','synced_exception','pending_cancel',
        'cancelled','conflict','remote_changed','remote_cancelled',
        'terminal_error','detached'
    )),
    last_synced_local_hash TEXT,
    last_synced_remote_hash TEXT,
    is_cancelled_remote INTEGER NOT NULL DEFAULT 0,
    conflict_reason TEXT,
    conflict_snapshot_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    detached_at TEXT
)
"""

CREATE_ACTIVE_OCCURRENCE_LINK_INDEX = """
CREATE UNIQUE INDEX IF NOT EXISTS idx_series_occurrence_links_active
ON task_series_occurrence_calendar_links (
    series_uid, occurrence_key, link_generation
)
WHERE detached_at IS NULL
"""

CREATE_OCCURRENCE_REMOTE_INSTANCE_INDEX = """
CREATE INDEX IF NOT EXISTS idx_series_occurrence_links_remote_instance
ON task_series_occurrence_calendar_links (
    remote_master_event_id, remote_instance_event_id
)
"""

CREATE_OCCURRENCE_LINK_STATUS_INDEX = """
CREATE INDEX IF NOT EXISTS idx_series_occurrence_links_status
ON task_series_occurrence_calendar_links (sync_status, updated_at)
"""


# Dedicated per-instance queue.  It never shares rows with ordinary Task
# events or recurring-master operations.
CREATE_PENDING_CALENDAR_SERIES_INSTANCE_OPS_TABLE = """
CREATE TABLE IF NOT EXISTS pending_calendar_series_instance_ops (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    series_uid TEXT NOT NULL,
    occurrence_key TEXT NOT NULL,
    series_link_id INTEGER NOT NULL,
    op TEXT NOT NULL CHECK (op IN ('update','cancel')),
    remote_master_event_id TEXT NOT NULL,
    remote_instance_event_id TEXT,
    original_start_value TEXT NOT NULL,
    acknowledged_remote_etag TEXT,
    desired_payload_hash TEXT,
    payload_json TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending','terminal')),
    created_at TEXT NOT NULL,
    next_try_at TEXT NOT NULL
)
"""

CREATE_PENDING_INSTANCE_OP_UNIQUE_INDEX = """
CREATE UNIQUE INDEX IF NOT EXISTS idx_pending_instance_ops_one_per_occurrence
ON pending_calendar_series_instance_ops (
    series_uid, occurrence_key, series_link_id
)
WHERE status = 'pending'
"""

CREATE_PENDING_INSTANCE_OP_DUE_INDEX = """
CREATE INDEX IF NOT EXISTS idx_pending_instance_ops_due
ON pending_calendar_series_instance_ops (status, next_try_at, id)
"""


# Changed/cancelled Google instances of a linked local master are quarantined
# rather than being imported as ordinary Tasks.  Schema v10 adds exact local
# matching and an explicit resolution lifecycle.
CREATE_EXTERNAL_SERIES_OCCURRENCE_CHANGES_TABLE = """
CREATE TABLE IF NOT EXISTS external_series_occurrence_changes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL,
    calendar_id TEXT NOT NULL,
    remote_master_event_id TEXT NOT NULL,
    remote_instance_event_id TEXT NOT NULL,
    original_start_value TEXT NOT NULL,
    status TEXT NOT NULL,
    payload_json TEXT,
    remote_etag TEXT,
    remote_updated_at TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    resolved_at TEXT,
    matched_series_uid TEXT,
    matched_occurrence_key TEXT,
    resolution_status TEXT NOT NULL DEFAULT 'unresolved',
    resolution_kind TEXT,
    resolution_error TEXT,
    UNIQUE (
        provider, calendar_id, remote_master_event_id,
        remote_instance_event_id, original_start_value
    )
)
"""

CREATE_EXTERNAL_OCCURRENCE_STATUS_INDEX = """
CREATE INDEX IF NOT EXISTS idx_external_occurrence_changes_status
ON external_series_occurrence_changes (resolved_at, last_seen_at)
"""


# Durable audit history of explicit conflict/remote-deleted resolutions
# (Phase 3.2B3A, schema v9).  Deliberately no foreign keys: the history must
# survive series tombstones and link detachment and never cascade-delete
# TaskSeries or materialized Task rows.
CREATE_SERIES_CONFLICT_RESOLUTIONS_TABLE = """
CREATE TABLE IF NOT EXISTS series_conflict_resolutions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    series_uid TEXT NOT NULL,
    link_id INTEGER NOT NULL,
    resolution_kind TEXT NOT NULL CHECK (resolution_kind IN (
        'keep_planner','use_google','disconnect',
        'keep_local','recreate','delete_local'
    )),
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending','completed','failed','superseded')),
    local_revision_before INTEGER NOT NULL,
    local_revision_after INTEGER,
    remote_etag_before TEXT,
    remote_etag_after TEXT,
    remote_payload_hash TEXT,
    acknowledged_remote_etag TEXT,
    created_at TEXT NOT NULL,
    completed_at TEXT,
    error TEXT
)
"""

CREATE_SERIES_CONFLICT_RESOLUTIONS_SERIES_INDEX = """
CREATE INDEX IF NOT EXISTS idx_series_conflict_resolutions_series
ON series_conflict_resolutions (series_uid, id)
"""

CREATE_SERIES_CONFLICT_RESOLUTIONS_STATUS_INDEX = """
CREATE INDEX IF NOT EXISTS idx_series_conflict_resolutions_status
ON series_conflict_resolutions (status, id)
"""


# Durable remote "this and future" split plans (Phase 3.2B3C1, schema v11).
# One row is the complete recoverable state machine of one two-master split:
# canonical source/trimmed/successor snapshots, acknowledged ETags and the
# current state.  Completed/rolled-back plans remain queryable history; no
# foreign keys cascade into Task or TaskSeries history.
CREATE_CALENDAR_SERIES_REMOTE_SPLITS_TABLE = """
CREATE TABLE IF NOT EXISTS calendar_series_remote_splits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_series_uid TEXT NOT NULL,
    source_link_id INTEGER NOT NULL,
    source_link_generation INTEGER NOT NULL DEFAULT 0,
    source_remote_event_id TEXT NOT NULL,
    target_occurrence_key TEXT NOT NULL,
    target_original_start_kind TEXT NOT NULL
        CHECK (target_original_start_kind IN ('date','datetime')),
    target_original_start_value TEXT NOT NULL,
    target_original_start_timezone TEXT,
    source_local_revision INTEGER NOT NULL,
    source_remote_etag_base TEXT NOT NULL,
    source_original_snapshot_json TEXT NOT NULL,
    source_original_payload_hash TEXT NOT NULL,
    source_trimmed_payload_json TEXT NOT NULL,
    source_trimmed_payload_hash TEXT NOT NULL,
    reserved_successor_series_uid TEXT NOT NULL,
    successor_remote_event_id TEXT NOT NULL,
    successor_series_snapshot_json TEXT NOT NULL,
    successor_payload_json TEXT NOT NULL,
    successor_payload_hash TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'pending' CHECK (state IN (
        'pending','source_trimmed','successor_created',
        'local_finalize_pending','completed','conflict','rollback_pending',
        'successor_removed_for_rollback','rolled_back','terminal_error'
    )),
    source_trimmed_remote_etag TEXT,
    successor_remote_etag TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT
)
"""

# Exactly one live plan may own a source series at a time; completed,
# rolled-back and dead-lettered plans do not block a new plan.
CREATE_ACTIVE_REMOTE_SPLIT_INDEX = """
CREATE UNIQUE INDEX IF NOT EXISTS idx_remote_splits_active_source
ON calendar_series_remote_splits (source_series_uid)
WHERE state IN (
    'pending','source_trimmed','successor_created','local_finalize_pending',
    'conflict','rollback_pending','successor_removed_for_rollback'
)
"""

CREATE_REMOTE_SPLIT_SUCCESSOR_UID_INDEX = """
CREATE UNIQUE INDEX IF NOT EXISTS idx_remote_splits_successor_uid
ON calendar_series_remote_splits (reserved_successor_series_uid)
"""

CREATE_REMOTE_SPLIT_SUCCESSOR_REMOTE_INDEX = """
CREATE UNIQUE INDEX IF NOT EXISTS idx_remote_splits_successor_remote
ON calendar_series_remote_splits (successor_remote_event_id)
"""

CREATE_REMOTE_SPLIT_STATE_INDEX = """
CREATE INDEX IF NOT EXISTS idx_remote_splits_state
ON calendar_series_remote_splits (state, updated_at)
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


def _migrate_series_conflict_columns(connection: sqlite3.Connection) -> None:
    """Additive v8 -> v9 columns; existing links backfill link_generation = 0.

    Idempotent: each column is added only when missing; no row data is
    rewritten destructively and v8 links, queues, catalog rows and quarantine
    rows survive untouched.
    """
    links = _column_names(connection, "task_series_calendar_links")
    if links:
        additions = (
            ("link_generation", "INTEGER NOT NULL DEFAULT 0"),
            ("conflict_detected_at", "TEXT"),
            ("conflict_reason", "TEXT"),
            ("conflict_remote_etag", "TEXT"),
            ("conflict_remote_payload_hash", "TEXT"),
            ("conflict_remote_snapshot_json", "TEXT"),
            ("resolved_at", "TEXT"),
            ("resolution_kind", "TEXT"),
        )
        for name, declaration in additions:
            if name not in links:
                connection.execute(
                    "ALTER TABLE task_series_calendar_links "
                    f"ADD COLUMN {name} {declaration}"
                )
    ops = _column_names(connection, "pending_calendar_series_ops")
    if ops:
        if "resolution_id" not in ops:
            connection.execute(
                "ALTER TABLE pending_calendar_series_ops "
                "ADD COLUMN resolution_id INTEGER"
            )
        if "acknowledged_remote_etag" not in ops:
            connection.execute(
                "ALTER TABLE pending_calendar_series_ops "
                "ADD COLUMN acknowledged_remote_etag TEXT"
            )


def _migrate_occurrence_quarantine_columns(
    connection: sqlite3.Connection,
) -> None:
    """Additive v9 -> v10 quarantine lifecycle columns."""
    existing = _column_names(connection, "external_series_occurrence_changes")
    if not existing:
        return
    additions = (
        ("matched_series_uid", "TEXT"),
        ("matched_occurrence_key", "TEXT"),
        ("resolution_status", "TEXT NOT NULL DEFAULT 'unresolved'"),
        ("resolution_kind", "TEXT"),
        ("resolution_error", "TEXT"),
    )
    for name, declaration in additions:
        if name not in existing:
            connection.execute(
                "ALTER TABLE external_series_occurrence_changes "
                f"ADD COLUMN {name} {declaration}"
            )


def _migrate_external_series_link_columns(connection: sqlite3.Connection) -> None:
    """Add B2 ownership diagnostics without rewriting B1 catalog rows."""
    existing = _column_names(connection, "external_calendar_series")
    if "planner_owned" not in existing:
        connection.execute(
            "ALTER TABLE external_calendar_series ADD COLUMN planner_owned "
            "INTEGER NOT NULL DEFAULT 0"
        )
    if "linked_series_uid" not in existing:
        connection.execute(
            "ALTER TABLE external_calendar_series ADD COLUMN linked_series_uid TEXT"
        )
    if "planner_payload_hash" not in existing:
        connection.execute(
            "ALTER TABLE external_calendar_series ADD COLUMN planner_payload_hash TEXT"
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
    connection.execute(CREATE_EXTERNAL_CALENDAR_SERIES_TABLE)
    _migrate_external_series_link_columns(connection)
    connection.execute(CREATE_EXTERNAL_SERIES_REMOTE_INDEX)
    connection.execute(CREATE_EXTERNAL_SERIES_STATUS_INDEX)
    connection.execute(CREATE_EXTERNAL_SERIES_LAST_SEEN_INDEX)
    connection.execute(CREATE_TASK_SERIES_CALENDAR_LINKS_TABLE)
    connection.execute(CREATE_ACTIVE_SERIES_LINK_INDEX)
    connection.execute(CREATE_ACTIVE_REMOTE_LINK_INDEX)
    connection.execute(CREATE_SERIES_LINK_STATUS_INDEX)
    connection.execute(CREATE_PENDING_CALENDAR_SERIES_OPS_TABLE)
    connection.execute(CREATE_PENDING_SERIES_OP_UNIQUE_INDEX)
    connection.execute(CREATE_PENDING_SERIES_OP_DUE_INDEX)
    connection.execute(CREATE_TASK_SERIES_OCCURRENCE_CALENDAR_LINKS_TABLE)
    connection.execute(CREATE_ACTIVE_OCCURRENCE_LINK_INDEX)
    connection.execute(CREATE_OCCURRENCE_REMOTE_INSTANCE_INDEX)
    connection.execute(CREATE_OCCURRENCE_LINK_STATUS_INDEX)
    connection.execute(CREATE_PENDING_CALENDAR_SERIES_INSTANCE_OPS_TABLE)
    connection.execute(CREATE_PENDING_INSTANCE_OP_UNIQUE_INDEX)
    connection.execute(CREATE_PENDING_INSTANCE_OP_DUE_INDEX)
    connection.execute(CREATE_EXTERNAL_SERIES_OCCURRENCE_CHANGES_TABLE)
    _migrate_occurrence_quarantine_columns(connection)
    connection.execute(CREATE_EXTERNAL_OCCURRENCE_STATUS_INDEX)
    _migrate_series_conflict_columns(connection)
    connection.execute(CREATE_SERIES_CONFLICT_RESOLUTIONS_TABLE)
    connection.execute(CREATE_SERIES_CONFLICT_RESOLUTIONS_SERIES_INDEX)
    connection.execute(CREATE_SERIES_CONFLICT_RESOLUTIONS_STATUS_INDEX)
    connection.execute(CREATE_CALENDAR_SERIES_REMOTE_SPLITS_TABLE)
    connection.execute(CREATE_ACTIVE_REMOTE_SPLIT_INDEX)
    connection.execute(CREATE_REMOTE_SPLIT_SUCCESSOR_UID_INDEX)
    connection.execute(CREATE_REMOTE_SPLIT_SUCCESSOR_REMOTE_INDEX)
    connection.execute(CREATE_REMOTE_SPLIT_STATE_INDEX)
    connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    connection.commit()
