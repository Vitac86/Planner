"""Пути экспериментального хранилища нового десктопа.

Правила:

- каталог данных — ``<user data dir>/PlannerDesktop`` — намеренно отделён
  от профиля старого Flet-приложения (``<user data dir>/Planner``);
- файл БД называется ``app_desktop.db``, чтобы даже по имени его нельзя
  было спутать со старым ``app.db``;
- это НЕ миграция: старый ``Planner/app.db`` отсюда не открывается;
- для разработки и тестов путь переопределяется переменной окружения
  ``PLANNER_DESKTOP_DATA_DIR`` (используется как есть, без суффиксов);
- функции ``get_*`` только вычисляют пути и ничего не создают на диске;
  создание каталога — отдельный явный шаг ``ensure_desktop_data_dir()``.

Модуль сознательно не импортирует ``core.settings`` старого приложения:
у того при импорте есть побочный эффект (mkdir старого профиля), а новый
пакет не должен зависеть от старого кода.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Mapping, Optional

DESKTOP_APP_DIR_NAME = "PlannerDesktop"
DESKTOP_DB_FILENAME = "app_desktop.db"
DATA_DIR_ENV_VAR = "PLANNER_DESKTOP_DATA_DIR"


def get_desktop_data_dir(
    *,
    platform: Optional[str] = None,
    env: Optional[Mapping[str, str]] = None,
    home: Optional[Path] = None,
) -> Path:
    """Каталог данных нового десктопа. Ничего не создаёт на диске.

    Приоритет: ``PLANNER_DESKTOP_DATA_DIR`` > платформенный каталог
    пользователя + ``PlannerDesktop``. Параметры ``platform``/``env``/``home``
    нужны тестам, по умолчанию берутся из текущего окружения.
    """
    environ = os.environ if env is None else env

    override = str(environ.get(DATA_DIR_ENV_VAR) or "").strip()
    if override:
        return Path(override).expanduser()

    platform_id = (platform or sys.platform).lower()
    home_dir = Path(home) if home is not None else Path.home()

    if platform_id.startswith("win"):
        base = Path(environ.get("APPDATA") or home_dir / "AppData" / "Roaming")
    elif platform_id == "darwin":
        base = home_dir / "Library" / "Application Support"
    else:
        base = Path(environ.get("XDG_DATA_HOME") or home_dir / ".local" / "share")

    return base.expanduser() / DESKTOP_APP_DIR_NAME


def get_desktop_db_path(
    *,
    platform: Optional[str] = None,
    env: Optional[Mapping[str, str]] = None,
    home: Optional[Path] = None,
) -> Path:
    """Путь к файлу БД нового десктопа. Ничего не создаёт на диске."""
    return (
        get_desktop_data_dir(platform=platform, env=env, home=home)
        / DESKTOP_DB_FILENAME
    )


def ensure_desktop_data_dir(
    *,
    platform: Optional[str] = None,
    env: Optional[Mapping[str, str]] = None,
    home: Optional[Path] = None,
) -> Path:
    """Явно создаёт каталог данных (единственное место с mkdir) и возвращает его."""
    data_dir = get_desktop_data_dir(platform=platform, env=env, home=home)
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir
