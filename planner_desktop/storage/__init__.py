"""Экспериментальное локальное хранилище нового десктопа.

Полностью изолировано от старого Flet-приложения: собственный каталог
``<user data dir>/PlannerDesktop`` и собственный файл ``app_desktop.db``.
Это НЕ миграция старого ``Planner/app.db`` — старый профиль отсюда
никогда не читается и не пишется.
"""

from .paths import (
    DATA_DIR_ENV_VAR,
    DESKTOP_APP_DIR_NAME,
    DESKTOP_DB_FILENAME,
    ensure_desktop_data_dir,
    get_desktop_data_dir,
    get_desktop_db_path,
)
from .sqlite_task_repository import SQLiteTaskRepository

__all__ = [
    "DATA_DIR_ENV_VAR",
    "DESKTOP_APP_DIR_NAME",
    "DESKTOP_DB_FILENAME",
    "ensure_desktop_data_dir",
    "get_desktop_data_dir",
    "get_desktop_db_path",
    "SQLiteTaskRepository",
]
