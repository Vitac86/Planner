"""Тесты ежедневных (повторяющихся) задач нового десктопа.

Покрывают требования фазы 2: выбор дней недели (маска), появление на
сегодня, отметка выполнения на конкретную дату и персистентность SQLite.
Чистый Python — без Qt и без окна.
"""
from datetime import date

import pytest

from planner_desktop.domain.daily_task import (
    ALL_WEEKDAYS_MASK,
    WEEKDAYS_ONLY_MASK,
    WEEKEND_MASK,
    DailyTask,
    describe_mask,
    mask_weekdays,
    normalize_mask,
    weekday_in_mask,
)
from planner_desktop.repositories.daily_task_repository import (
    InMemoryDailyTaskRepository,
)
from planner_desktop.storage.sqlite_daily_task_repository import (
    SQLiteDailyTaskRepository,
)
from planner_desktop.usecases.daily_task_service import (
    DailyTaskService,
    validate_daily,
)

MONDAY = date(2026, 7, 6)
TUESDAY = date(2026, 7, 7)
NEXT_MONDAY = date(2026, 7, 13)


def test_reference_dates_have_expected_weekdays():
    assert MONDAY.weekday() == 0
    assert TUESDAY.weekday() == 1
    assert NEXT_MONDAY.weekday() == 0


# ---- маска дней недели --------------------------------------------------------

def test_weekday_in_mask_monday_bit():
    monday_only = 0b0000001
    assert weekday_in_mask(monday_only, 0) is True
    assert weekday_in_mask(monday_only, 1) is False


def test_normalize_mask_clamps_and_survives_garbage():
    assert normalize_mask(255) == ALL_WEEKDAYS_MASK
    assert normalize_mask(-1) == ALL_WEEKDAYS_MASK  # -1 & 127 == 127
    assert normalize_mask("мусор") == 0
    assert normalize_mask(None) == 0


def test_mask_weekdays_lists_set_bits():
    assert mask_weekdays(0b0000101) == [0, 2]  # Пн, Ср


def test_describe_mask_named_sets():
    assert describe_mask(ALL_WEEKDAYS_MASK) == "Каждый день"
    assert describe_mask(WEEKDAYS_ONLY_MASK) == "По будням"
    assert describe_mask(WEEKEND_MASK) == "По выходным"
    assert describe_mask(0) == "Никогда"
    assert describe_mask(0b0000101) == "Пн, Ср"


# ---- occurs_on ----------------------------------------------------------------

def test_occurs_on_respects_weekday_and_enabled():
    monday_task = DailyTask(title="Зарядка", weekdays_mask=0b0000001)
    assert monday_task.occurs_on(MONDAY) is True
    assert monday_task.occurs_on(TUESDAY) is False

    monday_task.enabled = False
    assert monday_task.occurs_on(MONDAY) is False


def test_occurs_on_false_for_deleted():
    task = DailyTask(title="X", weekdays_mask=ALL_WEEKDAYS_MASK)
    task.mark_deleted()
    assert task.occurs_on(MONDAY) is False


# ---- сервис: появление на дату + сортировка -----------------------------------

@pytest.fixture()
def service():
    return DailyTaskService(InMemoryDailyTaskRepository())


def test_occurrences_only_include_matching_weekday(service):
    service.create("Только по понедельникам", weekdays_mask=0b0000001)
    service.create("Каждый день", weekdays_mask=ALL_WEEKDAYS_MASK)

    monday_titles = [o.task.title for o in service.occurrences_for(MONDAY)]
    assert "Только по понедельникам" in monday_titles
    assert "Каждый день" in monday_titles

    tuesday_titles = [o.task.title for o in service.occurrences_for(TUESDAY)]
    assert "Только по понедельникам" not in tuesday_titles
    assert "Каждый день" in tuesday_titles


def test_occurrences_sorted_by_time_then_title(service):
    service.create("Вечер", weekdays_mask=ALL_WEEKDAYS_MASK, preferred_time="20:00")
    service.create("Утро", weekdays_mask=ALL_WEEKDAYS_MASK, preferred_time="08:00")
    service.create("Без времени", weekdays_mask=ALL_WEEKDAYS_MASK)

    order = [o.task.title for o in service.occurrences_for(MONDAY)]
    assert order == ["Утро", "Вечер", "Без времени"]


def test_disabled_task_never_appears(service):
    service.create("Выключено", weekdays_mask=ALL_WEEKDAYS_MASK, enabled=False)
    assert service.occurrences_for(MONDAY) == []


# ---- отметка выполнения на конкретную дату ------------------------------------

def test_completion_is_per_date(service):
    task = service.create("Итоги дня", weekdays_mask=ALL_WEEKDAYS_MASK).task

    assert service.toggle_completed(task.uid, MONDAY) is True
    monday = {o.task.uid: o.done for o in service.occurrences_for(MONDAY)}
    assert monday[task.uid] is True

    # другой день той же задачи остаётся невыполненным
    next_monday = {o.task.uid: o.done for o in service.occurrences_for(NEXT_MONDAY)}
    assert next_monday[task.uid] is False

    # снятие отметки на исходную дату
    assert service.toggle_completed(task.uid, MONDAY) is False
    monday = {o.task.uid: o.done for o in service.occurrences_for(MONDAY)}
    assert monday[task.uid] is False


def test_toggle_unknown_daily_returns_none(service):
    assert service.toggle_completed("нет-такого", MONDAY) is None


# ---- валидация ----------------------------------------------------------------

def test_validate_daily_rules():
    assert validate_daily("Ok", ALL_WEEKDAYS_MASK, "") == []
    assert validate_daily("", ALL_WEEKDAYS_MASK, "")  # пустое имя
    assert validate_daily("Ok", 0, "")                # ни одного дня
    assert validate_daily("Ok", ALL_WEEKDAYS_MASK, "25:99")  # плохое время


def test_create_rejects_invalid_and_keeps_store_empty(service):
    result = service.create("", weekdays_mask=ALL_WEEKDAYS_MASK)
    assert not result.ok
    assert result.errors
    assert service.list_all() == []


def test_edit_updates_fields(service):
    task = service.create("Старое", weekdays_mask=ALL_WEEKDAYS_MASK).task
    result = service.edit(task.uid, "Новое", notes="n",
                          enabled=False, weekdays_mask=0b0000001,
                          preferred_time="07:30")
    assert result.ok
    saved = service.get(task.uid)
    assert saved.title == "Новое"
    assert saved.enabled is False
    assert saved.weekdays_mask == 0b0000001
    assert saved.preferred_time == "07:30"


def test_delete_tombstones(service):
    task = service.create("Удаляемая", weekdays_mask=ALL_WEEKDAYS_MASK).task
    assert service.delete(task.uid) is True
    assert service.list_all() == []
    assert service.get(task.uid) is None


# ---- персистентность SQLite ---------------------------------------------------

def test_sqlite_persists_daily_tasks_and_completions(tmp_path):
    db_path = tmp_path / "app_desktop.db"

    repo = SQLiteDailyTaskRepository(db_path)
    service = DailyTaskService(repo)
    task = service.create("Зарядка", weekdays_mask=0b0000001,
                          preferred_time="08:00").task
    service.set_completed(task.uid, MONDAY, True)
    repo.close()

    # переоткрываем БД новым соединением — данные на месте
    repo2 = SQLiteDailyTaskRepository(db_path)
    service2 = DailyTaskService(repo2)
    tasks = service2.list_all()
    assert [t.title for t in tasks] == ["Зарядка"]
    assert tasks[0].weekdays_mask == 0b0000001
    assert tasks[0].preferred_time == "08:00"

    occ = service2.occurrences_for(MONDAY)
    assert len(occ) == 1
    assert occ[0].done is True
    assert service2.is_completed(task.uid, NEXT_MONDAY) is False
    repo2.close()


def test_sqlite_delete_is_tombstone_and_persists(tmp_path):
    db_path = tmp_path / "app_desktop.db"
    repo = SQLiteDailyTaskRepository(db_path)
    service = DailyTaskService(repo)
    task = service.create("X", weekdays_mask=ALL_WEEKDAYS_MASK).task
    assert service.delete(task.uid) is True
    repo.close()

    repo2 = SQLiteDailyTaskRepository(db_path)
    assert DailyTaskService(repo2).list_all() == []
    repo2.close()
