import sqlite3

import pytest

from planner_desktop.domain.series_adoption import (
    CalendarSeriesAdoption,
)
from planner_desktop.storage.calendar_series_adoption_store import (
    CalendarSeriesAdoptionStore,
)
from planner_desktop.storage.schema import SCHEMA_VERSION, create_schema


def _columns(connection, table):
    return {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}


def test_schema_v12_is_additive_idempotent_and_backfills_links(tmp_path):
    db = tmp_path / "desktop.db"
    con = sqlite3.connect(db)
    create_schema(con)
    con.execute(
        "INSERT INTO task_series_calendar_links "
        "(series_uid,provider,calendar_id,remote_event_id,link_status,"
        "linked_at,updated_at) VALUES "
        "('s','google','primary','m','synced','x','x')"
    )
    con.execute("PRAGMA user_version = 11")
    con.commit()
    create_schema(con)
    create_schema(con)
    assert con.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION == 12
    assert "raw_snapshot_json" in _columns(con, "external_calendar_series")
    assert {
        "link_origin",
        "remote_id_strategy",
        "adoption_id",
        "ownership_claimed_at",
    } <= _columns(con, "task_series_calendar_links")
    row = con.execute(
        "SELECT link_origin,remote_id_strategy FROM "
        "task_series_calendar_links WHERE series_uid='s'"
    ).fetchone()
    assert row == ("planner_created", "planner_deterministic")
    assert "calendar_series_adoptions" in {
        row[0]
        for row in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    con.close()


def test_one_active_adoption_and_reopen_persistence(tmp_path):
    db = tmp_path / "desktop.db"
    store = CalendarSeriesAdoptionStore(db)
    first = store.create_plan(
        CalendarSeriesAdoption(
            provider="google",
            calendar_id="primary",
            remote_event_id="m",
            reserved_series_uid="s1",
            remote_etag_base='"1"',
            remote_snapshot_json="{}",
            canonical_remote_payload_hash="h",
            preexisting_private_properties_json="{}",
        )
    )
    duplicate = store.create_plan(
        CalendarSeriesAdoption(
            provider="google",
            calendar_id="primary",
            remote_event_id="m",
            reserved_series_uid="s2",
            remote_etag_base='"1"',
            remote_snapshot_json="{}",
            canonical_remote_payload_hash="h",
            preexisting_private_properties_json="{}",
        )
    )
    assert duplicate.id == first.id
    assert duplicate.reserved_series_uid == "s1"
    store.close()
    reopened = CalendarSeriesAdoptionStore(db)
    assert reopened.get_plan(first.id).reserved_series_uid == "s1"
    reopened.close()


def test_v11_rows_survive_v12_migration(tmp_path):
    db = tmp_path / "desktop.db"
    con = sqlite3.connect(db)
    create_schema(con)
    con.execute(
        "INSERT INTO task_series "
        "(uid,title,start_date,frequency,created_at,updated_at) "
        "VALUES ('survivor','Series','2026-01-01','daily','x','x')"
    )
    con.execute("PRAGMA user_version = 11")
    con.commit()
    create_schema(con)
    assert con.execute(
        "SELECT title FROM task_series WHERE uid='survivor'"
    ).fetchone()[0] == "Series"
    con.close()
