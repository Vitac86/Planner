import sqlite3

from planner_desktop.storage.schema import SCHEMA_VERSION, create_schema


def test_v10_is_additive_idempotent_and_reopens(tmp_path):
    db = tmp_path / "desktop.db"
    connection = sqlite3.connect(db)
    create_schema(connection)
    connection.execute(
        "INSERT INTO task_series_calendar_links "
        "(series_uid, provider, calendar_id, remote_event_id, link_status, "
        "linked_at, updated_at, link_generation) "
        "VALUES ('s1','google','primary','master-1','synced','x','x',2)"
    )
    link_id = connection.execute(
        "SELECT id FROM task_series_calendar_links WHERE series_uid='s1'"
    ).fetchone()[0]
    connection.execute(
        "INSERT INTO external_series_occurrence_changes "
        "(provider,calendar_id,remote_master_event_id,"
        "remote_instance_event_id,original_start_value,status,"
        "first_seen_at,last_seen_at) "
        "VALUES ('google','primary','master-1','instance-1',"
        "'2026-07-20T09:00:00+03:00','confirmed','x','x')"
    )
    connection.commit()
    create_schema(connection)
    create_schema(connection)
    assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    assert SCHEMA_VERSION == 10
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    assert "task_series_occurrence_calendar_links" in tables
    assert "pending_calendar_series_instance_ops" in tables
    old_change = connection.execute(
        "SELECT remote_instance_event_id,resolution_status "
        "FROM external_series_occurrence_changes"
    ).fetchone()
    assert old_change == ("instance-1", "unresolved")
    assert connection.execute(
        "SELECT id FROM task_series_calendar_links"
    ).fetchone()[0] == link_id
    connection.close()

    reopened = sqlite3.connect(db)
    create_schema(reopened)
    assert reopened.execute(
        "SELECT COUNT(*) FROM external_series_occurrence_changes"
    ).fetchone()[0] == 1
    reopened.close()
