import sqlite3

import pytest

from planner_desktop.domain.series_calendar_link import (
    SeriesCalendarLink,
    SeriesLinkStatus,
)
from planner_desktop.storage.calendar_series_sync_store import CalendarSeriesSyncStore


def test_link_and_queue_reopen_and_active_constraints(tmp_path):
    db = tmp_path / "desktop.db"
    store = CalendarSeriesSyncStore(db)
    link = SeriesCalendarLink(series_uid="s1", remote_event_id="plr00000")
    stored = store.create_pending_link(
        link, desired_revision=1, desired_payload_hash="h1", payload={"summary": "A"}
    )
    assert stored.link_status is SeriesLinkStatus.PENDING_CREATE
    assert store.get_pending_op("s1").op.value == "create"
    store.close()

    reopened = CalendarSeriesSyncStore(db)
    assert reopened.get_link("s1").remote_event_id == "plr00000"
    assert reopened.get_pending_op("s1").desired_payload_hash == "h1"
    with pytest.raises(sqlite3.IntegrityError):
        reopened._connection.execute(
            "INSERT INTO task_series_calendar_links "
            "(series_uid,provider,calendar_id,remote_event_id,link_status,linked_at,updated_at) "
            "VALUES ('s1','google','other','plr11111','synced','x','x')"
        )
    reopened._connection.rollback()
    with pytest.raises(sqlite3.IntegrityError):
        reopened._connection.execute(
            "INSERT INTO task_series_calendar_links "
            "(series_uid,provider,calendar_id,remote_event_id,link_status,linked_at,updated_at) "
            "VALUES ('s2','google','primary','plr00000','synced','x','x')"
        )
    reopened._connection.rollback()
    reopened.set_link_status("s1", SeriesLinkStatus.DETACHED)
    assert reopened.get_link("s1") is None
    assert reopened.get_link("s1", include_detached=True).detached_at is not None
    reopened.close()
