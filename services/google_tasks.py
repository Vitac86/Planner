"""Google Tasks synchronisation helpers."""

from __future__ import annotations

import io
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, Optional

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload

from core.settings import GOOGLE_SYNC
from models.task import Task
from models.task_sync import TaskSyncMapping
from services.task_sync_store import TaskSyncStore
from services.tasks import TaskService
from datetime_utils import parse_rfc3339, to_rfc3339


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _utcnow_iso() -> str:
    value = to_rfc3339(_utcnow())
    if value is None:
        raise ValueError("Unable to format current UTC time")
    return value


def _parse_google_datetime(value: Optional[str]) -> Optional[datetime]:
    parsed = parse_rfc3339(value)
    if parsed is None:
        return None
    return parsed.replace(tzinfo=None)


def _normalize_local(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


@dataclass
class DriveSnapshot:
    file_id: Optional[str]
    content: Optional[str]
    modified_at: Optional[datetime]


class DriveAppDataClient:
    """Helper for storing metadata in Drive appDataFolder."""

    def __init__(self, drive_service):
        self.drive_service = drive_service

    def download(self, filename: str, known_file_id: Optional[str] = None) -> DriveSnapshot:
        file_id = known_file_id
        meta = None
        if file_id:
            try:
                meta = (
                    self.drive_service.files()
                    .get(fileId=file_id, fields="id, name, modifiedTime")
                    .execute()
                )
            except HttpError as e:
                if getattr(e, "resp", None) and getattr(e.resp, "status", None) == 404:
                    file_id = None
                else:
                    raise

        if not file_id:
            res = (
                self.drive_service.files()
                .list(
                    spaces="appDataFolder",
                    q="name = '{}'".format(filename.replace("'", "\\'")),
                    fields="files(id, name, modifiedTime)",
                    pageSize=1,
                )
                .execute()
            )
            files = res.get("files", [])
            if files:
                meta = files[0]
                file_id = meta.get("id")
            else:
                return DriveSnapshot(file_id=None, content=None, modified_at=None)

        request = self.drive_service.files().get_media(fileId=file_id)
        buffer = io.BytesIO()
        downloader = MediaIoBaseDownload(buffer, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        content = buffer.getvalue().decode("utf-8")
        modified_at = _parse_google_datetime((meta or {}).get("modifiedTime")) if meta else None
        return DriveSnapshot(file_id=file_id, content=content, modified_at=modified_at)

    def upload(self, filename: str, content: str, file_id: Optional[str] = None) -> str:
        media = MediaIoBaseUpload(io.BytesIO(content.encode("utf-8")), mimetype="application/json")
        body = {"name": filename, "parents": ["appDataFolder"]}
        if file_id:
            res = (
                self.drive_service.files()
                .update(fileId=file_id, media_body=media, body={"name": filename})
                .execute()
            )
            return res.get("id", file_id)
        res = self.drive_service.files().create(body=body, media_body=media, fields="id").execute()
        return res.get("id")


class GoogleTasksSync:
    """Synchronise Planner unscheduled tasks with Google Tasks."""

    def __init__(self, auth, *, task_service: Optional[TaskService] = None, store: Optional[TaskSyncStore] = None):
        self.auth = auth
        self.task_service = task_service or TaskService()
        self.store = store or TaskSyncStore()
        self.service = None
        self.drive_service = None
        self.tasklist_id: Optional[str] = None
        self._drive_synced = False

    # ----- initialisation -----
    def connect(self):
        self._ensure_services()
        self._ensure_tasklist()
        self._ensure_drive_backup()

    def _ensure_services(self):
        if self.service and self.drive_service:
            return
        creds = None
        if hasattr(self.auth, "get_credentials"):
            creds = self.auth.get_credentials()
        elif hasattr(self.auth, "ensure_credentials"):
            if self.auth.ensure_credentials():
                creds = getattr(self.auth, "creds", None)
        if not creds:
            raise RuntimeError("Google credentials are not available")
        if not self.service:
            self.service = build("tasks", "v1", credentials=creds)
        if not self.drive_service:
            self.drive_service = build("drive", "v3", credentials=creds)

    def _ensure_tasklist(self) -> str:
        meta = self.store.get_meta()
        if self.tasklist_id:
            return self.tasklist_id
        candidate = meta.tasklist_id
        if candidate:
            try:
                self.service.tasklists().get(tasklist=candidate).execute()
                self.tasklist_id = candidate
                return candidate
            except HttpError as e:
                if getattr(e, "resp", None) and getattr(e.resp, "status", None) != 404:
                    raise

        name = GOOGLE_SYNC.tasks_tasklist_name
        found = self._find_tasklist_by_name(name)
        if not found:
            created = self.service.tasklists().insert(body={"title": name}).execute()
            found = created.get("id")
        if not found:
            raise RuntimeError("Unable to determine Google Tasks list id")
        self.tasklist_id = found
        self.store.update_meta(tasklist_id=found)
        return found

    def _find_tasklist_by_name(self, name: str) -> Optional[str]:
        page_token = None
        while True:
            resp = self.service.tasklists().list(maxResults=100, pageToken=page_token).execute()
            for item in resp.get("items", []):
                if item.get("title") == name:
                    return item.get("id")
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
        return None

    # ----- drive backup -----
    def _ensure_drive_backup(self, *, push_local: bool = False):
        if not self.drive_service:
            return
        client = DriveAppDataClient(self.drive_service)
        meta = self.store.get_meta()
        local_updated = self.store.max_mapping_updated_at()
        if push_local:
            if local_updated:
                payload = self._serialize_mappings(local_updated)
                file_id = client.upload(GOOGLE_SYNC.tasks_meta_filename, payload, meta.drive_file_id)
                self.store.update_meta(drive_snapshot_at=local_updated, drive_file_id=file_id)
            return

        snapshot = client.download(GOOGLE_SYNC.tasks_meta_filename, meta.drive_file_id)
        if snapshot.file_id and snapshot.file_id != meta.drive_file_id:
            self.store.update_meta(drive_file_id=snapshot.file_id)

        if snapshot.content:
            try:
                data = json.loads(snapshot.content)
            except json.JSONDecodeError:
                data = None
            if data:
                remote_updated = _parse_google_datetime(data.get("updated_at")) or snapshot.modified_at
                mappings_data = data.get("mappings", [])
                if mappings_data and (not local_updated or (remote_updated and remote_updated > local_updated)):
                    entries = []
                    for item in mappings_data:
                        local_id = item.get("local_id")
                        if not local_id:
                            continue
                        if not self.task_service.get(local_id):
                            continue
                        entry = TaskSyncMapping(
                            local_id=local_id,
                            google_task_id=item.get("google_task_id"),
                            tasklist_id=item.get("tasklist_id"),
                            etag=item.get("etag"),
                            updated_at_utc=_parse_google_datetime(item.get("updated_at_utc")) or _utcnow(),
                        )
                        entries.append(entry)
                    if entries:
                        self.store.replace_mappings(entries)
                        self.store.update_meta(
                            drive_snapshot_at=remote_updated or _utcnow(),
                            drive_file_id=snapshot.file_id,
                        )
                        self._drive_synced = True
                        return

        if local_updated:
            payload = self._serialize_mappings(local_updated)
            file_id = client.upload(GOOGLE_SYNC.tasks_meta_filename, payload, snapshot.file_id or meta.drive_file_id)
            self.store.update_meta(drive_snapshot_at=local_updated, drive_file_id=file_id)
        self._drive_synced = True

    def _serialize_mappings(self, updated_at: datetime) -> str:
        payload = {
            "updated_at": to_rfc3339(updated_at),
            "mappings": [
                {
                    "local_id": m.local_id,
                    "google_task_id": m.google_task_id,
                    "tasklist_id": m.tasklist_id,
                    "etag": m.etag,
                    "updated_at_utc": to_rfc3339(m.updated_at_utc),
                }
                for m in self.store.list_mappings()
            ],
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)

    # ----- pull -----
    def pull(self, *, incremental: bool = True) -> bool:
        if not GOOGLE_SYNC.enabled:
            return False
        self._ensure_services()
        tasklist_id = self._ensure_tasklist()
        if not self._drive_synced:
            self._ensure_drive_backup()

        meta = self.store.get_meta()
        updated_min = meta.updated_min if incremental else None
        changed = False
        page_token = None

        params = dict(
            tasklist=tasklist_id,
            showCompleted=True,
            showHidden=True,
            showDeleted=True,
            maxResults=100,
        )
        if updated_min:
            params["updatedMin"] = updated_min

        while True:
            if page_token:
                params["pageToken"] = page_token
            else:
                params.pop("pageToken", None)
            resp = self.service.tasks().list(**params).execute()
            for item in resp.get("items", []):
                if item.get("parent"):
                    continue  # ignore subtasks for now
                if self._apply_remote_task(item):
                    changed = True
            page_token = resp.get("nextPageToken")
            if not page_token:
                break

        now_iso = _utcnow_iso()
        self.store.update_meta(
            updated_min=now_iso,
            last_pull_at=_utcnow(),
            tasklist_id=tasklist_id,
        )
        if changed:
            self._ensure_drive_backup(push_local=True)
        return changed

    def _apply_remote_task(self, item: Dict) -> bool:
        google_id = item.get("id")
        if not google_id:
            return False
        mapping = self.store.get_mapping_by_google(google_id)
        status = item.get("status")
        deleted = item.get("deleted")
        notes = (item.get("notes") or "").strip()
        title = item.get("title") or "Без названия"
        updated_remote = _parse_google_datetime(item.get("updated")) or _utcnow()
        due = item.get("due")

        if deleted:
            if mapping:
                self.task_service.set_status(mapping.local_id, "done")
                self.store.delete_mapping(mapping.local_id)
                return True
            return False

        if status == "completed":
            if mapping:
                self.task_service.set_status(mapping.local_id, "done")
                self.store.upsert_mapping(
                    mapping.local_id,
                    google_task_id=google_id,
                    tasklist_id=self.tasklist_id,
                    etag=item.get("etag"),
                    updated_at_utc=updated_remote,
                )
                return True
            return False

        if due:
            # Currently we only track "без даты" задачи via Google Tasks
            return False

        if mapping:
            task = self.task_service.get(mapping.local_id)
            if not task:
                # Mapping exists but task missing -> recreate
                task = self.task_service.add(title=title, notes=notes or None)
                self.store.upsert_mapping(
                    task.id,
                    google_task_id=google_id,
                    tasklist_id=self.tasklist_id,
                    etag=item.get("etag"),
                    updated_at_utc=updated_remote,
                )
                return True
            changed = False
            updates = {}
            if task.title != title:
                updates["title"] = title
            if (task.notes or "").strip() != notes:
                updates["notes"] = notes
            if task.status == "done":
                self.task_service.set_status(task.id, "todo")
                changed = True
            if updates:
                self.task_service.update(task.id, **updates)
                changed = True
            self.store.upsert_mapping(
                task.id,
                google_task_id=google_id,
                tasklist_id=self.tasklist_id,
                etag=item.get("etag"),
                updated_at_utc=updated_remote,
            )
            return changed

        # New task from Google
        task = self.task_service.add(title=title, notes=notes or None)
        self.store.upsert_mapping(
            task.id,
            google_task_id=google_id,
            tasklist_id=self.tasklist_id,
            etag=item.get("etag"),
            updated_at_utc=updated_remote,
        )
        return True

    # ----- push -----
    def push(self, *, force_all: bool = False) -> bool:
        if not GOOGLE_SYNC.enabled:
            return False
        self._ensure_services()
        tasklist_id = self._ensure_tasklist()
        meta = self.store.get_meta()
        since = None if force_all else meta.last_push_at
        tasks = self.task_service.list_unscheduled_updated_since(since)
        changed = False
        for task in tasks:
            if task.status == "done":
                continue
            if self._push_task(task, tasklist_id, force=force_all):
                changed = True
        if changed or force_all:
            self.store.update_meta(last_push_at=_utcnow())
            self._ensure_drive_backup(push_local=True)
        return changed

    def _push_task(self, task: Task, tasklist_id: str, *, force: bool = False) -> bool:
        mapping = self.store.get_mapping(task.id)
        body = {
            "title": task.title or "Без названия",
            "notes": (task.notes or "").strip(),
            "status": "needsAction",
        }

        if mapping and mapping.google_task_id:
            if not force and not self._needs_remote_update(task, mapping):
                return False
            request = self.service.tasks().patch(
                tasklist=tasklist_id,
                task=mapping.google_task_id,
                body=body,
            )
            if mapping.etag:
                request.headers["If-Match"] = mapping.etag
            try:
                resp = request.execute()
            except HttpError as e:
                status = getattr(e.resp, "status", None)
                if status == 404:
                    mapping = None
                elif status == 412:
                    return self._handle_conflict(task, mapping, tasklist_id)
                else:
                    raise
            else:
                self.store.upsert_mapping(
                    task.id,
                    google_task_id=resp.get("id"),
                    tasklist_id=tasklist_id,
                    etag=resp.get("etag"),
                    updated_at_utc=_parse_google_datetime(resp.get("updated")) or _utcnow(),
                )
                return True

        if not mapping or not mapping.google_task_id:
            resp = self.service.tasks().insert(tasklist=tasklist_id, body=body).execute()
            self.store.upsert_mapping(
                task.id,
                google_task_id=resp.get("id"),
                tasklist_id=tasklist_id,
                etag=resp.get("etag"),
                updated_at_utc=_parse_google_datetime(resp.get("updated")) or _utcnow(),
            )
            return True
        return False

    def _needs_remote_update(self, task: Task, mapping: TaskSyncMapping) -> bool:
        remote_timestamp = _normalize_local(mapping.updated_at_utc)
        local_timestamp = _normalize_local(task.updated_at)
        if not remote_timestamp or not local_timestamp:
            return True
        return local_timestamp > remote_timestamp

    def _handle_conflict(self, task: Task, mapping: TaskSyncMapping, tasklist_id: str) -> bool:
        remote = (
            self.service.tasks()
            .get(tasklist=tasklist_id, task=mapping.google_task_id)
            .execute()
        )
        remote_updated = _parse_google_datetime(remote.get("updated"))
        local_updated = _normalize_local(task.updated_at)
        if remote_updated and (not local_updated or remote_updated >= local_updated):
            return self._apply_remote_task(remote)

        request = self.service.tasks().patch(
            tasklist=tasklist_id,
            task=mapping.google_task_id,
            body={
                "title": task.title or "Без названия",
                "notes": (task.notes or "").strip(),
                "status": "needsAction",
            },
        )
        etag = remote.get("etag")
        if etag:
            request.headers["If-Match"] = etag
        resp = request.execute()
        self.store.upsert_mapping(
            task.id,
            google_task_id=resp.get("id"),
            tasklist_id=tasklist_id,
            etag=resp.get("etag"),
            updated_at_utc=_parse_google_datetime(resp.get("updated")) or _utcnow(),
        )
        return True

    # ----- maintenance -----
    def cleanup_notes_metadata(self) -> int:
        changed = self.task_service.clean_notes_metadata()
        if changed:
            self.push(force_all=True)
        return changed

    def full_resync(self):
        self.store.update_meta(updated_min=None)
        self.pull(incremental=False)
        self.push(force_all=True)


__all__ = ["GoogleTasksSync"]
