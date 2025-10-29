# planner/services/google_auth.py
from pathlib import Path
from typing import Optional
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request

from core.settings import CLIENT_SECRET_PATH, TOKEN_PATH, GOOGLE_SYNC

SCOPES = list(GOOGLE_SYNC.scopes)


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
        if self.creds and self.creds.valid:
            return True

        if self.token_path.exists():
            self.creds = Credentials.from_authorized_user_file(
                str(self.token_path), SCOPES
            )

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
                self.creds = flow.run_local_server(port=0)

            # Сохраняем полученный токен
            self.token_path.parent.mkdir(parents=True, exist_ok=True)
            self.token_path.write_text(self.creds.to_json(), encoding="utf-8")

        return True
