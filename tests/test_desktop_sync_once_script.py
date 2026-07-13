"""Тесты CLI scripts/desktop_calendar_sync_once: отказ без --real-google,
вывод сводки, изоляция профиля, чистота импорта (ни OAuth, ни Google API,
ни QML, ни старого Flet UI).
"""
import subprocess
import sys
from pathlib import Path

import pytest

from planner_desktop.storage.paths import DATA_DIR_ENV_VAR
from planner_desktop.usecases.manual_sync_service import ManualSyncResult
from scripts import desktop_calendar_sync_once as cli

REPO_ROOT = Path(__file__).resolve().parent.parent


class FakeService:
    def __init__(self, result):
        self._result = result
        self.calls = 0

    def run_once(self):
        self.calls += 1
        return self._result


# ---- отказ без явного флага -----------------------------------------------------------

def test_refuses_without_real_google_flag(capsys):
    code = cli.main([])
    assert code == cli.REFUSED_EXIT_CODE == 2
    out = capsys.readouterr().out
    assert "--real-google" in out
    assert "Ничего не сделано" in out


def test_refusal_does_not_build_service(capsys):
    """Без флага сервис (и значит БД/шлюз) даже не создаётся."""
    def exploding_factory():
        raise AssertionError("не должен вызываться")

    code = cli.main([], service_factory=exploding_factory)
    assert code == 2


# ---- запуск с фейковым сервисом ---------------------------------------------------------

def test_success_prints_structured_summary(capsys):
    service = FakeService(ManualSyncResult(
        ok=True, pushed=2, pulled=3, pending_before=2, pending_after=0,
        terminal_ops=0, cursor_updated=True,
    ))
    code = cli.main(["--real-google"], service_factory=lambda: service)
    out = capsys.readouterr().out

    assert code == 0
    assert service.calls == 1
    assert "OK" in out
    assert "отправлено (push): 2" in out
    assert "получено (pull):   3" in out
    assert "2 -> 0" in out
    assert "курсор обновлён:   да" in out


def test_failure_prints_error_and_exit_code_1(capsys):
    service = FakeService(ManualSyncResult(
        ok=False, error="Google Calendar не подключён: нет token.json"))
    code = cli.main(["--real-google"], service_factory=lambda: service)
    out = capsys.readouterr().out

    assert code == 1
    assert "ОШИБКА" in out
    assert "не подключён" in out


def test_data_dir_flag_sets_isolated_profile(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv(DATA_DIR_ENV_VAR, raising=False)
    service = FakeService(ManualSyncResult(ok=True))

    import os
    cli.main(["--real-google", "--data-dir", str(tmp_path)],
             service_factory=lambda: service)
    assert os.environ[DATA_DIR_ENV_VAR] == str(tmp_path)
    monkeypatch.delenv(DATA_DIR_ENV_VAR, raising=False)


# ---- чистота импорта ---------------------------------------------------------------------

def test_import_has_no_google_qt_or_flet_side_effects():
    """Импорт CLI не тянет ни Google-клиенты (OAuth/сеть), ни PySide6/QML,
    ни старое Flet-приложение. Проверяется в чистом интерпретаторе."""
    probe = (
        "import sys; import scripts.desktop_calendar_sync_once; "
        "bad = [m for m in sys.modules "
        " if m.startswith('google') or m.startswith('PySide6') "
        " or m.startswith('flet') or m == 'main' or m.startswith('ui.')]; "
        "sys.exit(1 if bad else 0)"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_gateway_and_auth_import_is_pure_too():
    probe = (
        "import sys; "
        "import planner_desktop.sync.google_calendar_gateway; "
        "import planner_desktop.sync.google_auth; "
        "import planner_desktop.usecases.manual_sync_service; "
        "bad = [m for m in sys.modules "
        " if m.startswith('google') or m.startswith('PySide6') "
        " or m.startswith('flet')]; "
        "sys.exit(1 if bad else 0)"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr


# ---- старые файлы не участвуют --------------------------------------------------------------

def test_default_service_uses_isolated_db_not_old_app_db(monkeypatch, tmp_path):
    """Дефолтная сборка сервиса работает с app_desktop.db в изолированном
    профиле; файла старого app.db не появляется и он не требуется.
    Соединения per-run: после run_once открытых файлов БД не остаётся."""
    monkeypatch.setenv(DATA_DIR_ENV_VAR, str(tmp_path))
    service = cli._build_default_service()

    # шлюз ленивый: без token.json запуск честно вернёт ошибку, сети нет
    result = service.run_once()
    assert result.ok is False
    assert "не подключён" in result.error

    db_files = {p.name for p in tmp_path.iterdir()}
    assert "app_desktop.db" in db_files
    assert "app.db" not in db_files
