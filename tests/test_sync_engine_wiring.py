"""Phase 2 wiring of the undated engine behind the feature flag.

Covers the split of the Google surfaces between the two engines:

* legacy mode (`GOOGLE_SYNC.undated_engine == "legacy"`, the default):
  ``SyncService`` keeps both lanes exactly as before;
* undated mode: ``SyncService`` refuses the Google Tasks lane (no
  ``gtasks_*`` enqueue, no processing, no tasks pull) while the Calendar
  lane keeps working, and ``UndatedTasksSync`` routes TaskService events
  for unscheduled tasks;
* legacy-side tripwire: even in legacy mode, a shared
  ``planner_config.json`` engine marker naming another engine blocks the
  Google Tasks lane so two writers never share the "Planner Inbox" list.

Everything runs against fakes; no real Google APIs are required.
"""
import json
import logging
from dataclasses import replace
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from sqlmodel import SQLModel, Session, create_engine, select

from core.settings import (
    UNDATED_ENGINE_LEGACY,
    UNDATED_ENGINE_UNDATED,
    GoogleSyncSettings,
    resolve_undated_engine,
)
from models import SyncMapUndated, Task
from services.appdata import AppDataClient
from services.pending_ops_queue import PendingOperation, VALID_OPS
from services.sync_service import SyncService
from services.sync_token_storage import SyncTokenStorage
from services.undated_tasks_sync import UndatedTasksSync
from utils.datetime_utils import utc_now


# ---------------------------------------------------------------------------
# Fakes for the SyncService surfaces
# ---------------------------------------------------------------------------

class FakeQueue:
    """In-memory PendingOpsQueue: records every enqueue/requeue/remove."""

    def __init__(self):
        self.entries: List[PendingOperation] = []
        self.enqueued: List[Tuple[str, int, dict]] = []
        self.requeued: List[Tuple[int, str]] = []
        self.removed: List[int] = []
        self._next_id = 1

    def enqueue(self, op: str, task_id: int, payload: dict) -> None:
        if op not in VALID_OPS:
            raise ValueError(f"Unsupported op: {op}")
        self.enqueued.append((op, task_id, payload))
        self.entries.append(
            PendingOperation(
                id=self._next_id,
                op=op,
                task_id=task_id,
                payload=payload,
                attempts=0,
                last_error=None,
                next_try_at=utc_now(),
            )
        )
        self._next_id += 1

    def due(self, limit: int = 10):
        return list(self.entries)

    def remove(self, op_id: int) -> None:
        self.removed.append(op_id)
        self.entries = [e for e in self.entries if e.id != op_id]

    def requeue(self, op_id: int, error: str) -> None:
        self.requeued.append((op_id, error))

    def count(self) -> int:
        return len(self.entries)

    def enqueued_ops(self) -> List[str]:
        return [op for op, _task_id, _payload in self.enqueued]


class FakeRepo:
    """TaskService stand-in backed by a plain dict of Task instances."""

    def __init__(self):
        self.tasks: Dict[int, Task] = {}
        self.created: List[dict] = []
        self.sync_updates: List[Tuple[int, dict]] = []
        self._next_id = 1000

    def add(self, task: Task) -> Task:
        self.tasks[task.id] = task
        return task

    def get(self, task_id: int) -> Optional[Task]:
        return self.tasks.get(task_id)

    def get_by_event_id(self, event_id):
        return next(
            (t for t in self.tasks.values() if t.gcal_event_id == event_id), None
        )

    def get_by_gtasks_id(self, gtasks_id):
        return next(
            (t for t in self.tasks.values() if t.gtasks_id == gtasks_id), None
        )

    def create_from_sync(self, **fields) -> Task:
        task = Task(id=self._next_id, **fields)
        self._next_id += 1
        self.tasks[task.id] = task
        self.created.append(fields)
        return task

    def update_from_sync(self, task_id: int, *, updated_at=None, **fields):
        task = self.tasks.get(task_id)
        if not task:
            return None
        for key, value in fields.items():
            if hasattr(task, key):
                setattr(task, key, value)
        if updated_at is not None:
            task.updated_at = updated_at
        self.sync_updates.append((task_id, fields))
        return task

    def delete_from_sync(self, task_id: int) -> None:
        self.tasks.pop(task_id, None)


class FakeGoogleTasks:
    """GoogleTasks stand-in that records every API touch."""

    tasklist_id = "inbox-list"

    def __init__(self, items=None):
        self.items = list(items or [])
        self.connect_calls = 0
        self.list_calls: List[Optional[datetime]] = []
        self.inserted: List[tuple] = []
        self.patched: List[tuple] = []
        self.deleted: List[str] = []

    def connect(self):
        self.connect_calls += 1

    def list(self, updated_min=None):
        self.list_calls.append(updated_min)
        return list(self.items)

    def insert(self, title, notes, due):
        self.inserted.append((title, notes, due))
        return {"id": f"gt-{len(self.inserted)}", "updated": "2026-07-01T00:00:00Z"}

    def patch(self, task_id, **fields):
        self.patched.append((task_id, fields))
        return {}

    def delete(self, task_id):
        self.deleted.append(task_id)

    def touched(self) -> bool:
        return bool(
            self.connect_calls
            or self.list_calls
            or self.inserted
            or self.patched
            or self.deleted
        )


class _Response:
    def __init__(self, payload):
        self._payload = payload

    def execute(self):
        return self._payload


class FakeCalendarService:
    def __init__(self, list_payloads=()):
        self._list_payloads = list(list_payloads)
        self.list_calls: List[dict] = []
        self.inserted: List[tuple] = []
        self.patched: List[tuple] = []
        self.deleted: List[tuple] = []

    def events(self):
        return self

    def list(self, **params):
        self.list_calls.append(params)
        if self._list_payloads:
            payload = self._list_payloads.pop(0)
        else:
            payload = {"items": [], "nextSyncToken": "sync-tok-1"}
        return _Response(payload)

    def insert(self, calendarId=None, body=None):
        self.inserted.append((calendarId, body))
        return _Response(
            {"id": f"ev-{len(self.inserted)}", "etag": "etag-1",
             "updated": "2026-07-01T00:00:00Z"}
        )

    def patch(self, calendarId=None, eventId=None, body=None):
        self.patched.append((calendarId, eventId, body))
        return _Response(
            {"id": eventId, "etag": "etag-2", "updated": "2026-07-01T00:00:00Z"}
        )

    def delete(self, calendarId=None, eventId=None):
        self.deleted.append((calendarId, eventId))
        return _Response({})


class FakeGoogleCalendar:
    calendar_id = "primary"

    def __init__(self, list_payloads=()):
        self.service = FakeCalendarService(list_payloads)
        self.connect_calls = 0

    def connect(self):
        self.connect_calls += 1


class FakeAppDataConfig:
    """Just enough of AppDataClient for SyncService's marker reads."""

    def __init__(self, engine=None, fail=False):
        self.config = {
            "version": 1,
            "tasklist_id": None,
            "last_full_sync": None,
            "engine": engine,
        }
        self.fail = fail
        self.read_calls = 0

    def read_config(self):
        self.read_calls += 1
        if self.fail:
            raise RuntimeError("credentials are unavailable")
        return json.loads(json.dumps(self.config)), "cfg-1"


# ---------------------------------------------------------------------------
# Full FakeAppData/FakeBridge for the UndatedTasksSync router tests
# ---------------------------------------------------------------------------

class FakeAppData(AppDataClient):  # type: ignore[misc]
    def __init__(self):
        super().__init__(auth=None)
        self.config = {"version": 1, "tasklist_id": None, "last_full_sync": None, "engine": None}
        self.index = {"version": 1, "tasklist_id": None, "tasks": {}}
        self.config_etag = "cfg-0"
        self.index_etag = "idx-0"
        self.ensure_calls = 0

    def ensure_files(self):  # type: ignore[override]
        self.ensure_calls += 1
        return {self.CONFIG_NAME: "config", self.INDEX_NAME: "index"}

    def read_config(self):  # type: ignore[override]
        return (json.loads(json.dumps(self.config)), self.config_etag)

    def write_config(self, data, if_match=None, *, on_conflict=None):  # type: ignore[override]
        payload = json.loads(json.dumps(data))
        if if_match and if_match != self.config_etag and on_conflict:
            payload = json.loads(
                json.dumps(on_conflict(json.loads(json.dumps(self.config))))
            )
        self.config = payload
        major = int(self.config_etag.split("-")[1]) + 1
        self.config_etag = f"cfg-{major}"
        return json.loads(json.dumps(self.config)), self.config_etag

    def read_index(self):  # type: ignore[override]
        return (json.loads(json.dumps(self.index)), self.index_etag)

    def write_index(self, data, if_match=None, *, on_conflict=None):  # type: ignore[override]
        payload = json.loads(json.dumps(data))
        if if_match and if_match != self.index_etag and on_conflict:
            payload = json.loads(
                json.dumps(on_conflict(json.loads(json.dumps(self.index))))
            )
        self.index = payload
        major = int(self.index_etag.split("-")[1]) + 1
        self.index_etag = f"idx-{major}"
        return json.loads(json.dumps(self.index)), self.index_etag


class FakeBridge:
    tasklist_title = "Planner Inbox"

    def __init__(self):
        self.tasks: Dict[str, dict] = {}
        self.inserted: list = []
        self.deleted: list = []
        self.ensure_calls = 0
        self.fetch_calls = 0

    def ensure_tasklist(self):
        self.ensure_calls += 1
        return "list-1"

    def fetch_all(self, tasklist_id):
        self.fetch_calls += 1
        return [dict(item) for item in self.tasks.values()]

    def upsert_task(self, tasklist_id, local_task):
        self.inserted.append((tasklist_id, dict(local_task)))
        gtask_id = local_task.get("gtask_id") or f"gtask-{len(self.inserted)}"
        self.tasks[gtask_id] = {
            "id": gtask_id,
            "title": local_task.get("title") or "",
            "notes": "",
            "metadata": {},
            "detected_meta": {},
            "status": "needsAction",
            "deleted": False,
        }
        return gtask_id

    def delete_task(self, tasklist_id, gtask_id):
        self.deleted.append((tasklist_id, gtask_id))
        self.tasks.pop(gtask_id, None)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

NOW = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)


def _set_sync_service_flag(monkeypatch, value):
    import services.sync_service as ss

    monkeypatch.setattr(ss, "GOOGLE_SYNC", replace(ss.GOOGLE_SYNC, undated_engine=value))


def _set_undated_flag(monkeypatch, value):
    import services.undated_tasks_sync as uts

    monkeypatch.setattr(uts, "GOOGLE_SYNC", replace(uts.GOOGLE_SYNC, undated_engine=value))


def _make_service(tmp_path, *, appdata=None, gtasks=None, gcal=None,
                  repo=None, queue=None):
    return SyncService(
        gcal or FakeGoogleCalendar(),
        gtasks or FakeGoogleTasks(),
        repo or FakeRepo(),
        SyncTokenStorage(tmp_path / "tokens.json"),
        queue or FakeQueue(),
        appdata=appdata,
    )


def _unscheduled_task(task_id=1, **extra) -> Task:
    return Task(id=task_id, title=f"Undated {task_id}", start=None, **extra)


def _scheduled_task(task_id=2, **extra) -> Task:
    return Task(
        id=task_id,
        title=f"Scheduled {task_id}",
        start=NOW,
        duration_minutes=30,
        **extra,
    )


def _make_session_factory():
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)

    def factory():
        return Session(engine)

    return factory


def _make_undated_sync(session_factory, bridge, appdata):
    return UndatedTasksSync(
        auth=None,
        bridge=bridge,
        session_factory=session_factory,
        appdata=appdata,
        device_id="TEST-DEVICE",
    )


def _create_db_task(session_factory, *, title="Test", start=None):
    with session_factory() as session:
        task = Task(title=title, start=start)
        session.add(task)
        session.commit()
        session.refresh(task)
        return task.id


# ---------------------------------------------------------------------------
# Default mode
# ---------------------------------------------------------------------------

def test_default_mode_remains_legacy(tmp_path):
    assert resolve_undated_engine(env={}) == UNDATED_ENGINE_LEGACY
    field_default = GoogleSyncSettings.__dataclass_fields__["undated_engine"].default
    assert field_default == resolve_undated_engine()

    # With default settings and a vacant shared marker the Google Tasks lane
    # is open for the legacy SyncService.
    service = _make_service(tmp_path, appdata=FakeAppDataConfig(engine=None))
    assert service.tasks_lane_blocked_reason() is None


# ---------------------------------------------------------------------------
# Legacy mode keeps current behavior
# ---------------------------------------------------------------------------

def test_legacy_mode_preserves_tasks_lane_enqueue(tmp_path, monkeypatch):
    _set_sync_service_flag(monkeypatch, UNDATED_ENGINE_LEGACY)
    repo = FakeRepo()
    queue = FakeQueue()
    service = _make_service(tmp_path, repo=repo, queue=queue)

    repo.add(_unscheduled_task(1))
    service.on_task_created(1)
    assert ("gtasks_create", 1, {}) in queue.enqueued

    repo.add(_unscheduled_task(3, gtasks_id="gt-3"))
    service.on_task_updated(3)
    assert ("gtasks_update", 3, {"taskId": "gt-3"}) in queue.enqueued

    # Scheduled task previously living in Google Tasks: cross-lane cleanup.
    repo.add(_scheduled_task(2, gtasks_id="gt-2"))
    service.on_task_updated(2)
    assert ("gtasks_delete", 2, {"taskId": "gt-2"}) in queue.enqueued
    assert ("gcal_create", 2, {}) in queue.enqueued

    repo.add(_unscheduled_task(4, gtasks_id="gt-4", gcal_event_id="ev-4"))
    service.on_task_deleted(4)
    assert ("gtasks_delete", 4, {"taskId": "gt-4"}) in queue.enqueued
    assert ("gcal_delete", 4, {"eventId": "ev-4"}) in queue.enqueued


def test_legacy_mode_pulls_and_pushes_google_tasks(tmp_path, monkeypatch):
    _set_sync_service_flag(monkeypatch, UNDATED_ENGINE_LEGACY)
    repo = FakeRepo()
    queue = FakeQueue()
    gtasks = FakeGoogleTasks(
        items=[{"id": "gt-remote", "title": "Remote", "updated": "2026-07-01T10:00:00Z"}]
    )
    service = _make_service(tmp_path, repo=repo, queue=queue, gtasks=gtasks)

    assert service.pull_all() is True
    assert gtasks.list_calls, "legacy mode must pull Google Tasks"
    assert any(f.get("gtasks_id") == "gt-remote" for f in repo.created)

    repo.add(_unscheduled_task(1))
    queue.enqueue("gtasks_create", 1, {})
    processed = service.push_queue_worker()
    assert processed == 1
    assert gtasks.inserted, "legacy mode must push Google Tasks ops"
    assert queue.entries == []


def test_legacy_mode_with_vacant_marker_is_unchanged_and_cached(tmp_path, monkeypatch):
    _set_sync_service_flag(monkeypatch, UNDATED_ENGINE_LEGACY)
    appdata = FakeAppDataConfig(engine=None)
    repo = FakeRepo()
    queue = FakeQueue()
    service = _make_service(tmp_path, appdata=appdata, repo=repo, queue=queue)

    repo.add(_unscheduled_task(1))
    service.on_task_created(1)
    service.on_task_updated(1)
    assert queue.enqueued_ops() == ["gtasks_create", "gtasks_create"]
    # The marker is read once and cached for the TTL, not per event.
    assert appdata.read_calls == 1


def test_marker_read_failure_keeps_legacy_lane_open(tmp_path, monkeypatch):
    """An unreadable marker must never break the current legacy behavior."""
    _set_sync_service_flag(monkeypatch, UNDATED_ENGINE_LEGACY)
    appdata = FakeAppDataConfig(fail=True)
    repo = FakeRepo()
    queue = FakeQueue()
    service = _make_service(tmp_path, appdata=appdata, repo=repo, queue=queue)

    repo.add(_unscheduled_task(1))
    service.on_task_created(1)
    assert ("gtasks_create", 1, {}) in queue.enqueued


# ---------------------------------------------------------------------------
# Undated mode: SyncService refuses the Google Tasks lane
# ---------------------------------------------------------------------------

def test_undated_mode_does_not_enqueue_gtask_ops(tmp_path, monkeypatch):
    _set_sync_service_flag(monkeypatch, UNDATED_ENGINE_UNDATED)
    repo = FakeRepo()
    queue = FakeQueue()
    service = _make_service(tmp_path, repo=repo, queue=queue)

    repo.add(_unscheduled_task(1))
    service.on_task_created(1)
    service.on_task_updated(1)

    repo.add(_scheduled_task(2, gtasks_id="gt-2"))
    service.on_task_updated(2)

    repo.add(_unscheduled_task(3, gtasks_id="gt-3", gcal_event_id="ev-3"))
    service.on_task_deleted(3)

    gtask_ops = [op for op in queue.enqueued_ops() if op.startswith("gtasks_")]
    assert gtask_ops == [], "no gtasks_* op may be enqueued in undated mode"
    # The Calendar lane keeps its ops, including the deletion cleanup.
    assert ("gcal_create", 2, {}) in queue.enqueued
    assert ("gcal_delete", 3, {"eventId": "ev-3"}) in queue.enqueued


def test_undated_mode_does_not_process_pending_gtask_ops(tmp_path, monkeypatch):
    _set_sync_service_flag(monkeypatch, UNDATED_ENGINE_UNDATED)
    repo = FakeRepo()
    queue = FakeQueue()
    gtasks = FakeGoogleTasks()
    gcal = FakeGoogleCalendar()
    service = _make_service(tmp_path, repo=repo, queue=queue, gtasks=gtasks, gcal=gcal)

    repo.add(_unscheduled_task(1, gtasks_id="gt-1"))
    repo.add(_scheduled_task(2, gcal_event_id="ev-2"))
    # Stale ops from before the cutover plus a live Calendar op.
    queue.enqueue("gtasks_create", 1, {})
    queue.enqueue("gtasks_update", 1, {"taskId": "gt-1"})
    queue.enqueue("gtasks_delete", 1, {"taskId": "gt-1"})
    queue.enqueue("gcal_update", 2, {"eventId": "ev-2"})

    processed = service.push_queue_worker()

    assert not gtasks.touched(), "no Google Tasks API call may happen"
    # gtask ops are refused and kept queued (rollback-safe), not executed.
    assert len(queue.requeued) == 3
    assert all("undated" in reason.lower() for _id, reason in queue.requeued)
    # The Calendar op still went through.
    assert processed == 1
    assert gcal.service.patched
    assert [e.op for e in queue.entries] == ["gtasks_create", "gtasks_update", "gtasks_delete"]


def test_undated_mode_skips_tasks_pull_but_pulls_calendar(tmp_path, monkeypatch):
    _set_sync_service_flag(monkeypatch, UNDATED_ENGINE_UNDATED)
    gtasks = FakeGoogleTasks(items=[{"id": "gt-x", "title": "X"}])
    gcal = FakeGoogleCalendar()
    service = _make_service(tmp_path, gtasks=gtasks, gcal=gcal)

    service.pull_all()

    assert not gtasks.touched(), "undated mode must not pull Google Tasks"
    assert gcal.connect_calls == 1
    assert gcal.service.list_calls, "Calendar pull must remain active"


def test_scheduled_calendar_sync_remains_active_in_undated_mode(tmp_path, monkeypatch):
    _set_sync_service_flag(monkeypatch, UNDATED_ENGINE_UNDATED)
    repo = FakeRepo()
    queue = FakeQueue()
    gcal = FakeGoogleCalendar()
    service = _make_service(tmp_path, repo=repo, queue=queue, gcal=gcal)

    repo.add(_scheduled_task(2, gcal_event_id="ev-2"))
    service.on_task_updated(2)
    assert ("gcal_update", 2, {"eventId": "ev-2"}) in queue.enqueued

    processed = service.push_queue_worker()
    assert processed == 1
    assert gcal.service.patched, "Calendar push must remain active"


# ---------------------------------------------------------------------------
# Legacy-side tripwire: shared marker names the undated engine
# ---------------------------------------------------------------------------

def test_legacy_refuses_tasks_writes_when_marker_is_undated(tmp_path, monkeypatch, caplog):
    _set_sync_service_flag(monkeypatch, UNDATED_ENGINE_LEGACY)
    appdata = FakeAppDataConfig(engine=UNDATED_ENGINE_UNDATED)
    repo = FakeRepo()
    queue = FakeQueue()
    gtasks = FakeGoogleTasks(items=[{"id": "gt-x", "title": "X"}])
    gcal = FakeGoogleCalendar()
    service = _make_service(
        tmp_path, appdata=appdata, repo=repo, queue=queue, gtasks=gtasks, gcal=gcal
    )

    with caplog.at_level(logging.INFO, logger="planner.sync"):
        # No gtasks_* op is enqueued for local changes...
        repo.add(_unscheduled_task(1, gtasks_id="gt-1"))
        service.on_task_updated(1)
        repo.add(_scheduled_task(2, gtasks_id="gt-2"))
        service.on_task_updated(2)
        repo.add(_unscheduled_task(3, gtasks_id="gt-3"))
        service.on_task_deleted(3)
        assert not any(op.startswith("gtasks_") for op in queue.enqueued_ops())
        # ...while the Calendar lane keeps enqueueing normally.
        assert ("gcal_create", 2, {}) in queue.enqueued

        # A stale queued gtasks op is refused, not executed...
        queue.enqueue("gtasks_update", 1, {"taskId": "gt-1"})
        service.push_queue_worker()
        assert not gtasks.touched()
        assert any(
            op_id for op_id, reason in queue.requeued
            if "planner_config.json" in reason
        )

        # ...and the Tasks pull is skipped while Calendar still syncs.
        service.pull_all()
        assert not gtasks.touched()
        assert gcal.service.list_calls

    # A clear reason is reported.
    assert any(
        "planner_config.json" in message and "refuses" in message
        for message in caplog.messages
    )


def test_marker_legacy_or_vacant_does_not_trip(tmp_path, monkeypatch):
    _set_sync_service_flag(monkeypatch, UNDATED_ENGINE_LEGACY)
    for marker in (None, UNDATED_ENGINE_LEGACY):
        service = _make_service(tmp_path, appdata=FakeAppDataConfig(engine=marker))
        assert service.tasks_lane_blocked_reason() is None


# ---------------------------------------------------------------------------
# Undated mode: TaskService events route into UndatedTasksSync
# ---------------------------------------------------------------------------

def test_router_created_unscheduled_marks_dirty(monkeypatch):
    _set_undated_flag(monkeypatch, UNDATED_ENGINE_UNDATED)
    session_factory = _make_session_factory()
    sync = _make_undated_sync(session_factory, FakeBridge(), FakeAppData())
    task_id = _create_db_task(session_factory)

    sync.on_task_created(task_id)

    with session_factory() as session:
        mapping = session.get(SyncMapUndated, str(task_id))
        assert mapping is not None
        assert mapping.dirty_flag == 1


def test_router_update_unscheduled_marks_dirty(monkeypatch):
    _set_undated_flag(monkeypatch, UNDATED_ENGINE_UNDATED)
    session_factory = _make_session_factory()
    bridge = FakeBridge()
    sync = _make_undated_sync(session_factory, bridge, FakeAppData())
    task_id = _create_db_task(session_factory)

    assert sync.sync() is True  # pushed, mapping is clean now
    with session_factory() as session:
        assert session.get(SyncMapUndated, str(task_id)).dirty_flag == 0

    sync.on_task_updated(task_id)
    with session_factory() as session:
        assert session.get(SyncMapUndated, str(task_id)).dirty_flag == 1


def test_router_unscheduled_to_scheduled_releases_inbox(monkeypatch):
    _set_undated_flag(monkeypatch, UNDATED_ENGINE_UNDATED)
    session_factory = _make_session_factory()
    bridge = FakeBridge()
    appdata = FakeAppData()
    sync = _make_undated_sync(session_factory, bridge, appdata)
    task_id = _create_db_task(session_factory)

    assert sync.sync() is True
    with session_factory() as session:
        gtask_id = session.get(SyncMapUndated, str(task_id)).gtask_id
    assert gtask_id

    with session_factory() as session:
        task = session.get(Task, task_id)
        task.start = NOW
        task.duration_minutes = 30
        session.add(task)
        session.commit()

    sync.on_task_updated(task_id)

    with session_factory() as session:
        assert session.get(SyncMapUndated, str(task_id)) is None
    assert ("list-1", gtask_id) in bridge.deleted
    entry = appdata.index["tasks"][gtask_id]
    assert entry["deleted"] is True
    assert entry["reason"] == "scheduled"


def test_router_scheduled_to_unscheduled_arrives_at_engine(monkeypatch):
    _set_undated_flag(monkeypatch, UNDATED_ENGINE_UNDATED)
    session_factory = _make_session_factory()
    bridge = FakeBridge()
    sync = _make_undated_sync(session_factory, bridge, FakeAppData())
    task_id = _create_db_task(session_factory, start=NOW)

    # While scheduled the engine holds nothing for this task and no-ops.
    sync.on_task_created(task_id)
    with session_factory() as session:
        assert session.get(SyncMapUndated, str(task_id)) is None
    assert bridge.deleted == []

    with session_factory() as session:
        task = session.get(Task, task_id)
        task.start = None
        session.add(task)
        session.commit()

    sync.on_task_updated(task_id)
    with session_factory() as session:
        mapping = session.get(SyncMapUndated, str(task_id))
        assert mapping is not None
        assert mapping.dirty_flag == 1


def test_router_deleted_unscheduled_propagates(monkeypatch):
    _set_undated_flag(monkeypatch, UNDATED_ENGINE_UNDATED)
    session_factory = _make_session_factory()
    bridge = FakeBridge()
    appdata = FakeAppData()
    sync = _make_undated_sync(session_factory, bridge, appdata)
    task_id = _create_db_task(session_factory)

    assert sync.sync() is True
    with session_factory() as session:
        gtask_id = session.get(SyncMapUndated, str(task_id)).gtask_id

    with session_factory() as session:
        task = session.get(Task, task_id)
        session.delete(task)
        session.commit()

    sync.on_task_deleted(task_id)

    with session_factory() as session:
        assert session.get(SyncMapUndated, str(task_id)) is None
    assert ("list-1", gtask_id) in bridge.deleted
    entry = appdata.index["tasks"][gtask_id]
    assert entry["deleted"] is True
    assert entry["reason"] == "deleted"


def test_router_is_inert_in_legacy_mode(monkeypatch):
    _set_undated_flag(monkeypatch, UNDATED_ENGINE_LEGACY)
    session_factory = _make_session_factory()
    bridge = FakeBridge()
    appdata = FakeAppData()
    sync = _make_undated_sync(session_factory, bridge, appdata)
    task_id = _create_db_task(session_factory)

    sync.on_task_created(task_id)
    sync.on_task_updated(task_id)
    sync.on_task_deleted(task_id)

    with session_factory() as session:
        assert session.exec(select(SyncMapUndated)).all() == []
    assert bridge.inserted == []
    assert bridge.deleted == []
    assert appdata.config_etag == "cfg-0"
    assert appdata.index_etag == "idx-0"
