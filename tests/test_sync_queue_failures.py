"""Pending-op failure handling in SyncService.push_queue_worker.

Covers the retry/terminal split:

* retryable Google errors (409/412/429/5xx) and unknown transport failures
  are requeued with backoff;
* non-retryable 4xx (400/401/403/404 on update) move the op to the
  dead-letter table exactly once — no infinite retry loop — with enough
  detail (op, task_id, payload, status, error) to inspect it later;
* 404 on delete stays the documented success path (already gone remotely);
* one terminal failure does not stop the rest of the batch;
* lane-blocked gtasks_* ops in undated mode are still requeued (rollback
  safety), never dead-lettered.

Everything runs against fakes and in-memory SQLite; no real Google APIs.
"""
import json
import logging

import httplib2
from googleapiclient.errors import HttpError
from sqlmodel import SQLModel, Session, create_engine, select

from core.settings import UNDATED_ENGINE_UNDATED
from models.pending_op import DeadLetterOp, PendingOp
from services.pending_ops_queue import PendingOpsQueue
from services.sync_service import SyncService
from services.sync_token_storage import SyncTokenStorage

from test_sync_engine_wiring import (
    FakeGoogleCalendar,
    FakeGoogleTasks,
    FakeRepo,
    _Response,
    _scheduled_task,
    _set_sync_service_flag,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _http_error(code: int, message: str = "boom") -> HttpError:
    resp = httplib2.Response({"status": str(code), "reason": message})
    content = json.dumps({"error": {"code": code, "message": message}}).encode()
    return HttpError(resp, content, uri="https://example.invalid/calendar")


def _make_queue() -> PendingOpsQueue:
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    queue = PendingOpsQueue(session_factory=lambda: Session(engine))
    queue._test_engine = engine  # keep the engine alive / reachable for asserts
    return queue


def _rows(queue: PendingOpsQueue, model):
    with Session(queue._test_engine) as session:
        return list(session.exec(select(model)))


def _make_service(tmp_path, *, gcal, repo, queue) -> SyncService:
    return SyncService(
        gcal,
        FakeGoogleTasks(),
        repo,
        SyncTokenStorage(tmp_path / "tokens.json"),
        queue,
    )


class FlakyCalendarService:
    """FakeCalendarService twin whose patch/insert/delete raise scripted errors.

    ``errors`` is consumed one entry per API call; ``None`` means success,
    an exception instance is raised. An exhausted script means success.
    """

    def __init__(self, patch_errors=(), insert_errors=(), delete_errors=()):
        self.patch_errors = list(patch_errors)
        self.insert_errors = list(insert_errors)
        self.delete_errors = list(delete_errors)
        self.patched = []
        self.inserted = []
        self.deleted = []

    def events(self):
        return self

    def _maybe_raise(self, script):
        if script:
            err = script.pop(0)
            if err is not None:
                raise err

    def patch(self, calendarId=None, eventId=None, body=None):
        self.patched.append((calendarId, eventId, body))
        self._maybe_raise(self.patch_errors)
        return _Response(
            {"id": eventId, "etag": "etag-2", "updated": "2026-07-01T00:00:00Z"}
        )

    def insert(self, calendarId=None, body=None):
        self.inserted.append((calendarId, body))
        self._maybe_raise(self.insert_errors)
        return _Response(
            {"id": f"ev-{len(self.inserted)}", "etag": "etag-1",
             "updated": "2026-07-01T00:00:00Z"}
        )

    def delete(self, calendarId=None, eventId=None):
        self.deleted.append((calendarId, eventId))
        self._maybe_raise(self.delete_errors)
        return _Response({})


def _flaky_gcal(**kwargs) -> FakeGoogleCalendar:
    gcal = FakeGoogleCalendar()
    gcal.service = FlakyCalendarService(**kwargs)
    return gcal


# ---------------------------------------------------------------------------
# Retryable errors requeue
# ---------------------------------------------------------------------------

def test_retryable_error_requeues_with_backoff(tmp_path):
    queue = _make_queue()
    repo = FakeRepo()
    repo.add(_scheduled_task(2, gcal_event_id="ev-2"))
    gcal = _flaky_gcal(patch_errors=[_http_error(503, "backend error")])
    service = _make_service(tmp_path, gcal=gcal, repo=repo, queue=queue)
    queue.enqueue("gcal_update", 2, {"eventId": "ev-2"})

    assert service.push_queue_worker() == 0

    assert queue.count() == 1, "retryable failure must stay queued"
    assert queue.failed_count() == 0
    (row,) = _rows(queue, PendingOp)
    assert row.attempts == 1
    assert "503" in row.last_error
    # Backoff: the op is not due again immediately, so the next worker pass
    # does not hammer the API.
    assert queue.due() == []
    assert service.push_queue_worker() == 0
    assert len(gcal.service.patched) == 1


# ---------------------------------------------------------------------------
# Non-retryable 400: dead-letter, no infinite loop, local task intact
# ---------------------------------------------------------------------------

def test_non_retryable_400_dead_letters_once(tmp_path, caplog):
    queue = _make_queue()
    repo = FakeRepo()
    task = repo.add(_scheduled_task(2, gcal_event_id="ev-2"))
    original_title = task.title
    gcal = _flaky_gcal(patch_errors=[_http_error(400, "Invalid start time.")])
    service = _make_service(tmp_path, gcal=gcal, repo=repo, queue=queue)
    queue.enqueue("gcal_update", 2, {"eventId": "ev-2"})

    with caplog.at_level(logging.ERROR, logger="planner.sync"):
        assert service.push_queue_worker() == 0

    # The op left the queue for the dead-letter table with full context.
    assert queue.count() == 0
    assert queue.failed_count() == 1
    (dead,) = _rows(queue, DeadLetterOp)
    assert dead.op == "gcal_update"
    assert dead.task_id == 2
    assert json.loads(dead.payload) == {"eventId": "ev-2"}
    assert dead.attempts == 1
    assert dead.last_error.startswith("HTTP 400:")
    assert "Invalid start time." in dead.last_error
    assert dead.failed_at is not None

    # The log names op, task, payload keys and status for diagnosis.
    message = "\n".join(caplog.messages)
    assert "gcal_update" in message
    assert "task 2" in message
    assert "HTTP 400" in message
    assert "eventId" in message

    # The local task was not deleted or modified.
    assert repo.get(2) is task
    assert task.title == original_title
    assert task.gcal_event_id == "ev-2"
    assert repo.sync_updates == []

    # No retry loop: the next worker pass makes no API call.
    assert service.push_queue_worker() == 0
    assert len(gcal.service.patched) == 1


def test_404_on_update_is_terminal_and_deterministic(tmp_path):
    queue = _make_queue()
    repo = FakeRepo()
    repo.add(_scheduled_task(2, gcal_event_id="ev-gone"))
    gcal = _flaky_gcal(patch_errors=[_http_error(404, "Not Found")])
    service = _make_service(tmp_path, gcal=gcal, repo=repo, queue=queue)
    queue.enqueue("gcal_update", 2, {"eventId": "ev-gone"})

    assert service.push_queue_worker() == 0

    assert queue.count() == 0
    assert queue.failed_count() == 1
    (dead,) = _rows(queue, DeadLetterOp)
    assert dead.last_error.startswith("HTTP 404:")
    assert repo.get(2) is not None, "local task must survive"
    assert len(gcal.service.patched) == 1
    assert service.push_queue_worker() == 0
    assert len(gcal.service.patched) == 1


def test_404_on_delete_stays_success_path(tmp_path):
    queue = _make_queue()
    repo = FakeRepo()
    task = repo.add(_scheduled_task(2, gcal_event_id="ev-gone"))
    gcal = _flaky_gcal(delete_errors=[_http_error(404, "Not Found")])
    service = _make_service(tmp_path, gcal=gcal, repo=repo, queue=queue)
    queue.enqueue("gcal_delete", 2, {"eventId": "ev-gone"})

    assert service.push_queue_worker() == 1

    # Already gone remotely == done: op removed, nothing dead-lettered,
    # the local task itself is only unlinked, never deleted.
    assert queue.count() == 0
    assert queue.failed_count() == 0
    assert repo.get(2) is task
    assert task.gcal_event_id is None


# ---------------------------------------------------------------------------
# Success and batch behavior
# ---------------------------------------------------------------------------

def test_successful_op_is_removed(tmp_path):
    queue = _make_queue()
    repo = FakeRepo()
    repo.add(_scheduled_task(2, gcal_event_id="ev-2"))
    gcal = _flaky_gcal()  # no scripted errors: everything succeeds
    service = _make_service(tmp_path, gcal=gcal, repo=repo, queue=queue)
    queue.enqueue("gcal_update", 2, {"eventId": "ev-2"})

    assert service.push_queue_worker() == 1
    assert queue.count() == 0
    assert queue.failed_count() == 0


def test_batch_continues_after_terminal_failure(tmp_path):
    queue = _make_queue()
    repo = FakeRepo()
    repo.add(_scheduled_task(2, gcal_event_id="ev-2"))
    repo.add(_scheduled_task(3))
    gcal = _flaky_gcal(patch_errors=[_http_error(400, "Invalid start time.")])
    service = _make_service(tmp_path, gcal=gcal, repo=repo, queue=queue)
    queue.enqueue("gcal_update", 2, {"eventId": "ev-2"})
    queue.enqueue("gcal_create", 3, {})

    processed = service.push_queue_worker()

    assert processed == 1, "the create after the failed update must still run"
    assert gcal.service.inserted, "gcal_create was executed"
    assert queue.count() == 0
    assert queue.failed_count() == 1
    (dead,) = _rows(queue, DeadLetterOp)
    assert (dead.op, dead.task_id) == ("gcal_update", 2)


# ---------------------------------------------------------------------------
# Undated-mode lane block is not confused with API failures
# ---------------------------------------------------------------------------

def test_lane_blocked_gtasks_requeued_not_dead_lettered(tmp_path, monkeypatch):
    _set_sync_service_flag(monkeypatch, UNDATED_ENGINE_UNDATED)
    queue = _make_queue()
    repo = FakeRepo()
    repo.add(_scheduled_task(2, gcal_event_id="ev-2"))
    from test_sync_engine_wiring import _unscheduled_task

    repo.add(_unscheduled_task(1, gtasks_id="gt-1"))
    gcal = _flaky_gcal()
    service = _make_service(tmp_path, gcal=gcal, repo=repo, queue=queue)
    queue.enqueue("gtasks_update", 1, {"taskId": "gt-1"})
    queue.enqueue("gcal_update", 2, {"eventId": "ev-2"})

    processed = service.push_queue_worker()

    # The calendar lane is not starved by the blocked gtasks op...
    assert processed == 1
    assert gcal.service.patched
    # ...and the blocked op is requeued for a legacy rollback, not killed.
    assert queue.failed_count() == 0
    (row,) = _rows(queue, PendingOp)
    assert row.op == "gtasks_update"
    assert row.attempts == 1
    assert "undated" in row.last_error.lower()


# ---------------------------------------------------------------------------
# Queue-level dead-letter mechanics
# ---------------------------------------------------------------------------

def test_mark_failed_moves_row_to_dead_letter_table():
    queue = _make_queue()
    queue.enqueue("gcal_update", 7, {"eventId": "ev-7"})
    (pending,) = _rows(queue, PendingOp)

    queue.mark_failed(pending.id, "HTTP 400: Invalid start time.")

    assert queue.count() == 0
    assert queue.failed_count() == 1
    assert queue.due() == []
    (dead,) = _rows(queue, DeadLetterOp)
    assert dead.op == "gcal_update"
    assert dead.task_id == 7
    assert dead.payload == pending.payload
    assert dead.attempts == pending.attempts + 1
    assert dead.last_error == "HTTP 400: Invalid start time."
    assert dead.created_at == pending.created_at
    assert dead.failed_at is not None

    # Unknown ids are a no-op, not an error.
    queue.mark_failed(9999, "whatever")
    assert queue.failed_count() == 1
