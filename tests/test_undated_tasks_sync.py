from datetime import datetime

from sqlmodel import Session, SQLModel, create_engine

from models import SyncMapUndated, Task
from services.tasks_bridge import _compose_notes, _split_notes, _status_payload
from services.undated_tasks_sync import UndatedTasksSync


class FakeBridge:
    tasklist_title = "Planner Inbox"

    def __init__(self, items=None):
        self.items = items or []
        self.inserted = []
        self.deleted = []

    def ensure_tasklist(self):
        return "list-1"

    def fetch_all(self, tasklist_id):
        return list(self.items)

    def upsert_task(self, tasklist_id, local_task):
        self.inserted.append((tasklist_id, dict(local_task)))
        return "gtask-123"

    def delete_task(self, tasklist_id, gtask_id):
        self.deleted.append((tasklist_id, gtask_id))


def _session_factory():
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)

    def factory():
        return Session(engine)

    return factory


def test_push_dirty_creates_mapping():
    session_factory = _session_factory()
    with session_factory() as session:
        task = Task(title="Test", start=None)
        session.add(task)
        session.commit()
        session.refresh(task)
        task_id = task.id

    bridge = FakeBridge()
    sync = UndatedTasksSync(auth=None, bridge=bridge, session_factory=session_factory)

    assert sync.push_dirty() is True

    with session_factory() as session:
        mapping = session.get(SyncMapUndated, str(task_id))
        assert mapping is not None
        assert mapping.gtask_id == "gtask-123"
        assert mapping.dirty_flag == 0


def test_notes_compose_and_split_roundtrip():
    meta = {"task_id": "42", "status": "todo"}
    body = "Hello\nWorld"
    combined = _compose_notes(meta, body)
    parsed_meta, parsed_body = _split_notes(combined)
    assert parsed_meta == meta
    assert parsed_body == body


def test_status_done_completes_on_push_and_pull():
    status, completed_at = _status_payload({"status": "done", "updated_at": datetime.utcnow()})
    assert status == "completed"
    assert completed_at is not None

    session_factory = _session_factory()
    with session_factory() as session:
        task = Task(title="Remote", start=None, status="todo")
        session.add(task)
        session.commit()
        session.refresh(task)
        task_id = task.id

    remote_item = {
        "id": "gtask-remote",
        "title": "Remote",
        "notes": "Updated",
        "metadata": {"task_id": str(task_id)},
        "status": "completed",
        "updated": datetime.utcnow().isoformat(),
    }

    bridge = FakeBridge(items=[remote_item])
    sync = UndatedTasksSync(auth=None, bridge=bridge, session_factory=session_factory)

    assert sync.pull() is True

    with session_factory() as session:
        updated_task = session.get(Task, task_id)
        assert updated_task.status == "done"
        assert (updated_task.notes or "") == "Updated"
