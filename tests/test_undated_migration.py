"""Phase 3 migration tooling: pre-cutover backup/export and mapping backfill.

Everything here runs against fakes and temp dirs — no real database, no real
Google APIs, no reliance on the PLANNER_UNDATED_ENGINE flag (the tooling must
work while the default engine is still "legacy").
"""
import json
from datetime import datetime
from typing import Dict, Tuple

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from core.settings import UNDATED_ENGINE_LEGACY, resolve_undated_engine
from models import SyncMapUndated, Task
from services.appdata import AppDataClient
from services.undated_migration import (
    backfill_planner_mappings,
    export_planner_inbox_snapshot,
)
from services.undated_tasks_sync import EngineOwnershipError
from storage.backup import create_precutover_backup, ensure_daily_backup


class FakeAppData(AppDataClient):  # type: ignore[misc]
    """In-memory stub of :class:`AppDataClient` with write-call counters."""

    def __init__(self):
        super().__init__(auth=None)
        self.config = {"version": 1, "tasklist_id": None, "last_full_sync": None, "engine": None}
        self.index = {"version": 1, "tasklist_id": None, "tasks": {}}
        self.config_etag = "cfg-0"
        self.index_etag = "idx-0"
        self.write_config_calls = 0
        self.write_index_calls = 0

    def ensure_files(self) -> Dict[str, str]:  # type: ignore[override]
        return {self.CONFIG_NAME: "config", self.INDEX_NAME: "index"}

    def read_config(self) -> Tuple[Dict[str, object], str]:  # type: ignore[override]
        return (json.loads(json.dumps(self.config)), self.config_etag)

    def write_config(self, data, if_match=None, *, on_conflict=None):  # type: ignore[override]
        self.write_config_calls += 1
        payload = json.loads(json.dumps(data))
        if if_match and if_match != self.config_etag and on_conflict:
            payload = json.loads(
                json.dumps(on_conflict(json.loads(json.dumps(self.config))))
            )
        self.config = payload
        major = int(self.config_etag.split("-")[1]) + 1
        self.config_etag = f"cfg-{major}"
        return json.loads(json.dumps(self.config)), self.config_etag

    def read_index(self) -> Tuple[Dict[str, object], str]:  # type: ignore[override]
        return (json.loads(json.dumps(self.index)), self.index_etag)

    def write_index(self, data, if_match=None, *, on_conflict=None):  # type: ignore[override]
        self.write_index_calls += 1
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
    """Read-only remote fixture; records every call for no-write assertions."""

    tasklist_title = "Planner Inbox"

    def __init__(self, items=None):
        self.items = [dict(item) for item in (items or [])]
        self.ensure_calls = 0
        self.fetch_calls = 0
        self.inserted: list = []
        self.deleted: list = []

    def ensure_tasklist(self):
        self.ensure_calls += 1
        return "list-1"

    def fetch_all(self, tasklist_id):
        self.fetch_calls += 1
        return [dict(item) for item in self.items]

    def upsert_task(self, tasklist_id, local_task):  # pragma: no cover - must not run
        self.inserted.append((tasklist_id, dict(local_task)))
        raise AssertionError("migration tooling must never upsert remote tasks")

    def delete_task(self, tasklist_id, gtask_id):  # pragma: no cover - must not run
        self.deleted.append((tasklist_id, gtask_id))
        raise AssertionError("migration tooling must never delete remote tasks")


def _make_session_factory():
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)

    def factory():
        return Session(engine)

    return factory


def _create_task(session_factory, title="Test", **kwargs):
    with session_factory() as session:
        task = Task(title=title, **kwargs)
        session.add(task)
        session.commit()
        session.refresh(task)
        return task.id, task.uid


def _backup_evidence(tmp_path):
    backup = tmp_path / "app_precutover.db"
    backup.write_bytes(b"backup")
    export = tmp_path / "inbox_export.json"
    export.write_text("{}", encoding="utf-8")
    return backup, export


# ---------------------------------------------------------------------------
# Pre-cutover local backup
# ---------------------------------------------------------------------------

def test_precutover_backup_creates_timestamped_copy(tmp_path):
    db = tmp_path / "app.db"
    db.write_bytes(b"sqlite-bytes")
    backups = tmp_path / "backups"

    moment = datetime(2026, 7, 2, 12, 30, 45)
    path = create_precutover_backup(db, backups, now=moment)

    assert path == backups / "app_precutover_2026-07-02_123045.db"
    assert path.exists()
    assert path.read_bytes() == b"sqlite-bytes"


def test_precutover_backup_never_overwrites(tmp_path):
    db = tmp_path / "app.db"
    db.write_bytes(b"first")
    backups = tmp_path / "backups"
    moment = datetime(2026, 7, 2, 12, 30, 45)

    first = create_precutover_backup(db, backups, now=moment)
    db.write_bytes(b"second")
    second = create_precutover_backup(db, backups, now=moment)

    assert first != second
    assert first.read_bytes() == b"first"  # earlier backup untouched
    assert second.read_bytes() == b"second"


def test_precutover_backup_requires_existing_db(tmp_path):
    with pytest.raises(FileNotFoundError):
        create_precutover_backup(tmp_path / "missing.db", tmp_path / "backups")


def test_daily_rotation_never_deletes_precutover_backups(tmp_path):
    db = tmp_path / "app.db"
    db.write_bytes(b"data")
    backups = tmp_path / "backups"

    old = create_precutover_backup(db, backups, now=datetime(2000, 1, 1, 0, 0, 0))
    ensure_daily_backup(db, backups, keep_days=1)

    assert old.exists()


# ---------------------------------------------------------------------------
# Remote Planner Inbox export
# ---------------------------------------------------------------------------

def _remote_items():
    return [
        {
            "id": "g-live",
            "title": "Live",
            "notes": "",
            "metadata": {"uid": "uid-live"},
            "detected_meta": {"uid": "uid-live"},
            "status": "needsAction",
            "updated": "2026-07-01T10:00:00+00:00",
            "deleted": False,
        },
        {
            "id": "g-hidden",
            "title": "Hidden done",
            "notes": "",
            "metadata": {},
            "detected_meta": {},
            "status": "completed",
            "updated": "2026-07-01T09:00:00+00:00",
            "deleted": False,
        },
        {
            "id": "g-deleted",
            "title": "Gone",
            "notes": "",
            "metadata": {},
            "detected_meta": {},
            "status": "needsAction",
            "updated": "2026-06-30T09:00:00+00:00",
            "deleted": True,
        },
    ]


def test_export_payload_contains_config_index_and_remote_tasks(tmp_path):
    appdata = FakeAppData()
    appdata.config["tasklist_id"] = "list-1"
    appdata.index["tasks"]["g-live"] = {"task_uid": "uid-live", "status": "todo"}
    bridge = FakeBridge(items=_remote_items())

    target = tmp_path / "export" / "inbox.json"
    payload = export_planner_inbox_snapshot(bridge=bridge, appdata=appdata, path=target)

    assert payload["tasklist"] == {"id": "list-1", "title": "Planner Inbox"}
    assert payload["planner_config"]["tasklist_id"] == "list-1"
    assert payload["planner_index"]["tasks"]["g-live"]["task_uid"] == "uid-live"
    assert payload["exported_at"]

    ids = {item["id"] for item in payload["tasks"]}
    assert ids == {"g-live", "g-hidden", "g-deleted"}  # hidden + deleted included
    by_id = {item["id"]: item for item in payload["tasks"]}
    assert by_id["g-deleted"]["deleted"] is True
    assert by_id["g-live"]["metadata"] == {"uid": "uid-live"}  # parsed planner meta

    # The snapshot on disk equals the returned payload.
    assert json.loads(target.read_text(encoding="utf-8")) == payload
    # Read-only: the export never creates the tasklist or writes appData.
    assert bridge.ensure_calls == 0
    assert appdata.write_config_calls == 0
    assert appdata.write_index_calls == 0


def test_export_refuses_to_overwrite_existing_snapshot(tmp_path):
    appdata = FakeAppData()
    appdata.config["tasklist_id"] = "list-1"
    target = tmp_path / "inbox.json"
    target.write_text("precious", encoding="utf-8")

    with pytest.raises(FileExistsError):
        export_planner_inbox_snapshot(
            bridge=FakeBridge(), appdata=appdata, path=target
        )
    assert target.read_text(encoding="utf-8") == "precious"


def test_export_without_tasklist_id_stays_read_only():
    appdata = FakeAppData()
    bridge = FakeBridge(items=_remote_items())

    payload = export_planner_inbox_snapshot(bridge=bridge, appdata=appdata)

    assert payload["tasklist"]["id"] is None
    assert payload["tasks"] == []
    assert bridge.ensure_calls == 0
    assert bridge.fetch_calls == 0

    # Opt-in resolution goes through ensure_tasklist explicitly.
    payload = export_planner_inbox_snapshot(
        bridge=bridge, appdata=appdata, allow_ensure_tasklist=True
    )
    assert payload["tasklist"]["id"] == "list-1"
    assert len(payload["tasks"]) == 3


# ---------------------------------------------------------------------------
# Backfill: dry-run
# ---------------------------------------------------------------------------

def test_dry_run_reports_plan_but_writes_nothing():
    session_factory = _make_session_factory()
    task_id, task_uid = _create_task(
        session_factory, title="Mapped", start=None, gtasks_id="g-1", priority=2
    )
    _create_task(session_factory, title="Never synced", start=None, gtasks_id=None)
    _create_task(
        session_factory,
        title="Scheduled",
        start=datetime(2026, 7, 1, 9, 0),
        gtasks_id="g-sched",
    )

    appdata = FakeAppData()
    appdata.config["tasklist_id"] = "list-1"

    report = backfill_planner_mappings(
        appdata=appdata, session_factory=session_factory, device_id="DEV-1"
    )

    assert report.mode == "dry-run"
    assert report.applied is False
    assert report.blocked_reason is None
    assert report.tasklist_id == "list-1"

    # Only the undated task with a gtasks_id is planned.
    assert [m.task_id for m in report.mappings] == [str(task_id)]
    planned = report.mappings[0]
    assert planned.task_uid == task_uid
    assert planned.gtask_id == "g-1"
    assert planned.tasklist_id == "list-1"
    assert planned.dirty_flag == 0

    entry = report.index_entries["g-1"]
    assert entry["task_uid"] == task_uid
    assert entry["status"] == "todo"
    assert entry["priority"] == 2
    assert entry["updated_at"]
    assert entry["device_id"] == "DEV-1"
    assert "task_id" not in entry

    # Nothing was written: no mapping rows, no appData writes, gtasks_id intact.
    with session_factory() as session:
        assert session.exec(select(SyncMapUndated)).all() == []
        assert session.get(Task, task_id).gtasks_id == "g-1"
    assert appdata.write_config_calls == 0
    assert appdata.write_index_calls == 0
    assert appdata.index["tasks"] == {}


def test_dry_run_reports_blocked_when_tasklist_unresolvable():
    session_factory = _make_session_factory()
    _create_task(session_factory, start=None, gtasks_id="g-1")
    appdata = FakeAppData()  # no tasklist_id anywhere

    report = backfill_planner_mappings(
        appdata=appdata, session_factory=session_factory, device_id="DEV-1"
    )
    assert report.blocked_reason is not None
    assert report.mappings == []

    with pytest.raises(ValueError):
        backfill_planner_mappings(
            appdata=appdata,
            session_factory=session_factory,
            device_id="DEV-1",
            apply=True,
            confirm_without_backup=True,
        )


# ---------------------------------------------------------------------------
# Backfill: apply
# ---------------------------------------------------------------------------

def test_apply_creates_mappings_and_index_entries(tmp_path):
    session_factory = _make_session_factory()
    task_id, task_uid = _create_task(
        session_factory, title="Done task", start=None, gtasks_id="g-1", status="done"
    )
    appdata = FakeAppData()
    appdata.config["tasklist_id"] = "list-1"
    backup, export = _backup_evidence(tmp_path)

    report = backfill_planner_mappings(
        appdata=appdata,
        session_factory=session_factory,
        device_id="DEV-1",
        apply=True,
        local_backup_path=backup,
        remote_export=export,
    )

    assert report.mode == "apply"
    assert report.applied is True

    with session_factory() as session:
        mapping = session.get(SyncMapUndated, str(task_id))
        assert mapping is not None
        assert mapping.task_uid == task_uid
        assert mapping.gtask_id == "g-1"
        assert mapping.tasklist_id == "list-1"
        assert mapping.dirty_flag == 0
        # gtasks_id survives so rollback to legacy stays possible.
        assert session.get(Task, task_id).gtasks_id == "g-1"

    entry = appdata.index["tasks"]["g-1"]
    assert entry["task_uid"] == task_uid
    assert entry["status"] == "done"
    assert entry["device_id"] == "DEV-1"
    assert appdata.index["tasklist_id"] == "list-1"

    # Exactly one index write, no config writes, marker untouched.
    assert appdata.write_index_calls == 1
    assert appdata.write_config_calls == 0
    assert appdata.config["engine"] is None


def test_apply_accepts_export_payload_instead_of_path(tmp_path):
    session_factory = _make_session_factory()
    _create_task(session_factory, start=None, gtasks_id="g-1")
    appdata = FakeAppData()
    appdata.config["tasklist_id"] = "list-1"
    backup, _ = _backup_evidence(tmp_path)
    payload = export_planner_inbox_snapshot(bridge=FakeBridge(), appdata=appdata)

    report = backfill_planner_mappings(
        appdata=appdata,
        session_factory=session_factory,
        device_id="DEV-1",
        apply=True,
        local_backup_path=backup,
        remote_export=payload,
    )
    assert report.applied is True


def test_apply_is_idempotent(tmp_path):
    session_factory = _make_session_factory()
    task_id, _uid = _create_task(session_factory, start=None, gtasks_id="g-1")
    appdata = FakeAppData()
    appdata.config["tasklist_id"] = "list-1"
    backup, export = _backup_evidence(tmp_path)
    kwargs = dict(
        appdata=appdata,
        session_factory=session_factory,
        device_id="DEV-1",
        apply=True,
        local_backup_path=backup,
        remote_export=export,
    )

    first = backfill_planner_mappings(**kwargs)
    assert len(first.mappings) == 1
    index_snapshot = json.loads(json.dumps(appdata.index))
    with session_factory() as session:
        mapping = session.get(SyncMapUndated, str(task_id))
        first_updated_at = mapping.updated_at_utc

    second = backfill_planner_mappings(**kwargs)

    assert second.mappings == []
    assert second.index_entries == {}
    assert [skip.reason for skip in second.skipped] == [
        "SyncMapUndated row already exists"
    ]
    assert appdata.index == index_snapshot
    assert appdata.write_index_calls == 1  # no second write
    with session_factory() as session:
        rows = session.exec(select(SyncMapUndated)).all()
        assert len(rows) == 1
        assert rows[0].updated_at_utc == first_updated_at


def test_existing_mappings_are_never_overwritten(tmp_path):
    session_factory = _make_session_factory()
    task_id, task_uid = _create_task(session_factory, start=None, gtasks_id="g-new")
    with session_factory() as session:
        session.add(
            SyncMapUndated(
                task_id=str(task_id),
                task_uid=task_uid,
                gtask_id="g-old",
                tasklist_id="list-legacy",
                dirty_flag=1,
            )
        )
        session.commit()

    appdata = FakeAppData()
    appdata.config["tasklist_id"] = "list-1"
    backup, export = _backup_evidence(tmp_path)

    report = backfill_planner_mappings(
        appdata=appdata,
        session_factory=session_factory,
        device_id="DEV-1",
        apply=True,
        local_backup_path=backup,
        remote_export=export,
    )

    assert report.mappings == []
    assert report.skipped[0].reason == "SyncMapUndated row already exists"
    with session_factory() as session:
        mapping = session.get(SyncMapUndated, str(task_id))
        assert mapping.gtask_id == "g-old"  # untouched
        assert mapping.tasklist_id == "list-legacy"
        assert mapping.dirty_flag == 1


def test_conflicting_index_entries_block_items_not_the_run(tmp_path):
    session_factory = _make_session_factory()
    ok_id, ok_uid = _create_task(session_factory, start=None, gtasks_id="g-ok")
    tomb_id, _ = _create_task(session_factory, start=None, gtasks_id="g-tomb")
    foreign_id, _ = _create_task(session_factory, start=None, gtasks_id="g-foreign")

    appdata = FakeAppData()
    appdata.config["tasklist_id"] = "list-1"
    appdata.index["tasks"]["g-tomb"] = {"deleted": True, "reason": "deleted"}
    appdata.index["tasks"]["g-foreign"] = {"task_uid": "someone-else"}
    backup, export = _backup_evidence(tmp_path)

    report = backfill_planner_mappings(
        appdata=appdata,
        session_factory=session_factory,
        device_id="DEV-1",
        apply=True,
        local_backup_path=backup,
        remote_export=export,
    )

    assert [m.task_id for m in report.mappings] == [str(ok_id)]
    reasons = {skip.task_id: skip.reason for skip in report.skipped}
    assert "tombstone" in reasons[str(tomb_id)]
    assert "different task_uid" in reasons[str(foreign_id)]

    # Blocked entries stay byte-for-byte intact.
    assert appdata.index["tasks"]["g-tomb"] == {"deleted": True, "reason": "deleted"}
    assert appdata.index["tasks"]["g-foreign"] == {"task_uid": "someone-else"}
    assert appdata.index["tasks"]["g-ok"]["task_uid"] == ok_uid
    with session_factory() as session:
        assert session.get(SyncMapUndated, str(tomb_id)) is None
        assert session.get(SyncMapUndated, str(foreign_id)) is None


def test_duplicate_gtask_ids_are_planned_once(tmp_path):
    session_factory = _make_session_factory()
    first_id, _ = _create_task(session_factory, start=None, gtasks_id="g-dup")
    second_id, _ = _create_task(session_factory, start=None, gtasks_id="g-dup")

    appdata = FakeAppData()
    appdata.config["tasklist_id"] = "list-1"
    backup, export = _backup_evidence(tmp_path)

    report = backfill_planner_mappings(
        appdata=appdata,
        session_factory=session_factory,
        device_id="DEV-1",
        apply=True,
        local_backup_path=backup,
        remote_export=export,
    )

    assert [m.task_id for m in report.mappings] == [str(first_id)]
    assert report.skipped[0].task_id == str(second_id)
    assert "duplicated" in report.skipped[0].reason


def test_backfill_never_calls_google_tasks(tmp_path):
    """The backfill has no bridge at all; remote task writes are impossible.

    The remote export beforehand is read-only too — this exercises the full
    pre-cutover sequence against a poisoned bridge whose mutating methods
    raise.
    """
    session_factory = _make_session_factory()
    _create_task(session_factory, start=None, gtasks_id="g-1")
    appdata = FakeAppData()
    appdata.config["tasklist_id"] = "list-1"
    bridge = FakeBridge(items=_remote_items())

    backup = tmp_path / "app_precutover.db"
    backup.write_bytes(b"backup")
    export_path = tmp_path / "inbox.json"
    export_planner_inbox_snapshot(bridge=bridge, appdata=appdata, path=export_path)

    backfill_planner_mappings(
        appdata=appdata,
        session_factory=session_factory,
        device_id="DEV-1",
        apply=True,
        local_backup_path=backup,
        remote_export=export_path,
    )

    assert bridge.inserted == []
    assert bridge.deleted == []
    assert appdata.write_config_calls == 0


# ---------------------------------------------------------------------------
# Safety gates
# ---------------------------------------------------------------------------

def test_apply_refuses_without_backup_evidence(tmp_path):
    session_factory = _make_session_factory()
    _create_task(session_factory, start=None, gtasks_id="g-1")
    appdata = FakeAppData()
    appdata.config["tasklist_id"] = "list-1"

    with pytest.raises(ValueError, match="local_backup_path and remote_export"):
        backfill_planner_mappings(
            appdata=appdata,
            session_factory=session_factory,
            device_id="DEV-1",
            apply=True,
        )

    # A backup path that does not exist is not evidence.
    export = tmp_path / "inbox.json"
    export.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="does not exist"):
        backfill_planner_mappings(
            appdata=appdata,
            session_factory=session_factory,
            device_id="DEV-1",
            apply=True,
            local_backup_path=tmp_path / "missing.db",
            remote_export=export,
        )

    with session_factory() as session:
        assert session.exec(select(SyncMapUndated)).all() == []
    assert appdata.write_index_calls == 0

    # The waiver must be explicit.
    report = backfill_planner_mappings(
        appdata=appdata,
        session_factory=session_factory,
        device_id="DEV-1",
        apply=True,
        confirm_without_backup=True,
    )
    assert report.applied is True


def test_foreign_ownership_marker_blocks_backfill(tmp_path):
    session_factory = _make_session_factory()
    task_id, _uid = _create_task(session_factory, start=None, gtasks_id="g-1")
    appdata = FakeAppData()
    appdata.config["tasklist_id"] = "list-1"
    appdata.config["engine"] = "legacy"
    backup, export = _backup_evidence(tmp_path)

    dry = backfill_planner_mappings(
        appdata=appdata, session_factory=session_factory, device_id="DEV-1"
    )
    assert dry.blocked_reason is not None
    assert "legacy" in dry.blocked_reason
    assert dry.mappings == []

    with pytest.raises(EngineOwnershipError):
        backfill_planner_mappings(
            appdata=appdata,
            session_factory=session_factory,
            device_id="DEV-1",
            apply=True,
            local_backup_path=backup,
            remote_export=export,
        )

    # Nothing was written anywhere and the marker survived untouched.
    with session_factory() as session:
        assert session.exec(select(SyncMapUndated)).all() == []
        assert session.get(Task, task_id).gtasks_id == "g-1"
    assert appdata.write_config_calls == 0
    assert appdata.write_index_calls == 0
    assert appdata.config["engine"] == "legacy"


def test_backfill_does_not_claim_or_change_the_marker(tmp_path):
    """Marker contract: vacant before ⇒ vacant after; "undated" is allowed."""
    session_factory = _make_session_factory()
    _create_task(session_factory, start=None, gtasks_id="g-1")
    appdata = FakeAppData()
    appdata.config["tasklist_id"] = "list-1"
    appdata.config["engine"] = "undated"
    backup, export = _backup_evidence(tmp_path)

    report = backfill_planner_mappings(
        appdata=appdata,
        session_factory=session_factory,
        device_id="DEV-1",
        apply=True,
        local_backup_path=backup,
        remote_export=export,
    )
    assert report.applied is True
    assert appdata.config["engine"] == "undated"
    assert appdata.write_config_calls == 0


def test_default_engine_stays_legacy_and_tooling_is_flag_independent():
    """The migration tooling must not flip or depend on the engine flag."""
    assert resolve_undated_engine(env={}) == UNDATED_ENGINE_LEGACY

    # The backfill above ran without selecting the undated engine — prove the
    # same here explicitly: default flag, dry-run works, nothing activates.
    session_factory = _make_session_factory()
    _create_task(session_factory, start=None, gtasks_id="g-1")
    appdata = FakeAppData()
    appdata.config["tasklist_id"] = "list-1"

    report = backfill_planner_mappings(
        appdata=appdata, session_factory=session_factory, device_id="DEV-1"
    )
    assert len(report.mappings) == 1
    assert appdata.config["engine"] is None
