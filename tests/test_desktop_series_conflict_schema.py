import sqlite3

from planner_desktop.domain.series_calendar_link import SeriesLinkStatus
from planner_desktop.domain.series_conflict_resolution import (
    SeriesConflictResolution,
)
from planner_desktop.storage.calendar_series_sync_store import (
    CalendarSeriesSyncStore,
)
from planner_desktop.storage.schema import SCHEMA_VERSION, create_schema

V8_LINKS_TABLE = """
CREATE TABLE task_series_calendar_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    series_uid TEXT NOT NULL,
    provider TEXT NOT NULL,
    calendar_id TEXT NOT NULL,
    remote_event_id TEXT NOT NULL,
    remote_etag TEXT,
    remote_updated_at TEXT,
    link_status TEXT NOT NULL,
    last_synced_series_revision INTEGER,
    last_synced_payload_hash TEXT,
    linked_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    detached_at TEXT,
    last_error TEXT
)
"""

V8_OPS_TABLE = """
CREATE TABLE pending_calendar_series_ops (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    series_uid TEXT NOT NULL,
    op TEXT NOT NULL CHECK (op IN ('create','update','delete')),
    remote_event_id TEXT,
    desired_revision INTEGER,
    desired_payload_hash TEXT,
    payload_json TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    next_try_at TEXT NOT NULL
)
"""


def _columns(con, table):
    return {row[1] for row in con.execute(f"PRAGMA table_info({table})")}


def test_v9_migrates_v8_links_and_queues_additively(tmp_path):
    db = tmp_path / "desktop.db"
    con = sqlite3.connect(db)
    # Exact v8 shape with live rows: they must survive the v9 migration.
    con.execute(V8_LINKS_TABLE)
    con.execute(V8_OPS_TABLE)
    con.execute(
        "INSERT INTO task_series_calendar_links (series_uid, provider, "
        "calendar_id, remote_event_id, link_status, linked_at, updated_at) "
        "VALUES ('s1','google','primary','plrabc','synced','x','x')"
    )
    con.execute(
        "INSERT INTO pending_calendar_series_ops (series_uid, op, status, "
        "created_at, next_try_at) VALUES ('s1','update','pending','x','x')"
    )
    con.execute("PRAGMA user_version = 8")
    con.commit()

    create_schema(con)
    create_schema(con)  # idempotent

    assert con.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION == 10
    links = _columns(con, "task_series_calendar_links")
    assert {
        "link_generation", "conflict_detected_at", "conflict_reason",
        "conflict_remote_etag", "conflict_remote_payload_hash",
        "conflict_remote_snapshot_json", "resolved_at", "resolution_kind",
    } <= links
    ops = _columns(con, "pending_calendar_series_ops")
    assert {"resolution_id", "acknowledged_remote_etag"} <= ops
    # v8 rows survive; existing links backfill generation 0.
    row = con.execute(
        "SELECT link_status, link_generation FROM task_series_calendar_links "
        "WHERE series_uid = 's1'"
    ).fetchone()
    assert row == ("synced", 0)
    assert con.execute(
        "SELECT op, resolution_id FROM pending_calendar_series_ops"
    ).fetchone() == ("update", None)
    tables = {
        item[0] for item in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    assert "series_conflict_resolutions" in tables
    con.close()


def test_resolution_history_persists_across_reopen(tmp_path):
    db = tmp_path / "desktop.db"
    store = CalendarSeriesSyncStore(db)
    resolution = store.add_resolution(SeriesConflictResolution(
        series_uid="s1",
        link_id=7,
        resolution_kind="keep_planner",
        local_revision_before=3,
        remote_etag_before='"5"',
        acknowledged_remote_etag='"5"',
    ))
    store.complete_resolution(
        resolution.id, local_revision_after=3, remote_etag_after='"6"'
    )
    store.close()

    reopened = CalendarSeriesSyncStore(db)
    rows = reopened.list_resolutions("s1")
    assert len(rows) == 1
    stored = rows[0]
    assert stored.resolution_kind == "keep_planner"
    assert stored.status == "completed"
    assert stored.local_revision_before == 3
    assert stored.local_revision_after == 3
    assert stored.remote_etag_before == '"5"'
    assert stored.remote_etag_after == '"6"'
    assert stored.acknowledged_remote_etag == '"5"'
    assert stored.completed_at is not None
    reopened.close()


def test_no_cascade_from_history_to_series_or_tasks(tmp_path):
    db = tmp_path / "desktop.db"
    con = sqlite3.connect(db)
    con.execute("PRAGMA foreign_keys = ON")
    create_schema(con)
    con.execute(
        "INSERT INTO task_series (uid,title,start_date,frequency,created_at,"
        "updated_at) VALUES ('s1','Local','2026-07-15','daily','x','x')"
    )
    con.execute(
        "INSERT INTO tasks (uid,title,updated_at,series_uid,occurrence_key,"
        "completed) VALUES ('t1','Done','x','s1','2026-07-15',1)"
    )
    con.execute(
        "INSERT INTO series_conflict_resolutions (series_uid,link_id,"
        "resolution_kind,status,local_revision_before,created_at) "
        "VALUES ('s1',1,'disconnect','completed',1,'x')"
    )
    con.commit()
    con.execute("DELETE FROM series_conflict_resolutions")
    con.execute("DELETE FROM task_series_calendar_links")
    con.commit()
    assert con.execute("SELECT COUNT(*) FROM task_series").fetchone()[0] == 1
    assert con.execute(
        "SELECT COUNT(*) FROM tasks WHERE completed = 1"
    ).fetchone()[0] == 1
    con.close()


def test_detached_and_remote_deleted_generations_remain_queryable(tmp_path):
    store = CalendarSeriesSyncStore(tmp_path / "desktop.db")
    con = sqlite3.connect(store.db_path)
    con.execute(
        "INSERT INTO task_series_calendar_links (series_uid, provider, "
        "calendar_id, remote_event_id, link_status, linked_at, updated_at, "
        "link_generation, resolution_kind) VALUES "
        "('s1','google','primary','plr0','detached',"
        "'2026-07-15T08:00:00+00:00','2026-07-15T08:00:00+00:00',0,'recreate')"
    )
    con.execute(
        "INSERT INTO task_series_calendar_links (series_uid, provider, "
        "calendar_id, remote_event_id, link_status, linked_at, updated_at, "
        "link_generation) VALUES "
        "('s1','google','primary','plr1','remote_deleted',"
        "'2026-07-15T09:00:00+00:00','2026-07-15T09:00:00+00:00',1)"
    )
    con.commit()
    con.close()
    links = store.list_links(include_detached=True)
    assert [(l.link_generation, l.link_status) for l in links] == [
        (0, SeriesLinkStatus.DETACHED),
        (1, SeriesLinkStatus.REMOTE_DELETED),
    ]
    assert store.max_link_generation("s1") == 1
    store.close()
