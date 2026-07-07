"""Phase 4 pilot operator CLI for the undated ("Planner Inbox") sync cutover.

This is a hand tool for rehearsing the migration on a **test Google account**
(docs/UNDATED_SYNC_PILOT_RUNBOOK.md; docs/SYNC_ENGINE_DECISION.md §5 Phase 4).
It wraps the Phase 3 tooling (``storage/backup.py``,
``services/undated_migration.py``) and adds read-only verification.

Safety properties, by construction:

* importing this module has no side effects: no Flet UI, no ``AppShell``, no
  database writes, no Google API calls — commands run only via :func:`main`;
* the default behavior is dry-run/read-only; writes happen only in the
  explicit ``backup`` command (a local file copy) and the explicit ``apply``
  command (the Phase 3 backfill, exactly its documented writes);
* ``apply`` refuses to run without existing ``--backup`` and ``--export``
  evidence paths — the ``confirm_without_backup`` waiver of the underlying
  backfill is deliberately not exposed here;
* the engine flag is never touched: ``PLANNER_UNDATED_ENGINE`` stays whatever
  the operator set, and the default engine remains ``"legacy"``;
* remote Google Tasks are never inserted, updated or deleted — the backfill
  takes no bridge at all, and ``export`` only reads (it will not even create
  the tasklist unless ``--allow-ensure-tasklist`` is passed);
* ``Task.gtasks_id`` is never written, so rollback to legacy stays possible.

Run every command from the repo root with the pilot data-dir override set
(``APPDATA`` on Windows, ``XDG_DATA_HOME`` on Linux) so nothing targets the
real profile — see the runbook::

    python -m scripts.undated_migration_pilot backup
    python -m scripts.undated_migration_pilot export --out <new-file.json>
    python -m scripts.undated_migration_pilot dry-run
    python -m scripts.undated_migration_pilot apply --backup <p> --export <p>
    python -m scripts.undated_migration_pilot verify [--backup <p>] [--export <p>]

Exit codes: 0 = ok, 1 = verification issues or blocked dry-run, 2 = refused.
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Mapping, Optional, Union

# Allow `python scripts/undated_migration_pilot.py ...` from any directory.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from sqlmodel import select

from core.settings import resolve_undated_engine
from models import SyncMapUndated, Task
from services.undated_migration import (
    MODE_APPLY,
    BackfillReport,
    backfill_planner_mappings,
    export_planner_inbox_snapshot,
)
from services.undated_tasks_sync import ENGINE_NAME, EngineOwnershipError
from storage.backup import create_precutover_backup
from storage.db import get_session


# ---------------------------------------------------------------------------
# Verification helpers (read-only, reusable from tests and the REPL)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class UnmappedTask:
    """Local unscheduled task with a legacy ``gtasks_id`` but no mapping."""

    task_id: str
    task_uid: str
    gtask_id: str


@dataclass(frozen=True)
class UidMismatch:
    """``SyncMapUndated`` row whose ``task_uid`` does not match ``Task.uid``.

    ``task_uid`` is ``None`` when the local task row no longer exists.
    """

    task_id: str
    mapping_uid: Optional[str]
    task_uid: Optional[str]


@dataclass
class VerificationReport:
    """State of the pilot data, as reported by :func:`verify_pilot_state`."""

    local_engine_flag: str = "legacy"
    engine_marker: Optional[str] = None
    missing_mappings: List[UnmappedTask] = field(default_factory=list)
    uid_mismatches: List[UidMismatch] = field(default_factory=list)
    missing_index_entries: List[str] = field(default_factory=list)
    duplicate_index_uids: Dict[str, List[str]] = field(default_factory=dict)
    cleared_gtasks_ids: List[str] = field(default_factory=list)
    backup_path: Optional[str] = None
    backup_exists: Optional[bool] = None
    export_path: Optional[str] = None
    export_exists: Optional[bool] = None

    @property
    def issues(self) -> List[str]:
        found: List[str] = []
        if self.missing_mappings:
            found.append(
                f"{len(self.missing_mappings)} unscheduled task(s) carry a "
                "gtasks_id but have no SyncMapUndated row (backfill missing)"
            )
        if self.uid_mismatches:
            found.append(
                f"{len(self.uid_mismatches)} SyncMapUndated row(s) whose "
                "task_uid does not match Task.uid"
            )
        if self.missing_index_entries:
            found.append(
                f"{len(self.missing_index_entries)} mapped gtask id(s) have "
                "no live gtasks_index.json entry"
            )
        if self.duplicate_index_uids:
            found.append(
                f"{len(self.duplicate_index_uids)} task_uid value(s) appear "
                "under more than one gtask id in the index"
            )
        if self.cleared_gtasks_ids:
            found.append(
                f"{len(self.cleared_gtasks_ids)} mapped task(s) lost their "
                "Task.gtasks_id (legacy rollback link)"
            )
        if self.engine_marker not in (None, "", ENGINE_NAME):
            found.append(
                f"planner_config.json engine marker is {self.engine_marker!r}; "
                "a foreign engine owns the tasklist"
            )
        if self.backup_path is not None and not self.backup_exists:
            found.append(f"local DB backup does not exist: {self.backup_path}")
        if self.export_path is not None and not self.export_exists:
            found.append(f"remote export does not exist: {self.export_path}")
        return found

    @property
    def ok(self) -> bool:
        return not self.issues


def verify_pilot_state(
    *,
    appdata,
    session_factory: Callable = get_session,
    local_backup_path: Union[str, Path, None] = None,
    remote_export_path: Union[str, Path, None] = None,
    env: Optional[Mapping[str, str]] = None,
) -> VerificationReport:
    """Cross-check local DB, mapping table and the shared appData index.

    Read-only everywhere: the database is only queried, appData files are
    only read, Google Tasks is never contacted. ``local_backup_path`` /
    ``remote_export_path`` are optional; when given, their existence is
    reported (missing evidence is an issue).

    Note on ``cleared_gtasks_ids``: tasks created *after* the cutover never
    had a legacy ``Task.gtasks_id``, so they legitimately show up here. For
    the rollback-safety check, run verify before creating new tasks under
    the undated engine (see the runbook).
    """

    config, _ = appdata.read_config()
    index, _ = appdata.read_index()

    report = VerificationReport(
        local_engine_flag=resolve_undated_engine(env),
        engine_marker=config.get("engine") or None,
    )

    raw_tasks = index.get("tasks")
    index_tasks: Dict[str, object] = raw_tasks if isinstance(raw_tasks, dict) else {}

    with session_factory() as session:
        tasks = session.exec(select(Task)).all()
        mappings = session.exec(select(SyncMapUndated)).all()

    tasks_by_id = {str(task.id): task for task in tasks}
    mapped_task_ids = {mapping.task_id for mapping in mappings}

    for task in sorted(tasks, key=lambda item: item.id or 0):
        gtask_id = (task.gtasks_id or "").strip()
        if task.start is None and gtask_id and str(task.id) not in mapped_task_ids:
            report.missing_mappings.append(
                UnmappedTask(str(task.id), task.uid, gtask_id)
            )

    for mapping in sorted(mappings, key=lambda item: item.task_id):
        task = tasks_by_id.get(mapping.task_id)
        if task is None:
            report.uid_mismatches.append(
                UidMismatch(mapping.task_id, mapping.task_uid, None)
            )
        elif (mapping.task_uid or None) != task.uid:
            report.uid_mismatches.append(
                UidMismatch(mapping.task_id, mapping.task_uid, task.uid)
            )
        if not mapping.gtask_id:
            continue
        entry = index_tasks.get(mapping.gtask_id)
        if not isinstance(entry, dict) or entry.get("deleted"):
            report.missing_index_entries.append(mapping.gtask_id)
        if task is not None and not (task.gtasks_id or "").strip():
            report.cleared_gtasks_ids.append(mapping.task_id)

    by_uid: Dict[str, List[str]] = {}
    for gtask_id, entry in index_tasks.items():
        if not isinstance(entry, dict) or entry.get("deleted"):
            continue
        uid = entry.get("task_uid")
        if uid:
            by_uid.setdefault(str(uid), []).append(gtask_id)
    report.duplicate_index_uids = {
        uid: sorted(ids) for uid, ids in sorted(by_uid.items()) if len(ids) > 1
    }

    if local_backup_path is not None:
        report.backup_path = str(local_backup_path)
        report.backup_exists = Path(local_backup_path).exists()
    if remote_export_path is not None:
        report.export_path = str(remote_export_path)
        report.export_exists = Path(remote_export_path).exists()

    return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _default_appdata():
    from services.appdata import AppDataClient
    from services.google_auth import GoogleAuth

    return AppDataClient(GoogleAuth())


def _default_bridge():
    from services.google_auth import GoogleAuth
    from services.tasks_bridge import GoogleTasksBridge

    return GoogleTasksBridge(GoogleAuth())


def _print_backfill_report(report: BackfillReport) -> None:
    print(f"== Backfill {report.mode} ==")
    print(f"tasklist_id: {report.tasklist_id}")
    if report.blocked_reason:
        print(f"BLOCKED: {report.blocked_reason}")
    print(f"planned mappings: {len(report.mappings)}")
    for planned in report.mappings:
        print(
            f"  task {planned.task_id} (uid {planned.task_uid}) "
            f"-> gtask {planned.gtask_id}"
        )
    print(f"index entries to write: {len(report.index_entries)}")
    print(f"skipped: {len(report.skipped)}")
    for skip in report.skipped:
        print(f"  task {skip.task_id}: {skip.reason} (gtask {skip.gtask_id})")
    if report.mode == MODE_APPLY:
        print("APPLIED." if report.applied else "Nothing was applied.")
    else:
        print(
            "Dry-run: nothing was written anywhere. Review the plan above, "
            "then run 'apply --backup <path> --export <path>'."
        )


def _print_verification_report(report: VerificationReport) -> None:
    print("== Pilot verification ==")
    print(f"local engine flag (PLANNER_UNDATED_ENGINE): {report.local_engine_flag}")
    print(f"shared engine marker (planner_config.json): {report.engine_marker!r}")
    print(
        "unscheduled tasks with gtasks_id but no mapping: "
        f"{len(report.missing_mappings)}"
    )
    for item in report.missing_mappings:
        print(f"  task {item.task_id} (uid {item.task_uid}) gtask {item.gtask_id}")
    print(f"mapping rows with task_uid mismatch: {len(report.uid_mismatches)}")
    for item in report.uid_mismatches:
        print(
            f"  task {item.task_id}: mapping says {item.mapping_uid!r}, "
            f"Task.uid is {item.task_uid!r}"
        )
    print(
        "mapped gtask ids missing from the index: "
        f"{len(report.missing_index_entries)}"
    )
    for gtask_id in report.missing_index_entries:
        print(f"  {gtask_id}")
    print(
        "duplicate task_uid entries in the index: "
        f"{len(report.duplicate_index_uids)}"
    )
    for uid, gtask_ids in report.duplicate_index_uids.items():
        print(f"  {uid}: {', '.join(gtask_ids)}")
    print(
        "mapped tasks whose Task.gtasks_id was cleared: "
        f"{len(report.cleared_gtasks_ids)}"
    )
    for task_id in report.cleared_gtasks_ids:
        print(f"  task {task_id}")
    for label, path, exists in (
        ("local DB backup", report.backup_path, report.backup_exists),
        ("remote export", report.export_path, report.export_exists),
    ):
        if path is None:
            print(f"{label}: (not provided)")
        else:
            print(f"{label}: {path} ({'exists' if exists else 'MISSING'})")
    if report.ok:
        print("RESULT: OK")
    else:
        print("RESULT: ISSUES FOUND")
        for issue in report.issues:
            print(f"  - {issue}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="undated_migration_pilot",
        description=(
            "Pilot tooling for the undated ('Planner Inbox') sync migration. "
            "Test Google account only; dry-run/read-only by default; never "
            "starts the UI, never sets PLANNER_UNDATED_ENGINE, never deletes "
            "remote Google Tasks, never clears Task.gtasks_id."
        ),
        epilog=(
            "Run with the pilot data-dir override (APPDATA on Windows) — see "
            "docs/UNDATED_SYNC_PILOT_RUNBOOK.md. Exit codes: 0 ok, 1 issues, "
            "2 refused."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    backup = sub.add_parser(
        "backup", help="create the pre-cutover local app.db copy"
    )
    backup.add_argument("--db", default=None, help="database path (default: settings DB_PATH)")
    backup.add_argument("--backup-dir", default=None, help="target dir (default: settings backup dir)")

    export = sub.add_parser(
        "export",
        help="write a read-only JSON snapshot of the remote Planner Inbox state",
    )
    export.add_argument("--out", required=True, help="target file; existing files are never overwritten")
    export.add_argument("--tasklist-id", default=None)
    export.add_argument(
        "--allow-ensure-tasklist",
        action="store_true",
        help="opt-in: create the 'Planner Inbox' tasklist if missing (a remote write)",
    )

    dry = sub.add_parser("dry-run", help="plan the mapping backfill; writes nothing")
    dry.add_argument("--tasklist-id", default=None)
    dry.add_argument("--device-id", default=None)

    apply_cmd = sub.add_parser(
        "apply",
        help="run the backfill; requires existing backup and export evidence",
    )
    apply_cmd.add_argument("--backup", required=True, help="path returned by the 'backup' command")
    apply_cmd.add_argument("--export", required=True, help="path written by the 'export' command")
    apply_cmd.add_argument("--tasklist-id", default=None)
    apply_cmd.add_argument("--device-id", default=None)

    verify = sub.add_parser("verify", help="read-only consistency report")
    verify.add_argument("--backup", default=None, help="expected pre-cutover backup path")
    verify.add_argument("--export", default=None, help="expected remote export path")

    return parser


def main(
    argv: Optional[List[str]] = None,
    *,
    appdata_factory: Optional[Callable] = None,
    bridge_factory: Optional[Callable] = None,
    session_factory: Optional[Callable] = None,
) -> int:
    """Dispatch one pilot command; factories exist for offline tests."""

    args = build_parser().parse_args(argv)
    sessions = session_factory or get_session

    try:
        if args.command == "backup":
            path = create_precutover_backup(args.db, args.backup_dir)
            print(f"Pre-cutover backup created: {path}")
            print("Keep this path: 'apply' requires it as --backup evidence.")
            return 0

        if args.command == "export":
            payload = export_planner_inbox_snapshot(
                bridge=(bridge_factory or _default_bridge)(),
                appdata=(appdata_factory or _default_appdata)(),
                tasklist_id=args.tasklist_id,
                path=args.out,
                allow_ensure_tasklist=args.allow_ensure_tasklist,
            )
            tasklist = payload["tasklist"]
            print("== Planner Inbox export ==")
            print(f"tasklist: {tasklist['id']} ({tasklist['title']})")
            print(f"remote tasks (incl. hidden/deleted): {len(payload['tasks'])}")
            print(f"engine marker: {payload['planner_config'].get('engine')!r}")
            index_tasks = payload["planner_index"].get("tasks") or {}
            print(f"index entries: {len(index_tasks)}")
            print(f"written to: {args.out}")
            print("Keep this path: 'apply' requires it as --export evidence.")
            return 0

        if args.command == "dry-run":
            report = backfill_planner_mappings(
                appdata=(appdata_factory or _default_appdata)(),
                session_factory=sessions,
                tasklist_id=args.tasklist_id,
                device_id=args.device_id,
            )
            _print_backfill_report(report)
            return 1 if report.blocked_reason else 0

        if args.command == "apply":
            report = backfill_planner_mappings(
                appdata=(appdata_factory or _default_appdata)(),
                session_factory=sessions,
                tasklist_id=args.tasklist_id,
                device_id=args.device_id,
                apply=True,
                local_backup_path=args.backup,
                remote_export=args.export,
            )
            _print_backfill_report(report)
            print("Task.gtasks_id was not modified; rollback to legacy stays possible.")
            return 0

        if args.command == "verify":
            report = verify_pilot_state(
                appdata=(appdata_factory or _default_appdata)(),
                session_factory=sessions,
                local_backup_path=args.backup,
                remote_export_path=args.export,
            )
            _print_verification_report(report)
            return 0 if report.ok else 1
    except (ValueError, FileExistsError, FileNotFoundError, EngineOwnershipError) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2

    raise AssertionError(f"unhandled command: {args.command}")  # pragma: no cover


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "UnmappedTask",
    "UidMismatch",
    "VerificationReport",
    "verify_pilot_state",
    "build_parser",
    "main",
]
