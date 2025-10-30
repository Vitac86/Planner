"""Google Tasks bridge for synchronizing undated Planner tasks."""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, Optional, Tuple

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from core.settings import GOOGLE_SYNC

try:  # pragma: no cover - optional dependency when running tests without Google SDK
    from google.oauth2.credentials import Credentials
except Exception:  # pragma: no cover
    Credentials = None

DEFAULT_SCOPES = list(GOOGLE_SYNC.scopes)
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}
_MAX_RETRIES = 5
_INITIAL_BACKOFF = 1.0
_MAX_BACKOFF = 32.0
_TASKLIST_TITLE = "Planner Inbox"


def _ensure_datetime(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        else:
            value = value.astimezone(timezone.utc)
        return value.isoformat()
    return str(value)


def _path_exists(path: Any) -> bool:
    try:
        import os

        return os.path.exists(os.fspath(path))
    except Exception:
        return False


def _find_creds_in_auth(auth: Any, scopes: Optional[Iterable[str]] = None):
    for name in ("get_credentials", "credentials", "creds"):
        val = getattr(auth, name, None)
        if callable(val):
            try:
                val = val()
            except Exception:
                val = None
        if val is not None and hasattr(val, "valid"):
            return val

    token_path = None
    for attr in ("token_path", "token_file", "token", "token_json"):
        pth = getattr(auth, attr, None)
        if pth and _path_exists(pth):
            token_path = pth
            break

    if token_path and Credentials:
        try:
            return Credentials.from_authorized_user_file(token_path, scopes or DEFAULT_SCOPES)
        except Exception:
            return None
    return None


def _build_service(creds: Any):
    if creds is None:
        return None
    return build("tasks", "v1", credentials=creds)


def _metadata_from_task(local_task: Dict[str, Any]) -> Dict[str, Any]:
    meta = {
        "task_id": local_task.get("task_id"),
        "priority": local_task.get("priority"),
        "status": local_task.get("status"),
        "updated_at": _ensure_datetime(local_task.get("updated_at")),
        "device_id": local_task.get("device_id"),
    }
    # remove empty values to reduce payload noise
    return {k: v for k, v in meta.items() if v is not None and v != ""}


def _compose_notes(meta: Dict[str, Any], notes: Optional[str]) -> str:
    payload = json.dumps(meta, ensure_ascii=False, sort_keys=True)
    body = (notes or "").strip()
    if body:
        return f"{payload}\n\n{body}"
    return payload


def _split_notes(raw_notes: Optional[str]) -> Tuple[Dict[str, Any], str]:
    if not raw_notes:
        return {}, ""
    stripped = raw_notes.lstrip()
    first_line, remainder = (stripped, "")
    if "\n" in stripped:
        first_line, remainder = stripped.split("\n", 1)
    try:
        parsed = json.loads(first_line.strip())
        if isinstance(parsed, dict):
            return parsed, remainder.lstrip("\n")
    except json.JSONDecodeError:
        pass
    return {}, raw_notes


def _status_payload(local_task: Dict[str, Any]) -> Tuple[str, Optional[str]]:
    status = str(local_task.get("status") or "").lower()
    if status == "done":
        completed_at = _ensure_datetime(local_task.get("updated_at")) or datetime.now(timezone.utc).isoformat()
        return "completed", completed_at
    return "needsAction", None


class GoogleTasksBridge:
    """Lightweight wrapper over Google Tasks API with retry/backoff."""

    def __init__(self, auth: Any):
        self.auth = auth
        self.service = None
        self._maybe_build_service()

    @property
    def tasklist_title(self) -> str:
        return _TASKLIST_TITLE

    # ----- public API -----
    def ensure_tasklist(self) -> str:
        self._maybe_build_service(strict=True)
        page_token = None
        while True:
            response = self._call_with_backoff(
                self.service.tasklists().list,
                maxResults=100,
                pageToken=page_token,
            )
            for item in response.get("items", []):
                if (item.get("title") or "").strip().lower() == self.tasklist_title.lower():
                    return item.get("id")
            page_token = response.get("nextPageToken")
            if not page_token:
                break

        created = self._call_with_backoff(
            self.service.tasklists().insert,
            body={"title": self.tasklist_title},
        )
        return created.get("id")

    def fetch_all(self, tasklist_id: str) -> list[Dict[str, Any]]:
        self._maybe_build_service(strict=True)
        page_token = None
        results: list[Dict[str, Any]] = []
        while True:
            response = self._call_with_backoff(
                self.service.tasks().list,
                tasklist=tasklist_id,
                maxResults=100,
                showCompleted=True,
                showDeleted=False,
                pageToken=page_token,
            )
            for item in response.get("items", []):
                if item.get("deleted"):
                    continue
                meta, body = _split_notes(item.get("notes"))
                info = {
                    "id": item.get("id"),
                    "title": item.get("title") or "",
                    "notes": body,
                    "metadata": meta,
                    "updated": item.get("updated"),
                    "status": item.get("status"),
                    "raw": item,
                }
                if "task_id" not in info["metadata"] and item.get("id"):
                    info["metadata"]["task_id"] = None
                results.append(info)
            page_token = response.get("nextPageToken")
            if not page_token:
                break
        return results

    def find_task_by_local_id(self, tasklist_id: str, local_task_id: str) -> Optional[Dict[str, Any]]:
        for item in self.fetch_all(tasklist_id):
            metadata = item.get("metadata") or {}
            if str(metadata.get("task_id")) == str(local_task_id):
                return item
        return None

    def upsert_task(self, tasklist_id: str, local_task: Dict[str, Any]) -> str:
        if not local_task.get("task_id"):
            raise ValueError("local_task must contain task_id")
        self._maybe_build_service(strict=True)

        gtask_id = local_task.get("gtask_id")
        if not gtask_id:
            existing = self.find_task_by_local_id(tasklist_id, str(local_task["task_id"]))
            if existing:
                gtask_id = existing.get("id")

        metadata = _metadata_from_task(local_task)
        notes = _compose_notes(metadata, local_task.get("notes"))
        status, completed_ts = _status_payload(local_task)
        payload = {
            "title": local_task.get("title") or "",
            "notes": notes,
            "status": status,
        }
        if completed_ts:
            payload["completed"] = completed_ts
        elif gtask_id:
            # Explicitly clear "completed" if task switches back to needsAction
            payload["completed"] = None

        if gtask_id:
            payload["id"] = gtask_id
            response = self._call_with_backoff(
                self.service.tasks().update,
                tasklist=tasklist_id,
                task=gtask_id,
                body=payload,
            )
        else:
            response = self._call_with_backoff(
                self.service.tasks().insert,
                tasklist=tasklist_id,
                body=payload,
            )
        return response.get("id")

    def delete_task(self, tasklist_id: str, gtask_id: str) -> None:
        if not gtask_id:
            return
        self._maybe_build_service(strict=True)
        try:
            self._call_with_backoff(
                self.service.tasks().delete,
                tasklist=tasklist_id,
                task=gtask_id,
            )
        except HttpError as exc:
            status = getattr(getattr(exc, "resp", None), "status", None)
            if status == 404:
                return
            raise

    # ----- internal helpers -----
    def _maybe_build_service(self, strict: bool = False) -> None:
        if self.service is not None:
            return
        creds = _find_creds_in_auth(self.auth, DEFAULT_SCOPES)
        if creds and getattr(creds, "valid", False):
            self.service = _build_service(creds)
        elif strict:
            raise RuntimeError("GoogleTasksBridge: credentials are unavailable")

    def _call_with_backoff(self, method: Callable[..., Any], **kwargs) -> Dict[str, Any]:
        delay = _INITIAL_BACKOFF
        last_error: Optional[Exception] = None
        for attempt in range(_MAX_RETRIES):
            try:
                request = method(**kwargs)
                return request.execute()
            except HttpError as exc:
                last_error = exc
                status = getattr(getattr(exc, "resp", None), "status", None)
                if status not in _RETRYABLE_STATUS or attempt == _MAX_RETRIES - 1:
                    raise
            except Exception as exc:  # pragma: no cover - defensive fallback
                last_error = exc
                if attempt == _MAX_RETRIES - 1:
                    raise
            time.sleep(delay)
            delay = min(delay * 2, _MAX_BACKOFF)
        if last_error:
            raise last_error
        return {}


__all__ = ["GoogleTasksBridge"]
