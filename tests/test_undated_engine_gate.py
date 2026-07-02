"""Feature flag, ownership tripwire and DB migration for the undated engine.

The hardened ``UndatedTasksSync`` stack must stay inert unless
``GOOGLE_SYNC.undated_engine == "undated"`` is selected explicitly, and even
then it must refuse to write when the shared ``planner_config.json`` marker
says another engine owns the "Planner Inbox" list.
"""
import json
from dataclasses import replace
from typing import Dict, Tuple

import pytest
from sqlalchemy import text
from sqlmodel import SQLModel, Session, create_engine, select

from core.settings import (
    GoogleSyncSettings,
    UNDATED_ENGINE_LEGACY,
    UNDATED_ENGINE_UNDATED,
    resolve_undated_engine,
)
from models import SyncMapUndated, Task
from services.appdata import AppDataClient
from services.undated_tasks_sync import (
    EngineOwnershipError,
    SKIP_REASON_ENGINE_NOT_SELECTED,
    UndatedTasksSync,
)
from storage import migrations


class FakeAppData(AppDataClient):  # type: ignore[misc]
    def __init__(self):
        super().__init__(auth=None)
        self.config = {"version": 1, "tasklist_id": None, "last_full_sync": None, "engine": None}
        self.index = {"version": 1, "tasklist_id": None, "tasks": {}}
        self.config_etag = "cfg-0"
        self.index_etag = "idx-0"
        self.ensure_calls = 0

    def ensure_files(self) -> Dict[str, str]:  # type: ignore[override]
        self.ensure_calls += 1
        return {self.CONFIG_NAME: "config", self.INDEX_NAME: "index"}

    def read_config(self) -> Tuple[Dict[str, object], str]:  # type: ignore[override]
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

    def read_index(self) -> Tuple[Dict[str, object], str]:  # type: ignore[override]
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


def _make_session_factory():
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)

    def factory():
        return Session(engine)

    return factory


def _make_sync(session_factory, bridge, appdata):
    return UndatedTasksSync(
        auth=None,
        bridge=bridge,
        session_factory=session_factory,
        appdata=appdata,
        device_id="TEST-DEVICE",
    )


def _create_task(session_factory, title="Test"):
    with session_factory() as session:
        task = Task(title=title, start=None)
        session.add(task)
        session.commit()
        session.refresh(task)
        return task.id, task.uid


def _set_engine_flag(monkeypatch, value):
    import services.undated_tasks_sync as uts

    monkeypatch.setattr(
        uts, "GOOGLE_SYNC", replace(uts.GOOGLE_SYNC, undated_engine=value)
    )


# ----- feature flag defaults -----

def test_default_engine_is_legacy():
    assert resolve_undated_engine(env={}) == UNDATED_ENGINE_LEGACY
    assert GoogleSyncSettings().undated_engine in (
        UNDATED_ENGINE_LEGACY,
        UNDATED_ENGINE_UNDATED,
    )
    # Without the env var the dataclass default must be legacy.
    field_default = GoogleSyncSettings.__dataclass_fields__["undated_engine"].default
    assert resolve_undated_engine(env={}) == UNDATED_ENGINE_LEGACY
    assert field_default == resolve_undated_engine()


def test_engine_flag_env_override_and_invalid_values():
    assert (
        resolve_undated_engine(env={"PLANNER_UNDATED_ENGINE": "undated"})
        == UNDATED_ENGINE_UNDATED
    )
    assert (
        resolve_undated_engine(env={"PLANNER_UNDATED_ENGINE": " UNDATED "})
        == UNDATED_ENGINE_UNDATED
    )
    assert (
        resolve_undated_engine(env={"PLANNER_UNDATED_ENGINE": "legacy"})
        == UNDATED_ENGINE_LEGACY
    )
    # A typo must never activate the new engine.
    assert (
        resolve_undated_engine(env={"PLANNER_UNDATED_ENGINE": "undatedd"})
        == UNDATED_ENGINE_LEGACY
    )
    assert (
        resolve_undated_engine(env={"PLANNER_UNDATED_ENGINE": ""})
        == UNDATED_ENGINE_LEGACY
    )


# ----- engine inert unless explicitly selected -----

def test_engine_is_inert_with_legacy_flag(monkeypatch):
    _set_engine_flag(monkeypatch, UNDATED_ENGINE_LEGACY)
    session_factory = _make_session_factory()
    task_id, _uid = _create_task(session_factory)
    bridge = FakeBridge()
    appdata = FakeAppData()
    sync = _make_sync(session_factory, bridge, appdata)

    assert sync.sync() is False
    reasons = {item.reason for item in sync.last_report.skipped}
    assert SKIP_REASON_ENGINE_NOT_SELECTED in reasons

    # No remote surface was touched at all.
    assert bridge.ensure_calls == 0
    assert bridge.fetch_calls == 0
    assert bridge.inserted == []
    assert bridge.deleted == []
    assert appdata.ensure_calls == 0
    assert appdata.config["engine"] is None

    # Hooks and mark_dirty are inert too: no mapping rows appear.
    sync.mark_dirty(task_id)
    sync.on_task_deleted(task_id)
    with session_factory() as session:
        assert session.exec(select(SyncMapUndated)).all() == []
    assert bridge.deleted == []


def test_engine_runs_when_explicitly_selected(monkeypatch):
    _set_engine_flag(monkeypatch, UNDATED_ENGINE_UNDATED)
    session_factory = _make_session_factory()
    _create_task(session_factory, title="Explicit")
    bridge = FakeBridge()
    appdata = FakeAppData()
    sync = _make_sync(session_factory, bridge, appdata)

    assert sync.sync() is True
    assert len(bridge.inserted) == 1


# ----- ownership marker / tripwire -----

def test_foreign_ownership_marker_blocks_all_writes(monkeypatch):
    _set_engine_flag(monkeypatch, UNDATED_ENGINE_UNDATED)
    session_factory = _make_session_factory()
    task_id, _uid = _create_task(session_factory)
    bridge = FakeBridge()
    appdata = FakeAppData()
    appdata.config["engine"] = "legacy"

    sync = _make_sync(session_factory, bridge, appdata)

    with pytest.raises(EngineOwnershipError):
        sync.sync()
    with pytest.raises(EngineOwnershipError):
        sync.push_dirty()
    with pytest.raises(EngineOwnershipError):
        sync.mark_dirty(task_id)
    with pytest.raises(EngineOwnershipError):
        sync.on_task_deleted(task_id)

    # Nothing was written anywhere: no remote tasks, no marker takeover,
    # no tasklist_id side effects on either shared surface.
    assert bridge.inserted == []
    assert bridge.deleted == []
    assert bridge.ensure_calls == 0
    assert appdata.config["engine"] == "legacy"
    assert appdata.config["tasklist_id"] is None
    assert appdata.index["tasklist_id"] is None
    assert appdata.index["tasks"] == {}


def test_pull_checks_ownership_before_tasklist_writes(monkeypatch):
    """A foreign marker must abort pull() before tasklist resolution.

    Regression: _ensure_tasklist_id used to run first and wrote tasklist_id
    into planner_config.json and the index before ownership was checked.
    """
    _set_engine_flag(monkeypatch, UNDATED_ENGINE_UNDATED)
    session_factory = _make_session_factory()
    _create_task(session_factory)
    bridge = FakeBridge()
    appdata = FakeAppData()
    appdata.config["engine"] = "legacy"
    sync = _make_sync(session_factory, bridge, appdata)

    with pytest.raises(EngineOwnershipError):
        sync.pull()

    assert bridge.ensure_calls == 0
    assert bridge.fetch_calls == 0
    assert bridge.inserted == []
    assert bridge.deleted == []
    # No side-effect writes: neither surface gained a tasklist_id and the
    # foreign marker survived untouched.
    assert appdata.config["tasklist_id"] is None
    assert appdata.index["tasklist_id"] is None
    assert appdata.config["engine"] == "legacy"
    assert appdata.config_etag == "cfg-0"
    assert appdata.index_etag == "idx-0"


def test_push_checks_ownership_before_tasklist_writes(monkeypatch):
    _set_engine_flag(monkeypatch, UNDATED_ENGINE_UNDATED)
    session_factory = _make_session_factory()
    _create_task(session_factory)
    bridge = FakeBridge()
    appdata = FakeAppData()
    appdata.config["engine"] = "legacy"
    sync = _make_sync(session_factory, bridge, appdata)

    with pytest.raises(EngineOwnershipError):
        sync.push_dirty()

    assert bridge.ensure_calls == 0
    assert bridge.inserted == []
    assert bridge.deleted == []
    assert appdata.config["tasklist_id"] is None
    assert appdata.index["tasklist_id"] is None
    assert appdata.config["engine"] == "legacy"
    assert appdata.config_etag == "cfg-0"
    assert appdata.index_etag == "idx-0"


def test_vacant_ownership_marker_is_claimed(monkeypatch):
    _set_engine_flag(monkeypatch, UNDATED_ENGINE_UNDATED)
    session_factory = _make_session_factory()
    _create_task(session_factory)
    bridge = FakeBridge()
    appdata = FakeAppData()
    assert appdata.config["engine"] is None

    sync = _make_sync(session_factory, bridge, appdata)
    assert sync.sync() is True
    assert appdata.config["engine"] == UNDATED_ENGINE_UNDATED

    # A second engine instance sharing the marker keeps working (idempotent).
    sync2 = _make_sync(session_factory, bridge, appdata)
    sync2.sync()
    assert appdata.config["engine"] == UNDATED_ENGINE_UNDATED


def test_concurrent_foreign_claim_wins_on_conflict(monkeypatch):
    """A 412-style conflict that reveals a foreign owner must abort the claim."""
    _set_engine_flag(monkeypatch, UNDATED_ENGINE_UNDATED)
    session_factory = _make_session_factory()
    _create_task(session_factory)
    bridge = FakeBridge()
    appdata = FakeAppData()
    sync = _make_sync(session_factory, bridge, appdata)

    # Engine caches the vacant config, then another writer claims the marker
    # remotely (etag moves on), so our claim goes down the conflict path.
    sync._load_config()
    appdata.config["engine"] = "legacy"
    appdata.config_etag = "cfg-99"

    with pytest.raises(EngineOwnershipError):
        sync.sync()
    assert appdata.config["engine"] == "legacy"
    assert bridge.inserted == []


# ----- DB migration for syncmapundated.task_uid -----

def _create_legacy_schema(engine):
    """A database from before SyncMapUndated.task_uid existed."""
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE task (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    uid TEXT
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE syncmapundated (
                    task_id TEXT PRIMARY KEY,
                    gtask_id TEXT,
                    tasklist_id TEXT NOT NULL,
                    updated_at_utc TEXT NOT NULL,
                    dirty_flag INTEGER NOT NULL DEFAULT 0
                )
                """
            )
        )
        conn.execute(
            text("INSERT INTO task (id, title, uid) VALUES (7, 'Legacy', 'uid-legacy-7')")
        )
        conn.execute(
            text(
                """
                INSERT INTO syncmapundated
                    (task_id, gtask_id, tasklist_id, updated_at_utc, dirty_flag)
                VALUES ('7', 'gtask-7', 'list-1', '2026-01-01T00:00:00+00:00', 0)
                """
            )
        )


def _sync_map_columns(engine):
    with engine.begin() as conn:
        rows = conn.execute(text("PRAGMA table_info('syncmapundated')"))
        return {row[1] for row in rows}


def test_migration_adds_task_uid_and_backfills():
    engine = create_engine("sqlite:///:memory:")
    _create_legacy_schema(engine)
    assert "task_uid" not in _sync_map_columns(engine)

    with engine.begin() as conn:
        migrations.ensure_sync_map_undated_columns(conn)

    assert "task_uid" in _sync_map_columns(engine)
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT task_uid FROM syncmapundated WHERE task_id = '7'")
        ).first()
    assert row[0] == "uid-legacy-7"


def test_migration_is_idempotent_and_safe_on_current_schema():
    # Twice on a legacy schema: second run is a no-op, backfill survives.
    engine = create_engine("sqlite:///:memory:")
    _create_legacy_schema(engine)
    for _ in range(2):
        with engine.begin() as conn:
            migrations.ensure_sync_map_undated_columns(conn)
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT task_uid FROM syncmapundated WHERE task_id = '7'")
        ).first()
    assert row[0] == "uid-legacy-7"

    # On the current SQLModel schema (task_uid already present): no error,
    # existing values untouched.
    engine2 = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine2)
    with Session(engine2) as session:
        task = Task(title="Current", start=None)
        session.add(task)
        session.commit()
        session.refresh(task)
        session.add(
            SyncMapUndated(
                task_id=str(task.id),
                task_uid="explicit-uid",
                gtask_id="gtask-1",
                tasklist_id="list-1",
            )
        )
        session.commit()
    for _ in range(2):
        with engine2.begin() as conn:
            migrations.ensure_sync_map_undated_columns(conn)
    with engine2.begin() as conn:
        row = conn.execute(
            text("SELECT task_uid FROM syncmapundated")
        ).first()
    assert row[0] == "explicit-uid"


def test_migration_skips_when_table_missing():
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        migrations.ensure_sync_map_undated_columns(conn)  # must not raise
    with engine.begin() as conn:
        row = conn.execute(
            text(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='syncmapundated'"
            )
        ).first()
    assert row is None
