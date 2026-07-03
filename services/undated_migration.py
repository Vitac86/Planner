"""Phase 3 migration tooling for the undated ("Planner Inbox") sync cutover.

Nothing in this module runs automatically: there is no call site in
``main.py`` or ``AppShell``, no import-time side effect, and no dependency on
the ``PLANNER_UNDATED_ENGINE`` flag. Every entry point is invoked explicitly
by an operator (script, REPL, tests) before a cutover; the default engine
stays ``"legacy"`` throughout (docs/SYNC_ENGINE_DECISION.md §5 Phase 3).

Entry points
------------
* :func:`export_planner_inbox_snapshot` — read-only snapshot of the remote
  "Planner Inbox" state (tasklist, all Google Tasks including hidden and
  deleted ones, parsed planner metadata, both appData files) taken before
  migration. Pairs with :func:`storage.backup.create_precutover_backup` for
  the local side.
* :func:`backfill_planner_mappings` — idempotent backfill that gives every
  existing local unscheduled task that already carries a legacy
  ``Task.gtasks_id`` the state the ``UndatedTasksSync`` engine expects: a
  clean ``SyncMapUndated`` row and a ``gtasks_index.json`` entry keyed by the
  Google Task id. Dry-run by default; ``apply=True`` must be requested
  explicitly and is refused unless backup/export evidence is provided (or
  explicitly waived).

Ownership marker contract
-------------------------
The shared ``planner_config.json`` carries the single-writer ``engine``
marker (see :class:`services.undated_tasks_sync.UndatedTasksSync`).

* **Expected before backfill:** the marker is vacant (``None``) or already
  ``"undated"``. Any other value means a foreign engine owns the list:
  ``apply`` raises :class:`EngineOwnershipError`, dry-run reports
  ``blocked_reason`` and plans nothing.
* **After backfill:** the marker is byte-for-byte what it was before — the
  backfill never writes ``planner_config.json`` at all. Claiming the marker
  remains the engine's own first write once ``PLANNER_UNDATED_ENGINE=undated``
  is set, so legacy and undated can never end up writing simultaneously:
  while the flag is ``"legacy"`` the undated engine is inert, and once the
  flag flips the legacy Google Tasks lane is blocked (Phase 2 wiring) and the
  marker tripwire covers older installations.

What ``apply`` touches — and nothing else
-----------------------------------------
* ``syncmapundated``: **inserts only** (``dirty_flag=0``); existing rows are
  never updated or deleted.
* ``gtasks_index.json``: one etag-guarded merge write adding entries for the
  planned Google Task ids (and filling the file's ``tasklist_id`` if vacant);
  existing entries are only completed, never clobbered — a tombstoned entry
  or one carrying a different ``task_uid`` blocks that item instead.
* ``Task`` rows are read, never written; ``Task.gtasks_id`` is preserved so
  rollback to the legacy engine stays possible until Phase 5.
* Google Tasks itself is never called: the backfill does not even accept a
  bridge, so no remote insert/update/delete can happen.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Union

from sqlmodel import select

from models import SyncMapUndated, Task
from services.appdata import AppDataClient
from services.undated_tasks_sync import (
    ENGINE_NAME,
    EngineOwnershipError,
    _isoformat,
    _normalise_priority,
    _normalise_status,
)
from storage.db import get_session
from storage.device import get_device_id

MODE_DRY_RUN = "dry-run"
MODE_APPLY = "apply"

EXPORT_VERSION = 1


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Remote export
# ---------------------------------------------------------------------------

def export_planner_inbox_snapshot(
    *,
    bridge,
    appdata: AppDataClient,
    tasklist_id: Optional[str] = None,
    path: Union[str, Path, None] = None,
    allow_ensure_tasklist: bool = False,
    now: Optional[datetime] = None,
) -> Dict[str, object]:
    """Snapshot the remote "Planner Inbox" state before migration.

    The payload carries the tasklist id/title, every Google Task returned by
    ``bridge.fetch_all`` (which includes hidden and deleted items and the
    parsed planner ``metadata``), the current ``planner_config.json`` and
    ``gtasks_index.json`` contents, and the export timestamp.

    The tasklist id is resolved from the explicit argument, then from the
    config, then from the index. By default the helper performs **no remote
    writes**: if the id cannot be resolved, the task list is exported empty
    rather than calling ``ensure_tasklist`` (which would create the list on a
    pristine account); pass ``allow_ensure_tasklist=True`` to opt in.

    When ``path`` is given the payload is written there as JSON; an existing
    file is never overwritten (``FileExistsError``). The payload is returned
    either way.
    """

    config, _ = appdata.read_config()
    index, _ = appdata.read_index()

    resolved = (
        tasklist_id
        or config.get("tasklist_id")
        or index.get("tasklist_id")
    )
    if not resolved and allow_ensure_tasklist and bridge is not None:
        resolved = bridge.ensure_tasklist()

    tasks: List[Dict[str, object]] = []
    if resolved and bridge is not None:
        tasks = [dict(item) for item in bridge.fetch_all(resolved)]

    payload: Dict[str, object] = {
        "version": EXPORT_VERSION,
        "exported_at": _isoformat(now),
        "tasklist": {
            "id": resolved,
            "title": getattr(bridge, "tasklist_title", "Planner Inbox"),
        },
        "tasks": tasks,
        "planner_config": config,
        "planner_index": index,
    }

    if path is not None:
        target = Path(path)
        if target.exists():
            raise FileExistsError(
                f"refusing to overwrite existing export snapshot: {target}"
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str),
            encoding="utf-8",
        )

    return payload


# ---------------------------------------------------------------------------
# Backfill
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PlannedMapping:
    """One ``SyncMapUndated`` row the backfill will insert."""

    task_id: str
    task_uid: str
    gtask_id: str
    tasklist_id: str
    dirty_flag: int = 0


@dataclass(frozen=True)
class BackfillSkip:
    """One local task the backfill deliberately left alone."""

    task_id: str
    reason: str
    gtask_id: Optional[str] = None


@dataclass
class BackfillReport:
    """Plan (dry-run) or outcome (apply) of one backfill invocation."""

    mode: str
    tasklist_id: Optional[str] = None
    mappings: List[PlannedMapping] = field(default_factory=list)
    index_entries: Dict[str, Dict[str, object]] = field(default_factory=dict)
    skipped: List[BackfillSkip] = field(default_factory=list)
    applied: bool = False
    blocked_reason: Optional[str] = None


def backfill_planner_mappings(
    *,
    appdata: AppDataClient,
    session_factory=get_session,
    tasklist_id: Optional[str] = None,
    apply: bool = False,
    local_backup_path: Union[str, Path, None] = None,
    remote_export: Union[str, Path, Dict[str, object], None] = None,
    confirm_without_backup: bool = False,
    device_id: Optional[str] = None,
) -> BackfillReport:
    """Backfill engine state for tasks that already live in Google Tasks.

    For every local :class:`Task` with ``start is None``, a non-empty
    ``gtasks_id`` and no ``SyncMapUndated`` row yet, plan a clean mapping row
    (``dirty_flag=0``) plus a ``gtasks_index.json`` entry keyed by the Google
    Task id (``task_uid``/``status``/``priority``/``updated_at``/
    ``device_id`` — the exact shape ``UndatedTasksSync`` writes).

    Dry-run (the default) returns the full plan and writes nothing anywhere.
    ``apply=True`` performs the writes but is refused (``ValueError``) unless
    both ``local_backup_path`` (see
    :func:`storage.backup.create_precutover_backup`) and ``remote_export``
    (path to, or payload from, :func:`export_planner_inbox_snapshot`) are
    provided, or ``confirm_without_backup=True`` waives the gate explicitly.

    Idempotent: already-mapped tasks are skipped, existing rows and foreign
    or tombstoned index entries are never overwritten, ``Task.gtasks_id`` is
    never cleared, and Google Tasks is never called.
    """

    mode = MODE_APPLY if apply else MODE_DRY_RUN
    report = BackfillReport(mode=mode)

    if apply:
        _require_backup_evidence(local_backup_path, remote_export, confirm_without_backup)

    config, _ = appdata.read_config()
    owner = config.get("engine")
    if owner not in (None, "", ENGINE_NAME):
        reason = (
            f"planner_config.json says engine {owner!r} owns the tasklist; "
            "refusing to prepare data for a second writer"
        )
        if apply:
            raise EngineOwnershipError(reason)
        report.blocked_reason = reason
        return report

    index, _ = appdata.read_index()
    resolved_tasklist = (
        tasklist_id
        or config.get("tasklist_id")
        or index.get("tasklist_id")
    )
    if not resolved_tasklist:
        reason = (
            "cannot resolve the Planner Inbox tasklist id: pass tasklist_id "
            "explicitly or run export_planner_inbox_snapshot first"
        )
        if apply:
            raise ValueError(reason)
        report.blocked_reason = reason
        return report

    report.tasklist_id = resolved_tasklist
    resolved_device = device_id or get_device_id()
    index_tasks = index.get("tasks") if isinstance(index.get("tasks"), dict) else {}

    with session_factory() as session:
        existing = session.exec(select(SyncMapUndated)).all()
        mapped_task_ids = {mapping.task_id for mapping in existing}
        mapped_gtask_ids = {mapping.gtask_id for mapping in existing if mapping.gtask_id}

        candidates = session.exec(
            select(Task).where(Task.start == None)  # noqa: E711
        ).all()
        planned_gtask_ids: set[str] = set()

        for task in sorted(candidates, key=lambda item: item.id or 0):
            gtask_id = (task.gtasks_id or "").strip()
            if not gtask_id:
                continue  # never synced to Google Tasks; nothing to backfill
            task_id = str(task.id)
            if task_id in mapped_task_ids:
                report.skipped.append(
                    BackfillSkip(task_id, "SyncMapUndated row already exists", gtask_id)
                )
                continue
            if gtask_id in mapped_gtask_ids:
                report.skipped.append(
                    BackfillSkip(
                        task_id,
                        "gtask id is already mapped to another local task",
                        gtask_id,
                    )
                )
                continue
            if gtask_id in planned_gtask_ids:
                report.skipped.append(
                    BackfillSkip(
                        task_id,
                        "gtask id is duplicated on another local task",
                        gtask_id,
                    )
                )
                continue

            existing_entry = index_tasks.get(gtask_id)
            if isinstance(existing_entry, dict):
                if existing_entry.get("deleted"):
                    report.skipped.append(
                        BackfillSkip(
                            task_id,
                            "index entry is a deletion tombstone",
                            gtask_id,
                        )
                    )
                    continue
                entry_uid = existing_entry.get("task_uid")
                if entry_uid and str(entry_uid) != task.uid:
                    report.skipped.append(
                        BackfillSkip(
                            task_id,
                            "index entry belongs to a different task_uid",
                            gtask_id,
                        )
                    )
                    continue

            planned_gtask_ids.add(gtask_id)
            report.mappings.append(
                PlannedMapping(
                    task_id=task_id,
                    task_uid=task.uid,
                    gtask_id=gtask_id,
                    tasklist_id=resolved_tasklist,
                )
            )
            entry = _planned_index_entry(existing_entry, task, resolved_device)
            if entry != existing_entry:
                report.index_entries[gtask_id] = entry

    if not apply:
        return report

    with session_factory() as session:
        for planned in report.mappings:
            if session.get(SyncMapUndated, planned.task_id) is not None:
                continue
            session.add(
                SyncMapUndated(
                    task_id=planned.task_id,
                    task_uid=planned.task_uid,
                    gtask_id=planned.gtask_id,
                    tasklist_id=planned.tasklist_id,
                    dirty_flag=planned.dirty_flag,
                    updated_at_utc=_utcnow(),
                )
            )
        session.commit()

    if report.index_entries:
        payload, etag = appdata.read_index()
        merged = _merge_index_entries(payload, report.index_entries, resolved_tasklist)
        appdata.write_index(
            merged,
            if_match=etag,
            on_conflict=lambda remote: _merge_index_entries(
                remote, report.index_entries, resolved_tasklist
            ),
        )

    report.applied = True
    return report


def _require_backup_evidence(
    local_backup_path: Union[str, Path, None],
    remote_export: Union[str, Path, Dict[str, object], None],
    confirm_without_backup: bool,
) -> None:
    if confirm_without_backup:
        return
    if local_backup_path is None or remote_export is None:
        raise ValueError(
            "apply requires local_backup_path and remote_export (path or "
            "payload); pass confirm_without_backup=True to waive explicitly"
        )
    backup = Path(local_backup_path)
    if not backup.exists():
        raise ValueError(f"local backup does not exist: {backup}")
    if not isinstance(remote_export, dict):
        export_path = Path(remote_export)
        if not export_path.exists():
            raise ValueError(f"remote export does not exist: {export_path}")


def _planned_index_entry(
    existing: Optional[object],
    task: Task,
    device_id: str,
) -> Dict[str, object]:
    """Complete an index entry without clobbering existing metadata."""
    entry: Dict[str, object] = dict(existing) if isinstance(existing, dict) else {}
    # Legacy device-local ids must not survive in shared metadata.
    entry.pop("task_id", None)
    entry["task_uid"] = task.uid
    entry.setdefault("priority", _normalise_priority(task.priority))
    entry.setdefault("status", _normalise_status(task.status))
    entry.setdefault("updated_at", _isoformat(task.updated_at))
    entry.setdefault("device_id", device_id)
    return entry


def _merge_index_entries(
    payload,
    entries: Dict[str, Dict[str, object]],
    tasklist_id: Optional[str],
) -> Dict[str, object]:
    """Merge planned entries into an index payload, never clobbering.

    Also used on the etag-conflict path, so it re-checks tombstones and
    foreign ``task_uid`` values against the (possibly newer) remote payload
    instead of trusting the plan.
    """
    data = dict(payload or {})
    data.setdefault("version", 1)
    tasks = data.get("tasks")
    tasks = dict(tasks) if isinstance(tasks, dict) else {}

    for gtask_id, planned in entries.items():
        current = tasks.get(gtask_id)
        if isinstance(current, dict):
            if current.get("deleted"):
                continue
            current_uid = current.get("task_uid")
            if current_uid and str(current_uid) != str(planned.get("task_uid")):
                continue
            merged = dict(current)
            merged.pop("task_id", None)
            for key, value in planned.items():
                merged.setdefault(key, value)
            merged["task_uid"] = planned.get("task_uid")
            tasks[gtask_id] = merged
        else:
            tasks[gtask_id] = dict(planned)

    data["tasks"] = tasks
    if tasklist_id and not data.get("tasklist_id"):
        data["tasklist_id"] = tasklist_id
    return data


__all__ = [
    "export_planner_inbox_snapshot",
    "backfill_planner_mappings",
    "BackfillReport",
    "BackfillSkip",
    "PlannedMapping",
    "MODE_DRY_RUN",
    "MODE_APPLY",
]
