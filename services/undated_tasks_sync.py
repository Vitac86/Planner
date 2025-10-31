"""Synchronization of undated Planner tasks with Google Tasks."""
from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, Iterable, Optional

from sqlmodel import select

from core.priorities import DEFAULT_PRIORITY, normalize_priority
from core.settings import GOOGLE_SYNC
from models import SyncMapUndated, Task
from services.appdata import AppDataClient
from services.tasks_bridge import GoogleTasksBridge
from storage.db import get_session
from storage.device import get_device_id


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _isoformat(value: Optional[datetime]) -> str:
    if value is None:
        value = _utcnow()
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def _parse_google_timestamp(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except Exception:
        return None


def _parse_meta_timestamp(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except Exception:
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
    except Exception:
        return DEFAULT_PRIORITY


@dataclass
class _SyncResult:
    pulled: bool = False
    pushed: bool = False

    def changed(self) -> bool:
        return self.pulled or self.pushed


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
        result = _SyncResult()
        result.pulled = self.pull()
        result.pushed = self.push_dirty()
        return result.changed()

    # ----- high level operations -----
    def pull(self) -> bool:
        if not self._can_sync():
            return False

        tasklist_id = self._ensure_tasklist_id()
        if not tasklist_id:
            return False

        try:
            remote_tasks = self.bridge.fetch_all(tasklist_id)
        except Exception:
            return False

        index = self._get_index()
        changed = False
        now = datetime.utcnow()

        with self._session_factory() as session:
            mappings = {
                (mapping.gtask_id or ""): mapping
                for mapping in session.exec(
                    select(SyncMapUndated).where(SyncMapUndated.tasklist_id == tasklist_id)
                ).all()
            }

            for item in remote_tasks:
                gtask_id = item.get("id")
                if not gtask_id:
                    continue

                entry = self._ensure_index_entry(gtask_id, allow_create=True)
                detected_meta = item.get("detected_meta") or {}
                self._merge_detected_meta(entry, detected_meta, item)

                mapping = mappings.get(gtask_id)
                local_task = None
                if mapping:
                    local_task = self._load_task(session, mapping.task_id)

                if local_task is None and entry.get("task_id"):
                    local_task = self._load_task(session, entry.get("task_id"))

                if local_task is None:
                    local_task = self._create_local_task_from_remote(session, item, entry)
                    session.flush()
                    mapping = SyncMapUndated(
                        task_id=str(local_task.id),
                        gtask_id=gtask_id,
                        tasklist_id=tasklist_id,
                        dirty_flag=0,
                        updated_at_utc=_utcnow(),
                    )
                    session.add(mapping)
                    mappings[gtask_id] = mapping
                    changed = True
                else:
                    if not mapping:
                        mapping = SyncMapUndated(
                            task_id=str(local_task.id),
                            gtask_id=gtask_id,
                            tasklist_id=tasklist_id,
                            dirty_flag=0,
                            updated_at_utc=_utcnow(),
                        )
                        session.add(mapping)
                        mappings[gtask_id] = mapping

                    if not mapping.dirty_flag:
                        if self._apply_remote_payload(local_task, item):
                            changed = True
                    mapping.updated_at_utc = _utcnow()
                    session.add(mapping)

                if self._apply_meta_to_task(local_task, entry, item):
                    changed = True

                if entry.get("task_id") != str(local_task.id):
                    entry["task_id"] = str(local_task.id)
                    self._index_dirty = True

                session.add(local_task)

            session.commit()

        if changed:
            self._persist_index_if_dirty()
            self._update_last_sync()
        else:
            # We may still have cleaned up metadata from migration.
            self._persist_index_if_dirty()
        return changed

    def push_dirty(self) -> bool:
        if not self._can_sync():
            return False

        tasklist_id = self._ensure_tasklist_id()
        if not tasklist_id:
            return False

        index = self._get_index()
        changed = False

        with self._session_factory() as session:
            tasks: Iterable[Task] = session.exec(
                select(Task).where(Task.start == None)  # noqa: E711
            ).all()

            for task in tasks:
                mapping = session.get(SyncMapUndated, str(task.id))
                if mapping is None:
                    mapping = SyncMapUndated(
                        task_id=str(task.id),
                        gtask_id=None,
                        tasklist_id=tasklist_id,
                        dirty_flag=1,
                        updated_at_utc=_utcnow(),
                    )
                elif mapping.tasklist_id != tasklist_id:
                    mapping.tasklist_id = tasklist_id

                entry = None
                if mapping.gtask_id:
                    entry = index["tasks"].get(mapping.gtask_id)

                if not mapping.dirty_flag and mapping.gtask_id:
                    continue

                payload = {
                    "gtask_id": mapping.gtask_id,
                    "title": task.title,
                    "notes": task.notes,
                    "status": task.status,
                    "updated_at": task.updated_at,
                }

                try:
                    gtask_id = self.bridge.upsert_task(tasklist_id, payload)
                except Exception:
                    continue

                if mapping.gtask_id and mapping.gtask_id != gtask_id:
                    index["tasks"].pop(mapping.gtask_id, None)
                    self._index_dirty = True

                mapping.gtask_id = gtask_id
                mapping.dirty_flag = 0
                mapping.updated_at_utc = _utcnow()
                session.add(mapping)

                entry = self._ensure_index_entry(gtask_id, allow_create=True)
                entry["task_id"] = str(task.id)
                entry["priority"] = _normalise_priority(task.priority)
                entry["status"] = _normalise_status(task.status)
                entry["updated_at"] = _isoformat(None)
                entry["device_id"] = self.device_id
                index["tasks"][gtask_id] = entry
                self._index_dirty = True
                changed = True

            session.commit()

        if changed:
            self._persist_index_if_dirty()
        else:
            self._persist_index_if_dirty()
        return changed

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
                    gtask_id=None,
                    tasklist_id=self._tasklist_id or "",
                    dirty_flag=1,
                    updated_at_utc=_utcnow(),
                )
            else:
                mapping.dirty_flag = 1
                mapping.updated_at_utc = _utcnow()
            gtask_id = mapping.gtask_id
            session.add(mapping)
            session.commit()

        if gtask_id and task_snapshot:
            entry = self._ensure_index_entry(gtask_id, allow_create=True)
            entry["task_id"] = str(task_snapshot.id)
            entry["priority"] = _normalise_priority(task_snapshot.priority)
            entry["status"] = _normalise_status(task_snapshot.status)
            entry["updated_at"] = _isoformat(None)
            entry["device_id"] = self.device_id
            self._index_dirty = True
            self._persist_index_if_dirty()

    def remove_mapping(self, task_id: int, *, delete_remote: bool = False) -> None:
        gtask_id = None
        tasklist_id = None

        with self._session_factory() as session:
            mapping = session.get(SyncMapUndated, str(task_id))
            if not mapping:
                return
            gtask_id = mapping.gtask_id
            tasklist_id = mapping.tasklist_id or self._tasklist_id
            session.delete(mapping)
            session.commit()

        if gtask_id:
            index = self._get_index()
            if gtask_id in index["tasks"]:
                index["tasks"].pop(gtask_id, None)
                self._index_dirty = True
                self._persist_index_if_dirty()

        if delete_remote and gtask_id and tasklist_id:
            try:
                self.bridge.delete_task(tasklist_id, gtask_id)
            except Exception:
                pass

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
        except Exception:
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
        except Exception:
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
        if local_entry is None and remote_entry is None:
            return None
        if remote_entry is None:
            return self._normalise_meta(local_entry)
        if local_entry is None:
            return self._normalise_meta(remote_entry)

        local_norm = self._normalise_meta(local_entry)
        remote_norm = self._normalise_meta(remote_entry)

        local_ts = _parse_meta_timestamp(local_norm.get("updated_at"))
        remote_ts = _parse_meta_timestamp(remote_norm.get("updated_at"))

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
        data.setdefault("task_id", None)
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
                "task_id": None,
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

    def _merge_detected_meta(self, entry: Dict[str, object], detected: Dict[str, object], item) -> None:
        if not detected:
            return
        changed = False
        for key in ("task_id", "priority", "status", "updated_at", "device_id"):
            if key not in detected or detected[key] in (None, ""):
                continue
            if entry.get(key) != detected[key]:
                entry[key] = detected[key]
                changed = True
        if changed:
            if not entry.get("updated_at"):
                entry["updated_at"] = detected.get("updated_at") or item.get("updated") or _isoformat(None)
            if not entry.get("device_id"):
                entry["device_id"] = detected.get("device_id") or self.device_id
            self._index_dirty = True

    def _create_local_task_from_remote(self, session, item, entry: Dict[str, object]) -> Task:
        status = _normalise_status(entry.get("status"), _status_from_google(item.get("status")))
        priority = _normalise_priority(entry.get("priority"))
        notes = item.get("notes") or ""
        task = Task(
            title=item.get("title") or "",
            notes=notes or None,
            start=None,
            priority=priority,
            status=status,
        )
        session.add(task)
        return task

    def _apply_remote_payload(self, task: Task, item) -> bool:
        changed = False
        remote_title = item.get("title") or ""
        remote_notes = item.get("notes") or ""
        remote_updated = _parse_google_timestamp(item.get("updated"))
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
                task.updated_at = datetime.utcnow()
        return changed

    def _apply_meta_to_task(self, task: Task, entry: Dict[str, object], item) -> bool:
        changed = False
        remote_status = _status_from_google(item.get("status"))
        status = "done" if remote_status == "done" else _normalise_status(entry.get("status"), remote_status)
        priority = _normalise_priority(entry.get("priority"))

        meta_ts = _parse_meta_timestamp(entry.get("updated_at"))
        remote_ts = _parse_google_timestamp(item.get("updated"))
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
            task.updated_at = datetime.utcnow()
        return changed

    def _load_task(self, session, task_id: Optional[str]) -> Optional[Task]:
        if not task_id:
            return None
        try:
            numeric = int(task_id)
        except (TypeError, ValueError):
            return None
        return session.get(Task, numeric)

    def _update_last_sync(self) -> None:
        def mutator(data: Dict[str, object]) -> Dict[str, object]:
            data = self._normalise_config(data)
            data["last_full_sync"] = _isoformat(None)
            return data

        self._update_config(mutator)


def _status_from_google(value: Optional[str]) -> str:
    return "done" if str(value or "").lower() == "completed" else "todo"


__all__ = ["UndatedTasksSync"]

