"""Google Drive appDataFolder helper for Planner synchronisation metadata."""

from __future__ import annotations

import json
from typing import Dict, Optional

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaInMemoryUpload

CONFIG_FILENAME = "planner_config.json"
INDEX_FILENAME = "gtasks_index.json"
DEFAULT_CONFIG = {"version": 1, "tasklist_id": None, "last_full_sync": None}
DEFAULT_INDEX = {"version": 1, "tasks": {}}


class AppDataStore:
    def __init__(self, auth):
        self.auth = auth
        self.service = None
        self._files: Dict[str, Dict[str, Optional[str]]] = {}

    # ------------------------------------------------------------------
    # Public API
    def ensure_files(self) -> None:
        self._ensure_service()
        files = self._list_files()
        for name, default in (
            (CONFIG_FILENAME, DEFAULT_CONFIG),
            (INDEX_FILENAME, DEFAULT_INDEX),
        ):
            if name not in files:
                self._create_file(name, default)
                files = self._list_files()  # refresh to obtain metadata + etag
        self._files = files

    def read_config(self) -> Dict:
        return self._read_json(CONFIG_FILENAME, DEFAULT_CONFIG)

    def write_config(self, payload: Dict) -> None:
        self._write_json(CONFIG_FILENAME, payload, DEFAULT_CONFIG)

    def read_index(self) -> Dict:
        return self._read_json(INDEX_FILENAME, DEFAULT_INDEX)

    def write_index(self, payload: Dict) -> None:
        self._write_json(INDEX_FILENAME, payload, DEFAULT_INDEX)

    # ------------------------------------------------------------------
    # Service helpers
    def _ensure_service(self) -> None:
        if self.service is not None:
            return
        creds = None
        if hasattr(self.auth, "get_credentials") and callable(self.auth.get_credentials):
            creds = self.auth.get_credentials()
        elif hasattr(self.auth, "ensure_credentials") and callable(self.auth.ensure_credentials):
            if self.auth.ensure_credentials():  # type: ignore[misc]
                creds = getattr(self.auth, "creds", None) or getattr(
                    self.auth, "credentials", None
                )
        else:
            creds = getattr(self.auth, "creds", None) or getattr(
                self.auth, "credentials", None
            )
        if creds is None:
            raise RuntimeError("Google credentials are not available for appData access")
        self.service = build("drive", "v3", credentials=creds, cache_discovery=False)

    def _list_files(self) -> Dict[str, Dict[str, Optional[str]]]:
        service = self.service
        if service is None:
            return {}
        files: Dict[str, Dict[str, Optional[str]]] = {}
        page_token: Optional[str] = None
        while True:
            response = (
                service.files()
                .list(
                    spaces="appDataFolder",
                    fields="nextPageToken, files(id, name, modifiedTime)",
                    pageToken=page_token,
                )
                .execute()
            )
            for entry in response.get("files", []):
                name = entry.get("name")
                if not name:
                    continue
                meta = self._fetch_metadata(entry.get("id"))
                if meta:
                    files[name] = meta
            page_token = response.get("nextPageToken")
            if not page_token:
                break
        return files

    def _fetch_metadata(self, file_id: Optional[str]) -> Optional[Dict[str, Optional[str]]]:
        if not file_id or self.service is None:
            return None
        request = self.service.files().get(
            fileId=file_id,
            fields="id, name, modifiedTime, size",
            supportsAllDrives=False,
        )
        metadata = request.execute()
        etag = None
        if hasattr(request, "resp") and request.resp is not None:
            etag = request.resp.get("ETag") or request.resp.get("etag")
        metadata["etag"] = etag
        return metadata

    def _create_file(self, name: str, payload: Dict) -> None:
        service = self.service
        if service is None:
            raise RuntimeError("Drive service is not initialised")
        media = MediaInMemoryUpload(
            json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            mimetype="application/json",
            resumable=False,
        )
        service.files().create(
            body={"name": name, "parents": ["appDataFolder"]},
            media_body=media,
            fields="id, name",
        ).execute()

    # ------------------------------------------------------------------
    # JSON helpers
    def _ensure_cache(self) -> None:
        if not self._files:
            self.ensure_files()

    def _read_json(self, name: str, default: Dict) -> Dict:
        self._ensure_cache()
        info = self._files.get(name)
        if not info:
            return default.copy()
        file_id = info.get("id")
        if not file_id:
            return default.copy()
        request = self.service.files().get_media(fileId=file_id)
        try:
            content = request.execute()
        except HttpError as exc:
            status = getattr(exc, "resp", None) and getattr(exc.resp, "status", None)
            if status == 404:
                self._files.pop(name, None)
                return default.copy()
            raise
        if isinstance(content, bytes):
            text = content.decode("utf-8")
        else:
            text = str(content)
        if not text.strip():
            return default.copy()
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return default.copy()
        metadata = self._fetch_metadata(file_id)
        if metadata:
            self._files[name] = metadata
        return data if isinstance(data, dict) else default.copy()

    def _write_json(self, name: str, payload: Dict, default: Dict) -> None:
        self._ensure_cache()
        info = self._files.get(name)
        if not info:
            self.ensure_files()
            info = self._files.get(name)
        if not info:
            raise RuntimeError(f"Failed to locate {name} in appDataFolder")
        file_id = info.get("id")
        if not file_id:
            raise RuntimeError(f"Invalid file id for {name}")

        data = json.dumps(payload or default, ensure_ascii=False).encode("utf-8")
        media = MediaInMemoryUpload(data, mimetype="application/json", resumable=False)
        etag = info.get("etag")

        for attempt in range(2):
            request = self.service.files().update(
                fileId=file_id,
                media_body=media,
                fields="id, modifiedTime",
            )
            if etag:
                request.headers["If-Match"] = etag
            try:
                request.execute()
                refreshed = self._fetch_metadata(file_id)
                if refreshed:
                    self._files[name] = refreshed
                return
            except HttpError as exc:
                status = getattr(exc, "resp", None) and getattr(exc.resp, "status", None)
                if status == 412 and attempt == 0:
                    refreshed = self._fetch_metadata(file_id)
                    if refreshed:
                        etag = refreshed.get("etag")
                        self._files[name] = refreshed
                        continue
                raise


__all__ = ["AppDataStore", "CONFIG_FILENAME", "INDEX_FILENAME"]
