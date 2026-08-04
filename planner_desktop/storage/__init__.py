"""Локальное хранилище десктопа.

Собственный каталог ``<user data dir>/PlannerDesktop`` и собственный файл
``app_desktop.db``. Профиль удалённого legacy-приложения
(``<user data dir>/Planner/app.db``) отсюда никогда не читается и не
пишется — миграции из него нет.
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
