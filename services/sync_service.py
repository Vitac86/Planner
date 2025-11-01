from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from googleapiclient.errors import HttpError

from datetime_utils import parse_rfc3339, to_rfc3339_utc
from services.google_calendar import GoogleCalendar
from services.google_sync import build_event_payload, parse_marker, parse_event_datetime, strip_marker, ensure_marker
from services.pending_ops_queue import PendingOpsQueue
from services.sync_token_storage import SyncTokenStorage
from services.task_repository import TaskRepository

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SyncService:
    def __init__(
        self,
        gcal: GoogleCalendar,
        repo: TaskRepository,
        ops: PendingOpsQueue,
        tokens: SyncTokenStorage,
    ) -> None:
        self.gcal = gcal
        self.repo = repo
        self.ops = ops
        self.tokens = tokens

    # Pull: забрать изменения из Google и смержить в локальную БД
    def pull(self) -> int:
        calendar_id = getattr(self.gcal, "calendar_id", None)
        if not calendar_id:
            return 0

        self.gcal._maybe_build_service(strict=True)  # type: ignore[attr-defined]
        token = self.tokens.get()
        params = dict(
            calendarId=calendar_id,
            singleEvents=True,
            showDeleted=True,
            maxResults=250,
        )
        if token:
            params["syncToken"] = token
        else:
            params["timeMin"] = to_rfc3339_utc(_utcnow() - timedelta(days=90))

        changes = 0

        while True:
            try:
                response = self.gcal.service.events().list(**params).execute()  # type: ignore[attr-defined]
            except HttpError as exc:
                if getattr(exc, "resp", None) and getattr(exc.resp, "status", None) == 410:
                    logger.warning("Sync token expired, performing full resync")
                    self.reset_sync_token()
                    return self.pull()
                raise

            for event in response.get("items", []):
                if self._apply_remote_event(event):
                    changes += 1

            if "nextPageToken" in response:
                params.pop("syncToken", None)
                params.pop("timeMin", None)
                params["pageToken"] = response["nextPageToken"]
                continue

            if "nextSyncToken" in response:
                self.tokens.set(response["nextSyncToken"])
            break

        return changes

    def _apply_remote_event(self, event: dict) -> bool:
        status = event.get("status")
        event_id = event.get("id")
        if not event_id:
            return False

        description = event.get("description") or ""
        marker = parse_marker(description)
        task = None
        if marker:
            task = self.repo.get(marker)
        if task is None:
            task = self.repo.get_by_event_id(event_id)

        updated_remote = parse_rfc3339(event.get("updated"))
        notes = strip_marker(description)

        if status == "cancelled":
            if task:
                self.repo.mark_unscheduled(task.id)
                return True
            return False

        start = parse_event_datetime(event.get("start", {}))
        end = parse_event_datetime(event.get("end", {}))
        duration = None
        if start and end and end > start:
            duration = int((end - start).total_seconds() // 60)

        if not task:
            title = event.get("summary") or "Без названия"
            task = self.repo.add(
                title=title,
                notes=notes or None,
                start=start,
                duration_minutes=duration,
            )
            self.repo.update(
                task,
                gcal_event_id=event_id,
                gcal_etag=event.get("etag"),
                gcal_updated_utc=to_rfc3339_utc(updated_remote) if updated_remote else None,
            )
            self._ensure_marker_on_event(event, task.id, notes)
            return True

        local_updated = task.updated_at.replace(tzinfo=timezone.utc)
        remote_updated = updated_remote or local_updated
        known_remote = parse_rfc3339(task.gcal_updated_utc) if task.gcal_updated_utc else None

        if known_remote and remote_updated <= known_remote:
            return False

        if remote_updated >= local_updated:
            self.repo.update(
                task,
                title=event.get("summary") or task.title,
                notes=notes or None,
                start=start,
                duration_minutes=duration,
                gcal_event_id=event_id,
                gcal_etag=event.get("etag"),
                gcal_updated_utc=to_rfc3339_utc(remote_updated),
            )
            self._ensure_marker_on_event(event, task.id, notes)
            return True

        # локальная версия новее — отправим апдейт обратно
        body = build_event_payload(task)
        self.ops.enqueue("update", task.id, {"eventId": event_id, "body": body})
        return False

    def _ensure_marker_on_event(self, event: dict, task_id: int, notes: str) -> None:
        description = event.get("description") or ""
        if f"planner_task_id:{task_id}" in description:
            return
        new_description = ensure_marker(notes or "", task_id)
        try:
            self.gcal.service.events().patch(  # type: ignore[attr-defined]
                calendarId=self.gcal.calendar_id,
                eventId=event.get("id"),
                body={"description": new_description},
            ).execute()
        except Exception as exc:  # pragma: no cover - best effort
            logger.debug("Failed to patch marker: %s", exc)

    # Вызывается подпиской на события TaskService
    def on_task_created(self, task_id: int):
        task = self.repo.get(task_id)
        if not task:
            return
        if not task.start or not task.duration_minutes:
            return
        payload = build_event_payload(task)
        self.ops.enqueue("create", task_id, {"body": payload})

    def on_task_updated(self, task_id: int):
        task = self.repo.get(task_id)
        if not task:
            return
        event_id = task.gcal_event_id
        if not task.start or not task.duration_minutes:
            if event_id:
                self.ops.enqueue("delete", task_id, {"eventId": event_id})
            return
        payload = build_event_payload(task)
        if event_id:
            self.ops.enqueue("update", task_id, {"eventId": event_id, "body": payload})
        else:
            self.ops.enqueue("create", task_id, {"body": payload})

    def on_task_deleted(self, task_id: int):
        task = self.repo.get(task_id)
        if not task or not task.gcal_event_id:
            return
        self.ops.enqueue("delete", task_id, {"eventId": task.gcal_event_id})

    # Служебное
    def force_full_resync(self) -> None:
        self.reset_sync_token()
        self.pull()

    def reset_sync_token(self) -> None:
        self.tokens.clear()

    def process_pending(self, batch_size: int = 10) -> int:
        processed = 0
        for entry in self.ops.due(limit=batch_size):
            try:
                if self._execute_op(entry):
                    processed += 1
                    self.ops.remove(entry.id)
                else:
                    self.ops.requeue(entry.id, "invalid payload")
            except HttpError as exc:
                status = getattr(exc.resp, "status", None)
                if status and int(status) in {429, 500, 502, 503, 504}:
                    self.ops.requeue(entry.id, str(exc))
                else:
                    self.ops.requeue(entry.id, str(exc))
            except Exception as exc:  # pragma: no cover - best effort
                self.ops.requeue(entry.id, str(exc))
        return processed

    def _execute_op(self, entry) -> bool:
        payload = entry.payload or {}
        if entry.op == "create":
            body = payload.get("body", {})
            response = self.gcal.service.events().insert(  # type: ignore[attr-defined]
                calendarId=self.gcal.calendar_id,
                body=body,
            ).execute()
            task = self.repo.get(entry.task_id)
            if task:
                self.repo.update(
                    task,
                    gcal_event_id=response.get("id"),
                    gcal_etag=response.get("etag"),
                    gcal_updated_utc=response.get("updated"),
                )
            return True

        if entry.op == "update":
            body = payload.get("body", {})
            event_id = payload.get("eventId")
            if not event_id:
                return False
            response = self.gcal.service.events().patch(  # type: ignore[attr-defined]
                calendarId=self.gcal.calendar_id,
                eventId=event_id,
                body=body,
            ).execute()
            task = self.repo.get(entry.task_id)
            if task:
                self.repo.update(
                    task,
                    gcal_event_id=response.get("id", event_id),
                    gcal_etag=response.get("etag"),
                    gcal_updated_utc=response.get("updated"),
                )
            return True

        if entry.op == "delete":
            event_id = payload.get("eventId")
            if not event_id:
                return True
            try:
                self.gcal.service.events().delete(  # type: ignore[attr-defined]
                    calendarId=self.gcal.calendar_id,
                    eventId=event_id,
                ).execute()
            except HttpError as exc:
                status = getattr(exc.resp, "status", None)
                if status and int(status) == 404:
                    pass
                else:
                    raise
            task = self.repo.get(entry.task_id)
            if task:
                self.repo.update(
                    task,
                    gcal_event_id=None,
                    gcal_etag=None,
                    gcal_updated_utc=None,
                )
            return True

        return False


__all__ = ["SyncService"]
