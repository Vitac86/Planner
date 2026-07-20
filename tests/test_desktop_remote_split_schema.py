"""Schema v11: additive, idempotent, v10 data survives, reopen persistence."""
from __future__ import annotations

import sqlite3

from planner_desktop.domain.google_series_split import (
    RemoteSeriesSplitPlanRecord,
    RemoteSeriesSplitStatus,
)
from planner_desktop.storage.calendar_series_remote_split_store import (
    CalendarSeriesRemoteSplitStore,
)
from planner_desktop.storage.schema import SCHEMA_VERSION, create_schema


def _record(uid: str = "src-1", successor: str = "succ-1", remote: str = "plrsucc"):
    return RemoteSeriesSplitPlanRecord(
        source_series_uid=uid,
        source_link_id=1,
        source_link_generation=0,
        source_remote_event_id="plrsrc",
        target_occurrence_key="2026-08-05",
        target_original_start_kind="date",
        target_original_start_value="2026-08-05",
        source_local_revision=1,
        source_remote_etag_base='"3"',
        source_original_snapshot_json="{}",
        source_original_payload_hash="hash-src",
        source_trimmed_payload_json="{}",
        source_trimmed_payload_hash="hash-trim",
        reserved_successor_series_uid=successor,
        successor_remote_event_id=remote,
        successor_series_snapshot_json="{}",
        successor_payload_json="{}",
        successor_payload_hash="hash-succ",
    )


def test_schema_v11_is_additive_and_idempotent(tmp_path):
    db = tmp_path / "desktop.db"
    connection = sqlite3.connect(db)
    connection.execute("PRAGMA user_version = 10")
    create_schema(connection)
    create_schema(connection)  # idempotent
    assert connection.execute("PRAGMA user_version").fetchone()[0] == (
        SCHEMA_VERSION
    ) == 11
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    assert "calendar_series_remote_splits" in tables
    indexes = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index'"
        )
    }
    assert "idx_remote_splits_active_source" in indexes
    assert "idx_remote_splits_successor_uid" in indexes
    assert "idx_remote_splits_successor_remote" in indexes
    connection.close()


def test_v10_data_survives_migration(tmp_path):
    db = tmp_path / "desktop.db"
    connection = sqlite3.connect(db)
    create_schema(connection)
    connection.execute(
        "INSERT INTO tasks (uid, title, updated_at) VALUES ('t1', 'Keep', 'x')"
    )
    connection.execute(
        "INSERT INTO task_series (uid, title, start_date, frequency, "
        "created_at, updated_at) VALUES ('s1', 'Series', '2026-08-01', "
        "'daily', 'x', 'x')"
    )
    connection.commit()
    create_schema(connection)  # re-run must not rewrite existing rows
    assert connection.execute(
        "SELECT title FROM tasks WHERE uid = 't1'"
    ).fetchone()[0] == "Keep"
    assert connection.execute(
        "SELECT title FROM task_series WHERE uid = 's1'"
    ).fetchone()[0] == "Series"
    connection.close()


def test_one_active_plan_per_source_series(tmp_path):
    store = CalendarSeriesRemoteSplitStore(tmp_path / "desktop.db")
    first = store.create_plan(_record())
    duplicate = store.create_plan(_record(successor="succ-2", remote="plrsucc2"))
    assert duplicate.id == first.id  # rapid duplicate returns the same plan
    assert duplicate.reserved_successor_series_uid == "succ-1"
    # A completed plan no longer blocks a new one.
    store.mark_source_trimmed(first.id, remote_etag='"4"')
    store.mark_successor_created(first.id, remote_etag='"1"')
    store._transition(first.id, RemoteSeriesSplitStatus.COMPLETED, completed=True)
    second = store.create_plan(_record(successor="succ-3", remote="plrsucc3"))
    assert second.id != first.id
    store.close()


def test_reserved_uid_and_remote_id_are_unique(tmp_path):
    store = CalendarSeriesRemoteSplitStore(tmp_path / "desktop.db")
    first = store.create_plan(_record())
    store._transition(
        first.id, RemoteSeriesSplitStatus.ROLLED_BACK, completed=True
    )
    try:
        store.create_plan(_record(successor="succ-1", remote="plrother"))
        raised = False
    except sqlite3.IntegrityError:
        raised = True
    assert raised  # one reserved successor UID per plan, ever
    try:
        store.create_plan(_record(successor="succ-9", remote="plrsucc"))
        raised = False
    except sqlite3.IntegrityError:
        raised = True
    assert raised  # one successor remote id per plan, ever
    store.close()


def test_reopen_persistence(tmp_path):
    db = tmp_path / "desktop.db"
    store = CalendarSeriesRemoteSplitStore(db)
    created = store.create_plan(_record())
    store.mark_source_trimmed(created.id, remote_etag='"9"')
    store.close()

    reopened = CalendarSeriesRemoteSplitStore(db)
    plan = reopened.get_plan(created.id)
    assert plan is not None
    assert plan.state is RemoteSeriesSplitStatus.SOURCE_TRIMMED
    assert plan.source_trimmed_remote_etag == '"9"'
    assert plan.source_remote_etag_base == '"3"'
    assert reopened.get_active_plan("src-1") is not None
    reopened.close()
