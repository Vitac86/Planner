"""Тесты SQLite-репозитория нового десктопа.

Чистый Python + временная БД (tmp_path): без окна, без сети, без Google
API и без какого-либо доступа к старому Planner/app.db.
"""
import re
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from planner_desktop.domain.task import Task
from planner_desktop.storage.sqlite_task_repository import SQLiteTaskRepository


@pytest.fixture()
def db_path(tmp_path):
    return tmp_path / "app_desktop.db"


@pytest.fixture()
def repo(db_path):
    repository = SQLiteTaskRepository(db_path)
    yield repository
    repository.close()


def reopen(repository, db_path):
    """Эмулирует перезапуск приложения: новое соединение с тем же файлом."""
    repository.close()
    return SQLiteTaskRepository(db_path)


# ---- схема --------------------------------------------------------------------

def test_repository_creates_schema_in_temp_db(repo, db_path):
    assert db_path.exists()
    with sqlite3.connect(str(db_path)) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert "tasks" in tables


# ---- add/list + персистентность после переоткрытия ------------------------------

def test_add_and_list_persist_after_reopen(repo, db_path):
    first = repo.add(Task(title="Первая"))
    second = repo.add(Task(title="Вторая", notes="с заметкой", priority=2))
    assert first.id is not None and second.id is not None

    repo = reopen(repo, db_path)
    try:
        tasks = repo.list_all()
        assert [t.title for t in tasks] == ["Первая", "Вторая"]
        assert tasks[0].uid == first.uid
        assert tasks[1].notes == "с заметкой"
        assert tasks[1].priority == 2
    finally:
        repo.close()


def test_title_only_task_appears_in_undated(repo, db_path):
    repo.add(Task(title="Купить билеты"))

    repo = reopen(repo, db_path)
    try:
        undated = repo.list_undated()
        assert [t.title for t in undated] == ["Купить билеты"]
        assert undated[0].start is None
        assert undated[0].is_all_day is False
        assert repo.list_today() == []
    finally:
        repo.close()


def test_scheduled_task_appears_in_today_for_its_date(repo, db_path):
    start = datetime(2026, 7, 8, 10, 30)
    task = Task(
        title="Встреча",
        start=start,
        end=start + timedelta(minutes=45),
        duration_minutes=45,
    )
    repo.add(task)

    repo = reopen(repo, db_path)
    try:
        todays = repo.list_today(reference_date=date(2026, 7, 8))
        assert [t.title for t in todays] == ["Встреча"]
        assert todays[0].start == start
        assert todays[0].end == start + timedelta(minutes=45)
        assert todays[0].duration_minutes == 45
        assert repo.list_today(reference_date=date(2026, 7, 9)) == []
        assert repo.list_undated() == []
    finally:
        repo.close()


def test_all_day_task_round_trip(repo, db_path):
    # Семантика all-day: start — полночь дня, end — эксклюзивный (+1 день).
    task = Task(
        title="Отпуск",
        start=datetime(2026, 7, 10, 0, 0),
        end=datetime(2026, 7, 11, 0, 0),
        is_all_day=True,
    )
    repo.add(task)

    repo = reopen(repo, db_path)
    try:
        stored = repo.list_today(reference_date=date(2026, 7, 10))[0]
        assert stored.is_all_day is True
        assert stored.start == datetime(2026, 7, 10, 0, 0)
        assert stored.end == datetime(2026, 7, 11, 0, 0)
        assert stored.duration_minutes is None
    finally:
        repo.close()


# ---- complete -------------------------------------------------------------------

def test_complete_persists(repo, db_path):
    task = repo.add(Task(title="Сделать"))
    assert repo.complete(task.id) is True

    repo = reopen(repo, db_path)
    assert repo.get(task.id).completed is True

    assert repo.complete(task.id, completed=False) is True
    repo = reopen(repo, db_path)
    assert repo.get(task.id).completed is False
    repo.close()


def test_toggle_completed_by_uid_matches_fake_interface(repo, db_path):
    task = repo.add(Task(title="Сделать"))
    assert repo.toggle_completed(task.uid) is True
    assert repo.get(task.id).completed is True
    assert repo.toggle_completed(task.uid) is True
    assert repo.get(task.id).completed is False
    assert repo.toggle_completed("нет-такого-uid") is False


def test_complete_missing_task_returns_false(repo):
    assert repo.complete(12345) is False


# ---- delete = тумбстоун ----------------------------------------------------------

def test_delete_creates_tombstone_and_hides_from_active_lists(repo, db_path):
    kept = repo.add(Task(title="Остаётся"))
    doomed = repo.add(Task(title="Удаляется"))

    assert repo.delete(doomed.id) is True
    assert repo.delete(doomed.id) is False  # повторное удаление — no-op

    repo = reopen(repo, db_path)
    try:
        assert [t.title for t in repo.list_all()] == ["Остаётся"]
        assert [t.title for t in repo.list_undated()] == ["Остаётся"]
        assert repo.get(kept.id).is_deleted is False

        tombstone = repo.get(doomed.id)
        assert tombstone is not None
        assert tombstone.is_deleted is True
        assert tombstone.deleted_at is not None
    finally:
        repo.close()

    # Физически строка осталась — будущий sync сможет допушить delete.
    with sqlite3.connect(str(db_path)) as connection:
        count = connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
    assert count == 2


def test_deleted_task_cannot_be_completed_or_toggled(repo):
    task = repo.add(Task(title="Удаляется"))
    repo.delete(task.id)
    assert repo.complete(task.id) is False
    assert repo.toggle_completed(task.uid) is False


# ---- поля Calendar-синка ----------------------------------------------------------

def test_calendar_sync_fields_persist_round_trip(repo, db_path):
    start = datetime(2026, 7, 10, 9, 0)
    task = repo.add(Task(title="Событие", start=start,
                         end=start + timedelta(hours=1)))

    task.google_calendar_event_id = "evt_123"
    task.google_calendar_etag = '"etag-42"'
    task.google_calendar_recurring_event_id = "rec_777"
    task.google_calendar_original_start = datetime(
        2026, 7, 10, 9, 0, tzinfo=timezone.utc
    )
    repo.update(task)

    repo = reopen(repo, db_path)
    try:
        stored = repo.get(task.id)
        assert stored.google_calendar_event_id == "evt_123"
        assert stored.google_calendar_etag == '"etag-42"'
        assert stored.google_calendar_recurring_event_id == "rec_777"
        assert stored.google_calendar_original_start == datetime(
            2026, 7, 10, 9, 0, tzinfo=timezone.utc
        )
    finally:
        repo.close()


def test_update_persists_edits_and_bumps_updated_at(repo, db_path):
    task = repo.add(Task(title="Черновик"))
    before = repo.get(task.id).updated_at

    task.title = "Чистовик"
    task.notes = "поправлено"
    repo.update(task)

    repo = reopen(repo, db_path)
    try:
        stored = repo.get(task.id)
        assert stored.title == "Чистовик"
        assert stored.notes == "поправлено"
        assert stored.updated_at >= before
    finally:
        repo.close()


# ---- ViewModel: задачи переживают «перезапуск приложения» --------------------------

def test_today_viewmodel_persists_tasks_across_restart(db_path):
    from planner_desktop.viewmodels.today_viewmodel import TodayViewModel

    vm = TodayViewModel(SQLiteTaskRepository(db_path))
    assert vm.addTask("Купить хлеб", "", False, False, "", "", "") is True
    assert vm.addTask("Встреча", "", True, False, "2026-07-08", "10:30", "45") is True
    assert vm.addTask("Отпуск", "", True, True, "2026-07-10", "", "") is True
    undated_uid = vm.undatedTasks[0]["uid"]
    assert vm.toggleCompleted(undated_uid) is True
    vm.repository.close()

    vm = TodayViewModel(SQLiteTaskRepository(db_path))
    try:
        titles = sorted(t.title for t in vm.repository.list_all())
        assert titles == ["Встреча", "Купить хлеб", "Отпуск"]
        undated = vm.undatedTasks
        assert [row["title"] for row in undated] == ["Купить хлеб"]
        assert undated[0]["completed"] is True
        scheduled = vm.repository.list_today(reference_date=date(2026, 7, 8))
        assert [t.title for t in scheduled] == ["Встреча"]
        all_day = vm.repository.list_today(reference_date=date(2026, 7, 10))[0]
        assert all_day.is_all_day is True
    finally:
        vm.repository.close()


# ---- изоляция от старого приложения -------------------------------------------------

FORBIDDEN_IMPORT = re.compile(
    r"^\s*(?:from|import)\s+(?:models|services|storage|core|ui|main)\b",
    re.MULTILINE,
)


def test_storage_package_does_not_import_old_app_code():
    """Новый пакет хранения не тянет старые models/, storage/, core/ и т.п."""
    package_dir = (
        Path(__file__).resolve().parent.parent / "planner_desktop" / "storage"
    )
    for source_file in sorted(package_dir.glob("*.py")):
        source = source_file.read_text(encoding="utf-8")
        match = FORBIDDEN_IMPORT.search(source)
        assert match is None, f"{source_file.name}: запрещённый импорт {match.group()!r}"


def test_repository_touches_only_its_own_db_file(tmp_path):
    db_path = tmp_path / "isolated" / "app_desktop.db"
    repository = SQLiteTaskRepository(db_path)
    repository.add(Task(title="Локально"))
    repository.close()
    created = sorted(p.name for p in db_path.parent.iterdir())
    assert created == ["app_desktop.db"]
    assert repository.db_path == db_path
