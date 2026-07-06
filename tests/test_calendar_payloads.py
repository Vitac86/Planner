"""All-day vs timed Google Calendar payloads.

Root cause of the historic dead-letter pile-up: ``build_event_payload``
always emitted timed ``dateTime`` start/end, which Google rejects for
all-day events (recurring all-day instances such as
``ivc7pjg531mq4i9328p9hqdhi4_YYYYMMDD`` fail with HTTP 400
"Invalid start time."). The fix remembers the event shape in
``Task.gcal_all_day`` and keeps it on push:

* timed tasks emit ``{"dateTime": ..., "timeZone": ...}``;
* all-day tasks emit ``{"date": ...}`` with the Google-conventional
  exclusive end date (single day => start + 1 day);
* the calendar pull sets ``gcal_all_day`` from the event shape and the
  flag survives create/update/unlink;
* the migration adds the column idempotently to legacy databases.

Updating an *existing* all-day event is remote-aware (events.patch merges
start/end per field, and moving an all-day recurring instance is rejected
with HTTP 400 "Invalid start time."): the push fetches the event once and
omits start/end when the dates are unchanged, moves plain all-day events
with explicit dateTime nulls, and refuses (dead-letter with a readable
reason, nothing sent) recurring-instance moves and shape mismatches.

Existing dead-letter rows are never replayed automatically; after this
fix is verified manually they may be inspected and requeued by hand in a
later task.

Everything runs against fakes and in-memory SQLite; no real Google APIs.
"""
import logging
from datetime import datetime, timezone

from sqlalchemy import text
from sqlmodel import create_engine

from models.task import Task
from services.google_sync import build_event_payload
from services.sync_service import SyncService
from services.sync_token_storage import SyncTokenStorage
from storage import migrations

from test_sync_engine_wiring import (
    FakeGoogleCalendar,
    FakeGoogleTasks,
    FakeQueue,
    FakeRepo,
)

UTC = timezone.utc
RECURRING_INSTANCE_ID = "ivc7pjg531mq4i9328p9hqdhi4_20260706"


def _make_service(tmp_path, *, gcal=None, repo=None, queue=None) -> SyncService:
    return SyncService(
        gcal or FakeGoogleCalendar(),
        FakeGoogleTasks(),
        repo or FakeRepo(),
        SyncTokenStorage(tmp_path / "tokens.json"),
        queue or FakeQueue(),
    )


def _timed_task(task_id=1, **extra) -> Task:
    return Task(
        id=task_id,
        title="Timed",
        start=datetime(2026, 7, 6, 9, 30, tzinfo=UTC),
        duration_minutes=45,
        **extra,
    )


def _all_day_task(task_id=2, days=1, **extra) -> Task:
    return Task(
        id=task_id,
        title="All day",
        start=datetime(2026, 7, 6, tzinfo=UTC),
        duration_minutes=days * 24 * 60,
        gcal_all_day=True,
        **extra,
    )


def _calendar_event(**overrides) -> dict:
    event = {
        "id": "ev-remote",
        "status": "confirmed",
        "summary": "Remote",
        "etag": "etag-r1",
        "updated": "2026-07-01T00:00:00Z",
        "start": {"dateTime": "2026-07-06T09:00:00Z"},
        "end": {"dateTime": "2026-07-06T10:00:00Z"},
    }
    event.update(overrides)
    return event


def _gcal_with_events(*events) -> FakeGoogleCalendar:
    return FakeGoogleCalendar(
        list_payloads=[{"items": list(events), "nextSyncToken": "tok-1"}]
    )


# ---------------------------------------------------------------------------
# build_event_payload: timed vs all-day
# ---------------------------------------------------------------------------

def test_timed_task_payload_emits_datetime():
    body = build_event_payload(_timed_task())

    assert body["start"] == {"dateTime": "2026-07-06T09:30:00Z", "timeZone": "UTC"}
    assert body["end"] == {"dateTime": "2026-07-06T10:15:00Z", "timeZone": "UTC"}
    assert "date" not in body["start"]
    assert "date" not in body["end"]


def test_all_day_task_payload_emits_exclusive_date_range():
    body = build_event_payload(_all_day_task())

    assert body["start"] == {"date": "2026-07-06"}
    assert body["end"] == {"date": "2026-07-07"}, "end.date is exclusive"
    assert "dateTime" not in body["start"]
    assert "dateTime" not in body["end"]


def test_multi_day_all_day_payload_keeps_whole_day_duration():
    body = build_event_payload(_all_day_task(days=3))

    assert body["start"] == {"date": "2026-07-06"}
    assert body["end"] == {"date": "2026-07-09"}


def test_all_day_payload_with_partial_day_duration_falls_back_to_single_day():
    # A locally edited duration that is not a whole number of days must not
    # produce a dateTime payload nor a bogus multi-day range.
    task = _all_day_task()
    task.duration_minutes = 30

    body = build_event_payload(task)

    assert body["start"] == {"date": "2026-07-06"}
    assert body["end"] == {"date": "2026-07-07"}


# ---------------------------------------------------------------------------
# Calendar pull sets gcal_all_day from the event shape
# ---------------------------------------------------------------------------

def test_pull_all_day_event_sets_flag(tmp_path):
    repo = FakeRepo()
    gcal = _gcal_with_events(
        _calendar_event(
            id=RECURRING_INSTANCE_ID,
            start={"date": "2026-07-06"},
            end={"date": "2026-07-07"},
        )
    )
    service = _make_service(tmp_path, gcal=gcal, repo=repo)

    assert service.pull_all() is True

    (created,) = repo.created
    assert created["gcal_all_day"] is True
    assert created["gcal_event_id"] == RECURRING_INSTANCE_ID
    assert created["start"] == datetime(2026, 7, 6, tzinfo=UTC)
    assert created["duration_minutes"] == 24 * 60


def test_pull_timed_event_sets_flag_false(tmp_path):
    repo = FakeRepo()
    gcal = _gcal_with_events(_calendar_event())
    service = _make_service(tmp_path, gcal=gcal, repo=repo)

    assert service.pull_all() is True

    (created,) = repo.created
    assert created["gcal_all_day"] is False
    assert created["duration_minutes"] == 60


def test_pull_update_preserves_all_day_flag(tmp_path):
    old = datetime(2026, 6, 1, tzinfo=UTC)
    repo = FakeRepo()
    repo.add(
        Task(
            id=5,
            title="Old title",
            start=datetime(2026, 7, 6, tzinfo=UTC),
            duration_minutes=24 * 60,
            gcal_event_id=RECURRING_INSTANCE_ID,
            gcal_all_day=True,
            gcal_updated=old,
            updated_at=old,
        )
    )
    gcal = _gcal_with_events(
        _calendar_event(
            id=RECURRING_INSTANCE_ID,
            summary="New title",
            start={"date": "2026-07-06"},
            end={"date": "2026-07-07"},
        )
    )
    service = _make_service(tmp_path, gcal=gcal, repo=repo)

    assert service.pull_all() is True

    ((task_id, fields),) = repo.sync_updates
    assert task_id == 5
    assert fields["gcal_all_day"] is True
    task = repo.get(5)
    assert task.title == "New title"
    assert task.gcal_all_day is True


def test_pull_cancelled_event_unlinks_and_clears_flag(tmp_path):
    old = datetime(2026, 6, 1, tzinfo=UTC)
    repo = FakeRepo()
    repo.add(
        Task(
            id=5,
            title="Yearly",
            start=datetime(2026, 7, 6, tzinfo=UTC),
            duration_minutes=24 * 60,
            gcal_event_id=RECURRING_INSTANCE_ID,
            gcal_all_day=True,
            gcal_updated=old,
            updated_at=old,
        )
    )
    gcal = _gcal_with_events(
        _calendar_event(id=RECURRING_INSTANCE_ID, status="cancelled")
    )
    service = _make_service(tmp_path, gcal=gcal, repo=repo)

    assert service.pull_all() is True

    task = repo.get(5)
    assert task is not None, "local task must survive a remote cancellation"
    assert task.gcal_event_id is None
    assert task.gcal_all_day is False


# ---------------------------------------------------------------------------
# Push keeps the event shape (recurring all-day instances included)
# ---------------------------------------------------------------------------

def _remote_all_day_instance(start="2026-07-06", end="2026-07-07", **extra) -> dict:
    event = {
        "id": RECURRING_INSTANCE_ID,
        "status": "confirmed",
        "recurringEventId": "ivc7pjg531mq4i9328p9hqdhi4",
        "originalStartTime": {"date": start},
        "start": {"date": start},
        "end": {"date": end},
    }
    event.update(extra)
    return event


def test_update_all_day_recurring_instance_with_same_date_omits_start_end(tmp_path, caplog):
    """The repaired-dead-letter case: dates match remote, so the patch must
    not try to move the instance at all (Google rejects such moves with
    HTTP 400 "Invalid start time.")."""
    queue = FakeQueue()
    repo = FakeRepo()
    repo.add(_all_day_task(2, gcal_event_id=RECURRING_INSTANCE_ID))
    gcal = FakeGoogleCalendar(
        events_by_id={RECURRING_INSTANCE_ID: _remote_all_day_instance()}
    )
    service = _make_service(tmp_path, gcal=gcal, repo=repo, queue=queue)
    queue.enqueue("gcal_update", 2, {"eventId": RECURRING_INSTANCE_ID})

    with caplog.at_level(logging.INFO, logger="planner.sync"):
        assert service.push_queue_worker() == 1

    ((_cal, event_id, body),) = gcal.service.patched
    assert event_id == RECURRING_INSTANCE_ID
    assert "start" not in body, "unchanged dates must not be patched"
    assert "end" not in body
    assert body["summary"] == "All day"
    assert queue.failed_count() == 0
    # The outgoing payload shape is logged for diagnosis.
    assert any("start=omitted" in message for message in caplog.messages)


def test_update_all_day_plain_event_with_changed_date_sends_date_payload(tmp_path):
    queue = FakeQueue()
    repo = FakeRepo()
    repo.add(_all_day_task(2, gcal_event_id="ev-allday"))
    remote = {
        "id": "ev-allday",
        "status": "confirmed",
        "start": {"date": "2026-07-01"},
        "end": {"date": "2026-07-02"},
    }
    gcal = FakeGoogleCalendar(events_by_id={"ev-allday": remote})
    service = _make_service(tmp_path, gcal=gcal, repo=repo, queue=queue)
    queue.enqueue("gcal_update", 2, {"eventId": "ev-allday"})

    assert service.push_queue_worker() == 1

    ((_cal, _event_id, body),) = gcal.service.patched
    # A real date move on a non-recurring all-day event: date/date with
    # explicit dateTime nulls (events.patch merges start/end per field).
    assert body["start"] == {"date": "2026-07-06", "dateTime": None}
    assert body["end"] == {"date": "2026-07-07", "dateTime": None}
    assert queue.failed_count() == 0


def test_update_all_day_recurring_instance_with_changed_date_dead_letters(tmp_path):
    queue = FakeQueue()
    repo = FakeRepo()
    task = repo.add(_all_day_task(2, gcal_event_id=RECURRING_INSTANCE_ID))
    gcal = FakeGoogleCalendar(
        events_by_id={
            RECURRING_INSTANCE_ID: _remote_all_day_instance("2026-07-01", "2026-07-02")
        }
    )
    service = _make_service(tmp_path, gcal=gcal, repo=repo, queue=queue)
    queue.enqueue("gcal_update", 2, {"eventId": RECURRING_INSTANCE_ID})

    assert service.push_queue_worker() == 0

    # No invalid payload is ever sent; the op dead-letters with a clear reason.
    assert gcal.service.patched == []
    assert queue.count() == 0
    assert queue.failed_count() == 1
    ((_op_id, reason),) = queue.failed
    assert "recurring instance" in reason
    assert "2026-07-06" in reason and "2026-07-01" in reason
    # The local task is intact.
    assert repo.get(2) is task
    assert task.gcal_all_day is True


def test_update_all_day_task_with_timed_remote_dead_letters_with_repair_hint(tmp_path):
    queue = FakeQueue()
    repo = FakeRepo()
    repo.add(_all_day_task(2, gcal_event_id="ev-timed"))
    remote = {
        "id": "ev-timed",
        "status": "confirmed",
        "start": {"dateTime": "2026-07-06T09:00:00Z"},
        "end": {"dateTime": "2026-07-06T10:00:00Z"},
    }
    gcal = FakeGoogleCalendar(events_by_id={"ev-timed": remote})
    service = _make_service(tmp_path, gcal=gcal, repo=repo, queue=queue)
    queue.enqueue("gcal_update", 2, {"eventId": "ev-timed"})

    assert service.push_queue_worker() == 0

    assert gcal.service.patched == []
    assert queue.failed_count() == 1
    ((_op_id, reason),) = queue.failed
    assert "timed" in reason
    assert "dead_letter_recovery" in reason, "reason must point at the repair tool"
    assert repo.get(2) is not None


def test_update_timed_task_keeps_datetime_payload_and_skips_remote_fetch(tmp_path):
    queue = FakeQueue()
    repo = FakeRepo()
    repo.add(_timed_task(3, gcal_event_id="ev-3"))
    gcal = FakeGoogleCalendar()
    service = _make_service(tmp_path, gcal=gcal, repo=repo, queue=queue)
    queue.enqueue("gcal_update", 3, {"eventId": "ev-3"})

    assert service.push_queue_worker() == 1

    ((_cal, _event_id, body),) = gcal.service.patched
    assert body["start"]["dateTime"] == "2026-07-06T09:30:00Z"
    assert body["end"]["dateTime"] == "2026-07-06T10:15:00Z"
    assert "date" not in body["start"]
    # Timed updates keep the old one-call behavior: no events().get().
    assert gcal.service.got == []


def test_pulled_all_day_event_round_trips_as_all_day(tmp_path):
    """Pull an all-day instance, then push a title edit: the patch keeps the
    all-day event intact by not touching its unchanged start/end."""
    repo = FakeRepo()
    queue = FakeQueue()
    remote = _calendar_event(
        id=RECURRING_INSTANCE_ID,
        start={"date": "2026-07-06"},
        end={"date": "2026-07-07"},
    )
    gcal = FakeGoogleCalendar(
        list_payloads=[{"items": [remote], "nextSyncToken": "tok-1"}],
        events_by_id={RECURRING_INSTANCE_ID: remote},
    )
    service = _make_service(tmp_path, gcal=gcal, repo=repo, queue=queue)
    assert service.pull_all() is True
    (task,) = repo.tasks.values()
    assert task.gcal_all_day is True

    task.title = "Edited locally"
    queue.enqueue("gcal_update", task.id, {"eventId": RECURRING_INSTANCE_ID})
    assert service.push_queue_worker() == 1

    ((_cal, _event_id, body),) = gcal.service.patched
    assert body["summary"] == "Edited locally"
    assert "start" not in body, "pulled dates match remote: start/end omitted"
    assert "end" not in body
    assert "dateTime" not in str(body)


def test_user_created_timed_task_pushes_timed_payload(tmp_path):
    # A task created locally with an explicit time (gcal_all_day defaults to
    # False) must never be converted to an all-day event.
    task = Task(
        id=4,
        title="Meeting",
        start=datetime(2026, 7, 6, 14, 0, tzinfo=UTC),
        duration_minutes=30,
    )
    assert task.gcal_all_day is False

    queue = FakeQueue()
    repo = FakeRepo()
    repo.add(task)
    gcal = FakeGoogleCalendar()
    service = _make_service(tmp_path, gcal=gcal, repo=repo, queue=queue)
    queue.enqueue("gcal_create", 4, {})

    assert service.push_queue_worker() == 1

    ((_cal, body),) = gcal.service.inserted
    assert "dateTime" in body["start"]
    assert "date" not in body["start"]


# ---------------------------------------------------------------------------
# Migration: legacy databases gain gcal_all_day idempotently
# ---------------------------------------------------------------------------

def _legacy_engine(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'legacy.db').as_posix()}")
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE task (
                    id INTEGER PRIMARY KEY,
                    title TEXT NOT NULL,
                    notes TEXT,
                    start TEXT,
                    due TEXT,
                    duration_minutes INTEGER,
                    priority INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'todo',
                    created_at TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL DEFAULT ''
                )
                """
            )
        )
        conn.execute(text("CREATE TABLE dailytask (id INTEGER PRIMARY KEY, title TEXT)"))
        conn.execute(text("INSERT INTO task (id, title) VALUES (1, 'Legacy')"))
    return engine


def test_migration_adds_gcal_all_day_idempotently(tmp_path):
    engine = _legacy_engine(tmp_path)

    migrations.run_all(engine)
    migrations.run_all(engine)  # second run must be a no-op, not an error

    with engine.begin() as conn:
        columns = [row[1] for row in conn.execute(text("PRAGMA table_info('task')"))]
        assert columns.count("gcal_all_day") == 1
        value = conn.execute(
            text("SELECT gcal_all_day FROM task WHERE id = 1")
        ).scalar()
        assert value == 0, "existing rows default to timed"
