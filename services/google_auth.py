# planner/services/google_auth.py
from pathlib import Path
from typing import Optional
import logging

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request

from core.settings import CLIENT_SECRET_PATH, TOKEN_PATH


LOGGER = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/tasks",
    "https://www.googleapis.com/auth/drive.appdata",
]


def _has_all_scopes(creds: Optional[Credentials]) -> bool:
    if not creds:
        return False
    current = set(creds.scopes or [])
    return all(scope in current for scope in SCOPES)


def _log_scopes(creds: Optional[Credentials]) -> None:
    if not logging.getLogger().handlers:
        logging.basicConfig(level=logging.INFO)
    scopes = sorted(creds.scopes) if creds and creds.scopes else []
    LOGGER.info("Active Google credentials scopes: %s", scopes)


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

    def ensure_credentials(self) -> bool:
        if self.creds and self.creds.valid and _has_all_scopes(self.creds):
            _log_scopes(self.creds)
            return True

        if self.token_path.exists():
            try:
                self.creds = Credentials.from_authorized_user_file(
                    str(self.token_path), SCOPES
                )
            except Exception:
                self.creds = None

        if self.creds and not _has_all_scopes(self.creds):
            try:
                self.token_path.unlink(missing_ok=True)
            except FileNotFoundError:
                pass
            self.creds = None

        if not self.creds or not self.creds.valid:
            if self.creds and self.creds.expired and self.creds.refresh_token:
                self.creds.refresh(Request())
            else:
                if not self.secrets_path.exists():
                    raise FileNotFoundError(
                        f"Не найден {self.secrets_path}. "
                        "Создайте OAuth-клиент (Desktop) в Google Cloud и скачайте JSON."
                    )
                flow = InstalledAppFlow.from_client_secrets_file(
                    str(self.secrets_path), SCOPES
                )
                # Откроет браузер и поднимет локальный сервер для callback
                self.creds = flow.run_local_server(
                    port=0, access_type="offline", prompt="consent"
                )

            # Сохраняем полученный токен
            self.token_path.parent.mkdir(parents=True, exist_ok=True)
            self.token_path.write_text(self.creds.to_json(), encoding="utf-8")

        if not _has_all_scopes(self.creds):
            try:
                self.token_path.unlink(missing_ok=True)
            except FileNotFoundError:
                pass
            self.creds = None
            return self.ensure_credentials()

        _log_scopes(self.creds)
        return True

    def get_credentials(self) -> Optional[Credentials]:
        if not self.creds or not self.creds.valid:
            try:
                if not self.ensure_credentials():
                    return None
            except Exception:
                return None
        return self.creds
