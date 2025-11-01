from __future__ import annotations
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional

from googleapiclient.errors import HttpError

from core.settings import GOOGLE_SYNC
from datetime_utils import ensure_utc, parse_rfc3339, to_rfc3339_utc, utc_now
from models.task import Task
from services.google_calendar import GoogleCalendar
from services.google_tasks import GoogleTasks
from services.google_sync import (
    build_event_payload,
    event_updated,
    extract_event_times,
    extract_notes,
)
from services.pending_ops_queue import PendingOpsQueue
from services.sync_token_storage import SyncTokenStorage
from services.tasks import TaskService


RETRYABLE_STATUS = {409, 412, 429, 500, 502, 503, 504}
SYNC_LOG_PATH = "logs/sync.log"


def _ensure_logger() -> logging.Logger:
    logger = logging.getLogger("planner.sync")
    if not logger.handlers:
        Path(SYNC_LOG_PATH).parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(SYNC_LOG_PATH, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
        formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    return logger


def _is_scheduled(task: Task) -> bool:
    return bool(task.start and task.duration_minutes)


def _due_datetime(task: Task) -> Optional[datetime]:
    if task.start is None:
        return None
    dt = ensure_utc(task.start)
    return dt.replace(hour=0, minute=0, second=0, microsecond=0)


def _duration_minutes(start: Optional[datetime], end: Optional[datetime]) -> Optional[int]:
    if not start or not end:
        return None
    delta = end - start
    minutes = int(delta.total_seconds() // 60)
    return minutes if minutes > 0 else None


class SyncService:
    def __init__(
        self,
        gcal: GoogleCalendar,
        gtasks: GoogleTasks,
        repo: TaskService,
        token_store: SyncTokenStorage,
        queue: Optional[PendingOpsQueue] = None,
    ) -> None:
        self.gcal = gcal
        self.gtasks = gtasks
        self.repo = repo
        self.tokens = token_store
        self.queue = queue or PendingOpsQueue()
        self.logger = _ensure_logger()

    # ------------------------------------------------------------------
    # Event hooks from TaskService
    def on_task_created(self, task_id: int) -> None:
        if not GOOGLE_SYNC.enabled:
            return
        task = self.repo.get(task_id)
        if not task:
            return
        self.logger.debug("Task created: %s", task_id)
        if _is_scheduled(task):
            self._ensure_tasks_delete(task)
            self._queue_calendar_sync(task)
        else:
            self._ensure_calendar_delete(task)
            self._queue_tasks_sync(task)

    def on_task_updated(self, task_id: int) -> None:
        if not GOOGLE_SYNC.enabled:
            return
        task = self.repo.get(task_id)
        if not task:
            return
        self.logger.debug("Task updated: %s", task_id)
        if _is_scheduled(task):
            self._ensure_tasks_delete(task)
            self._queue_calendar_sync(task)
        else:
            self._ensure_calendar_delete(task)
            self._queue_tasks_sync(task)

    def on_task_deleted(self, task_id: int) -> None:
        if not GOOGLE_SYNC.enabled:
            return
        task = self.repo.get(task_id)
        if not task:
            return
        self.logger.debug("Task deleted: %s", task_id)
        if task.gcal_event_id:
            self.queue.enqueue("gcal_delete", task_id, {"eventId": task.gcal_event_id})
        if task.gtasks_id:
            self.queue.enqueue("gtasks_delete", task_id, {"taskId": task.gtasks_id})

    # ------------------------------------------------------------------
    # Public API
    def pull_all(self) -> bool:
        if not GOOGLE_SYNC.enabled:
            return False
        changed = False
        try:
            changed |= self._pull_calendar()
        except HttpError as exc:
            status = getattr(exc, "resp", None) and getattr(exc.resp, "status", None)
            if status == 410:
                self.logger.warning("Calendar sync token expired, triggering full resync")
                self.reset_calendar_sync_token()
                changed |= self._pull_calendar()
            else:
                self.logger.error("Calendar pull failed: %s", exc)
                raise
        except Exception as exc:  # pragma: no cover - defensive
            self.logger.error("Calendar pull error: %s", exc)
            raise

        try:
            changed |= self._pull_tasks()
        except Exception as exc:  # pragma: no cover - defensive
            self.logger.error("Tasks pull error: %s", exc)
            raise

        return changed

    def push_queue_worker(self) -> int:
        processed = 0
        for entry in self.queue.due():
            try:
                if self._execute_op(entry):
                    processed += 1
                    self.queue.remove(entry.id)
                    self.tokens.set_last_push_timestamp()
                else:
                    self.queue.requeue(entry.id, "invalid payload")
            except HttpError as exc:
                status = getattr(exc, "resp", None) and getattr(exc.resp, "status", None)
                code = int(status or 0)
                self.logger.warning("Push op %s failed with %s", entry.op, code)
                if code in RETRYABLE_STATUS:
                    self.queue.requeue(entry.id, str(exc))
                else:
                    self.queue.requeue(entry.id, str(exc))
            except Exception as exc:  # pragma: no cover - defensive
                self.logger.error("Push op %s crashed: %s", entry.op, exc)
                self.queue.requeue(entry.id, str(exc))
        return processed

    def force_full_resync(self) -> None:
        self.logger.info("Force full resync requested")
        self.tokens.clear_all()
        self.pull_all()

    def reset_calendar_sync_token(self) -> None:
        self.logger.info("Resetting calendar sync token")
        self.tokens.clear_calendar_token()

    def status(self) -> dict:
        return {
            "calendar": {
                "calendarId": getattr(self.gcal, "calendar_id", None),
                "syncToken": bool(self.tokens.get_calendar_token()),
                "lastPullAt": self.tokens.get_calendar_pull_timestamp(),
            },
            "tasks": {
                "tasklist": getattr(self.gtasks, "tasklist_id", None),
                "updatedMin": self.tokens.get_tasks_updated_min(),
                "lastPullAt": self.tokens.get_tasks_pull_timestamp(),
            },
            "lastPushAt": self.tokens.get_last_push_timestamp(),
            "queueSize": self.queue.count(),
        }

    # ------------------------------------------------------------------
    # Pull helpers
    def _pull_calendar(self) -> bool:
        self.logger.debug("Pulling Google Calendar")
        self.gcal.connect()
        service = getattr(self.gcal, "service", None)
        if service is None:
            return False

        params = dict(
            calendarId=self.gcal.calendar_id,
            singleEvents=True,
            showDeleted=True,
            maxResults=250,
        )
        token = self.tokens.get_calendar_token()
        if token:
            params["syncToken"] = token
        else:
            params["timeMin"] = to_rfc3339_utc(utc_now() - timedelta(days=90))

        changed = False
        while True:
            response = service.events().list(**params).execute()
            for event in response.get("items", []):
                if self._apply_calendar_event(event):
                    changed = True
            if "nextPageToken" in response:
                params.pop("syncToken", None)
                params.pop("timeMin", None)
                params["pageToken"] = response["nextPageToken"]
                continue
            if "nextSyncToken" in response:
                self.tokens.set_calendar_token(response["nextSyncToken"])
            break

        self.tokens.set_calendar_pull_timestamp()
        return changed

    def _apply_calendar_event(self, event: dict) -> bool:
        event_id = event.get("id")
        if not event_id:
            return False

        status = event.get("status")
        task = self.repo.get_by_event_id(event_id)
        remote_updated = event_updated(event) or utc_now()

        if status == "cancelled":
            if not task:
                return False
            self.logger.info("Calendar event deleted remotely for task %s", task.id)
            updated_task = self.repo.update_from_sync(
                task.id,
                start=None,
                duration_minutes=None,
                gcal_event_id=None,
                gcal_etag=None,
                gcal_updated=remote_updated,
                updated_at=remote_updated,
            )
            self._queue_tasks_sync(updated_task or task)
            return True

        start, end = extract_event_times(event)
        duration = _duration_minutes(start, end)
        notes = extract_notes(event)
        summary = event.get("summary") or "Без названия"

        if not task:
            self.logger.info("New calendar event -> creating task")
            self.repo.create_from_sync(
                title=summary,
                notes=notes,
                start=start,
                duration_minutes=duration,
                status="todo",
                gcal_event_id=event_id,
                gcal_etag=event.get("etag"),
                gcal_updated=remote_updated,
            )
            return True

        local_updated = ensure_utc(task.updated_at)
        known_remote = ensure_utc(task.gcal_updated)
        if known_remote and remote_updated <= known_remote:
            return False

        if remote_updated >= local_updated:
            self.logger.info("Calendar event %s newer than local task %s", event_id, task.id)
            self.repo.update_from_sync(
                task.id,
                title=summary,
                notes=notes or None,
                start=start,
                duration_minutes=duration,
                gcal_event_id=event_id,
                gcal_etag=event.get("etag"),
                gcal_updated=remote_updated,
                updated_at=remote_updated,
            )
            return True

        self.logger.debug("Local task %s wins over calendar event %s", task.id, event_id)
        self._queue_calendar_sync(task)
        return False

    def _pull_tasks(self) -> bool:
        self.logger.debug("Pulling Google Tasks")
        self.gtasks.connect()
        updated_min = self.tokens.get_tasks_updated_min()
        items = self.gtasks.list(updated_min=updated_min)
        if not items:
            self.tokens.set_tasks_pull_timestamp()
            return False

        changed = False
        latest_remote: Optional[datetime] = updated_min
        for entry in items:
            if self._apply_task_entry(entry):
                changed = True
            remote_updated = ensure_utc(parse_rfc3339(entry.get("updated")))
            if remote_updated and (latest_remote is None or remote_updated > latest_remote):
                latest_remote = remote_updated

        if latest_remote:
            self.tokens.set_tasks_updated_min(latest_remote)
        self.tokens.set_tasks_pull_timestamp()
        return changed

    def _apply_task_entry(self, entry: dict) -> bool:
        task_id = entry.get("id")
        if not task_id:
            return False
        deleted = entry.get("deleted") or entry.get("status") == "deleted"
        remote_updated = ensure_utc(parse_rfc3339(entry.get("updated"))) or utc_now()
        title = entry.get("title") or "Без названия"
        notes = entry.get("notes") or None
        due_raw = entry.get("due")
        due_dt = ensure_utc(parse_rfc3339(due_raw)) if due_raw else None
        if due_dt:
            due_dt = due_dt.replace(hour=0, minute=0, second=0, microsecond=0)

        task = self.repo.get_by_gtasks_id(task_id)

        if deleted:
            if task:
                self.logger.info("Remote task deleted -> removing local task %s", task.id)
                self.repo.delete_from_sync(task.id)
                return True
            return False

        if not task:
            self.logger.info("New Google Task -> creating local task")
            self.repo.create_from_sync(
                title=title,
                notes=notes,
                start=due_dt,
                duration_minutes=None,
                status="todo",
                gtasks_id=task_id,
                gtasks_updated=remote_updated,
            )
            return True

        local_updated = ensure_utc(task.updated_at)
        known_remote = ensure_utc(task.gtasks_updated)
        if known_remote and remote_updated <= known_remote:
            return False

        if remote_updated >= local_updated:
            self.logger.info("Google Task %s newer than local task %s", task_id, task.id)
            self.repo.update_from_sync(
                task.id,
                title=title,
                notes=notes,
                start=due_dt,
                duration_minutes=None,
                gtasks_id=task_id,
                gtasks_updated=remote_updated,
                gcal_event_id=None if not _is_scheduled(task) else task.gcal_event_id,
                updated_at=remote_updated,
            )
            return True

        self.logger.debug("Local task %s wins over Google Task %s", task.id, task_id)
        self._queue_tasks_sync(task)
        return False

    # ------------------------------------------------------------------
    # Queue helpers
    def _queue_calendar_sync(self, task: Task) -> None:
        if task.gcal_event_id:
            self.queue.enqueue("gcal_update", task.id, {"eventId": task.gcal_event_id})
        else:
            self.queue.enqueue("gcal_create", task.id, {})

    def _queue_tasks_sync(self, task: Optional[Task]) -> None:
        if not task:
            return
        if task.gtasks_id:
            self.queue.enqueue("gtasks_update", task.id, {"taskId": task.gtasks_id})
        else:
            self.queue.enqueue("gtasks_create", task.id, {})

    def _ensure_calendar_delete(self, task: Task) -> None:
        if task.gcal_event_id:
            self.queue.enqueue("gcal_delete", task.id, {"eventId": task.gcal_event_id})

    def _ensure_tasks_delete(self, task: Task) -> None:
        if task.gtasks_id:
            self.queue.enqueue("gtasks_delete", task.id, {"taskId": task.gtasks_id})

    # ------------------------------------------------------------------
    def _execute_op(self, entry) -> bool:
        op = entry.op
        payload = entry.payload or {}

        if op == "gcal_create":
            task = self.repo.get(entry.task_id)
            if not task or not _is_scheduled(task):
                return True
            self.gcal.connect()
            service = getattr(self.gcal, "service", None)
            if service is None:
                return False
            body = build_event_payload(task)
            response = service.events().insert(calendarId=self.gcal.calendar_id, body=body).execute()
            updated = event_updated(response) or utc_now()
            self.repo.update_from_sync(
                task.id,
                gcal_event_id=response.get("id"),
                gcal_etag=response.get("etag"),
                gcal_updated=updated,
                updated_at=updated,
            )
            return True

        if op == "gcal_update":
            task = self.repo.get(entry.task_id)
            if not task or not _is_scheduled(task):
                return True
            event_id = payload.get("eventId") or task.gcal_event_id
            if not event_id:
                return True
            self.gcal.connect()
            service = getattr(self.gcal, "service", None)
            if service is None:
                return False
            body = build_event_payload(task)
            response = service.events().patch(
                calendarId=self.gcal.calendar_id, eventId=event_id, body=body
            ).execute()
            updated = event_updated(response) or utc_now()
            self.repo.update_from_sync(
                task.id,
                gcal_event_id=response.get("id", event_id),
                gcal_etag=response.get("etag"),
                gcal_updated=updated,
                updated_at=updated,
            )
            return True

        if op == "gcal_delete":
            event_id = payload.get("eventId")
            task = self.repo.get(entry.task_id)
            if not event_id and task:
                event_id = task.gcal_event_id
            if not event_id:
                return True
            self.gcal.connect()
            service = getattr(self.gcal, "service", None)
            if service is None:
                return False
            try:
                service.events().delete(calendarId=self.gcal.calendar_id, eventId=event_id).execute()
            except HttpError as exc:
                status = getattr(exc, "resp", None) and getattr(exc.resp, "status", None)
                if status and int(status) == 404:
                    pass
                else:
                    raise
            if task:
                self.repo.update_from_sync(
                    task.id,
                    gcal_event_id=None,
                    gcal_etag=None,
                    gcal_updated=utc_now(),
                )
            return True

        if op == "gtasks_create":
            task = self.repo.get(entry.task_id)
            if not task or _is_scheduled(task):
                return True
            due = _due_datetime(task)
            response = self.gtasks.insert(task.title, task.notes, due)
            remote_updated = ensure_utc(parse_rfc3339(response.get("updated"))) or utc_now()
            self.repo.update_from_sync(
                task.id,
                gtasks_id=response.get("id"),
                gtasks_updated=remote_updated,
                updated_at=remote_updated,
            )
            return True

        if op == "gtasks_update":
            task = self.repo.get(entry.task_id)
            if not task:
                return True
            task_id = payload.get("taskId") or task.gtasks_id
            if not task_id:
                return True
            due = _due_datetime(task)
            self.gtasks.patch(
                task_id,
                title=task.title,
                notes=task.notes,
                due=due,
            )
            self.repo.update_from_sync(
                task.id,
                gtasks_id=task_id,
                gtasks_updated=utc_now(),
            )
            return True

        if op == "gtasks_delete":
            task_id = payload.get("taskId")
            task = self.repo.get(entry.task_id)
            if not task_id and task:
                task_id = task.gtasks_id
            if not task_id:
                return True
            try:
                self.gtasks.delete(task_id)
            except HttpError as exc:
                status = getattr(exc, "resp", None) and getattr(exc.resp, "status", None)
                if status and int(status) == 404:
                    pass
                else:
                    raise
            if task:
                self.repo.update_from_sync(
                    task.id,
                    gtasks_id=None,
                    gtasks_updated=utc_now(),
                )
            return True

        return False


__all__ = ["SyncService", "SYNC_LOG_PATH"]
