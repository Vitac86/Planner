"""Minimal Google Tasks client used by the synchronisation service."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from core.settings import GOOGLE_SYNC
from datetime_utils import ensure_utc, to_rfc3339_utc


class GoogleTasks:
    def __init__(self, auth, tasklist_name: str | None = None) -> None:
        self.auth = auth
        self.tasklist_name = tasklist_name or GOOGLE_SYNC.tasks_tasklist_name or "Planner"
        self.service = None
        self.tasklist_id: Optional[str] = None

    # ------------------------------------------------------------------
    # Initialisation helpers
    def connect(self) -> None:
        self._ensure_service(strict=True)
        self.ensure_tasklist()

    def _ensure_service(self, strict: bool = False) -> None:
        if self.service is not None:
            return

        creds = None
        if hasattr(self.auth, "get_credentials") and callable(self.auth.get_credentials):
            creds = self.auth.get_credentials()
        elif hasattr(self.auth, "ensure_credentials") and callable(self.auth.ensure_credentials):
            if self.auth.ensure_credentials():  # type: ignore[misc]
                creds = getattr(self.auth, "creds", None) or getattr(self.auth, "credentials", None)
        else:
            creds = getattr(self.auth, "creds", None) or getattr(self.auth, "credentials", None)

        if not creds and strict:
            raise RuntimeError("Google credentials are not available")
        if not creds:
            return

        self.service = build("tasks", "v1", credentials=creds)

    def ensure_tasklist(self) -> str:
        self._ensure_service(strict=True)
        if self.tasklist_id:
            return self.tasklist_id

        service = self.service
        if service is None:  # pragma: no cover - defensive, should not happen
            raise RuntimeError("Google Tasks service is not initialised")

        page_token: Optional[str] = None
        while True:
            response = (
                service.tasklists()
                .list(maxResults=100, pageToken=page_token)
                .execute()
            )
            for item in response.get("items", []):
                if item.get("title") == self.tasklist_name:
                    self.tasklist_id = item.get("id")
                    if self.tasklist_id:
                        return self.tasklist_id
            page_token = response.get("nextPageToken")
            if not page_token:
                break

        created = (
            service.tasklists()
            .insert(body={"title": self.tasklist_name})
            .execute()
        )
        self.tasklist_id = created.get("id")
        if not self.tasklist_id:
            raise RuntimeError("Failed to create Google Tasks list")
        return self.tasklist_id

    # ------------------------------------------------------------------
    # CRUD helpers
    def list(self, updated_min: Optional[datetime] = None) -> List[Dict]:
        self.connect()
        service = self.service
        tasklist_id = self.tasklist_id
        if service is None or tasklist_id is None:  # pragma: no cover - defensive
            return []

        params: Dict[str, Any] = {
            "tasklist": tasklist_id,
            "showDeleted": True,
            "showHidden": True,
            "maxResults": 100,
        }
        if updated_min:
            params["updatedMin"] = to_rfc3339_utc(ensure_utc(updated_min)) or ""

        items: List[Dict] = []
        page_token: Optional[str] = None
        while True:
            response = (
                service.tasks()
                .list(pageToken=page_token, **params)
                .execute()
            )
            items.extend(response.get("items", []))
            page_token = response.get("nextPageToken")
            if not page_token:
                break
        return items

    def insert(self, title: str, notes: Optional[str], due: Optional[datetime]) -> Dict:
        self.connect()
        service = self.service
        tasklist_id = self.tasklist_id
        if service is None or tasklist_id is None:  # pragma: no cover - defensive
            raise RuntimeError("Google Tasks service is not initialised")

        body: Dict[str, Optional[str]] = {"title": title.strip() or "Задача"}
        notes_value = (notes or "").strip()
        if notes_value:
            body["notes"] = notes_value
        due_value = _format_due(due)
        if due_value:
            body["due"] = due_value
        return service.tasks().insert(tasklist=tasklist_id, body=body).execute()

    def patch(
        self,
        task_id: str,
        *,
        title: Optional[str] = None,
        notes: Optional[str] = None,
        due: Optional[datetime] = None,
        status: Optional[str] = None,
    ) -> Dict:
        self.connect()
        service = self.service
        tasklist_id = self.tasklist_id
        if service is None or tasklist_id is None:  # pragma: no cover - defensive
            raise RuntimeError("Google Tasks service is not initialised")

        body: Dict[str, Optional[str]] = {}
        if title is not None:
            body["title"] = title.strip() or "Задача"
        if notes is not None:
            notes_value = notes.strip()
            body["notes"] = notes_value or None
        if due is not None:
            body["due"] = _format_due(due)
        if status is not None:
            body["status"] = status

        return service.tasks().patch(tasklist=tasklist_id, task=task_id, body=body).execute()

    def delete(self, task_id: str) -> None:
        self.connect()
        service = self.service
        tasklist_id = self.tasklist_id
        if service is None or tasklist_id is None:  # pragma: no cover - defensive
            return
        try:
            service.tasks().delete(tasklist=tasklist_id, task=task_id).execute()
        except HttpError as exc:
            status = getattr(exc, "resp", None) and getattr(exc.resp, "status", None)
            if status and int(status) == 404:
                return
            raise


-def _format_due(value: Optional[datetime]) -> Optional[str]:
+def _format_due(value: Optional[datetime]) -> Optional[str]:
     if value is None:
         return None
-    normalized = ensure_utc(value).replace(microsecond=0)
-    # tasks API expects midnight timestamps for all-day entries
-    normalized = normalized.replace(hour=0, minute=0, second=0)
-    return to_rfc3339_utc(normalized)
+    normalized = ensure_utc(value)
+    normalized = normalized.replace(hour=0, minute=0, second=0, microsecond=0)
+    return to_rfc3339_utc(normalized)
+
+
+__all__ = ["GoogleTasks"]
