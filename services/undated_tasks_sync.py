"""Synchronization of undated Planner tasks with Google Tasks.

Identity model
--------------
The stable cross-device identity of a task is ``Task.uid`` (uuid4). The local
autoincrement ``Task.id`` is device-local and never leaves the device: the
shared appDataFolder index stores ``task_uid`` per Google Task, while the
local ``SyncMapUndated`` table keeps both the local id (primary key, for fast
local lookup) and the uid.

Deletion model
--------------
Deletions are represented as tombstones in the shared index entry:
``{"deleted": true, "reason": "deleted" | "scheduled", ...}``.

* Local deletion: the remote Google Task is deleted and the index entry is
  tombstoned (``reason="deleted"``), either eagerly via
  :meth:`UndatedTasksSync.on_task_deleted` or lazily by ``push_dirty`` when it
  finds a mapping whose local task no longer exists.
* Remote deletion (remote task flagged ``deleted``, missing from the list, or
  tombstoned in the index by another device): the local task is deleted only
  when it is clean (not dirty) and still undated; a task with unsynced local
  edits is kept, detached from the dead Google Task and re-pushed (edits win
  over deletion). ``reason="scheduled"`` tombstones never touch the local
  task — they only release the Inbox mapping, because ownership moved to the
  Calendar lane.
* A live remote task that was edited *after* a tombstone was recorded
  resurrects the entry (edit wins); otherwise the recorded deletion is
  finished by deleting the surviving remote task.

All deletion handling is idempotent: replaying a pull or push after
convergence changes nothing.

Transition hooks (not wired into AppShell yet)
----------------------------------------------
:meth:`on_task_unscheduled` (ownership arrives at this engine) and
:meth:`on_task_scheduled` (ownership leaves to the Calendar lane) are the
seams a future migration step wires into ``TaskService`` events. See
``docs/SYNC_ENGINE_DECISION.md`` §5.
"""
from __future__ import annotations

import copy
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional

from sqlmodel import select

from core.priorities import DEFAULT_PRIORITY, normalize_priority
from core.settings import GOOGLE_SYNC
from models import SyncMapUndated, Task
from services.appdata import AppDataClient
from services.tasks_bridge import GoogleTasksBridge
from storage.db import get_session
from storage.device import get_device_id

logger = logging.getLogger("planner.sync.undated")

TOMBSTONE_REASON_DELETED = "deleted"
TOMBSTONE_REASON_SCHEDULED = "scheduled"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _isoformat(value: Optional[datetime]) -> str:
    if value is None:
        value = _utcnow()
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def _parse_timestamp(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except (TypeError, ValueError):
        return None


def _normalise_status(value: Optional[str], fallback: str = "todo") -> str:
    if not value:
        return fallback
    lowered = str(value).strip().lower()
    if lowered in {"todo", "doing", "done"}:
        return lowered
    if lowered in {"completed", "complete", "finished"}:
        return "done"
    if lowered in {"needsaction", "needs_action"}:
        return "todo"
    return fallback


def _normalise_priority(value: Optional[int]) -> int:
    if value is None:
        return DEFAULT_PRIORITY
    try:
        return normalize_priority(int(value))
    except (TypeError, ValueError):
        return DEFAULT_PRIORITY


@dataclass(frozen=True)
class SyncSkippedItem:
    """One remote or local item the sync could not process."""

    stage: str  # "pull" or "push"
    reason: str
    gtask_id: Optional[str] = None
    local_task_id: Optional[str] = None


@dataclass
class SyncReport:
    """Outcome of one sync run, including deterministically skipped items."""

    pulled: bool = False
    pushed: bool = False
    skipped: List[SyncSkippedItem] = field(default_factory=list)

    def changed(self) -> bool:
        return self.pulled or self.pushed

    def skip(
        self,
        stage: str,
        reason: str,
        *,
        gtask_id: Optional[str] = None,
        local_task_id: Optional[str] = None,
    ) -> None:
        item = SyncSkippedItem(
            stage=stage,
            reason=reason,
            gtask_id=gtask_id,
            local_task_id=local_task_id,
        )
        self.skipped.append(item)
        logger.warning(
            "undated sync skipped [%s] gtask=%s task=%s: %s",
            stage,
            gtask_id,
            local_task_id,
            reason,
        )


class UndatedTasksSync:
    """Encapsulates Google Tasks synchronisation for undated Planner tasks."""

    def __init__(
        self,
        auth,
        *,
        bridge: GoogleTasksBridge | None = None,
        session_factory=get_session,
        appdata: AppDataClient | None = None,
        device_id: Optional[str] = None,
    ) -> None:
        self.auth = auth
        self.bridge = bridge or GoogleTasksBridge(auth)
        self.appdata = appdata or AppDataClient(auth)
        self._session_factory = session_factory
        self.device_id = device_id or get_device_id()

        self._tasklist_id: Optional[str] = None
        self._config_cache: Optional[Dict[str, object]] = None
        self._config_etag: Optional[str] = None
        self._index_cache: Optional[Dict[str, object]] = None
        self._index_etag: Optional[str] = None
        self._index_dirty = False
        self.last_report = SyncReport()

    # ----- public helpers -----
    @property
    def tasklist_title(self) -> str:
        return self.bridge.tasklist_title if self.bridge else "Planner Inbox"

    def status_text(self) -> str:
        if not GOOGLE_SYNC.enabled:
            return "Tasks: синхронизация отключена"
        if not self.bridge:
            return "Tasks: недоступны"
        tasklist_id = self._tasklist_id or self._load_config().get("tasklist_id")
        if not tasklist_id:
            return "Tasks: не подключено"
        return f"Tasks: подключено, список: {self.tasklist_title}"

    def reset_cache(self) -> None:
        self._tasklist_id = None
        self._config_cache = None
        self._config_etag = None
        self._index_cache = None
        self._index_etag = None
        self._index_dirty = False

    def sync(self) -> bool:
        report = SyncReport()
        self.last_report = report
        report.pulled = self._run_pull(report)
        report.pushed = self._run_push(report)
        return report.changed()

    def pull(self) -> bool:
        report = SyncReport()
        self.last_report = report
        report.pulled = self._run_pull(report)
        return report.pulled

    def push_dirty(self) -> bool:
        report = SyncReport()
        self.last_report = report
        report.pushed = self._run_push(report)
        return report.pushed

    # ----- transition / deletion hooks (to be wired into AppShell later) -----
    def on_task_unscheduled(self, task_id: int) -> None:
        """The task lost its date: ownership arrives at this engine.

        Marks the task dirty so the next ``push_dirty`` publishes it to the
        "Planner Inbox" list. The caller (a future migration step) must also
        stop the Calendar lane from writing this task to Google Tasks.
        """
        self.mark_dirty(task_id)

    def on_task_scheduled(self, task_id: int) -> None:
        """The task gained a date: ownership leaves to the Calendar lane.

        Removes the Google Task and records a ``scheduled`` tombstone so
        other devices release their Inbox mapping without deleting the task.
        """
        self.remove_mapping(
            task_id,
            delete_remote=True,
            tombstone_reason=TOMBSTONE_REASON_SCHEDULED,
        )

    def on_task_deleted(self, task_id: int) -> None:
        """The local task was deleted: propagate the deletion remotely."""
        self.remove_mapping(
            task_id,
            delete_remote=True,
            tombstone_reason=TOMBSTONE_REASON_DELETED,
        )

    # ----- high level operations -----
    def mark_dirty(self, task_id: int) -> None:
        if not GOOGLE_SYNC.enabled:
            return

        gtask_id = None
        task_snapshot: Optional[Task] = None

        with self._session_factory() as session:
            task = session.get(Task, task_id)
            if not task:
                return
            task_snapshot = copy.copy(task)

            mapping = session.get(SyncMapUndated, str(task_id))
            if mapping is None:
                mapping = SyncMapUndated(
                    task_id=str(task_id),
                    task_uid=task.uid,
                    gtask_id=None,
                    tasklist_id=self._tasklist_id or "",
                    dirty_flag=1,
                    updated_at_utc=_utcnow(),
                )
            else:
                mapping.dirty_flag = 1
                mapping.task_uid = task.uid
                mapping.updated_at_utc = _utcnow()
            gtask_id = mapping.gtask_id
            session.add(mapping)
            session.commit()

        if gtask_id and task_snapshot:
            entry = self._ensure_index_entry(gtask_id, allow_create=True)
            self._write_live_entry(entry, task_snapshot)
            self._index_dirty = True
            self._persist_index_if_dirty()

    def remove_mapping(
        self,
        task_id: int,
        *,
        delete_remote: bool = False,
        tombstone_reason: Optional[str] = None,
    ) -> None:
        gtask_id = None
        task_uid = None
        tasklist_id = None

        with self._session_factory() as session:
            mapping = session.get(SyncMapUndated, str(task_id))
            if not mapping:
                return
            gtask_id = mapping.gtask_id
            task_uid = mapping.task_uid
            tasklist_id = mapping.tasklist_id or self._tasklist_id
            session.delete(mapping)
            session.commit()

        if gtask_id:
            if tombstone_reason:
                self._tombstone_index_entry(gtask_id, tombstone_reason, task_uid=task_uid)
            else:
                index = self._get_index()
                if index["tasks"].pop(gtask_id, None) is not None:
                    self._index_dirty = True
            self._persist_index_if_dirty()

        if delete_remote and gtask_id and tasklist_id:
            try:
                self.bridge.delete_task(tasklist_id, gtask_id)
            except Exception as exc:
                # The tombstone is already recorded; the next pull finishes
                # the deletion if the remote task survived.
                logger.warning(
                    "failed to delete remote task %s: %s", gtask_id, exc
                )

    # ----- pull -----
    def _run_pull(self, report: SyncReport) -> bool:
        if not self._can_sync():
            return False

        tasklist_id = self._ensure_tasklist_id()
        if not tasklist_id:
            report.skip("pull", "tasklist is unavailable")
            return False

        try:
            remote_tasks = self.bridge.fetch_all(tasklist_id)
        except Exception as exc:
            report.skip("pull", f"fetch_all failed: {exc}")
            return False

        changed = False
        seen_gtask_ids: set[str] = set()

        with self._session_factory() as session:
            mappings = {
                mapping.gtask_id: mapping
                for mapping in session.exec(
                    select(SyncMapUndated).where(SyncMapUndated.tasklist_id == tasklist_id)
                ).all()
                if mapping.gtask_id
            }

            for item in remote_tasks:
                gtask_id = item.get("id") if isinstance(item, dict) else None
                if not gtask_id:
                    report.skip("pull", "remote item without id")
                    continue
                seen_gtask_ids.add(gtask_id)
                try:
                    if self._process_remote_item(
                        session, tasklist_id, gtask_id, item, mappings, report
                    ):
                        changed = True
                except ValueError as exc:
                    report.skip("pull", str(exc), gtask_id=gtask_id)
                except Exception as exc:
                    report.skip("pull", f"unexpected error: {exc}", gtask_id=gtask_id)

            # A mapped task missing from the (complete, deleted-inclusive)
            # remote listing was deleted remotely.
            for gtask_id, mapping in list(mappings.items()):
                if gtask_id in seen_gtask_ids:
                    continue
                reason = self._tombstone_reason_for(gtask_id) or TOMBSTONE_REASON_DELETED
                if self._apply_remote_deletion(session, mapping, gtask_id, reason):
                    changed = True

            session.commit()

        self._persist_index_if_dirty()
        if changed:
            self._update_last_sync()
        return changed

    def _process_remote_item(
        self,
        session,
        tasklist_id: str,
        gtask_id: str,
        item: Dict[str, object],
        mappings: Dict[str, SyncMapUndated],
        report: SyncReport,
    ) -> bool:
        metadata = item.get("metadata") or item.get("detected_meta") or {}
        if not isinstance(metadata, dict):
            raise ValueError(
                f"malformed planner metadata ({type(metadata).__name__} instead of dict)"
            )

        index = self._get_index()
        raw_entry = index["tasks"].get(gtask_id)
        if raw_entry is not None and not isinstance(raw_entry, dict):
            raise ValueError(
                f"malformed index entry ({type(raw_entry).__name__} instead of dict)"
            )

        mapping = mappings.get(gtask_id)
        remote_deleted = bool(item.get("deleted"))
        tombstone_reason = None
        if raw_entry and raw_entry.get("deleted"):
            tombstone_reason = str(raw_entry.get("reason") or TOMBSTONE_REASON_DELETED)

        if remote_deleted:
            changed = False
            if mapping is not None:
                changed = self._apply_remote_deletion(
                    session, mapping, gtask_id,
                    tombstone_reason or TOMBSTONE_REASON_DELETED,
                )
                mappings.pop(gtask_id, None)
            elif raw_entry is not None and not raw_entry.get("deleted"):
                self._tombstone_index_entry(gtask_id, TOMBSTONE_REASON_DELETED)
            return changed

        if tombstone_reason:
            tomb_ts = _parse_timestamp(raw_entry.get("updated_at"))
            item_ts = _parse_timestamp(item.get("updated"))
            if item_ts and tomb_ts and item_ts > tomb_ts:
                # Edited after the deletion was recorded: resurrect the entry.
                raw_entry.pop("deleted", None)
                raw_entry.pop("reason", None)
                self._index_dirty = True
            else:
                # The recorded deletion stands; another device may have failed
                # to delete the remote task — finish the job here.
                changed = False
                if mapping is not None:
                    changed = self._apply_remote_deletion(
                        session, mapping, gtask_id, tombstone_reason
                    )
                    mappings.pop(gtask_id, None)
                try:
                    self.bridge.delete_task(tasklist_id, gtask_id)
                except Exception as exc:
                    report.skip(
                        "pull", f"remote delete failed: {exc}", gtask_id=gtask_id
                    )
                return changed

        local_task: Optional[Task] = None
        if mapping is not None:
            local_task = self._load_task(session, mapping.task_id)
            if local_task is None:
                # The local task was deleted; push propagates that deletion.
                # Do not resurrect it from the remote copy.
                return False

        entry = self._ensure_index_entry(gtask_id, allow_create=True)
        self._merge_detected_meta(entry, metadata, item)

        changed = False
        if local_task is None:
            local_task = self._find_task_by_uid(session, entry.get("task_uid"), metadata)
        if local_task is None:
            local_task = self._create_local_task_from_remote(session, item, entry, metadata)
            session.flush()
            mapping = SyncMapUndated(
                task_id=str(local_task.id),
                task_uid=local_task.uid,
                gtask_id=gtask_id,
                tasklist_id=tasklist_id,
                dirty_flag=0,
                updated_at_utc=_utcnow(),
            )
            session.add(mapping)
            mappings[gtask_id] = mapping
            changed = True
        else:
            if mapping is None:
                mapping = session.get(SyncMapUndated, str(local_task.id))
            if mapping is None:
                mapping = SyncMapUndated(
                    task_id=str(local_task.id),
                    task_uid=local_task.uid,
                    gtask_id=gtask_id,
                    tasklist_id=tasklist_id,
                    dirty_flag=0,
                    updated_at_utc=_utcnow(),
                )
                changed = True
            else:
                if mapping.gtask_id != gtask_id:
                    mapping.gtask_id = gtask_id
                    changed = True
                if mapping.task_uid != local_task.uid:
                    mapping.task_uid = local_task.uid
            if not mapping.dirty_flag:
                if self._apply_remote_payload(local_task, item):
                    changed = True
            mapping.updated_at_utc = _utcnow()
            session.add(mapping)
            mappings[gtask_id] = mapping

        if self._apply_meta_to_task(local_task, entry, item):
            changed = True

        if entry.get("task_uid") != local_task.uid or "task_id" in entry:
            entry.pop("task_id", None)
            entry["task_uid"] = local_task.uid
            self._index_dirty = True

        session.add(local_task)
        return changed

    def _apply_remote_deletion(
        self,
        session,
        mapping: SyncMapUndated,
        gtask_id: str,
        reason: str,
    ) -> bool:
        """Apply a deletion originating remotely (or from another device)."""
        task = self._load_task(session, mapping.task_id)
        task_uid = mapping.task_uid or (task.uid if task is not None else None)

        if reason == TOMBSTONE_REASON_SCHEDULED:
            # Ownership moved to the Calendar lane; never touch the task row.
            session.delete(mapping)
        elif task is None:
            session.delete(mapping)
        elif mapping.dirty_flag:
            # Unsynced local edits win over a remote deletion: detach the
            # mapping so the next push recreates the remote task.
            mapping.gtask_id = None
            mapping.updated_at_utc = _utcnow()
            session.add(mapping)
        elif task.start is not None:
            # The task is scheduled locally by now; the Inbox no longer owns it.
            session.delete(mapping)
        else:
            session.delete(task)
            session.delete(mapping)

        self._tombstone_index_entry(gtask_id, reason, task_uid=task_uid)
        return True

    # ----- push -----
    def _run_push(self, report: SyncReport) -> bool:
        if not self._can_sync():
            return False

        tasklist_id = self._ensure_tasklist_id()
        if not tasklist_id:
            report.skip("push", "tasklist is unavailable")
            return False

        index = self._get_index()
        changed = False

        with self._session_factory() as session:
            if self._propagate_local_deletions(session, tasklist_id, report):
                changed = True

            tasks: Iterable[Task] = session.exec(
                select(Task).where(Task.start == None)  # noqa: E711
            ).all()

            for task in tasks:
                mapping = session.get(SyncMapUndated, str(task.id))
                if mapping is None:
                    mapping = SyncMapUndated(
                        task_id=str(task.id),
                        task_uid=task.uid,
                        gtask_id=None,
                        tasklist_id=tasklist_id,
                        dirty_flag=1,
                        updated_at_utc=_utcnow(),
                    )
                else:
                    if mapping.tasklist_id != tasklist_id:
                        mapping.tasklist_id = tasklist_id
                    if mapping.task_uid != task.uid:
                        mapping.task_uid = task.uid

                if not mapping.dirty_flag and mapping.gtask_id:
                    continue

                if not mapping.gtask_id:
                    # Dedupe against the shared index before inserting: a lost
                    # mapping must not create a second remote task.
                    mapping.gtask_id = self._find_index_gtask_for_uid(task.uid)

                payload = {
                    "gtask_id": mapping.gtask_id,
                    "uid": task.uid,
                    "title": task.title,
                    "notes": task.notes,
                    "status": task.status,
                    "updated_at": task.updated_at,
                }

                try:
                    gtask_id = self.bridge.upsert_task(tasklist_id, payload)
                except Exception as exc:
                    report.skip(
                        "push", f"upsert failed: {exc}", local_task_id=str(task.id)
                    )
                    continue
                if not gtask_id:
                    report.skip(
                        "push", "bridge returned no task id", local_task_id=str(task.id)
                    )
                    continue

                if mapping.gtask_id and mapping.gtask_id != gtask_id:
                    if index["tasks"].pop(mapping.gtask_id, None) is not None:
                        self._index_dirty = True

                mapping.gtask_id = gtask_id
                mapping.dirty_flag = 0
                mapping.updated_at_utc = _utcnow()
                session.add(mapping)

                entry = self._ensure_index_entry(gtask_id, allow_create=True)
                self._write_live_entry(entry, task)
                index["tasks"][gtask_id] = entry
                self._index_dirty = True
                changed = True

            session.commit()

        self._persist_index_if_dirty()
        return changed

    def _propagate_local_deletions(self, session, tasklist_id: str, report: SyncReport) -> bool:
        """Treat mappings whose local task vanished as local deletions."""
        changed = False
        mappings = session.exec(
            select(SyncMapUndated).where(SyncMapUndated.tasklist_id == tasklist_id)
        ).all()
        for mapping in mappings:
            if self._load_task(session, mapping.task_id) is not None:
                continue
            gtask_id = mapping.gtask_id
            if gtask_id:
                self._tombstone_index_entry(
                    gtask_id, TOMBSTONE_REASON_DELETED, task_uid=mapping.task_uid
                )
                try:
                    self.bridge.delete_task(tasklist_id, gtask_id)
                except Exception as exc:
                    # Keep the mapping so the deletion is retried next cycle.
                    report.skip(
                        "push",
                        f"remote delete failed: {exc}",
                        gtask_id=gtask_id,
                        local_task_id=mapping.task_id,
                    )
                    continue
            session.delete(mapping)
            changed = True
        return changed

    # ----- internal helpers -----
    def _can_sync(self) -> bool:
        return GOOGLE_SYNC.enabled and self.bridge is not None

    def _ensure_tasklist_id(self) -> Optional[str]:
        if self._tasklist_id:
            return self._tasklist_id
        if not self.bridge:
            return None

        try:
            if hasattr(self.auth, "ensure_credentials"):
                self.auth.ensure_credentials()
        except Exception as exc:
            logger.warning("undated sync: credentials are unavailable: %s", exc)
            return None

        try:
            self.appdata.ensure_files()
            config = self._load_config()
            tasklist_id = config.get("tasklist_id")
            if not tasklist_id:
                tasklist_id = self.bridge.ensure_tasklist()

                def mutator(payload: Dict[str, object]) -> Dict[str, object]:
                    payload = self._normalise_config(payload)
                    payload["tasklist_id"] = tasklist_id
                    return payload

                self._update_config(mutator)

            self._tasklist_id = tasklist_id
            index = self._get_index()
            if index.get("tasklist_id") != tasklist_id:
                index["tasklist_id"] = tasklist_id
                self._index_dirty = True
                self._persist_index_if_dirty()
            return self._tasklist_id
        except Exception as exc:
            logger.warning("undated sync: failed to resolve tasklist: %s", exc)
            self._tasklist_id = None
            return None

    def _load_config(self) -> Dict[str, object]:
        if self._config_cache is None:
            payload, etag = self.appdata.read_config()
            self._config_cache = self._normalise_config(payload)
            self._config_etag = etag
        return self._config_cache

    def _update_config(self, mutator) -> Dict[str, object]:
        base = copy.deepcopy(self._load_config())
        updated = mutator(base)
        result, etag = self.appdata.write_config(
            updated,
            if_match=self._config_etag,
            on_conflict=lambda remote: mutator(self._normalise_config(remote)),
        )
        self._config_cache = self._normalise_config(result)
        self._config_etag = etag
        return self._config_cache

    def _get_index(self) -> Dict[str, object]:
        if self._index_cache is None:
            payload, etag = self.appdata.read_index()
            self._index_cache = self._normalise_index(payload)
            self._index_etag = etag
        return self._index_cache

    def _persist_index_if_dirty(self) -> None:
        if not self._index_dirty or self._index_cache is None:
            return

        payload = copy.deepcopy(self._index_cache)
        result, etag = self.appdata.write_index(
            payload,
            if_match=self._index_etag,
            on_conflict=lambda remote: self._merge_index_payload(remote, payload),
        )
        self._index_cache = self._normalise_index(result)
        self._index_etag = etag
        self._index_dirty = False

    def _merge_index_payload(self, remote_payload, local_payload) -> Dict[str, object]:
        remote = self._normalise_index(remote_payload)
        local = self._normalise_index(local_payload)

        merged = copy.deepcopy(remote)
        merged["version"] = local.get("version", remote.get("version", 1))
        if local.get("tasklist_id"):
            merged["tasklist_id"] = local.get("tasklist_id")

        remote_tasks = remote.get("tasks", {})
        local_tasks = local.get("tasks", {})
        result_tasks: Dict[str, Dict[str, object]] = dict(remote_tasks)

        for gtask_id, local_entry in local_tasks.items():
            resolved = self._resolve_meta_entry(local_entry, remote_tasks.get(gtask_id))
            if resolved is None:
                result_tasks.pop(gtask_id, None)
            else:
                result_tasks[gtask_id] = resolved

        merged["tasks"] = result_tasks
        return merged

    def _resolve_meta_entry(self, local_entry, remote_entry):
        if local_entry is not None and not isinstance(local_entry, dict):
            local_entry = None
        if remote_entry is not None and not isinstance(remote_entry, dict):
            remote_entry = None
        if local_entry is None and remote_entry is None:
            return None
        if remote_entry is None:
            return self._normalise_meta(local_entry)
        if local_entry is None:
            return self._normalise_meta(remote_entry)

        local_norm = self._normalise_meta(local_entry)
        remote_norm = self._normalise_meta(remote_entry)

        local_ts = _parse_timestamp(local_norm.get("updated_at"))
        remote_ts = _parse_timestamp(remote_norm.get("updated_at"))

        if local_ts and remote_ts:
            if local_ts > remote_ts:
                return local_norm
            if remote_ts > local_ts:
                return remote_norm
        elif local_ts:
            return local_norm
        elif remote_ts:
            return remote_norm

        local_device = str(local_norm.get("device_id", ""))
        remote_device = str(remote_norm.get("device_id", ""))
        if local_device > remote_device:
            return local_norm
        if remote_device > local_device:
            return remote_norm
        return local_norm

    def _normalise_meta(self, entry) -> Dict[str, object]:
        data = dict(entry or {})
        # Legacy device-local ids must not survive in shared metadata.
        data.pop("task_id", None)
        data.setdefault("task_uid", None)
        data["priority"] = _normalise_priority(data.get("priority"))
        data["status"] = _normalise_status(data.get("status"))
        if not data.get("updated_at"):
            data["updated_at"] = _isoformat(None)
        if not data.get("device_id"):
            data["device_id"] = self.device_id
        return data

    def _normalise_config(self, payload) -> Dict[str, object]:
        data = dict(payload or {})
        data.setdefault("version", 1)
        data.setdefault("tasklist_id", None)
        data.setdefault("last_full_sync", None)
        return data

    def _normalise_index(self, payload) -> Dict[str, object]:
        data = dict(payload or {})
        data.setdefault("version", 1)
        data.setdefault("tasklist_id", None)
        tasks = data.get("tasks") or {}
        if not isinstance(tasks, dict):
            tasks = {}
        data["tasks"] = tasks
        return data

    def _ensure_index_entry(self, gtask_id: str, *, allow_create: bool) -> Dict[str, object]:
        index = self._get_index()
        tasks = index.setdefault("tasks", {})
        entry = tasks.get(gtask_id)
        if entry is None and allow_create:
            entry = {
                "task_uid": None,
                "priority": DEFAULT_PRIORITY,
                "status": "todo",
                "updated_at": _isoformat(None),
                "device_id": self.device_id,
            }
            tasks[gtask_id] = entry
            self._index_dirty = True
        elif entry is None:
            entry = {}
        return entry

    def _write_live_entry(self, entry: Dict[str, object], task: Task) -> None:
        entry.pop("task_id", None)
        entry.pop("deleted", None)
        entry.pop("reason", None)
        entry["task_uid"] = task.uid
        entry["priority"] = _normalise_priority(task.priority)
        entry["status"] = _normalise_status(task.status)
        entry["updated_at"] = _isoformat(None)
        entry["device_id"] = self.device_id

    def _tombstone_index_entry(
        self,
        gtask_id: str,
        reason: str,
        *,
        task_uid: Optional[str] = None,
    ) -> None:
        index = self._get_index()
        entry = index["tasks"].get(gtask_id)
        if not isinstance(entry, dict):
            entry = {}
            index["tasks"][gtask_id] = entry
        if entry.get("deleted") and entry.get("reason") == reason:
            # Already tombstoned; keep the original timestamp for idempotency.
            if task_uid and not entry.get("task_uid"):
                entry["task_uid"] = task_uid
                self._index_dirty = True
            return
        entry.pop("task_id", None)
        if task_uid:
            entry["task_uid"] = task_uid
        entry["deleted"] = True
        entry["reason"] = reason
        entry["updated_at"] = _isoformat(None)
        entry["device_id"] = self.device_id
        self._index_dirty = True

    def _tombstone_reason_for(self, gtask_id: str) -> Optional[str]:
        entry = self._get_index()["tasks"].get(gtask_id)
        if isinstance(entry, dict) and entry.get("deleted"):
            return str(entry.get("reason") or TOMBSTONE_REASON_DELETED)
        return None

    def _find_index_gtask_for_uid(self, task_uid: str) -> Optional[str]:
        index = self._get_index()
        for gtask_id, entry in index["tasks"].items():
            if not isinstance(entry, dict) or entry.get("deleted"):
                continue
            if str(entry.get("task_uid") or "") == str(task_uid):
                return gtask_id
        return None

    def _merge_detected_meta(
        self,
        entry: Dict[str, object],
        detected: Dict[str, object],
        item,
    ) -> None:
        if not detected:
            return
        normalised = dict(detected)
        if "uid" in normalised and "task_uid" not in normalised:
            normalised["task_uid"] = normalised["uid"]
        # The legacy local-id key is device-local and unsafe across devices.
        normalised.pop("task_id", None)
        changed = False
        for key in ("task_uid", "priority", "status", "updated_at", "device_id"):
            value = normalised.get(key)
            if value in (None, ""):
                continue
            if entry.get(key) != value:
                entry[key] = value
                changed = True
        if changed:
            if not entry.get("updated_at"):
                entry["updated_at"] = (
                    normalised.get("updated_at") or item.get("updated") or _isoformat(None)
                )
            if not entry.get("device_id"):
                entry["device_id"] = normalised.get("device_id") or self.device_id
            self._index_dirty = True

    def _create_local_task_from_remote(
        self,
        session,
        item,
        entry: Dict[str, object],
        metadata: Dict[str, object],
    ) -> Task:
        status = _normalise_status(entry.get("status"), _status_from_google(item.get("status")))
        priority = _normalise_priority(entry.get("priority"))
        notes = item.get("notes") or ""
        uid = entry.get("task_uid") or metadata.get("uid") or metadata.get("task_uid")
        kwargs = {"uid": str(uid)} if uid else {}
        task = Task(
            title=item.get("title") or "",
            notes=notes or None,
            start=None,
            priority=priority,
            status=status,
            **kwargs,
        )
        session.add(task)
        return task

    def _apply_remote_payload(self, task: Task, item) -> bool:
        changed = False
        remote_title = item.get("title") or ""
        remote_notes = item.get("notes") or ""
        remote_updated = _parse_timestamp(item.get("updated"))
        local_updated = task.updated_at
        if local_updated and local_updated.tzinfo is None:
            local_updated = local_updated.replace(tzinfo=timezone.utc)

        should_update = True
        if remote_updated and local_updated:
            should_update = remote_updated >= local_updated

        if should_update:
            if task.title != remote_title:
                task.title = remote_title
                changed = True
            if (task.notes or "") != remote_notes:
                task.notes = remote_notes or None
                changed = True
            if changed:
                task.updated_at = _utcnow()
        return changed

    def _apply_meta_to_task(self, task: Task, entry: Dict[str, object], item) -> bool:
        changed = False
        remote_status = _status_from_google(item.get("status"))
        status = "done" if remote_status == "done" else _normalise_status(entry.get("status"), remote_status)
        priority = _normalise_priority(entry.get("priority"))

        meta_ts = _parse_timestamp(entry.get("updated_at"))
        remote_ts = _parse_timestamp(item.get("updated"))
        if remote_status != "done" and status == "done" and remote_ts and meta_ts:
            if remote_ts > meta_ts:
                status = remote_status

        if task.status != status:
            task.status = status
            changed = True
        if task.priority != priority:
            task.priority = priority
            changed = True
        if changed:
            task.updated_at = _utcnow()
        return changed

    def _load_task(self, session, task_id: Optional[str]) -> Optional[Task]:
        if not task_id:
            return None
        try:
            numeric = int(task_id)
        except (TypeError, ValueError):
            return None
        return session.get(Task, numeric)

    def _find_task_by_uid(
        self,
        session,
        entry_uid: Optional[object],
        metadata: Dict[str, object],
    ) -> Optional[Task]:
        for candidate in (entry_uid, metadata.get("uid"), metadata.get("task_uid")):
            if not candidate:
                continue
            task = session.exec(
                select(Task).where(Task.uid == str(candidate))
            ).first()
            if task is not None:
                return task
        return None

    def _update_last_sync(self) -> None:
        def mutator(data: Dict[str, object]) -> Dict[str, object]:
            data = self._normalise_config(data)
            data["last_full_sync"] = _isoformat(None)
            return data

        self._update_config(mutator)


def _status_from_google(value: Optional[str]) -> str:
    return "done" if str(value or "").lower() == "completed" else "todo"


__all__ = [
    "UndatedTasksSync",
    "SyncReport",
    "SyncSkippedItem",
    "TOMBSTONE_REASON_DELETED",
    "TOMBSTONE_REASON_SCHEDULED",
]
