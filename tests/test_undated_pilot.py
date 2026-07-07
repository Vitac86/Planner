"""Phase 4 pilot script: import safety, read-only defaults, verification.

Everything runs against fakes and temp dirs — no real database, no real
Google APIs, no Flet UI, and no dependence on PLANNER_UNDATED_ENGINE (the
tooling must work while the default engine is still "legacy").
"""
import os
import json
import subprocess
import sys
from pathlib import Path

import pytest
from sqlmodel import select

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.settings import UNDATED_ENGINE_LEGACY, resolve_undated_engine
from models import SyncMapUndated, Task

import scripts.undated_migration_pilot as pilot
from test_undated_migration import (  # shared offline fakes/helpers
    FakeAppData,
    FakeBridge,
    _backup_evidence,
    _create_task,
    _make_session_factory,
    _remote_items,
)


def _pilot_fixture(tasklist_id="list-1"):
    session_factory = _make_session_factory()
    appdata = FakeAppData()
    appdata.config["tasklist_id"] = tasklist_id
    return session_factory, appdata


def _apply_backfill(session_factory, appdata, tmp_path):
    backup, export = _backup_evidence(tmp_path)
    rc = pilot.main(
        [
            "apply",
            "--backup", str(backup),
            "--export", str(export),
            "--device-id", "DEV-1",
        ],
        appdata_factory=lambda: appdata,
        session_factory=session_factory,
    )
    assert rc == 0
    return backup, export


# ---------------------------------------------------------------------------
# Import safety
# ---------------------------------------------------------------------------

def test_script_import_does_not_start_ui_or_appshell():
    """Importing the pilot module must not load Flet or any ui module."""
    code = (
        "import sys\n"
        "import scripts.undated_migration_pilot\n"
        "loaded = [m for m in sys.modules"
        " if m == 'flet' or m.startswith('flet.')"
        " or m == 'ui' or m.startswith('ui.')]\n"
        "assert not loaded, f'UI modules loaded on import: {loaded}'\n"
        "print('clean-import-ok')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    assert "clean-import-ok" in result.stdout


# ---------------------------------------------------------------------------
# Read-only defaults
# ---------------------------------------------------------------------------

def test_cli_dry_run_default_is_read_only(capsys):
    session_factory, appdata = _pilot_fixture()
    task_id, _uid = _create_task(session_factory, start=None, gtasks_id="g-1")

    rc = pilot.main(
        ["dry-run", "--device-id", "DEV-1"],
        appdata_factory=lambda: appdata,
        session_factory=session_factory,
    )

    assert rc == 0
    out = capsys.readouterr().out
    assert "dry-run" in out
    assert "nothing was written" in out.lower()

    with session_factory() as session:
        assert session.exec(select(SyncMapUndated)).all() == []
        assert session.get(Task, task_id).gtasks_id == "g-1"
    assert appdata.write_config_calls == 0
    assert appdata.write_index_calls == 0
    assert appdata.index["tasks"] == {}


def test_cli_verify_is_read_only(capsys):
    session_factory, appdata = _pilot_fixture()
    _create_task(session_factory, start=None, gtasks_id="g-1")

    rc = pilot.main(
        ["verify"],
        appdata_factory=lambda: appdata,
        session_factory=session_factory,
    )

    assert rc == 1  # missing mapping reported, nothing written
    assert "ISSUES FOUND" in capsys.readouterr().out
    with session_factory() as session:
        assert session.exec(select(SyncMapUndated)).all() == []
    assert appdata.write_config_calls == 0
    assert appdata.write_index_calls == 0


# ---------------------------------------------------------------------------
# Apply gates
# ---------------------------------------------------------------------------

def test_cli_apply_requires_backup_and_export_arguments():
    with pytest.raises(SystemExit) as excinfo:
        pilot.main(["apply"])  # argparse: --backup/--export are required
    assert excinfo.value.code == 2


def test_cli_apply_rejects_missing_evidence_files(tmp_path, capsys):
    session_factory, appdata = _pilot_fixture()
    _create_task(session_factory, start=None, gtasks_id="g-1")
    export = tmp_path / "inbox.json"
    export.write_text("{}", encoding="utf-8")

    rc = pilot.main(
        [
            "apply",
            "--backup", str(tmp_path / "missing.db"),
            "--export", str(export),
            "--device-id", "DEV-1",
        ],
        appdata_factory=lambda: appdata,
        session_factory=session_factory,
    )

    assert rc == 2
    assert "REFUSED" in capsys.readouterr().err
    with session_factory() as session:
        assert session.exec(select(SyncMapUndated)).all() == []
    assert appdata.write_index_calls == 0


def test_cli_apply_with_evidence_writes_mappings(tmp_path):
    session_factory, appdata = _pilot_fixture()
    task_id, task_uid = _create_task(session_factory, start=None, gtasks_id="g-1")

    _apply_backfill(session_factory, appdata, tmp_path)

    with session_factory() as session:
        mapping = session.get(SyncMapUndated, str(task_id))
        assert mapping is not None
        assert mapping.task_uid == task_uid
        assert mapping.gtask_id == "g-1"
        # gtasks_id survives so rollback to legacy stays possible.
        assert session.get(Task, task_id).gtasks_id == "g-1"
    assert appdata.index["tasks"]["g-1"]["task_uid"] == task_uid
    assert appdata.write_config_calls == 0  # marker never claimed


# ---------------------------------------------------------------------------
# Verification helpers
# ---------------------------------------------------------------------------

def test_verify_reports_missing_mappings_before_backfill():
    session_factory, appdata = _pilot_fixture()
    task_id, task_uid = _create_task(session_factory, start=None, gtasks_id="g-1")
    _create_task(session_factory, title="Never synced", start=None, gtasks_id=None)

    report = pilot.verify_pilot_state(
        appdata=appdata, session_factory=session_factory, env={}
    )

    assert report.ok is False
    assert report.missing_mappings == [
        pilot.UnmappedTask(str(task_id), task_uid, "g-1")
    ]
    assert any("no SyncMapUndated row" in issue for issue in report.issues)
    assert report.local_engine_flag == UNDATED_ENGINE_LEGACY
    assert report.engine_marker is None


def test_verify_passes_after_backfill(tmp_path):
    session_factory, appdata = _pilot_fixture()
    _create_task(session_factory, start=None, gtasks_id="g-1", priority=2)
    _create_task(session_factory, start=None, gtasks_id="g-2", status="done")

    backup, export = _apply_backfill(session_factory, appdata, tmp_path)

    report = pilot.verify_pilot_state(
        appdata=appdata,
        session_factory=session_factory,
        local_backup_path=backup,
        remote_export_path=export,
        env={},
    )

    assert report.ok, report.issues
    assert report.missing_mappings == []
    assert report.uid_mismatches == []
    assert report.missing_index_entries == []
    assert report.duplicate_index_uids == {}
    assert report.cleared_gtasks_ids == []
    assert report.backup_exists is True
    assert report.export_exists is True


def test_verify_rollback_safety_flags_cleared_gtasks_id(tmp_path):
    session_factory, appdata = _pilot_fixture()
    task_id, _uid = _create_task(session_factory, start=None, gtasks_id="g-1")
    _apply_backfill(session_factory, appdata, tmp_path)

    before = pilot.verify_pilot_state(
        appdata=appdata, session_factory=session_factory, env={}
    )
    assert before.cleared_gtasks_ids == []  # rollback link preserved

    with session_factory() as session:
        task = session.get(Task, task_id)
        task.gtasks_id = None
        session.add(task)
        session.commit()

    after = pilot.verify_pilot_state(
        appdata=appdata, session_factory=session_factory, env={}
    )
    assert after.cleared_gtasks_ids == [str(task_id)]
    assert after.ok is False
    assert any("gtasks_id" in issue for issue in after.issues)


def test_verify_reports_uid_and_index_anomalies():
    session_factory, appdata = _pilot_fixture()
    task_id, _uid = _create_task(session_factory, start=None, gtasks_id="g-a")
    with session_factory() as session:
        session.add(
            SyncMapUndated(
                task_id=str(task_id),
                task_uid="someone-else",
                gtask_id="g-a",
                tasklist_id="list-1",
            )
        )
        session.commit()
    # No index entry for the mapped g-a; duplicate uid under g-x/g-y; the
    # tombstoned g-z must not count towards duplicates.
    appdata.index["tasks"]["g-x"] = {"task_uid": "dup-uid"}
    appdata.index["tasks"]["g-y"] = {"task_uid": "dup-uid"}
    appdata.index["tasks"]["g-z"] = {"task_uid": "dup-uid", "deleted": True}

    report = pilot.verify_pilot_state(
        appdata=appdata, session_factory=session_factory, env={}
    )

    assert report.uid_mismatches[0].mapping_uid == "someone-else"
    assert report.missing_index_entries == ["g-a"]
    assert report.duplicate_index_uids == {"dup-uid": ["g-x", "g-y"]}
    assert report.ok is False


# ---------------------------------------------------------------------------
# Export safety
# ---------------------------------------------------------------------------

def test_cli_export_refuses_overwrite_unless_new_path(tmp_path, capsys):
    session_factory, appdata = _pilot_fixture()
    bridge = FakeBridge(items=_remote_items())
    target = tmp_path / "inbox.json"
    target.write_text("precious", encoding="utf-8")

    rc = pilot.main(
        ["export", "--out", str(target)],
        appdata_factory=lambda: appdata,
        bridge_factory=lambda: bridge,
    )
    assert rc == 2
    assert "REFUSED" in capsys.readouterr().err
    assert target.read_text(encoding="utf-8") == "precious"

    fresh = tmp_path / "inbox_2.json"
    rc = pilot.main(
        ["export", "--out", str(fresh)],
        appdata_factory=lambda: appdata,
        bridge_factory=lambda: bridge,
    )
    assert rc == 0
    payload = json.loads(fresh.read_text(encoding="utf-8"))
    assert payload["tasklist"]["id"] == "list-1"
    assert len(payload["tasks"]) == 3


# ---------------------------------------------------------------------------
# Remote tasks are never touched
# ---------------------------------------------------------------------------

def test_full_cli_sequence_never_touches_remote_tasks(tmp_path):
    """backup → export → dry-run → apply → verify against a poisoned bridge."""
    session_factory, appdata = _pilot_fixture()
    _create_task(session_factory, start=None, gtasks_id="g-live")
    bridge = FakeBridge(items=_remote_items())  # upsert/delete raise

    db = tmp_path / "app.db"
    db.write_bytes(b"sqlite-bytes")
    rc = pilot.main(
        ["backup", "--db", str(db), "--backup-dir", str(tmp_path / "backups")]
    )
    assert rc == 0
    backup = next((tmp_path / "backups").glob("app_precutover_*.db"))

    export = tmp_path / "inbox.json"
    common = dict(
        appdata_factory=lambda: appdata,
        bridge_factory=lambda: bridge,
        session_factory=session_factory,
    )
    assert pilot.main(["export", "--out", str(export)], **common) == 0
    assert pilot.main(["dry-run", "--device-id", "DEV-1"], **common) == 0
    assert pilot.main(
        [
            "apply",
            "--backup", str(backup),
            "--export", str(export),
            "--device-id", "DEV-1",
        ],
        **common,
    ) == 0
    assert pilot.main(
        ["verify", "--backup", str(backup), "--export", str(export)], **common
    ) == 0

    assert bridge.inserted == []
    assert bridge.deleted == []
    assert appdata.write_config_calls == 0


# ---------------------------------------------------------------------------
# Engine flag stays legacy
# ---------------------------------------------------------------------------

def test_default_engine_remains_legacy(monkeypatch, tmp_path):
    monkeypatch.delenv("PLANNER_UNDATED_ENGINE", raising=False)
    assert resolve_undated_engine(env={}) == UNDATED_ENGINE_LEGACY

    session_factory, appdata = _pilot_fixture()
    _create_task(session_factory, start=None, gtasks_id="g-1")
    _apply_backfill(session_factory, appdata, tmp_path)

    # No command set the flag or claimed the shared marker.
    assert "PLANNER_UNDATED_ENGINE" not in os.environ
    assert appdata.config["engine"] is None
    report = pilot.verify_pilot_state(
        appdata=appdata, session_factory=session_factory
    )
    assert report.local_engine_flag == UNDATED_ENGINE_LEGACY
