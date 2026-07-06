"""Selective dead-letter inspection, repair and replay.

The tooling in ``services.dead_letter_recovery`` is a hand tool for the
rows that piled up while ``build_event_payload`` still sent timed
``dateTime`` payloads for all-day events. The safety contract under test:

* everything defaults to dry-run — no DB writes without ``dry_run=False``;
* repairs touch only ``Task.gcal_all_day`` and only after reading the
  real remote event shape; nothing else on the task changes;
* replay requires an explicit ``dead_letter_id`` list (no replay-all) and
  removes the dead-letter row only in the same transaction that creates
  the replacement PendingOp row;
* local tasks are never deleted.

Everything runs against fakes and in-memory SQLite; no real Google APIs.
"""
import json
from datetime import datetime, timezone

import pytest
from sqlmodel import SQLModel, Session, create_engine, select

from models.pending_op import DeadLetterOp, PendingOp
from models.task import Task
from services.dead_letter_recovery import (
    REMOTE_ALL_DAY,
    REMOTE_MISSING,
    REMOTE_TIMED,
    DeadLetterRecovery,
    classify_event_shape,
)
from services.pending_ops_queue import PendingOpsQueue

from test_sync_engine_wiring import FakeRepo

UTC = timezone.utc
RECURRING_INSTANCE_ID = "ivc7pjg531mq4i9328p9hqdhi4_20260706"


class FakeRecoveryCalendar:
    """GoogleCalendar stand-in: connect() + get_event_by_id() from a dict."""

    calendar_id = "primary"

    def __init__(self, events=None):
        self.events = dict(events or {})
        self.connect_calls = 0
        self.fetched = []

    def connect(self):
        self.connect_calls += 1

    def get_event_by_id(self, event_id):
        self.fetched.append(event_id)
        return self.events.get(event_id)


def _make_env(events=None):
    """In-memory queue tables + FakeRepo + fake calendar + recovery tool."""
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)

    def session_factory():
        return Session(engine)

    repo = FakeRepo()
    gcal = FakeRecoveryCalendar(events)
    tool = DeadLetterRecovery(gcal, repo, session_factory=session_factory)
    queue = PendingOpsQueue(session_factory=session_factory)
    return tool, repo, gcal, queue, session_factory


def _dead_letter(session_factory, *, op="gcal_update", task_id=2,
                 payload=None, error='HTTP 400: "Invalid start time."') -> int:
    with session_factory() as session:
        record = DeadLetterOp(
            op=op,
            task_id=task_id,
            payload=json.dumps(payload if payload is not None else {"eventId": RECURRING_INSTANCE_ID}),
            attempts=1,
            last_error=error,
        )
        session.add(record)
        session.commit()
        session.refresh(record)
        return record.id


def _rows(session_factory, model):
    with session_factory() as session:
        return list(session.exec(select(model)))


def _legacy_all_day_task(task_id=2, **extra) -> Task:
    """A pre-fix task: linked to an all-day event but flagged as timed."""
    fields = dict(
        title="Yearly reminder",
        start=datetime(2026, 7, 6, tzinfo=UTC),
        duration_minutes=24 * 60,
        gcal_event_id=RECURRING_INSTANCE_ID,
        gcal_all_day=False,
    )
    fields.update(extra)
    return Task(id=task_id, **fields)


def _all_day_event(event_id=RECURRING_INSTANCE_ID) -> dict:
    return {
        "id": event_id,
        "status": "confirmed",
        "start": {"date": "2026-07-06"},
        "end": {"date": "2026-07-07"},
    }


def _timed_event(event_id="ev-timed") -> dict:
    return {
        "id": event_id,
        "status": "confirmed",
        "start": {"dateTime": "2026-07-06T09:00:00Z"},
        "end": {"dateTime": "2026-07-06T10:00:00Z"},
    }


# ---------------------------------------------------------------------------
# Shape classification
# ---------------------------------------------------------------------------

def test_classify_event_shape():
    assert classify_event_shape(_all_day_event()) == REMOTE_ALL_DAY
    assert classify_event_shape(_timed_event()) == REMOTE_TIMED
    assert classify_event_shape(None) == REMOTE_MISSING
    assert classify_event_shape({"status": "cancelled"}) == "cancelled"
    assert classify_event_shape({"status": "confirmed"}) == "unknown"


# ---------------------------------------------------------------------------
# 1. Inspection
# ---------------------------------------------------------------------------

def test_inspect_lists_dead_letter_rows_with_task_snapshot():
    tool, repo, _gcal, _queue, session_factory = _make_env()
    repo.add(_legacy_all_day_task(2))
    dl_1 = _dead_letter(session_factory, task_id=2)
    dl_2 = _dead_letter(session_factory, op="gcal_delete", task_id=99,
                        payload={"eventId": "ev-gone"}, error="HTTP 403: forbidden")

    rows = tool.inspect()

    assert [r.dead_letter_id for r in rows] == [dl_1, dl_2]
    first, second = rows
    assert (first.op, first.task_id) == ("gcal_update", 2)
    assert first.payload == {"eventId": RECURRING_INSTANCE_ID}
    assert first.attempts == 1
    assert "Invalid start time." in first.last_error
    assert first.failed_at is not None
    # Local task snapshot travels with the row.
    assert first.task_exists is True
    assert first.task_title == "Yearly reminder"
    assert first.task_start == datetime(2026, 7, 6, tzinfo=UTC)
    assert first.task_duration_minutes == 24 * 60
    assert first.task_gcal_event_id == RECURRING_INSTANCE_ID
    assert first.task_gcal_all_day is False
    # A row whose task is gone is still listed, flagged as such.
    assert second.task_exists is False
    assert second.task_title is None

    # Selection by id works too.
    assert [r.dead_letter_id for r in tool.inspect([dl_2])] == [dl_2]


# ---------------------------------------------------------------------------
# 2/5. Verification is read-only; dry-run is the default and writes nothing
# ---------------------------------------------------------------------------

def test_dry_run_repair_reports_but_mutates_nothing():
    tool, repo, gcal, queue, session_factory = _make_env(
        {RECURRING_INSTANCE_ID: _all_day_event()}
    )
    task = repo.add(_legacy_all_day_task(2))
    dl_id = _dead_letter(session_factory, task_id=2)

    (action,) = tool.repair([dl_id])  # dry_run defaults to True

    assert action.action == "set_all_day"
    assert action.remote_shape == REMOTE_ALL_DAY
    assert action.local_all_day is False
    assert action.event_id == RECURRING_INSTANCE_ID
    assert action.applied is False
    assert gcal.fetched == [RECURRING_INSTANCE_ID], "remote shape was verified"
    # No writes anywhere: task untouched, no repo updates, queues unchanged.
    assert task.gcal_all_day is False
    assert repo.sync_updates == []
    assert queue.count() == 0
    assert queue.failed_count() == 1


def test_verify_remote_is_alias_for_dry_run():
    tool, repo, _gcal, queue, session_factory = _make_env(
        {RECURRING_INSTANCE_ID: _all_day_event()}
    )
    repo.add(_legacy_all_day_task(2))
    dl_id = _dead_letter(session_factory, task_id=2)

    (action,) = tool.verify_remote([dl_id])

    assert action.remote_shape == REMOTE_ALL_DAY
    assert action.applied is False
    assert repo.sync_updates == []
    assert queue.failed_count() == 1


def test_dry_run_replay_writes_nothing():
    tool, repo, _gcal, queue, session_factory = _make_env()
    repo.add(_legacy_all_day_task(2))
    dl_id = _dead_letter(session_factory, task_id=2)

    (action,) = tool.replay([dl_id])  # dry_run defaults to True

    assert action.action == "requeue"
    assert action.applied is False
    assert queue.count() == 0, "dry-run must not create PendingOp rows"
    assert queue.failed_count() == 1, "dry-run must not remove dead-letter rows"


# ---------------------------------------------------------------------------
# 3. Selective repair from the real remote shape
# ---------------------------------------------------------------------------

def test_repair_sets_all_day_from_remote_and_preserves_task_fields():
    tool, repo, _gcal, _queue, session_factory = _make_env(
        {RECURRING_INSTANCE_ID: _all_day_event()}
    )
    task = repo.add(_legacy_all_day_task(2, gtasks_id="gt-2", notes="keep me"))
    unrelated = repo.add(_legacy_all_day_task(3, gcal_event_id="ev-other"))
    dl_id = _dead_letter(session_factory, task_id=2)

    (action,) = tool.repair([dl_id], dry_run=False)

    assert action.action == "set_all_day"
    assert action.applied is True
    assert task.gcal_all_day is True
    # Everything else is preserved.
    assert task.title == "Yearly reminder"
    assert task.start == datetime(2026, 7, 6, tzinfo=UTC)
    assert task.duration_minutes == 24 * 60
    assert task.gcal_event_id == RECURRING_INSTANCE_ID
    assert task.gtasks_id == "gt-2"
    assert task.notes == "keep me"
    # Unrelated tasks are not touched, no task is deleted.
    assert unrelated.gcal_all_day is False
    assert repo.get(2) is task
    assert repo.get(3) is unrelated
    assert len(repo.sync_updates) == 1


def test_repair_clears_flag_when_remote_is_timed():
    tool, repo, _gcal, _queue, session_factory = _make_env({"ev-timed": _timed_event()})
    task = repo.add(
        Task(
            id=4,
            title="Actually timed",
            start=datetime(2026, 7, 6, 9, 0, tzinfo=UTC),
            duration_minutes=60,
            gcal_event_id="ev-timed",
            gcal_all_day=True,
        )
    )
    dl_id = _dead_letter(session_factory, task_id=4, payload={"eventId": "ev-timed"})

    (action,) = tool.repair([dl_id], dry_run=False)

    assert action.action == "clear_all_day"
    assert action.applied is True
    assert task.gcal_all_day is False
    assert repo.get(4) is task


def test_repair_skips_missing_remote_and_matching_shape():
    tool, repo, _gcal, _queue, session_factory = _make_env(
        {RECURRING_INSTANCE_ID: _all_day_event()}
    )
    repo.add(_legacy_all_day_task(2, gcal_all_day=True))  # already consistent
    task_gone_remote = repo.add(
        Task(id=5, title="Remote missing", start=datetime(2026, 7, 6, tzinfo=UTC),
             duration_minutes=30, gcal_event_id="ev-vanished")
    )
    dl_ok = _dead_letter(session_factory, task_id=2)
    dl_missing = _dead_letter(session_factory, task_id=5,
                              payload={"eventId": "ev-vanished"})

    ok_action, missing_action = tool.repair([dl_ok, dl_missing], dry_run=False)

    assert ok_action.action == "none"
    assert ok_action.applied is False
    assert missing_action.action == "skip"
    assert missing_action.remote_shape == REMOTE_MISSING
    assert missing_action.applied is False
    assert repo.sync_updates == [], "consistent/missing rows must not be written"
    assert task_gone_remote.gcal_all_day is False


# ---------------------------------------------------------------------------
# 4. Selective replay
# ---------------------------------------------------------------------------

def test_replay_moves_only_selected_rows_back_to_pending():
    tool, repo, _gcal, queue, session_factory = _make_env()
    repo.add(_legacy_all_day_task(2, gcal_all_day=True))
    repo.add(_legacy_all_day_task(3, gcal_event_id="ev-other"))
    dl_selected = _dead_letter(session_factory, task_id=2)
    dl_untouched = _dead_letter(session_factory, task_id=3,
                                payload={"eventId": "ev-other"})

    (action,) = tool.replay([dl_selected], dry_run=False)

    assert action.action == "requeue"
    assert action.applied is True
    # Exactly one op moved back, with the original op/task/payload.
    (pending,) = _rows(session_factory, PendingOp)
    assert (pending.op, pending.task_id) == ("gcal_update", 2)
    assert json.loads(pending.payload) == {"eventId": RECURRING_INSTANCE_ID}
    assert pending.attempts == 0, "backoff restarts from scratch"
    assert f"dead-letter #{dl_selected}" in pending.last_error
    # The unselected row is still dead-lettered; nothing was mass-replayed.
    (remaining,) = _rows(session_factory, DeadLetterOp)
    assert remaining.id == dl_untouched
    assert queue.count() == 1
    assert queue.failed_count() == 1
    # Local tasks survive.
    assert repo.get(2) is not None
    assert repo.get(3) is not None


def test_replay_requires_explicit_ids():
    tool, _repo, _gcal, queue, session_factory = _make_env()
    _dead_letter(session_factory, task_id=2)

    with pytest.raises(ValueError, match="dead_letter_ids"):
        tool.replay(None)
    with pytest.raises(ValueError, match="dead_letter_ids"):
        tool.replay([])
    with pytest.raises(ValueError, match="dead_letter_ids"):
        tool.repair([])

    assert queue.count() == 0
    assert queue.failed_count() == 1, "no mass replay happened"


def test_replay_removal_is_transactional():
    """If creating the PendingOp row fails, the dead-letter row survives."""
    tool, repo, _gcal, queue, session_factory = _make_env()
    repo.add(_legacy_all_day_task(2, gcal_all_day=True))
    dl_id = _dead_letter(session_factory, task_id=2)

    class BoomSession(Session):
        def add(self, instance, *args, **kwargs):
            if isinstance(instance, PendingOp):
                raise RuntimeError("simulated insert failure")
            return super().add(instance, *args, **kwargs)

    assert _rows(session_factory, DeadLetterOp), "dead-letter row exists beforehand"

    engine = session_factory().get_bind()
    boom_tool = DeadLetterRecovery(
        FakeRecoveryCalendar(),
        repo,
        session_factory=lambda: BoomSession(engine),
    )

    with pytest.raises(RuntimeError, match="simulated insert failure"):
        boom_tool.replay([dl_id], dry_run=False)

    assert queue.count() == 0, "no PendingOp row may exist after the failure"
    assert queue.failed_count() == 1, "dead-letter row must survive the failure"
    (dead,) = _rows(session_factory, DeadLetterOp)
    assert dead.id == dl_id
    assert repo.get(2) is not None


def test_replay_skips_rows_whose_task_is_gone():
    tool, _repo, _gcal, queue, session_factory = _make_env()
    dl_id = _dead_letter(session_factory, task_id=777)  # no such local task

    (action,) = tool.replay([dl_id], dry_run=False)

    assert action.action == "skip"
    assert "task" in action.reason
    assert queue.count() == 0
    assert queue.failed_count() == 1, "skipped rows stay in dead-letter"


def test_unknown_ids_are_reported_not_fatal():
    tool, repo, _gcal, queue, session_factory = _make_env(
        {RECURRING_INSTANCE_ID: _all_day_event()}
    )
    repo.add(_legacy_all_day_task(2))
    dl_id = _dead_letter(session_factory, task_id=2)

    repair_actions = tool.repair([dl_id, 9999], dry_run=False)
    replay_actions = tool.replay([9999])

    assert [a.action for a in repair_actions] == ["set_all_day", "skip"]
    assert repair_actions[1].reason == "dead-letter row not found"
    assert replay_actions[0].action == "skip"
    assert queue.failed_count() == 1
