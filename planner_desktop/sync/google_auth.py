"""Изолированная авторизация Google нового десктопа.

Жёсткие правила:

- токен живёт ТОЛЬКО в профиле нового десктопа:
  ``<PlannerDesktop data dir>/token.json`` (учитывает
  ``PLANNER_DESKTOP_DATA_DIR``); профиль старого приложения
  (``<Planner>/token.json``) не читается, не пишется и не копируется
  автоматически НИКОГДА;
- client_secret.json пользователь кладёт вручную в
  ``<PlannerDesktop data dir>/secrets/client_secret.json`` (можно тот же
  OAuth-клиент, что у старого приложения, — это просто идентификатор
  приложения, не аккаунт);
- первый вход выполняется ЯВНО кнопкой «Подключить Google Calendar»
  (или CLI) и предполагает ТЕСТОВЫЙ Google-аккаунт — не боевой;
- при импорте модуля не происходит ни OAuth, ни сети, ни обращения к
  Google-библиотекам: все google-импорты ленивые, внутри функций;
- автоматического/фонового входа и синка нет.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from planner_desktop.storage.paths import get_desktop_data_dir

# Только Calendar: Google Tasks новый десктоп не использует.
DESKTOP_GOOGLE_SCOPES = ("https://www.googleapis.com/auth/calendar",)

TOKEN_FILENAME = "token.json"
SECRETS_DIR_NAME = "secrets"
CLIENT_SECRET_FILENAME = "client_secret.json"

NOT_CONNECTED_ERROR = (
    "Google Calendar не подключён: нет token.json в профиле нового десктопа. "
    "Нажмите «Подключить Google Calendar» в настройках (используйте тестовый "
    "аккаунт) или выполните вход через CLI."
)
NO_CLIENT_SECRET_ERROR = (
    "Не найден client_secret.json. Положите OAuth-секрет приложения в "
    "{path} и повторите."
)


# ---- пути (только изолированный профиль) -------------------------------------------

def get_desktop_token_path() -> Path:
    """token.json нового десктопа. Старый <Planner>/token.json не участвует."""
    return get_desktop_data_dir() / TOKEN_FILENAME


def get_desktop_client_secret_path() -> Path:
    return get_desktop_data_dir() / SECRETS_DIR_NAME / CLIENT_SECRET_FILENAME


# ---- статус подключения (только файловая система, без сети) --------------------------

@dataclass
class ConnectionStatus:
    """Что видно на диске изолированного профиля. Токены наружу не отдаются."""

    has_client_secret: bool
    has_token: bool
    token_path: str
    client_secret_path: str

    @property
    def connected(self) -> bool:
        return self.has_token


def get_connection_status() -> ConnectionStatus:
    token_path = get_desktop_token_path()
    secret_path = get_desktop_client_secret_path()
    return ConnectionStatus(
        has_client_secret=secret_path.is_file(),
        has_token=token_path.is_file(),
        token_path=str(token_path),
        client_secret_path=str(secret_path),
    )


# ---- учётные данные (ленивые google-импорты, сеть только по явному вызову) -----------

def load_credentials() -> Any:
    """Credentials из изолированного token.json (с refresh-ом при истечении).

    Возвращает None, если токена нет. Refresh — единственная возможная
    сеть здесь, и только при явном вызове (кнопка синка / CLI).
    """
    token_path = get_desktop_token_path()
    if not token_path.is_file():
        return None

    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    credentials = Credentials.from_authorized_user_file(
        str(token_path), list(DESKTOP_GOOGLE_SCOPES)
    )
    if not credentials.valid and credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())
        _save_credentials(credentials)
    return credentials


def connect_interactive() -> Any:
    """Явный первый вход: браузерный OAuth-флоу локального приложения.

    Вызывается ТОЛЬКО по действию пользователя (кнопка/CLI). Токен
    сохраняется в изолированный профиль. Рекомендуется тестовый аккаунт.
    """
    secret_path = get_desktop_client_secret_path()
    if not secret_path.is_file():
        raise FileNotFoundError(NO_CLIENT_SECRET_ERROR.format(path=secret_path))

    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_secrets_file(
        str(secret_path), list(DESKTOP_GOOGLE_SCOPES)
    )
    credentials = flow.run_local_server(port=0)
    _save_credentials(credentials)
    return credentials


def _save_credentials(credentials: Any) -> None:
    token_path = get_desktop_token_path()
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(credentials.to_json(), encoding="utf-8")


# ---- сборка реального шлюза -----------------------------------------------------------

def build_calendar_service(credentials: Any) -> Any:
    """Сервис Calendar API v3 поверх готовых credentials."""
    from googleapiclient.discovery import build

    return build("calendar", "v3", credentials=credentials, cache_discovery=False)


def build_real_gateway() -> Any:
    """Готовый GoogleCalendarGateway для ManualSyncService/CLI.

    Поднимает RuntimeError с человекочитаемым текстом, если десктоп ещё
    не подключён (нет токена) — это сообщение показывается в UI как есть.
    """
    credentials = load_credentials()
    if credentials is None:
        raise RuntimeError(NOT_CONNECTED_ERROR)

    from planner_desktop.sync.google_calendar_gateway import GoogleCalendarGateway

    return GoogleCalendarGateway(build_calendar_service(credentials))


__all__ = [
    "DESKTOP_GOOGLE_SCOPES",
    "ConnectionStatus",
    "NOT_CONNECTED_ERROR",
    "build_calendar_service",
    "build_real_gateway",
    "connect_interactive",
    "get_connection_status",
    "get_desktop_client_secret_path",
    "get_desktop_token_path",
    "load_credentials",
]
