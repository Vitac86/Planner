"""Centralized application configuration."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional
import os
import sys


def get_default_data_dir(
    app_name: str,
    *,
    platform: Optional[str] = None,
    env: Optional[Mapping[str, str]] = None,
    home: Optional[Path] = None,
) -> Path:
    """Return an OS-specific user data directory for ``app_name``."""

    platform_id = (platform or sys.platform).lower()
    environ = dict(env or os.environ)
    home_dir = Path(home or Path.home())
    sanitized = app_name.strip() or "app"
    sanitized = sanitized.replace("/", "-").replace("\\", "-")

    if platform_id.startswith("win"):
        base = Path(environ.get("APPDATA") or home_dir / "AppData" / "Roaming")
    elif platform_id == "darwin":
        base = Path(environ.get("APPDATA") or home_dir / "Library" / "Application Support")
    else:
        base = Path(environ.get("XDG_DATA_HOME") or home_dir / ".local" / "share")

    return (base.expanduser() / sanitized)


APP_NAME = "Planner"


DATA_DIR = get_default_data_dir(APP_NAME)
STORAGE_DIR = DATA_DIR / "storage"
SECRETS_DIR = DATA_DIR / "secrets"
BACKUP_DIR = DATA_DIR / "backups"

for _dir in (DATA_DIR, STORAGE_DIR, SECRETS_DIR, BACKUP_DIR):
    _dir.mkdir(parents=True, exist_ok=True)


DB_PATH = DATA_DIR / "app.db"
TOKEN_PATH = DATA_DIR / "token.json"
CLIENT_SECRET_PATH = SECRETS_DIR / "client_secret.json"
SYNC_TOKEN_PATH = STORAGE_DIR / "gcal_sync_token.json"


@dataclass(frozen=True)
class ThemeColors:
    safe_surface_bg: str = "#F1F5F9"
    outline: str = "#E5E7EB"
    surface_variant: str = "#F1F5F9"
    text_subtle: str = "#6B7280"
    today_bg: str = "#EEF2FF"
    now_line: str = "#EF4444"
    chip: str = "#E0E7FF"
    chip_text: str = "#1F2937"
    unscheduled_bg: str = "#FFF59D"
    backdrop: str = "#000000"


@dataclass(frozen=True)
class CalendarUISettings:
    day_start: int = 0
    day_end: int = 23
    row_min_height: int = 36
    day_column_width: int = 160
    hours_column_width: int = 76
    side_panel_width: int = 240
    header_height: int = 54
    chip_estimated_height: int = 26
    cell_vertical_padding: int = 8
    chips_spacing: int = 4
    import_new_from_google: bool = True
    dialog_width_narrow: int = 460
    dialog_width_wide: int = 680


@dataclass(frozen=True)
class TodayUISettings:
    list_section_height: int = 240
    default_duration_minutes: int = 30
    add_to_calendar_by_default: bool = True


@dataclass(frozen=True)
class AutoRefreshSettings:
    enabled: bool = True
    interval_sec: int = 60


@dataclass(frozen=True)
class UISettings:
    app_title: str = APP_NAME
    theme_mode: str = "system"
    color_scheme_seed: str = "#4F46E5"
    dark_mode_default: bool = False
    window_min_width: int = 900
    window_min_height: int = 600
    theme: ThemeColors = ThemeColors()
    calendar: CalendarUISettings = CalendarUISettings()
    today: TodayUISettings = TodayUISettings()
    auto_refresh: AutoRefreshSettings = AutoRefreshSettings()


UI = UISettings()


@dataclass(frozen=True)
class GoogleSyncSettings:
    enabled: bool = True
    auto_pull_interval_sec: int = 60
    auto_push_on_edit: bool = True
    scopes: tuple[str, ...] = (
        "https://www.googleapis.com/auth/calendar",
        "https://www.googleapis.com/auth/calendar.events",
        "https://www.googleapis.com/auth/tasks",
        "https://www.googleapis.com/auth/drive.appdata",
    )
    sync_token_path: Path = SYNC_TOKEN_PATH
    delete_on_google_cancel: bool = False
    tasks_tasklist_name: str = "Planner Inbox"
    tasks_pull_interval_sec: int = 90
    tasks_push_interval_sec: int = 90
    tasks_meta_filename: str = "planner-meta.json"


GOOGLE_SYNC = GoogleSyncSettings()


@dataclass(frozen=True)
class BackupSettings:
    enabled: bool = True
    directory: Path = BACKUP_DIR
    keep_days: int = 7


BACKUP = BackupSettings()


__all__ = [
    "APP_NAME",
    "DATA_DIR",
    "STORAGE_DIR",
    "SECRETS_DIR",
    "BACKUP_DIR",
    "DB_PATH",
    "TOKEN_PATH",
    "CLIENT_SECRET_PATH",
    "SYNC_TOKEN_PATH",
    "UI",
    "GOOGLE_SYNC",
    "BACKUP",
    "get_default_data_dir",
]

