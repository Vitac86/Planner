"""Manual inspection, repair and selective replay of dead-lettered ops.

Dead-letter rows are written by ``PendingOpsQueue.mark_failed`` when a push
fails with a non-retryable 4xx and are never touched by the push worker
again. Most of the historic rows are ``gcal_update`` ops that failed with
HTTP 400 "Invalid start time." because the local task predates
``Task.gcal_all_day`` and still carries ``gcal_all_day=False`` for an
all-day remote event.

This module is a hand tool, not part of the sync loop:

* nothing here runs automatically — every entry point takes an explicit
  list of ``dead_letter_id`` values and refuses an empty selection;
* ``repair`` and ``replay`` default to ``dry_run=True`` (no DB writes);
* repairs set only ``Task.gcal_all_day`` and only after reading the real
  remote event shape from Google Calendar; title/start/duration/links are
  never modified and local tasks are never deleted;
* replay moves a dead-letter row back to ``PendingOp`` and deletes the
  dead-letter row in the same transaction, so a failure leaves the row
  in place (rollback-safe).

Manual usage against the real database::

    python -m services.dead_letter_recovery list
    python -m services.dead_letter_recovery verify --ids 3 5
    python -m services.dead_letter_recovery repair --ids 3 5          # dry-run
    python -m services.dead_letter_recovery repair --ids 3 5 --apply
    python -m services.dead_letter_recovery replay --ids 3 5 --apply
"""

from __future__ import annotations

import argparse
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, List, Optional, Sequence, Union

from sqlmodel import select

from models.pending_op import DeadLetterOp, PendingOp
from services.pending_ops_queue import VALID_OPS
from storage.db import get_session
from utils.datetime_utils import utc_now


REMOTE_ALL_DAY = "all_day"
REMOTE_TIMED = "timed"
REMOTE_MISSING = "missing"
REMOTE_CANCELLED = "cancelled"
REMOTE_UNKNOWN = "unknown"


def classify_event_shape(event: Optional[dict]) -> str:
    """Shape of a Google Calendar event as returned by events().get()."""
    if not event:
        return REMOTE_MISSING
    if event.get("status") == "cancelled":
        return REMOTE_CANCELLED
    start = event.get("start") or {}
    if "date" in start:
        return REMOTE_ALL_DAY
    if "dateTime" in start:
        return REMOTE_TIMED
    return REMOTE_UNKNOWN


@dataclass
class DeadLetterRow:
    dead_letter_id: int
    op: str
    task_id: int
    payload: dict
    attempts: int
    last_error: Optional[str]
    failed_at: Optional[datetime]
    task_exists: bool
    task_title: Optional[str] = None
    task_start: Optional[datetime] = None
    task_duration_minutes: Optional[int] = None
    task_gcal_event_id: Optional[str] = None
    task_gcal_all_day: Optional[bool] = None


@dataclass
class RepairAction:
    dead_letter_id: int
    task_id: Optional[int]
    event_id: Optional[str] = None
    remote_shape: Optional[str] = None
    local_all_day: Optional[bool] = None
    action: str = "skip"  # set_all_day | clear_all_day | none | skip
    reason: Optional[str] = None
    applied: bool = False


@dataclass
class ReplayAction:
    dead_letter_id: int
    op: Optional[str] = None
    task_id: Optional[int] = None
    action: str = "skip"  # requeue | skip
    reason: Optional[str] = None
    applied: bool = False


IdSelection = Union[int, Iterable[int]]


def _require_explicit_ids(dead_letter_ids: Optional[IdSelection]) -> List[int]:
    if isinstance(dead_letter_ids, int):
        return [dead_letter_ids]
    ids = [int(i) for i in (dead_letter_ids or [])]
    if not ids:
        raise ValueError(
            "Explicit dead_letter_ids are required; repairing or replaying "
            "all dead-letter rows at once is not supported."
        )
    return ids


def _parse_payload(raw: str) -> dict:
    try:
        payload = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


class DeadLetterRecovery:
    """Selective dead-letter tooling wired to a GoogleCalendar and TaskService."""

    def __init__(self, gcal, repo, session_factory=None, logger=None):
        self.gcal = gcal
        self.repo = repo
        self._session = session_factory or get_session
        self.logger = logger or logging.getLogger("planner.sync")

    # ------------------------------------------------------------------
    # 1. Inspection (read-only)
    def inspect(self, dead_letter_ids: Optional[IdSelection] = None) -> List[DeadLetterRow]:
        """List dead-letter rows (all by default) with the local task snapshot."""
        if isinstance(dead_letter_ids, int):
            dead_letter_ids = [dead_letter_ids]
        with self._session() as session:
            stmt = select(DeadLetterOp).order_by(
                DeadLetterOp.failed_at.asc(), DeadLetterOp.id.asc()
            )
            if dead_letter_ids is not None:
                stmt = stmt.where(DeadLetterOp.id.in_([int(i) for i in dead_letter_ids]))
            records = list(session.exec(stmt))

        rows: List[DeadLetterRow] = []
        for record in records:
            task = self.repo.get(record.task_id)
            rows.append(
                DeadLetterRow(
                    dead_letter_id=record.id,
                    op=record.op,
                    task_id=record.task_id,
                    payload=_parse_payload(record.payload),
                    attempts=record.attempts,
                    last_error=record.last_error,
                    failed_at=record.failed_at,
                    task_exists=task is not None,
                    task_title=getattr(task, "title", None),
                    task_start=getattr(task, "start", None),
                    task_duration_minutes=getattr(task, "duration_minutes", None),
                    task_gcal_event_id=getattr(task, "gcal_event_id", None),
                    task_gcal_all_day=getattr(task, "gcal_all_day", None),
                )
            )
        return rows

    # ------------------------------------------------------------------
    # 2. Remote verification (read-only) and 3. selective repair
    def verify_remote(self, dead_letter_ids: IdSelection) -> List[RepairAction]:
        """Fetch the remote event for each selected op and report its shape.

        Read-only: identical to ``repair(..., dry_run=True)``.
        """
        return self.repair(dead_letter_ids, dry_run=True)

    def repair(self, dead_letter_ids: IdSelection, dry_run: bool = True) -> List[RepairAction]:
        """Align ``Task.gcal_all_day`` with the real remote event shape.

        Only the selected rows are examined; only ``gcal_all_day`` is ever
        written and only when the remote shape contradicts it. Everything
        else on the task (title/start/duration/gcal_event_id/gtasks_id)
        is left untouched, as are unrelated tasks.
        """
        ids = _require_explicit_ids(dead_letter_ids)
        by_id = {row.dead_letter_id: row for row in self.inspect(ids)}

        actions: List[RepairAction] = []
        for dl_id in ids:
            row = by_id.get(dl_id)
            if row is None:
                actions.append(RepairAction(dl_id, None, reason="dead-letter row not found"))
                continue
            action = RepairAction(dl_id, row.task_id)
            if not row.op.startswith("gcal_"):
                action.reason = f"not a Calendar op: {row.op}"
                actions.append(action)
                continue
            task = self.repo.get(row.task_id)
            if task is None:
                action.reason = "local task no longer exists"
                actions.append(action)
                continue
            event_id = row.payload.get("eventId") or task.gcal_event_id
            if not event_id:
                action.reason = "no eventId in payload or task"
                actions.append(action)
                continue

            action.event_id = event_id
            action.local_all_day = bool(getattr(task, "gcal_all_day", False))
            action.remote_shape = classify_event_shape(self._remote_event(event_id))

            if action.remote_shape == REMOTE_ALL_DAY and not action.local_all_day:
                action.action = "set_all_day"
            elif action.remote_shape == REMOTE_TIMED and action.local_all_day:
                action.action = "clear_all_day"
            elif action.remote_shape in (REMOTE_ALL_DAY, REMOTE_TIMED):
                action.action = "none"
                action.reason = "local flag already matches remote shape"
            else:
                action.reason = f"remote event is {action.remote_shape}; nothing to repair"

            if not dry_run and action.action in ("set_all_day", "clear_all_day"):
                # updated_at is preserved on purpose: the repair is metadata
                # only and must not make the task "newer" for conflict
                # resolution or trigger new pushes by itself.
                self.repo.update_from_sync(
                    task.id,
                    gcal_all_day=(action.action == "set_all_day"),
                    updated_at=task.updated_at,
                )
                action.applied = True
                self.logger.info(
                    "Dead-letter repair: task %s gcal_all_day -> %s (dead_letter_id=%s, event %s)",
                    task.id,
                    action.action == "set_all_day",
                    dl_id,
                    event_id,
                )
            actions.append(action)
        return actions

    def _remote_event(self, event_id: str) -> Optional[dict]:
        connect = getattr(self.gcal, "connect", None)
        if callable(connect):
            connect()
        return self.gcal.get_event_by_id(event_id)

    # ------------------------------------------------------------------
    # 4. Selective replay (explicit ids only, dry-run by default)
    def replay(self, dead_letter_ids: IdSelection, dry_run: bool = True) -> List[ReplayAction]:
        """Move the selected dead-letter rows back to the pending queue.

        The PendingOp insert and the dead-letter delete happen in one
        transaction per row: if anything fails before commit, the
        dead-letter row survives untouched. Attempts are reset so the
        normal worker backoff applies from scratch.
        """
        ids = _require_explicit_ids(dead_letter_ids)
        actions: List[ReplayAction] = []
        for dl_id in ids:
            with self._session() as session:
                record = session.get(DeadLetterOp, dl_id)
                if record is None:
                    actions.append(ReplayAction(dl_id, reason="dead-letter row not found"))
                    continue
                action = ReplayAction(dl_id, op=record.op, task_id=record.task_id)
                if record.op not in VALID_OPS:
                    action.reason = f"unsupported op: {record.op}"
                    actions.append(action)
                    continue
                if record.op in ("gcal_create", "gcal_update") and self.repo.get(record.task_id) is None:
                    action.reason = "local task no longer exists"
                    actions.append(action)
                    continue
                action.action = "requeue"
                if dry_run:
                    actions.append(action)
                    continue
                session.add(
                    PendingOp(
                        op=record.op,
                        task_id=record.task_id,
                        payload=record.payload,
                        attempts=0,
                        last_error=f"requeued from dead-letter #{dl_id}"[:1000],
                        created_at=record.created_at,
                        next_try_at=utc_now(),
                    )
                )
                session.delete(record)
                session.commit()
                action.applied = True
                self.logger.info(
                    "Dead-letter replay: op %s for task %s requeued (dead_letter_id=%s)",
                    record.op,
                    record.task_id,
                    dl_id,
                )
                actions.append(action)
        return actions


# ---------------------------------------------------------------------------
# Manual CLI (no automatic replay: ids are always explicit, writes need --apply)
def _build_default() -> DeadLetterRecovery:
    from services.google_auth import GoogleAuth
    from services.google_calendar import GoogleCalendar
    from services.tasks import TaskService
    from storage.db import init_db

    init_db()
    return DeadLetterRecovery(GoogleCalendar(GoogleAuth()), TaskService())


def _print_rows(rows: Sequence) -> None:
    for row in rows:
        print(row)
    if not rows:
        print("(no rows)")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="dead_letter_recovery",
        description="Inspect, repair and selectively replay dead-lettered sync ops.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="list all dead-letter rows with local task state")
    for name, doc in (
        ("verify", "fetch remote event shape for selected rows (read-only)"),
        ("repair", "align Task.gcal_all_day with the remote shape"),
        ("replay", "move selected rows back to the pending queue"),
    ):
        cmd = sub.add_parser(name, help=doc)
        cmd.add_argument("--ids", type=int, nargs="+", required=True,
                         help="explicit dead_letter_id values (no replay-all)")
        if name in ("repair", "replay"):
            cmd.add_argument("--apply", action="store_true",
                             help="actually write; default is dry-run")
    args = parser.parse_args(argv)

    tool = _build_default()
    if args.command == "list":
        _print_rows(tool.inspect())
    elif args.command == "verify":
        _print_rows(tool.verify_remote(args.ids))
    elif args.command == "repair":
        _print_rows(tool.repair(args.ids, dry_run=not args.apply))
        if not args.apply:
            print("Dry-run: nothing was written. Re-run with --apply to repair.")
    elif args.command == "replay":
        _print_rows(tool.replay(args.ids, dry_run=not args.apply))
        if not args.apply:
            print("Dry-run: nothing was requeued. Re-run with --apply to replay.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "DeadLetterRecovery",
    "DeadLetterRow",
    "RepairAction",
    "ReplayAction",
    "classify_event_shape",
    "REMOTE_ALL_DAY",
    "REMOTE_CANCELLED",
    "REMOTE_MISSING",
    "REMOTE_TIMED",
    "REMOTE_UNKNOWN",
]
