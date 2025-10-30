# planner/services/google_auth.py
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Iterable, Optional

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

from core.settings import CLIENT_SECRET_PATH, TOKEN_PATH


SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/drive.appdata",
    # "https://www.googleapis.com/auth/drive.file",
]


class GoogleAuth:
    def __init__(
        self,
        secrets_path: str | Path = CLIENT_SECRET_PATH,
        token_path: str | Path = TOKEN_PATH,
    ):
        self.secrets_path = Path(secrets_path)
        self.token_path = Path(token_path)
        self.secrets_path.parent.mkdir(parents=True, exist_ok=True)
        self.token_path.parent.mkdir(parents=True, exist_ok=True)
        self.creds: Optional[Credentials] = None
        self._log(f"Token path: {self.token_path}")

    def ensure_credentials(self) -> bool:
        if self.creds and self.creds.valid and self._has_required_scopes(self.creds):
            self._log_active_scopes(self.creds.scopes)
            return True

        if self.token_path.exists():
            try:
                self.creds = Credentials.from_authorized_user_file(
                    str(self.token_path), SCOPES
                )
            except (ValueError, json.JSONDecodeError) as exc:
                self._log(f"Failed to load token.json: {exc}; triggering reauth")
                self.reset_credentials()
                self.creds = None

        if self.creds and not self._has_required_scopes(self.creds):
            self._log(
                "Token is missing required scopes; removing token and requesting consent"
            )
            self.reset_credentials()
            self.creds = None

        if not self.creds or not self.creds.valid:
            if self.creds and self.creds.expired and self.creds.refresh_token:
                try:
                    self.creds.refresh(Request())
                except RefreshError as exc:
                    self._log(f"Token refresh failed: {exc}; forcing reauth")
                    self.reset_credentials()
                    self.creds = None
            if not self.creds:
                if not self.secrets_path.exists():
                    raise FileNotFoundError(
                        f"Не найден {self.secrets_path}. "
                        "Создайте OAuth-клиент (Desktop) в Google Cloud и скачайте JSON."
                    )
                flow = InstalledAppFlow.from_client_secrets_file(
                    str(self.secrets_path), SCOPES
                )
                self._log("Running OAuth consent flow (local server)")
                self.creds = flow.run_local_server(
                    port=0,
                    access_type="offline",
                    prompt="consent",
                    include_granted_scopes=True,
                )

        if not self.creds:
            raise RuntimeError("Не удалось получить учетные данные Google")

        if not self._has_required_scopes(self.creds):
            raise RuntimeError("Авторизация без обязательных прав доступа Google")

        self._persist_credentials(self.creds)
        self._log_active_scopes(self.creds.scopes)
        return True

    def get_credentials(self) -> Optional[Credentials]:
        return self.creds

    def get_active_scopes(self) -> list[str]:
        if not self.creds:
            return []
        return sorted(set(self.creds.scopes or []))

    def reset_credentials(self) -> None:
        self.creds = None
        try:
            if self.token_path.exists():
                self.token_path.unlink()
                self._log("Removed cached Google token")
        except OSError as exc:
            self._log(f"Failed to remove cached token: {exc}")

    # ----- helpers -----
    def _log(self, message: str) -> None:
        print(f"[GoogleAuth] {message}")

    def _persist_credentials(self, creds: Credentials) -> None:
        data = creds.to_json()
        tmp_path = self.token_path.with_suffix(".tmp")
        try:
            tmp_path.write_text(data, encoding="utf-8")
            os.replace(tmp_path, self.token_path)
        finally:
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass

    @staticmethod
    def _has_required_scopes(creds: Credentials) -> bool:
        current = set(creds.scopes or [])
        return all(scope in current for scope in SCOPES)

    def _log_active_scopes(self, scopes: Iterable[str] | None) -> None:
        scopes_list = sorted(set(scopes or []))
        self._log(f"Active scopes: {', '.join(scopes_list) if scopes_list else '—'}")
