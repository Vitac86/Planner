from datetime import datetime, timedelta
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import settings
from storage import backup as backup_module
from storage.backup import ensure_daily_backup


def test_linux_data_dir_with_xdg():
    env = {"XDG_DATA_HOME": "/tmp/xdg"}
    result = settings.get_default_data_dir(
        settings.APP_NAME,
        platform="linux",
        env=env,
        home=Path("/home/test"),
    )
    assert result == Path("/tmp/xdg") / settings.APP_NAME


def test_linux_data_dir_default_home():
    result = settings.get_default_data_dir(
        settings.APP_NAME,
        platform="linux",
        env={},
        home=Path("/home/test"),
    )
    assert result == Path("/home/test/.local/share") / settings.APP_NAME


def test_macos_data_dir():
    result = settings.get_default_data_dir(
        settings.APP_NAME,
        platform="darwin",
        env={},
        home=Path("/Users/test"),
    )
    expected = Path("/Users/test/Library/Application Support") / settings.APP_NAME
    assert result == expected


def test_windows_data_dir_appdata():
    env = {"APPDATA": "C:/Users/test/AppData/Roaming"}
    result = settings.get_default_data_dir(
        settings.APP_NAME,
        platform="win32",
        env=env,
        home=Path("C:/Users/test"),
    )
    expected = Path(env["APPDATA"]) / settings.APP_NAME
    assert result == expected


def test_runtime_paths_inside_data_dir():
    assert settings.DB_PATH.parent == settings.DATA_DIR
    assert settings.TOKEN_PATH.parent == settings.DATA_DIR
    assert settings.CLIENT_SECRET_PATH.parent == settings.SECRETS_DIR
    assert settings.SYNC_TOKEN_PATH.parent == settings.STORAGE_DIR


def test_backup_rotation(monkeypatch, tmp_path):
    db_path = tmp_path / "app.db"
    db_path.write_text("seed", encoding="utf-8")
    backup_dir = tmp_path / "backups"

    base = datetime(2024, 1, 1)

    for offset in range(5):
        db_path.write_text(f"content-{offset}", encoding="utf-8")

        class FakeDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return base + timedelta(days=offset)

        monkeypatch.setattr(backup_module, "datetime", FakeDateTime)
        ensure_daily_backup(db_path, backup_dir, keep_days=3)

    monkeypatch.setattr(backup_module, "datetime", datetime)

    backups = sorted(p.name for p in backup_dir.iterdir())
    assert backups == [
        "app_2024-01-03.db",
        "app_2024-01-04.db",
        "app_2024-01-05.db",
    ]
