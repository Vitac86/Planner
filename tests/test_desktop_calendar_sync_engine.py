"""Тесты двустороннего движка Calendar-синка на фейковом шлюзе.

Сеть, OAuth и Google API не используются вовсе: «календарь» — это
FakeCalendarGateway в памяти, «телефон» — прямые вызовы его методов.
БД — только временная (tmp_path); старый Planner/app.db не открывается.
"""
import re
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from planner_desktop.domain.task import Task, utc_now
from planner_desktop.storage import calendar_sync_store as store_module
from planner_desktop.storage.calendar_sync_store import CalendarSyncStore
from planner_desktop.storage.sqlite_task_repository import SQLiteTaskRepository
from planner_desktop.sync.calendar_sync_engine import CalendarSyncEngine
from planner_desktop.sync.fake_calendar_gateway import FakeCalendarGateway
from planner_desktop.sync.sync_types import (
    CalendarEvent,
    RetryableGatewayError,
    TerminalGatewayError,
)


class FakeClock:
    def __init__(self):
        self.now = utc_now()

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += timedelta(seconds=seconds)


@pytest.fixture()
def db_path(tmp_path):
    return tmp_path / "app_desktop.db"


@pytest.fixture()
def repo(db_path):
    repository = SQLiteTaskRepository(db_path)
    yield repository
    repository.close()


@pytest.fixture()
def clock():
    return FakeClock()


@pytest.fixture()
def store(db_path, clock):
    sync_store = CalendarSyncStore(db_path, clock=clock)
    yield sync_store
    sync_store.close()


@pytest.fixture()
def gateway():
    # база чуть в будущем: правки «с телефона» всегда новее локальных задач
    return FakeCalendarGateway(base_time=utc_now() + timedelta(minutes=5))


@pytest.fixture()
def engine(repo, store, gateway):
    return CalendarSyncEngine(repo, store, gateway)


def add_timed_task(repo, title="Встреча", **kwargs):
    defaults = dict(
        start=datetime(2026, 7, 8, 10, 30),
        end=datetime(2026, 7, 8, 11, 15),
        duration_minutes=45,
    )
    defaults.update(kwargs)
    return repo.add(Task(title=title, **defaults))


def phone_creates_timed_event(gateway, summary="С телефона"):
    return gateway.insert_event(CalendarEvent(
        summary=summary,
        start=datetime(2026, 7, 9, 14, 0),
        end=datetime(2026, 7, 9, 15, 0),
    ))


# ---- push: локальное -> календарь --------------------------------------------

def test_local_create_pushes_event_and_stores_id_and_etag(engine, repo, store, gateway):
    task = add_timed_task(repo)
    engine.handle_local_task_created(task)
    assert store.has_pending_op(task.uid) is True

    engine.push_pending()

    stored = repo.get(task.id)
    assert stored.google_calendar_event_id is not None
    assert stored.google_calendar_etag == '"1"'
    remote = gateway.get_event(stored.google_calendar_event_id)
    assert remote.summary == "Встреча"
    assert remote.start == datetime(2026, 7, 8, 10, 30)
    assert store.has_pending_op(task.uid) is False  # очередь очищена


def test_local_all_day_create_pushes_date_date_event(engine, repo, gateway):
    task = repo.add(Task(
        title="Отпуск",
        start=datetime(2026, 7, 10, 0, 0),
        end=datetime(2026, 7, 11, 0, 0),
        is_all_day=True,
    ))
    engine.handle_local_task_created(task)
    engine.push_pending()

    remote = gateway.get_event(repo.get(task.id).google_calendar_event_id)
    assert remote.is_all_day is True
    assert remote.start == date(2026, 7, 10)
    assert remote.end == date(2026, 7, 11)  # эксклюзивный конец


def test_undated_task_never_enqueued(engine, repo, store):
    task = repo.add(Task(title="Без даты"))
    engine.handle_local_task_created(task)
    assert store.has_pending_op(task.uid) is False


def test_local_update_pushes_patch(engine, repo, store, gateway):
    task = add_timed_task(repo)
    engine.handle_local_task_created(task)
    engine.push_pending()
    task = repo.get(task.id)

    task.title = "Встреча (перенос)"
    task.start = datetime(2026, 7, 8, 12, 0)
    task.end = datetime(2026, 7, 8, 12, 45)
    repo.update(task)
    engine.handle_local_task_updated(task)
    assert store.has_pending_op(task.uid) is True

    engine.push_pending()

    remote = gateway.get_event(task.google_calendar_event_id)
    assert remote.summary == "Встреча (перенос)"
    assert remote.start == datetime(2026, 7, 8, 12, 0)
    assert remote.etag == '"2"'
    assert repo.get(task.id).google_calendar_etag == '"2"'


def test_local_tombstone_cancels_remote_event(engine, repo, store, gateway):
    task = add_timed_task(repo)
    engine.handle_local_task_created(task)
    engine.push_pending()
    task = repo.get(task.id)
    event_id = task.google_calendar_event_id

    repo.delete(task.id)
    engine.handle_local_task_deleted(repo.get(task.id))
    engine.push_pending()

    assert gateway.get_event(event_id).is_cancelled is True
    # локально задача осталась тумбстоуном, не стёрта
    assert repo.get(task.id).is_deleted is True


def test_delete_before_first_push_cancels_create(engine, repo, store, gateway):
    task = add_timed_task(repo)
    engine.handle_local_task_created(task)
    repo.delete(task.id)
    engine.handle_local_task_deleted(repo.get(task.id))

    engine.push_pending()
    assert gateway.events == []  # событие так и не создавалось
    assert store.list_due_ops() == []


# ---- pull: календарь (телефон) -> локальное -----------------------------------

def test_remote_new_timed_event_creates_local_task(engine, repo, gateway):
    created = phone_creates_timed_event(gateway, "Звонок маме")
    engine.pull_remote_changes()

    task = repo.get_by_google_event_id(created.id)
    assert task is not None
    assert task.title == "Звонок маме"
    assert task.is_all_day is False
    assert task.start == datetime(2026, 7, 9, 14, 0)
    assert task.duration_minutes == 60
    assert task.google_calendar_etag == created.etag


def test_remote_new_all_day_event_creates_all_day_task(engine, repo, gateway):
    created = gateway.insert_event(CalendarEvent(
        summary="Выходной",
        start=date(2026, 7, 20),
        end=date(2026, 7, 21),
        is_all_day=True,
    ))
    engine.pull_remote_changes()

    task = repo.get_by_google_event_id(created.id)
    assert task.is_all_day is True
    assert task.start == datetime(2026, 7, 20, 0, 0)
    assert task.end == datetime(2026, 7, 21, 0, 0)


def test_remote_update_applies_when_no_pending_op(engine, repo, gateway):
    created = phone_creates_timed_event(gateway)
    engine.pull_remote_changes()
    task = repo.get_by_google_event_id(created.id)

    gateway.patch_event(created.id, {"summary": "Переименовано на телефоне"})
    engine.pull_remote_changes()

    assert repo.get(task.id).title == "Переименовано на телефоне"


def test_remote_delete_tombstones_local_task(engine, repo, gateway):
    created = phone_creates_timed_event(gateway)
    engine.pull_remote_changes()
    task = repo.get_by_google_event_id(created.id)

    gateway.delete_event(created.id)
    engine.pull_remote_changes()

    stored = repo.get(task.id)
    assert stored.is_deleted is True  # тумбстоун, записи не стёрта


def test_cursor_advances_and_changes_not_reapplied(engine, repo, store, gateway):
    phone_creates_timed_event(gateway)
    engine.pull_remote_changes()
    assert store.get_sync_cursor() == "1"
    assert len(repo.list_all()) == 1

    engine.pull_remote_changes()  # без новых изменений
    assert len(repo.list_all()) == 1  # дубликат не создан


# ---- конфликтная политика --------------------------------------------------------

def test_pending_local_op_protects_task_from_remote_overwrite(engine, repo, store, gateway):
    created = phone_creates_timed_event(gateway)
    engine.pull_remote_changes()
    task = repo.get_by_google_event_id(created.id)

    # локальная правка ждёт push-а...
    task.title = "Локальная правка"
    repo.update(task)
    engine.handle_local_task_updated(task)

    # ...а с телефона приходит конкурирующая правка
    gateway.patch_event(created.id, {"summary": "Правка с телефона"})
    engine.pull_remote_changes()

    assert repo.get(task.id).title == "Локальная правка"  # remote не затёр


def test_pending_op_protects_from_remote_delete(engine, repo, store, gateway):
    created = phone_creates_timed_event(gateway)
    engine.pull_remote_changes()
    task = repo.get_by_google_event_id(created.id)

    task.title = "Локальная правка"
    repo.update(task)
    engine.handle_local_task_updated(task)

    gateway.delete_event(created.id)
    engine.pull_remote_changes()

    assert repo.get(task.id).is_deleted is False


def test_remote_newer_wins_without_pending_op(engine, repo, gateway):
    created = phone_creates_timed_event(gateway)
    engine.pull_remote_changes()
    task = repo.get_by_google_event_id(created.id)

    # правка с телефона: база фейковых часов в будущем -> remote новее
    gateway.patch_event(created.id, {"summary": "Новее на телефоне"})
    engine.pull_remote_changes()

    assert repo.get(task.id).title == "Новее на телефоне"


def test_local_newer_wins_and_enqueues_push(repo, store, db_path):
    # шлюз с часами в прошлом: любая правка «с телефона» старее локальной
    gateway = FakeCalendarGateway(base_time=utc_now() - timedelta(hours=1))
    engine = CalendarSyncEngine(repo, store, gateway)

    created = phone_creates_timed_event(gateway)
    engine.pull_remote_changes()
    task = repo.get_by_google_event_id(created.id)

    task.title = "Свежая локальная"
    repo.update(task)  # bump updated_at: локальная версия новее remote
    gateway.patch_event(created.id, {"summary": "Старая правка с телефона"})
    engine.pull_remote_changes()

    stored = repo.get(task.id)
    assert stored.title == "Свежая локальная"       # локальная не затёрта
    assert store.has_pending_op(stored.uid) is True  # и поставлена в push

    engine.push_pending()
    assert gateway.get_event(created.id).summary == "Свежая локальная"


def test_tie_keeps_local_version(engine, repo, store, gateway, db_path):
    created = phone_creates_timed_event(gateway)
    engine.pull_remote_changes()
    task = repo.get_by_google_event_id(created.id)

    remote = gateway.patch_event(created.id, {"summary": "Правка с телефона"})
    # выравниваем updated_at до точного равенства (ничья)
    with sqlite3.connect(str(db_path)) as connection:
        connection.execute(
            "UPDATE tasks SET updated_at = ? WHERE id = ?",
            (remote.updated_at.isoformat(), task.id),
        )
        connection.commit()

    engine.pull_remote_changes()

    stored = repo.get(task.id)
    assert stored.title != "Правка с телефона"       # локальная осталась
    assert store.has_pending_op(stored.uid) is False  # и ничего не пушится


def test_conflict_policy_is_deterministic(engine, repo, store, gateway):
    """Одинаковый сценарий двумя прогонами даёт одинаковый результат."""
    created = phone_creates_timed_event(gateway)
    engine.pull_remote_changes()
    task = repo.get_by_google_event_id(created.id)

    gateway.patch_event(created.id, {"summary": "Раунд 1"})
    engine.sync_once()
    first = repo.get(task.id).title

    engine.sync_once()  # без новых изменений — ничего не меняется
    assert repo.get(task.id).title == first == "Раунд 1"


def test_own_push_echo_does_not_bounce_back(engine, repo, store, gateway):
    """sync_once дважды: собственный push не возвращается как чужая правка."""
    task = add_timed_task(repo)
    engine.handle_local_task_created(task)
    engine.sync_once()
    engine.sync_once()

    assert len(repo.list_all()) == 1        # задача не задвоилась
    assert len(gateway.events) == 1         # событие тоже одно
    assert store.list_due_ops() == []       # и очередь пуста


# ---- ошибки шлюза: ретраи и dead-letter --------------------------------------------

def test_retryable_error_requeues_op(engine, repo, store, gateway, clock):
    task = add_timed_task(repo)
    engine.handle_local_task_created(task)

    gateway.fail_next(RetryableGatewayError("сеть моргнула"))
    engine.push_pending()

    assert repo.get(task.id).google_calendar_event_id is None  # не ушло
    assert store.has_pending_op(task.uid) is True              # но не потеряно
    assert store.list_due_ops() == []                          # ждёт бэкофф

    clock.advance(store_module.RETRY_BASE_DELAY_SECONDS + 1)
    engine.push_pending()  # вторая попытка успешна
    assert repo.get(task.id).google_calendar_event_id is not None
    assert store.list_due_ops() == []


def test_terminal_error_dead_letters_op(engine, repo, store, gateway, clock):
    task = add_timed_task(repo)
    engine.handle_local_task_created(task)

    gateway.fail_next(TerminalGatewayError("постоянная ошибка (400)"))
    engine.push_pending()

    terminal = store.list_terminal_ops()
    assert len(terminal) == 1
    assert "400" in terminal[0].last_error
    # dead-letter не ретраится никогда
    clock.advance(10 * store_module.RETRY_MAX_DELAY_SECONDS)
    engine.push_pending()
    assert repo.get(task.id).google_calendar_event_id is None
    assert len(gateway.events) == 0


# ---- повторяющиеся all-day экземпляры -----------------------------------------------

def recurring_all_day_instance(gateway):
    return gateway.insert_event(CalendarEvent(
        summary="Стендап",
        start=date(2026, 7, 13),
        end=date(2026, 7, 14),
        is_all_day=True,
        recurring_event_id="rec-42",
        original_start=datetime(2026, 7, 13, tzinfo=timezone.utc),
    ))


def test_recurring_instance_metadata_pulled_to_task(engine, repo, gateway):
    created = recurring_all_day_instance(gateway)
    engine.pull_remote_changes()
    task = repo.get_by_google_event_id(created.id)
    assert task.google_calendar_recurring_event_id == "rec-42"
    assert task.google_calendar_original_start is not None


def test_recurring_instance_local_move_does_not_patch_start_end(engine, repo, store, gateway):
    """Локальный «перенос» экземпляра серии уходит без start/end:
    текст обновляется, дата события в календаре не трогается."""
    created = recurring_all_day_instance(gateway)
    engine.pull_remote_changes()
    task = repo.get_by_google_event_id(created.id)

    task.title = "Стендап (переименован)"
    task.start = datetime(2026, 7, 15, 0, 0)  # локально «передвинули»
    task.end = datetime(2026, 7, 16, 0, 0)
    repo.update(task)
    engine.handle_local_task_updated(task)
    engine.push_pending()

    remote = gateway.get_event(created.id)
    assert remote.summary == "Стендап (переименован)"
    assert remote.start == date(2026, 7, 13)  # дата НЕ изменилась
    assert remote.end == date(2026, 7, 14)
    assert store.list_terminal_ops() == []    # и никакой ошибки 400


def test_gateway_refuses_blind_start_end_patch_on_recurring_instance(gateway):
    """Сам фейк воспроизводит отказ Google: слепой перенос экземпляра — 400."""
    created = recurring_all_day_instance(gateway)
    with pytest.raises(TerminalGatewayError):
        gateway.patch_event(created.id, {"start": date(2026, 7, 15),
                                         "end": date(2026, 7, 16)})


# ---- изоляция: ни Google, ни старого приложения ---------------------------------------

FORBIDDEN_IMPORT = re.compile(
    r"^\s*(?:from|import)\s+"
    r"(?:googleapiclient|google_auth_oauthlib|google_auth_httplib2|google"
    r"|requests|httplib2"
    r"|models|services|storage|core|ui|main)\b",
    re.MULTILINE,
)


def test_desktop_sync_packages_import_no_google_and_no_old_app():
    """Ни Google-клиентов/OAuth, ни модулей старого Flet-приложения."""
    root = Path(__file__).resolve().parent.parent / "planner_desktop"
    for package in ("sync", "storage", "usecases"):
        for source_file in sorted((root / package).glob("*.py")):
            source = source_file.read_text(encoding="utf-8")
            match = FORBIDDEN_IMPORT.search(source)
            assert match is None, (
                f"{package}/{source_file.name}: запрещённый импорт {match.group()!r}"
            )


def test_google_modules_not_imported_at_runtime():
    """Импорт всего ядра синка в чистом интерпретаторе не тянет Google-клиентов.

    Отдельный процесс нужен, чтобы sys.modules не был загрязнён тестами
    старого приложения, которые легально импортируют googleapiclient.
    """
    import subprocess
    import sys

    script = "\n".join([
        "import sys",
        "import planner_desktop.storage.calendar_sync_store",
        "import planner_desktop.sync.calendar_mapper",
        "import planner_desktop.sync.calendar_sync_engine",
        "import planner_desktop.sync.fake_calendar_gateway",
        "import planner_desktop.usecases.task_service",
        "bad = sorted({m.split('.')[0] for m in sys.modules"
        " if m.split('.')[0] in ('google', 'googleapiclient',"
        " 'google_auth_oauthlib', 'google_auth_httplib2')})",
        "print('FORBIDDEN:', bad) if bad else None",
        "sys.exit(1 if bad else 0)",
    ])
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(Path(__file__).resolve().parent.parent),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_sync_scenario_touches_only_tmp_db(tmp_path, monkeypatch):
    """Полный цикл синка открывает только временную БД — никакого app.db."""
    opened = []
    real_connect = sqlite3.connect

    def recording_connect(database, *args, **kwargs):
        opened.append(str(database))
        return real_connect(database, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", recording_connect)

    db_path = tmp_path / "app_desktop.db"
    repository = SQLiteTaskRepository(db_path)
    sync_store = CalendarSyncStore(db_path)
    gateway = FakeCalendarGateway()
    engine = CalendarSyncEngine(repository, sync_store, gateway)
    try:
        task = add_timed_task(repository)
        engine.handle_local_task_created(task)
        phone_creates_timed_event(gateway)
        engine.sync_once()
    finally:
        sync_store.close()
        repository.close()

    assert opened != []
    for database in opened:
        assert Path(database) == db_path  # только tmp_path, никакого app.db
