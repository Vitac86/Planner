import json
from datetime import datetime, timedelta, timezone
from typing import Dict, Tuple

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from core.priorities import DEFAULT_PRIORITY
from models import SyncMapUndated, Task
from services.appdata import AppDataClient
from services.tasks_bridge import _split_notes, _status_payload
from services.undated_tasks_sync import (
    TOMBSTONE_REASON_DELETED,
    UndatedTasksSync,
)


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
        payload = json.loads(json.dumps(data))
        if if_match and if_match != self.config_etag and on_conflict:
            # Stale etag: emulate the 412-merge path of the real client.
            payload = json.loads(
                json.dumps(on_conflict(json.loads(json.dumps(self.config))))
            )
        self.config = payload
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
        if if_match and if_match != self.index_etag and on_conflict:
            payload = json.loads(
                json.dumps(on_conflict(json.loads(json.dumps(self.index))))
            )
        self.index = payload
        major = int(self.index_etag.split("-")[1]) + 1
        self.index_etag = f"idx-{major}"
        return json.loads(json.dumps(self.index)), self.index_etag


class FakeBridge:
    """Stateful in-memory stand-in for :class:`GoogleTasksBridge`."""

    tasklist_title = "Planner Inbox"

    def __init__(self, items=None):
        self.tasks: Dict[str, dict] = {}
        self.inserted: list[tuple[str, dict]] = []
        self.deleted: list[tuple[str, str]] = []
        self._counter = 0
        for item in items or []:
            entry = dict(item)
            entry.setdefault("metadata", entry.get("detected_meta") or {})
            entry.setdefault("detected_meta", entry.get("metadata") or {})
            entry.setdefault("deleted", False)
            self.tasks[entry["id"]] = entry

    def ensure_tasklist(self):
        return "list-1"

    def fetch_all(self, tasklist_id):
        return [dict(item) for item in self.tasks.values()]

    def upsert_task(self, tasklist_id, local_task):
        self.inserted.append((tasklist_id, dict(local_task)))
        gtask_id = local_task.get("gtask_id")
        if not gtask_id:
            # Mirror the real bridge: dedupe by uid found in planner metadata.
            uid = local_task.get("uid") or local_task.get("task_uid")
            for gid, item in self.tasks.items():
                if item.get("deleted"):
                    continue
                meta = item.get("metadata") or {}
                candidate = meta.get("uid") or meta.get("task_uid")
                if uid and candidate and str(candidate) == str(uid):
                    gtask_id = gid
                    break
        if not gtask_id:
            self._counter += 1
            gtask_id = f"gtask-{self._counter}"
        existing = self.tasks.get(gtask_id) or {}
        existing.update(
            {
                "id": gtask_id,
                "title": local_task.get("title") or "",
                "notes": (local_task.get("notes") or "").strip(),
                "status": "completed"
                if local_task.get("status") == "done"
                else "needsAction",
                "updated": datetime.now(timezone.utc).isoformat(),
                "deleted": False,
            }
        )
        existing.setdefault("metadata", {})
        existing.setdefault("detected_meta", {})
        self.tasks[gtask_id] = existing
        return gtask_id

    def delete_task(self, tasklist_id, gtask_id):
        self.deleted.append((tasklist_id, gtask_id))
        self.tasks.pop(gtask_id, None)


def _make_session_factory():
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)

    def factory():
        return Session(engine)

    return factory


@pytest.fixture()
def session_factory():
    return _make_session_factory()


def _make_sync(session_factory, bridge, appdata, device_id="TEST-DEVICE"):
    return UndatedTasksSync(
        auth=None,
        bridge=bridge,
        session_factory=session_factory,
        appdata=appdata,
        device_id=device_id,
    )


def _create_task(session_factory, title="Test", **kwargs):
    with session_factory() as session:
        task = Task(title=title, start=None, **kwargs)
        session.add(task)
        session.commit()
        session.refresh(task)
        return task.id, task.uid


def test_push_dirty_creates_mapping_and_updates_index(session_factory):
    task_id, task_uid = _create_task(session_factory)

    bridge = FakeBridge()
    appdata = FakeAppData()
    sync = _make_sync(session_factory, bridge, appdata)

    assert sync.push_dirty() is True

    with session_factory() as session:
        mapping = session.get(SyncMapUndated, str(task_id))
        assert mapping is not None
        assert mapping.gtask_id == "gtask-1"
        assert mapping.task_uid == task_uid
        assert mapping.dirty_flag == 0

    entry = appdata.index["tasks"].get("gtask-1")
    assert entry is not None
    assert entry["task_uid"] == task_uid
    assert "task_id" not in entry
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

    task_id, task_uid = _create_task(session_factory)

    with session_factory() as session:
        mapping = SyncMapUndated(
            task_id=str(task_id),
            task_uid=task_uid,
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
        "task_uid": task_uid,
        "priority": DEFAULT_PRIORITY,
        "status": "todo",
        "updated_at": datetime.utcnow().isoformat(),
        "device_id": "OTHER",
    }

    sync = _make_sync(session_factory, bridge, appdata, device_id="LOCAL")

    assert sync.pull() is True

    with session_factory() as session:
        updated_task = session.get(Task, task_id)
        assert updated_task.status == "done"
        assert (updated_task.notes or "") == "Updated"


def test_two_devices_with_colliding_local_ids_do_not_corrupt_each_other():
    bridge = FakeBridge()
    appdata = FakeAppData()

    factory_a = _make_session_factory()
    factory_b = _make_session_factory()

    a_id, a_uid = _create_task(factory_a, title="Alpha")
    b_id, b_uid = _create_task(factory_b, title="Bravo")
    # The dangerous precondition: both devices use the same local integer id.
    assert a_id == b_id

    sync_a = _make_sync(factory_a, bridge, appdata, device_id="DEV-A")
    sync_b = _make_sync(factory_b, bridge, appdata, device_id="DEV-B")

    assert sync_a.sync() is True
    assert sync_b.sync() is True

    # Device B keeps its own task untouched and receives Alpha as a new row
    # that adopts the shared uid.
    with factory_b() as session:
        tasks = session.exec(select(Task)).all()
        by_uid = {t.uid: t for t in tasks}
        assert len(tasks) == 2
        assert by_uid[b_uid].title == "Bravo"
        assert by_uid[a_uid].title == "Alpha"
        assert by_uid[a_uid].id != by_uid[b_uid].id

    # Device A picks up Bravo on its next cycle without touching Alpha.
    sync_a.reset_cache()
    assert sync_a.sync() is True
    with factory_a() as session:
        tasks = session.exec(select(Task)).all()
        by_uid = {t.uid: t for t in tasks}
        assert len(tasks) == 2
        assert by_uid[a_uid].title == "Alpha"
        assert by_uid[b_uid].title == "Bravo"

    # The shared index identifies tasks by uid only — never by local id.
    assert len(appdata.index["tasks"]) == 2
    for entry in appdata.index["tasks"].values():
        assert entry["task_uid"] in {a_uid, b_uid}
        assert "task_id" not in entry


def test_pull_dedupes_remote_task_with_uid_metadata(session_factory):
    task_id, task_uid = _create_task(session_factory, title="Local")

    remote_item = {
        "id": "gtask-remote",
        "title": "Local",
        "notes": "",
        "status": "needsAction",
        "updated": datetime.now(timezone.utc).isoformat(),
        "metadata": {"uid": task_uid},
    }
    bridge = FakeBridge(items=[remote_item])
    appdata = FakeAppData()
    sync = _make_sync(session_factory, bridge, appdata, device_id="LOCAL")

    assert sync.pull() is True

    with session_factory() as session:
        tasks = session.exec(select(Task)).all()
        assert len(tasks) == 1  # no duplicate local task
        mapping = session.get(SyncMapUndated, str(task_id))
        assert mapping is not None
        assert mapping.gtask_id == "gtask-remote"
        assert mapping.task_uid == task_uid

    entry = appdata.index["tasks"]["gtask-remote"]
    assert entry["task_uid"] == task_uid


def test_push_adopts_index_entry_with_matching_uid(session_factory):
    task_id, task_uid = _create_task(session_factory, title="Adopt me")

    bridge = FakeBridge(
        items=[
            {
                "id": "gtask-existing",
                "title": "Adopt me",
                "notes": "",
                "status": "needsAction",
                "updated": datetime.now(timezone.utc).isoformat(),
            }
        ]
    )
    appdata = FakeAppData()
    appdata.index["tasks"]["gtask-existing"] = {
        "task_uid": task_uid,
        "priority": DEFAULT_PRIORITY,
        "status": "todo",
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "device_id": "OTHER",
    }

    sync = _make_sync(session_factory, bridge, appdata, device_id="LOCAL")
    assert sync.push_dirty() is True

    with session_factory() as session:
        mapping = session.get(SyncMapUndated, str(task_id))
        assert mapping is not None
        assert mapping.gtask_id == "gtask-existing"

    # No second remote task was created despite the lost mapping.
    assert set(bridge.tasks.keys()) == {"gtask-existing"}


def test_on_task_deleted_tombstones_and_deletes_remote(session_factory):
    task_id, task_uid = _create_task(session_factory)

    bridge = FakeBridge()
    appdata = FakeAppData()
    sync = _make_sync(session_factory, bridge, appdata, device_id="LOCAL")
    assert sync.push_dirty() is True

    with session_factory() as session:
        task = session.get(Task, task_id)
        session.delete(task)
        session.commit()

    sync.on_task_deleted(task_id)

    assert ("list-1", "gtask-1") in bridge.deleted
    assert "gtask-1" not in bridge.tasks
    entry = appdata.index["tasks"]["gtask-1"]
    assert entry["deleted"] is True
    assert entry["reason"] == TOMBSTONE_REASON_DELETED
    assert entry["task_uid"] == task_uid
    with session_factory() as session:
        assert session.get(SyncMapUndated, str(task_id)) is None

    # Idempotent: repeating the hook changes nothing.
    deletes_before = list(bridge.deleted)
    sync.on_task_deleted(task_id)
    assert bridge.deleted == deletes_before


def test_push_propagates_local_deletion_without_hook(session_factory):
    task_id, task_uid = _create_task(session_factory)

    bridge = FakeBridge()
    appdata = FakeAppData()
    sync = _make_sync(session_factory, bridge, appdata, device_id="LOCAL")
    assert sync.push_dirty() is True

    with session_factory() as session:
        task = session.get(Task, task_id)
        session.delete(task)
        session.commit()

    assert sync.push_dirty() is True

    assert "gtask-1" not in bridge.tasks
    entry = appdata.index["tasks"]["gtask-1"]
    assert entry["deleted"] is True
    assert entry["task_uid"] == task_uid
    with session_factory() as session:
        assert session.get(SyncMapUndated, str(task_id)) is None

    # Idempotent: a further push finds nothing to do.
    assert sync.push_dirty() is False


def test_remote_deletion_removes_clean_local_task(session_factory):
    task_id, task_uid = _create_task(session_factory)

    bridge = FakeBridge()
    appdata = FakeAppData()
    sync = _make_sync(session_factory, bridge, appdata, device_id="LOCAL")
    assert sync.push_dirty() is True

    # The task disappears from Google Tasks (deleted in the Google UI).
    bridge.tasks.pop("gtask-1")

    assert sync.pull() is True

    with session_factory() as session:
        assert session.get(Task, task_id) is None
        assert session.get(SyncMapUndated, str(task_id)) is None

    entry = appdata.index["tasks"]["gtask-1"]
    assert entry["deleted"] is True
    assert entry["task_uid"] == task_uid

    # Idempotent: a second pull changes nothing and does not crash.
    assert sync.pull() is False


def test_remote_deletion_keeps_dirty_local_task_and_repushes(session_factory):
    task_id, task_uid = _create_task(session_factory)

    bridge = FakeBridge()
    appdata = FakeAppData()
    sync = _make_sync(session_factory, bridge, appdata, device_id="LOCAL")
    assert sync.push_dirty() is True

    # Unsynced local edit, then the remote task is deleted elsewhere.
    with session_factory() as session:
        task = session.get(Task, task_id)
        task.title = "Edited offline"
        session.add(task)
        session.commit()
    sync.mark_dirty(task_id)
    bridge.tasks.pop("gtask-1")

    assert sync.pull() is True

    with session_factory() as session:
        task = session.get(Task, task_id)
        assert task is not None  # edits win over deletion
        assert task.title == "Edited offline"
        mapping = session.get(SyncMapUndated, str(task_id))
        assert mapping is not None
        assert mapping.gtask_id is None
        assert mapping.dirty_flag == 1

    # The next push recreates the remote task under a new id.
    assert sync.push_dirty() is True
    with session_factory() as session:
        mapping = session.get(SyncMapUndated, str(task_id))
        assert mapping.gtask_id == "gtask-2"
    assert "gtask-2" in bridge.tasks
    assert appdata.index["tasks"]["gtask-2"]["task_uid"] == task_uid
    # The old entry stays as a tombstone for the dead Google Task.
    assert appdata.index["tasks"]["gtask-1"]["deleted"] is True


def test_pull_finishes_recorded_deletion_when_remote_survives(session_factory):
    task_id, task_uid = _create_task(session_factory)

    bridge = FakeBridge()
    appdata = FakeAppData()
    sync = _make_sync(session_factory, bridge, appdata, device_id="LOCAL")
    assert sync.push_dirty() is True

    # Another device tombstoned the entry but failed to delete the remote
    # task. The tombstone is newer than the last remote modification.
    appdata.index["tasks"]["gtask-1"] = {
        "task_uid": task_uid,
        "priority": DEFAULT_PRIORITY,
        "status": "todo",
        "deleted": True,
        "reason": TOMBSTONE_REASON_DELETED,
        "updated_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        "device_id": "OTHER",
    }
    sync.reset_cache()

    assert sync.pull() is True

    with session_factory() as session:
        assert session.get(Task, task_id) is None
        assert session.get(SyncMapUndated, str(task_id)) is None
    assert ("list-1", "gtask-1") in bridge.deleted
    assert "gtask-1" not in bridge.tasks


def test_malformed_remote_metadata_is_skipped_deterministically(session_factory):
    now_iso = datetime.now(timezone.utc).isoformat()
    good = {
        "id": "gtask-good",
        "title": "Good",
        "notes": "",
        "status": "needsAction",
        "updated": now_iso,
        "metadata": {},
    }
    bad = {
        "id": "gtask-bad",
        "title": "Bad",
        "notes": "",
        "status": "needsAction",
        "updated": now_iso,
        "metadata": "not-a-dict",
        "detected_meta": "not-a-dict",
    }
    bridge = FakeBridge(items=[good, bad])
    appdata = FakeAppData()
    sync = _make_sync(session_factory, bridge, appdata, device_id="LOCAL")

    # The malformed item does not crash the pull; the good one is applied.
    assert sync.pull() is True

    with session_factory() as session:
        tasks = session.exec(select(Task)).all()
        assert [t.title for t in tasks] == ["Good"]

    skipped = sync.last_report.skipped
    assert len(skipped) == 1
    assert skipped[0].stage == "pull"
    assert skipped[0].gtask_id == "gtask-bad"
    assert "metadata" in skipped[0].reason

    # Deterministic: the same item is reported the same way on every cycle.
    sync.pull()
    skipped_again = sync.last_report.skipped
    assert len(skipped_again) == 1
    assert skipped_again[0].gtask_id == "gtask-bad"
    assert skipped_again[0].reason == skipped[0].reason
