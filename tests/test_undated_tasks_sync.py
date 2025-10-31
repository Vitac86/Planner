import json
from datetime import datetime
from typing import Dict, Tuple

import pytest
from sqlmodel import Session, SQLModel, create_engine

from core.priorities import DEFAULT_PRIORITY
from models import SyncMapUndated, Task
from services.appdata import AppDataClient
from services.tasks_bridge import _split_notes, _status_payload
from services.undated_tasks_sync import UndatedTasksSync


class FakeAppData(AppDataClient):  # type: ignore[misc]
    """In-memory stub of :class:`AppDataClient` for unit tests."""

    def __init__(self):
        # ``AppDataClient`` expects an ``auth`` object but we don't need one here.
        super().__init__(auth=None)
        self.config = {"version": 1, "tasklist_id": None, "last_full_sync": None}
        self.index = {"version": 1, "tasklist_id": None, "tasks": {}}
        self.config_etag = "cfg-0"
        self.index_etag = "idx-0"
        self.ensure_calls = 0

    # ``AppDataClient`` normally builds Drive services. Our stub skips that part.
    def ensure_files(self) -> Dict[str, str]:  # type: ignore[override]
        self.ensure_calls += 1
        return {self.CONFIG_NAME: "config", self.INDEX_NAME: "index"}

    def read_config(self) -> Tuple[Dict[str, object], str]:  # type: ignore[override]
        return (json.loads(json.dumps(self.config)), self.config_etag)

    def write_config(  # type: ignore[override]
        self,
        data,
        if_match=None,
        *,
        on_conflict=None,
    ):
        self.config = json.loads(json.dumps(data))
        major = int(self.config_etag.split("-")[1]) + 1
        self.config_etag = f"cfg-{major}"
        return json.loads(json.dumps(self.config)), self.config_etag

    def read_index(self) -> Tuple[Dict[str, object], str]:  # type: ignore[override]
        return (json.loads(json.dumps(self.index)), self.index_etag)

    def write_index(  # type: ignore[override]
        self,
        data,
        if_match=None,
        *,
        on_conflict=None,
    ):
        payload = json.loads(json.dumps(data))
        if on_conflict:
            payload = on_conflict(payload)
        self.index = payload
        major = int(self.index_etag.split("-")[1]) + 1
        self.index_etag = f"idx-{major}"
        return json.loads(json.dumps(self.index)), self.index_etag


class FakeBridge:
    tasklist_title = "Planner Inbox"

    def __init__(self, items=None):
        self.items = items or []
        self.inserted: list[tuple[str, dict]] = []
        self.deleted: list[tuple[str, str]] = []

    def ensure_tasklist(self):
        return "list-1"

    def fetch_all(self, tasklist_id):
        return list(self.items)

    def upsert_task(self, tasklist_id, local_task):
        self.inserted.append((tasklist_id, dict(local_task)))
        return "gtask-123"

    def delete_task(self, tasklist_id, gtask_id):
        self.deleted.append((tasklist_id, gtask_id))


@pytest.fixture()
def session_factory():
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)

    def factory():
        return Session(engine)

    return factory


def _create_task(session_factory):
    with session_factory() as session:
        task = Task(title="Test", start=None)
        session.add(task)
        session.commit()
        session.refresh(task)
        return task.id


def test_push_dirty_creates_mapping_and_updates_index(session_factory):
    task_id = _create_task(session_factory)

    bridge = FakeBridge()
    appdata = FakeAppData()
    sync = UndatedTasksSync(
        auth=None,
        bridge=bridge,
        session_factory=session_factory,
        appdata=appdata,
        device_id="TEST-DEVICE",
    )

    assert sync.push_dirty() is True

    with session_factory() as session:
        mapping = session.get(SyncMapUndated, str(task_id))
        assert mapping is not None
        assert mapping.gtask_id == "gtask-123"
        assert mapping.dirty_flag == 0

    entry = appdata.index["tasks"].get("gtask-123")
    assert entry is not None
    assert entry["task_id"] == str(task_id)
    assert entry["status"] == "todo"
    assert entry["priority"] == DEFAULT_PRIORITY
    assert appdata.config["tasklist_id"] == "list-1"


def test_split_notes_extracts_metadata_and_body():
    meta = {"task_id": "42", "status": "todo"}
    body = "Hello\nWorld"
    combined = json.dumps(meta) + "\n\n" + body
    parsed_meta, parsed_body, had_meta = _split_notes(combined)
    assert parsed_meta == meta
    assert parsed_body == body
    assert had_meta is True


def test_status_done_completes_on_push_and_pull(session_factory):
    status, completed_at = _status_payload({"status": "done", "updated_at": datetime.utcnow()})
    assert status == "completed"
    assert completed_at is not None

    task_id = _create_task(session_factory)

    with session_factory() as session:
        mapping = SyncMapUndated(
            task_id=str(task_id),
            gtask_id="gtask-remote",
            tasklist_id="list-1",
            dirty_flag=0,
        )
        session.add(mapping)
        session.commit()

    remote_item = {
        "id": "gtask-remote",
        "title": "Remote",
        "notes": "Updated",
        "status": "completed",
        "updated": datetime.utcnow().isoformat(),
        "detected_meta": {},
    }

    bridge = FakeBridge(items=[remote_item])
    appdata = FakeAppData()
    appdata.index["tasks"]["gtask-remote"] = {
        "task_id": str(task_id),
        "priority": DEFAULT_PRIORITY,
        "status": "todo",
        "updated_at": datetime.utcnow().isoformat(),
        "device_id": "OTHER",
    }

    sync = UndatedTasksSync(
        auth=None,
        bridge=bridge,
        session_factory=session_factory,
        appdata=appdata,
        device_id="LOCAL",
    )

    assert sync.pull() is True

    with session_factory() as session:
        updated_task = session.get(Task, task_id)
        assert updated_task.status == "done"
        assert (updated_task.notes or "") == "Updated"

