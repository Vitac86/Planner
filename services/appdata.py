"""Helpers for working with Google Drive ``appDataFolder`` storage."""
from __future__ import annotations

import io
import json
import time
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload

from core.settings import GOOGLE_SYNC

try:  # pragma: no cover - optional dependency in tests
    from google.oauth2.credentials import Credentials
except Exception:  # pragma: no cover
    Credentials = None


_RETRYABLE_STATUS = {429, 500, 502, 503, 504}
_MAX_RETRIES = 5
_INITIAL_BACKOFF = 1.0
_MAX_BACKOFF = 32.0


class AppDataClient:
    """Wrapper around the Google Drive ``appDataFolder`` storage."""

    CONFIG_NAME = "planner_config.json"
    INDEX_NAME = "gtasks_index.json"

    def __init__(self, auth: Any):
        self.auth = auth
        self.service = None
        self._file_ids: Dict[str, str] = {}

    # ----- public helpers -----
    def ensure_files(self) -> Dict[str, str]:
        self._maybe_build_service(strict=True)
        existing = self._list_files()
        for name, default_payload in (
            (self.CONFIG_NAME, self._default_config()),
            (self.INDEX_NAME, self._default_index()),
        ):
            if name in existing:
                continue
            file_id = self._create_file(name, default_payload)
            existing[name] = file_id
        self._file_ids = existing
        return dict(existing)

    def read_config(self) -> tuple[Dict[str, Any], Optional[str]]:
        file_id = self._resolve_file_id(self.CONFIG_NAME)
        payload, etag = self._download_json(file_id)
        if not payload:
            payload = self._default_config()
        return payload, etag

    def write_config(
        self,
        data: Dict[str, Any],
        if_match: Optional[str] = None,
        *,
        on_conflict: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
    ) -> tuple[Dict[str, Any], str]:
        file_id = self._resolve_file_id(self.CONFIG_NAME)
        payload = deepcopy(data)
        etag = if_match
        attempt = 0
        last_error: Optional[Exception] = None
        while attempt < _MAX_RETRIES:
            try:
                new_etag = self._upload_json(file_id, payload, etag)
                return payload, new_etag
            except HttpError as exc:
                last_error = exc
                status = getattr(getattr(exc, "resp", None), "status", None)
                if status == 412 and on_conflict:
                    remote, etag = self.read_config()
                    payload = on_conflict(remote)
                    attempt += 1
                    continue
                raise
        if last_error:
            raise last_error
        raise RuntimeError("Failed to write config to appData")

    def read_index(self) -> tuple[Dict[str, Any], Optional[str]]:
        file_id = self._resolve_file_id(self.INDEX_NAME)
        payload, etag = self._download_json(file_id)
        if not payload:
            payload = self._default_index()
        payload.setdefault("version", 1)
        payload.setdefault("tasks", {})
        return payload, etag

    def write_index(
        self,
        data: Dict[str, Any],
        if_match: Optional[str] = None,
        *,
        on_conflict: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
    ) -> tuple[Dict[str, Any], str]:
        file_id = self._resolve_file_id(self.INDEX_NAME)
        payload = deepcopy(data)
        etag = if_match
        attempt = 0
        last_error: Optional[Exception] = None
        while attempt < _MAX_RETRIES:
            try:
                new_etag = self._upload_json(file_id, payload, etag)
                return payload, new_etag
            except HttpError as exc:
                last_error = exc
                status = getattr(getattr(exc, "resp", None), "status", None)
                if status == 412 and on_conflict:
                    remote, etag = self.read_index()
                    payload = on_conflict(remote)
                    attempt += 1
                    continue
                raise
        if last_error:
            raise last_error
        raise RuntimeError("Failed to write index to appData")

    # ----- internal helpers -----
    def _maybe_build_service(self, strict: bool = False) -> None:
        if self.service is not None:
            return
        creds = self._find_creds(DEFAULT_SCOPES())
        if creds and getattr(creds, "valid", False):
            self.service = build("drive", "v3", credentials=creds, cache_discovery=False)
        elif strict:
            raise RuntimeError("AppDataClient: credentials are unavailable")

    def _find_creds(self, scopes: tuple[str, ...]):
        if hasattr(self.auth, "get_credentials"):
            try:
                creds = self.auth.get_credentials()
                if creds and getattr(creds, "valid", False):
                    return creds
            except Exception:  # pragma: no cover - defensive
                pass
        if hasattr(self.auth, "creds"):
            creds = getattr(self.auth, "creds")
            if creds and getattr(creds, "valid", False):
                return creds
        if hasattr(self.auth, "token_path") and Credentials:
            token_path = getattr(self.auth, "token_path")
            if token_path and Path(token_path).exists():
                try:
                    return Credentials.from_authorized_user_file(str(token_path), scopes)
                except Exception:  # pragma: no cover - defensive fallback
                    return None
        return None

    def _list_files(self) -> Dict[str, str]:
        results: Dict[str, str] = {}
        page_token: Optional[str] = None
        while True:
            response = self._call_with_backoff(
                self.service.files().list,
                spaces="appDataFolder",
                fields="nextPageToken, files(id, name)",
                pageToken=page_token,
            )
            for item in response.get("files", []):
                name = item.get("name")
                file_id = item.get("id")
                if name and file_id:
                    results[name] = file_id
            page_token = response.get("nextPageToken")
            if not page_token:
                break
        return results

    def _create_file(self, name: str, payload: Dict[str, Any]) -> str:
        body = {"name": name, "parents": ["appDataFolder"]}
        media = MediaIoBaseUpload(
            io.BytesIO(self._encode_json(payload)),
            mimetype="application/json",
            resumable=False,
        )
        response = self._call_with_backoff(
            self.service.files().create,
            body=body,
            media_body=media,
            fields="id",
        )
        return response.get("id")

    def _resolve_file_id(self, name: str) -> str:
        if name not in self._file_ids:
            self.ensure_files()
        file_id = self._file_ids.get(name)
        if not file_id:
            raise RuntimeError(f"AppData file {name!r} is unavailable")
        return file_id

    def _download_json(self, file_id: str) -> tuple[Dict[str, Any], Optional[str]]:
        request = self.service.files().get_media(fileId=file_id)
        buffer = io.BytesIO()
        downloader = MediaIoBaseDownload(buffer, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        raw = buffer.getvalue()
        etag = None
        resp = getattr(request, "resp", None)
        if resp:
            etag = resp.get("etag") or resp.get("ETag")
        if not raw:
            return {}, etag
        try:
            decoded = raw.decode("utf-8")
            data = json.loads(decoded)
            if isinstance(data, dict):
                return data, etag
        except (UnicodeDecodeError, json.JSONDecodeError):
            pass
        return {}, etag

    def _upload_json(self, file_id: str, payload: Dict[str, Any], etag: Optional[str]) -> str:
        media = MediaIoBaseUpload(
            io.BytesIO(self._encode_json(payload)),
            mimetype="application/json",
            resumable=False,
        )
        request = self.service.files().update(fileId=file_id, media_body=media)
        if etag:
            request.headers["If-Match"] = etag
        response = self._call_with_backoff(lambda **_: request, execute_immediately=False)
        new_etag = None
        resp = getattr(request, "resp", None)
        if resp:
            new_etag = resp.get("etag") or resp.get("ETag")
        if not new_etag:
            metadata = self._call_with_backoff(
                self.service.files().get,
                fileId=file_id,
                fields="id, modifiedTime, version",
            )
            new_etag = metadata.get("version") or metadata.get("modifiedTime")
        return new_etag or ""

    def _call_with_backoff(
        self,
        method,
        *args,
        execute_immediately: bool = True,
        **kwargs,
    ) -> Dict[str, Any]:
        delay = _INITIAL_BACKOFF
        last_error: Optional[Exception] = None
        for attempt in range(_MAX_RETRIES):
            try:
                request = method(*args, **kwargs)
                if not execute_immediately:
                    request.execute()
                    return {}
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

    @staticmethod
    def _encode_json(payload: Dict[str, Any]) -> bytes:
        text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
        return text.encode("utf-8")

    @staticmethod
    def _default_config() -> Dict[str, Any]:
        return {"version": 1, "tasklist_id": None, "last_full_sync": None}

    @staticmethod
    def _default_index() -> Dict[str, Any]:
        return {"version": 1, "tasklist_id": None, "tasks": {}}


def DEFAULT_SCOPES() -> tuple[str, ...]:
    return GOOGLE_SYNC.scopes


__all__ = ["AppDataClient"]

