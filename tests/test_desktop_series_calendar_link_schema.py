import sqlite3

from planner_desktop.storage.schema import SCHEMA_VERSION, create_schema


def test_v8_is_additive_idempotent_and_preserves_v7_rows(tmp_path):
    db = tmp_path / "desktop.db"
    con = sqlite3.connect(db)
    create_schema(con)
    con.execute(
        "INSERT INTO task_series (uid,title,start_date,frequency,created_at,updated_at) "
        "VALUES ('s1','Local','2026-07-15','daily','x','x')"
    )
    con.execute(
        """INSERT INTO external_calendar_series (
        provider,calendar_id,remote_event_id,start_kind,support_status,
        first_seen_at,last_seen_at) VALUES ('google','primary','m1','all_day',
        'supported','x','x')"""
    )
    con.commit()
    # Simulate the exact v7 boundary while retaining v7 families.
    for table in (
        "task_series_calendar_links",
        "pending_calendar_series_ops",
        "external_series_occurrence_changes",
    ):
        con.execute(f"DROP TABLE {table}")
    con.execute("PRAGMA user_version = 7")
    con.commit()

    create_schema(con)
    create_schema(con)

    # Phase 3.2B3A moved the version forward additively; the v8 families and
    # rows created above must still survive unchanged.
    assert con.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION == 10
    assert con.execute("SELECT title FROM task_series WHERE uid='s1'").fetchone()[0] == "Local"
    row = con.execute(
        "SELECT remote_event_id, planner_owned, linked_series_uid "
        "FROM external_calendar_series WHERE remote_event_id='m1'"
    ).fetchone()
    assert row == ("m1", 0, None)
    names = {
        item[0]
        for item in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert {
        "task_series_calendar_links",
        "pending_calendar_series_ops",
        "external_series_occurrence_changes",
    } <= names
    con.close()
