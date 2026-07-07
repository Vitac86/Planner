"""Тесты путей экспериментального хранилища нового десктопа.

Главное свойство: каталог PlannerDesktop/app_desktop.db всегда отделён
от профиля старого Flet-приложения Planner/app.db.
"""
from pathlib import Path

from planner_desktop.storage import paths

WIN_ENV = {"APPDATA": "C:/Users/test/AppData/Roaming"}
WIN_HOME = Path("C:/Users/test")


# ---- отделение от старого профиля -------------------------------------------

def test_windows_data_dir_is_separate_from_old_planner_profile():
    desktop_dir = paths.get_desktop_data_dir(
        platform="win32", env=WIN_ENV, home=WIN_HOME,
    )
    assert desktop_dir == Path(WIN_ENV["APPDATA"]) / "PlannerDesktop"
    assert desktop_dir != Path(WIN_ENV["APPDATA"]) / "Planner"


def test_db_path_differs_from_old_app_db():
    db_path = paths.get_desktop_db_path(
        platform="win32", env=WIN_ENV, home=WIN_HOME,
    )
    old_db = Path(WIN_ENV["APPDATA"]) / "Planner" / "app.db"
    assert db_path.name == "app_desktop.db"
    assert db_path.parent.name == "PlannerDesktop"
    assert db_path != old_db
    assert db_path.parent != old_db.parent


def test_runtime_default_never_points_into_old_profile(monkeypatch):
    monkeypatch.delenv(paths.DATA_DIR_ENV_VAR, raising=False)
    # Старый конфиг импортируется только для сравнения путей.
    from core import settings

    assert paths.get_desktop_data_dir() != settings.DATA_DIR
    assert paths.get_desktop_db_path() != settings.DB_PATH
    assert paths.get_desktop_db_path().name != settings.DB_PATH.name


# ---- платформенные ветки -----------------------------------------------------

def test_linux_data_dir_with_xdg():
    env = {"XDG_DATA_HOME": "/tmp/xdg"}
    result = paths.get_desktop_data_dir(
        platform="linux", env=env, home=Path("/home/test"),
    )
    assert result == Path("/tmp/xdg") / "PlannerDesktop"


def test_linux_data_dir_default_home():
    result = paths.get_desktop_data_dir(
        platform="linux", env={}, home=Path("/home/test"),
    )
    assert result == Path("/home/test/.local/share") / "PlannerDesktop"


def test_macos_data_dir_ignores_appdata():
    # В отличие от старого get_default_data_dir, ветка darwin не смотрит
    # на APPDATA, поэтому тест стабилен и на Windows.
    result = paths.get_desktop_data_dir(
        platform="darwin", env=dict(WIN_ENV), home=Path("/Users/test"),
    )
    assert result == Path("/Users/test/Library/Application Support") / "PlannerDesktop"


# ---- переопределение через окружение -----------------------------------------

def test_env_override_wins_over_platform_default(tmp_path):
    custom = tmp_path / "custom-desktop-data"
    env = {paths.DATA_DIR_ENV_VAR: str(custom), "APPDATA": WIN_ENV["APPDATA"]}
    assert paths.get_desktop_data_dir(platform="win32", env=env) == custom
    assert paths.get_desktop_db_path(platform="win32", env=env) == custom / "app_desktop.db"


def test_env_override_from_process_environment(monkeypatch, tmp_path):
    monkeypatch.setenv(paths.DATA_DIR_ENV_VAR, str(tmp_path))
    assert paths.get_desktop_data_dir() == tmp_path
    assert paths.get_desktop_db_path() == tmp_path / "app_desktop.db"


def test_blank_env_override_is_ignored():
    env = {paths.DATA_DIR_ENV_VAR: "   ", "APPDATA": WIN_ENV["APPDATA"]}
    result = paths.get_desktop_data_dir(platform="win32", env=env, home=WIN_HOME)
    assert result == Path(WIN_ENV["APPDATA"]) / "PlannerDesktop"


# ---- создание каталога только по явному запросу -------------------------------

def test_get_functions_do_not_create_directories(tmp_path):
    target = tmp_path / "nested" / "desktop"
    env = {paths.DATA_DIR_ENV_VAR: str(target)}
    assert paths.get_desktop_data_dir(env=env) == target
    assert paths.get_desktop_db_path(env=env) == target / "app_desktop.db"
    assert not target.exists()


def test_ensure_creates_directory_on_explicit_request(tmp_path):
    target = tmp_path / "nested" / "desktop"
    env = {paths.DATA_DIR_ENV_VAR: str(target)}
    created = paths.ensure_desktop_data_dir(env=env)
    assert created == target
    assert target.is_dir()
