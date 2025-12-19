# planner/services/daily_tasks_sync.py
from __future__ import annotations

import json
from datetime import datetime
from typing import Optional

from core.settings import GOOGLE_SYNC
from models.daily_task import DailyTask
from services.google_tasks import GoogleTasks
from services.daily_tasks import DailyTaskService
from utils.datetime_utils import ensure_utc


def _parse_google_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return ensure_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except Exception:
        return None


class DailyTasksSync:
    def __init__(self, gtasks: GoogleTasks, repo: DailyTaskService) -> None:
        self.gtasks = gtasks
        self.repo = repo

    # ------------------------------------------------------------------ helpers
    def _notes_payload(self, task: DailyTask) -> str:
        payload = {
            "planner_kind": "daily",
            "local_id": task.id,
            "weekdays": task.weekdays,
        }
        return json.dumps(payload, ensure_ascii=False)

    def _parse_notes(self, notes: Optional[str]) -> dict:
        if not notes:
            return {}
        try:
            return json.loads(notes)
        except Exception:
            return {}

    def _needs_push(self) -> bool:
        return GOOGLE_SYNC.enabled

    # ------------------------------------------------------------------ push
    def on_task_created(self, task_id: str) -> None:
        if not self._needs_push():
            return
        task = self.repo.get(task_id)
        if task:
            self._push_task(task)

    def on_task_updated(self, task_id: str) -> None:
        if not self._needs_push():
            return
        task = self.repo.get(task_id)
        if task:
            self._push_task(task)

    def on_task_deleted(self, task_id: str) -> None:
        if not self._needs_push():
            return
        # If we still know remote id — delete it
        task = self.repo.get(task_id)
        remote_id = getattr(task, "gtasks_id", None) if task else None
        if remote_id:
            try:
                self.gtasks.delete(remote_id)
            except Exception:
                # best-effort
                pass

    def _push_task(self, task: DailyTask) -> None:
        try:
            notes = self._notes_payload(task)
            if not task.gtasks_id:
                created = self.gtasks.insert(task.title, notes, None)
                remote_id = created.get("id")
                updated = _parse_google_datetime(created.get("updated"))
                if remote_id:
                    self.repo.update_from_sync(
                        task.id,
                        gtasks_id=remote_id,
                        gtasks_updated=updated,
                    )
            else:
                updated = self.gtasks.patch(
                    task.gtasks_id,
                    title=task.title,
                    notes=notes,
                    due=None,
                )
                self.repo.update_from_sync(
                    task.id,
                    gtasks_updated=_parse_google_datetime(updated.get("updated")),
                )
        except Exception as exc:
            print("Daily tasks push error:", exc)

    # ------------------------------------------------------------------ pull
    def pull(self) -> bool:
        if not GOOGLE_SYNC.enabled:
            return False
        try:
            items = self.gtasks.list()
        except Exception as exc:
            print("Daily tasks pull error:", exc)
            return False

        changed = False
        local = {t.id: t for t in self.repo.list_all()}
        by_remote = {t.gtasks_id: t for t in local.values() if t.gtasks_id}

        for item in items:
            remote_id = item.get("id")
            is_deleted = bool(item.get("deleted"))
            notes_payload = self._parse_notes(item.get("notes"))
            if notes_payload.get("planner_kind") != "daily":
                continue
            local_id = notes_payload.get("local_id")
            weekdays = notes_payload.get("weekdays")
            if weekdays is None:
                continue
            title = (item.get("title") or "").strip() or "Daily task"
            remote_updated = _parse_google_datetime(item.get("updated"))

            target = None
            if local_id and local_id in local:
                target = local[local_id]
            elif remote_id and remote_id in by_remote:
                target = by_remote[remote_id]

            if is_deleted:
                if target:
                    self.repo.delete_from_sync(target.id)
                    changed = True
                continue

            if not target:
                created = self.repo.create_from_sync(
                    id=local_id,
                    title=title,
                    weekdays=int(weekdays),
                    gtasks_id=remote_id,
                    gtasks_updated=remote_updated,
                )
                local[created.id] = created
                if remote_id:
                    by_remote[remote_id] = created
                changed = True
                continue

            local_updated = _parse_google_datetime(target.gtasks_updated)
            if remote_updated and local_updated and remote_updated <= local_updated:
                continue

            if (
                target.title != title
                or target.weekdays != int(weekdays)
                or (remote_id and target.gtasks_id != remote_id)
            ):
                self.repo.update_from_sync(
                    target.id,
                    title=title,
                    weekdays=int(weekdays),
                    gtasks_id=remote_id or target.gtasks_id,
                    gtasks_updated=remote_updated,
                )
                changed = True

        return changed


__all__ = ["DailyTasksSync"]
