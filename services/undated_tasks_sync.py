"""Synchronization of undated tasks with Google Tasks."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Optional

from sqlmodel import select

from core.settings import GOOGLE_SYNC
from models import SyncMapUndated, Task
from services.tasks_bridge import GoogleTasksBridge
from storage.db import get_session


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


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
    ) -> None:
        self.auth = auth
        self.bridge = bridge or GoogleTasksBridge(auth)
        self._session_factory = session_factory
        self._tasklist_id: Optional[str] = None

    # ----- public helpers -----
    @property
    def tasklist_title(self) -> str:
        return self.bridge.tasklist_title if self.bridge else "Planner Inbox"

    def status_text(self) -> str:
        if not GOOGLE_SYNC.enabled:
            return "Tasks: синхронизация отключена"
        if not self.bridge:
            return "Tasks: недоступны"
        if not self._tasklist_id:
            return "Tasks: не подключено"
        return f"Tasks: подключено, список: {self.tasklist_title}"

    def reset_cache(self) -> None:
        self._tasklist_id = None

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

        overall_changed = False
        with self._session_factory() as session:
            for item in remote_tasks:
                metadata = item.get("metadata") or {}
                local_id = metadata.get("task_id")
                if not local_id:
                    continue
                try:
                    task_id = int(local_id)
                except (TypeError, ValueError):
                    continue

                task: Task | None = session.get(Task, task_id)
                if not task or task.start is not None:
                    self._update_mapping(session, str(task_id), tasklist_id, item.get("id"))
                    continue

                mapping = session.get(SyncMapUndated, str(task_id))
                if mapping and mapping.dirty_flag:
                    continue

                notes = item.get("notes") or ""
                status = item.get("status") or "needsAction"
                new_status = "done" if status.lower() == "completed" else "todo"

                updated_remote = _parse_google_timestamp(item.get("updated"))
                local_updated = task.updated_at
                if isinstance(local_updated, datetime) and local_updated.tzinfo is None:
                    local_updated = local_updated.replace(tzinfo=timezone.utc)

                should_update = True
                if updated_remote and local_updated:
                    should_update = updated_remote >= local_updated

                if should_update:
                    task_changed = False
                    if task.title != (item.get("title") or ""):
                        task.title = item.get("title") or ""
                        task_changed = True
                    if (task.notes or "") != notes:
                        task.notes = notes or None
                        task_changed = True
                    if task.status != new_status:
                        task.status = new_status
                        task_changed = True
                    if task_changed:
                        task.updated_at = datetime.utcnow()
                        session.add(task)
                        overall_changed = True

                self._update_mapping(session, str(task_id), tasklist_id, item.get("id"))
                if mapping:
                    mapping.dirty_flag = 0
                    mapping.updated_at_utc = _utcnow()
                    session.add(mapping)

            session.commit()

        return overall_changed

    def push_dirty(self) -> bool:
        if not self._can_sync():
            return False

        tasklist_id = self._ensure_tasklist_id()
        if not tasklist_id:
            return False

        changed = False
        with self._session_factory() as session:
            tasks: Iterable[Task] = session.exec(select(Task).where(Task.start == None)).all()  # noqa: E711

            for task in tasks:
                mapping = session.get(SyncMapUndated, str(task.id))
                if mapping is None:
                    mapping = SyncMapUndated(
                        task_id=str(task.id),
                        gtask_id=None,
                        tasklist_id=tasklist_id,
                        dirty_flag=1,
                    )
                elif mapping.tasklist_id != tasklist_id:
                    mapping.tasklist_id = tasklist_id

                if not mapping.dirty_flag and mapping.gtask_id:
                    continue

                local_payload = {
                    "task_id": task.id,
                    "title": task.title,
                    "notes": task.notes,
                    "priority": getattr(task, "priority", None),
                    "status": task.status,
                    "updated_at": task.updated_at,
                    "device_id": None,
                    "gtask_id": mapping.gtask_id,
                }

                try:
                    gtask_id = self.bridge.upsert_task(tasklist_id, local_payload)
                except Exception:
                    continue

                mapping.gtask_id = gtask_id
                mapping.dirty_flag = 0
                mapping.updated_at_utc = _utcnow()
                session.add(mapping)
                task.updated_at = datetime.utcnow()
                session.add(task)
                changed = True

            session.commit()

        return changed

    def mark_dirty(self, task_id: int) -> None:
        if not GOOGLE_SYNC.enabled:
            return
        with self._session_factory() as session:
            mapping = session.get(SyncMapUndated, str(task_id))
            if mapping is None:
                mapping = SyncMapUndated(
                    task_id=str(task_id),
                    gtask_id=None,
                    tasklist_id=self._tasklist_id or "",
                    dirty_flag=1,
                )
            else:
                mapping.dirty_flag = 1
            mapping.updated_at_utc = _utcnow()
            session.add(mapping)
            session.commit()

    def remove_mapping(self, task_id: int, *, delete_remote: bool = False) -> None:
        with self._session_factory() as session:
            mapping = session.get(SyncMapUndated, str(task_id))
            if not mapping:
                return
            gtask_id = mapping.gtask_id
            tasklist_id = mapping.tasklist_id or self._tasklist_id
            session.delete(mapping)
            session.commit()
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
            self._tasklist_id = self.bridge.ensure_tasklist()
        except Exception:
            self._tasklist_id = None
        return self._tasklist_id

    @staticmethod
    def _update_mapping(session, task_id: str, tasklist_id: str, gtask_id: Optional[str]) -> None:
        mapping = session.get(SyncMapUndated, task_id)
        now_utc = _utcnow()
        if mapping is None:
            mapping = SyncMapUndated(
                task_id=task_id,
                gtask_id=gtask_id,
                tasklist_id=tasklist_id,
                dirty_flag=0,
                updated_at_utc=now_utc,
            )
        else:
            mapping.tasklist_id = tasklist_id
            mapping.gtask_id = gtask_id
            mapping.updated_at_utc = now_utc
            mapping.dirty_flag = 0
        session.add(mapping)


__all__ = ["UndatedTasksSync"]
