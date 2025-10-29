from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from core.settings import GOOGLE_SYNC

try:
    from google.oauth2.credentials import Credentials
except Exception:
    Credentials = None

DEFAULT_SCOPES = list(GOOGLE_SYNC.scopes)

# ---------- время в RFC3339 ----------
def _ensure_tz(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = datetime.now().astimezone().replace(year=dt.year, month=dt.month, day=dt.day,
                                                 hour=dt.hour, minute=dt.minute, second=dt.second, microsecond=dt.microsecond)
    return dt.astimezone()

def _to_rfc3339(dt: datetime) -> str:
    return _ensure_tz(dt).isoformat()

def _path_exists(p) -> bool:
    try:
        return os.path.exists(os.fspath(p))
    except Exception:
        return False

# ---------- извлечение сервиса из auth ----------
def _build_service_from_creds(creds) -> Any:
    if creds is None:
        return None
    return build("calendar", "v3", credentials=creds)

def _find_creds_in_auth(auth, scopes: Optional[List[str]] = None):
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
        p = getattr(auth, attr, None)
        if p and _path_exists(p):
            token_path = os.fspath(p)
            break
    if token_path and Credentials:
        try:
            return Credentials.from_authorized_user_file(token_path, scopes or DEFAULT_SCOPES)
        except Exception:
            pass
    return None

def _find_service_in_auth(auth) -> Any:
    for attr in ("calendar_service", "calendar", "service", "svc", "calendar_v3"):
        svc = getattr(auth, attr, None)
        if svc and hasattr(svc, "events"):
            return svc
    for meth in ("get_calendar_service", "build_calendar_service", "create_calendar_service"):
        if hasattr(auth, meth) and callable(getattr(auth, meth)):
            svc = getattr(auth, meth)()
            if svc and hasattr(svc, "events"):
                return svc
    for meth in ("get_service", "service_for", "build_service"):
        if hasattr(auth, meth) and callable(getattr(auth, meth)):
            try:
                svc = getattr(auth, meth)("calendar", "v3")
            except TypeError:
                svc = getattr(auth, meth)("calendar")
            if svc and hasattr(svc, "events"):
                return svc
    return None

# ---------- основной класс ----------
class GoogleCalendar:
    """
    Совместим с GoogleCalendar(self.auth) из AppShell.
    Ставит маркер planner_task_id:<id> в description для двусторонней синхронизации.
    """
    def __init__(self, auth, calendar_id: str = "primary"):
        self.auth = auth
        self.calendar_id = getattr(auth, "calendar_id", None) or calendar_id
        self.service = None
        self._maybe_build_service()

    def connect(self):
        if hasattr(self.auth, "ensure_credentials") and callable(getattr(self.auth, "ensure_credentials")):
            self.auth.ensure_credentials()
        self._maybe_build_service(strict=True)
        return True

    def _maybe_build_service(self, strict: bool = False):
        if self.service and hasattr(self.service, "events"):
            return
        svc = _find_service_in_auth(self.auth)
        if svc:
            self.service = svc
            return
        creds = _find_creds_in_auth(self.auth, DEFAULT_SCOPES)
        if creds:
            self.service = _build_service_from_creds(creds)
            return
        if strict:
            raise RuntimeError(
                "GoogleCalendar: не удалось собрать сервис из auth. Нужен token.json "
                "или ensure_credentials(), или get_credentials()/credentials/creds, "
                "или готовый service в auth."
            )

    # ----- utils для маркера -----
    @staticmethod
    def _with_marker(task, notes: Optional[str]) -> str:
        base = (notes or "").strip()
        marker = f"planner_task_id:{getattr(task, 'id', '')}"
        return f"{base}\n{marker}" if base else marker

    @staticmethod
    def parse_task_id_from_description(desc: Optional[str]) -> Optional[int]:
        if not desc:
            return None
        key = "planner_task_id:"
        try:
            for line in desc.splitlines():
                line = line.strip()
                if line.startswith(key):
                    return int(line[len(key):].strip())
        except Exception:
            return None
        return None

    # ----- операции -----
    def list_range(self, start_dt: datetime, end_dt: datetime, show_deleted: bool = False) -> List[Dict[str, Any]]:
        self._maybe_build_service(strict=True)
        params = dict(
            calendarId=self.calendar_id,
            timeMin=_to_rfc3339(start_dt),
            timeMax=_to_rfc3339(end_dt),
            singleEvents=True,
            orderBy="startTime",
            maxResults=2500,
        )
        if show_deleted:
            params["showDeleted"] = True
        res = self.service.events().list(**params).execute()
        return res.get("items", [])

    def create_event_for_task(self, task, start_dt: datetime, duration_minutes: int) -> Dict[str, Any]:
        self._maybe_build_service(strict=True)
        end_dt = _ensure_tz(start_dt) + timedelta(minutes=duration_minutes)
        body = {
            "summary": getattr(task, "title", "Задача"),
            "description": self._with_marker(task, getattr(task, "notes", None)),
            "start": {"dateTime": _to_rfc3339(start_dt)},
            "end": {"dateTime": _to_rfc3339(end_dt)},
        }
        return self.service.events().insert(calendarId=self.calendar_id, body=body).execute()

    def update_event_for_task(self, event_id: str, task, start_dt: datetime, duration_minutes: int) -> Dict[str, Any]:
        self._maybe_build_service(strict=True)
        end_dt = _ensure_tz(start_dt) + timedelta(minutes=duration_minutes)
        body = {
            "summary": getattr(task, "title", "Задача"),
            "description": self._with_marker(task, getattr(task, "notes", None)),
            "start": {"dateTime": _to_rfc3339(start_dt)},
            "end": {"dateTime": _to_rfc3339(end_dt)},
        }
        return self.service.events().patch(
            calendarId=self.calendar_id, eventId=event_id, body=body
        ).execute()

    def delete_event_by_id(self, event_id: str) -> None:
        self._maybe_build_service(strict=True)
        try:
            self.service.events().delete(calendarId=self.calendar_id, eventId=event_id).execute()
        except HttpError as e:
            if getattr(e, "resp", None) and getattr(e.resp, "status", None) == 404:
                return
            raise
