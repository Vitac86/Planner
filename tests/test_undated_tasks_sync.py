import json
from datetime import datetime

import pytest
from sqlmodel import SQLModel, Session, create_engine

from models import SyncMapUndated, Task
from services.tasks_bridge import _split_notes, _status_payload
from services.undated_tasks_sync import UndatedTasksSync
from storage import config as config_module
from storage.store import MetadataStore, init_store


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


@pytest.fixture()
def metadata_store():
    engine = create_engine("sqlite:///:memory:")
    init_store(engine=engine)

    def factory():
        return Session(engine)

    return MetadataStore(session_factory=factory)


@pytest.fixture(autouse=True)
def config_path(tmp_path, monkeypatch):
    cfg_path = tmp_path / "config.json"
    monkeypatch.setattr(config_module, "CONFIG_PATH", cfg_path)
    return cfg_path


def _create_task(session_factory):
    with session_factory() as session:
        task = Task(title="Test", start=None)
        session.add(task)
        session.commit()
        session.refresh(task)
        return task.id


def test_push_dirty_creates_mapping(session_factory, metadata_store):
    task_id = _create_task(session_factory)

    bridge = FakeBridge()
    sync = UndatedTasksSync(
        auth=None,
        bridge=bridge,
        session_factory=session_factory,
        metadata_store=metadata_store,
    )

    assert sync.push_dirty() is True

    with session_factory() as session:
        mapping = session.get(SyncMapUndated, str(task_id))
        assert mapping is not None
        assert mapping.gtask_id == "gtask-123"
        assert mapping.dirty_flag == 0

    stored_meta = metadata_store.load_task_meta("gtask-123", "list-1")
    assert stored_meta.get("task_id") == str(task_id)


def test_split_notes_extracts_metadata_and_body():
    meta = {"task_id": "42", "status": "todo"}
    body = "Hello\nWorld"
    combined = json.dumps(meta) + "\n\n" + body
    parsed_meta, parsed_body, had_meta = _split_notes(combined)
    assert parsed_meta == meta
    assert parsed_body == body
    assert had_meta is True


def test_status_done_completes_on_push_and_pull(session_factory, metadata_store):
    status, completed_at = _status_payload({"status": "done", "updated_at": datetime.utcnow()})
    assert status == "completed"
    assert completed_at is not None

    task_id = _create_task(session_factory)

    remote_item = {
        "id": "gtask-remote",
        "title": "Remote",
        "notes": "Updated",
        "metadata": {"task_id": str(task_id)},
        "status": "completed",
        "updated": datetime.utcnow().isoformat(),
    }

    bridge = FakeBridge(items=[remote_item])
    sync = UndatedTasksSync(
        auth=None,
        bridge=bridge,
        session_factory=session_factory,
        metadata_store=metadata_store,
    )

    assert sync.pull() is True

    with session_factory() as session:
        updated_task = session.get(Task, task_id)
        assert updated_task.status == "done"
        assert (updated_task.notes or "") == "Updated"
