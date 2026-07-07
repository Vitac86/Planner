"""Тесты чистого маппинга Task <-> CalendarEvent (planner_desktop).

Без сети, без Qt, без Google: маппер — обычные функции над dataclass-ами.
"""
from datetime import date, datetime, timedelta, timezone

import pytest

from planner_desktop.domain.task import Task
from planner_desktop.sync import calendar_mapper
from planner_desktop.sync.sync_types import CalendarEvent, EVENT_STATUS_CANCELLED


def timed_task(**kwargs):
    defaults = dict(
        title="Встреча",
        notes="созвон",
        start=datetime(2026, 7, 8, 10, 30),
        end=datetime(2026, 7, 8, 11, 15),
        duration_minutes=45,
    )
    defaults.update(kwargs)
    return Task(**defaults)


def all_day_task(**kwargs):
    defaults = dict(
        title="Отпуск",
        start=datetime(2026, 7, 10, 0, 0),
        end=datetime(2026, 7, 11, 0, 0),
        is_all_day=True,
    )
    defaults.update(kwargs)
    return Task(**defaults)


# ---- Task -> CalendarEvent (create) -----------------------------------------

def test_timed_task_maps_to_datetime_event():
    event = calendar_mapper.task_to_event(timed_task())
    assert event.is_all_day is False
    # семантика dateTime/dateTime: оба конца — datetime
    assert isinstance(event.start, datetime)
    assert isinstance(event.end, datetime)
    assert event.start == datetime(2026, 7, 8, 10, 30)
    assert event.end == datetime(2026, 7, 8, 11, 15)
    assert event.summary == "Встреча"
    assert event.description == "созвон"


def test_timed_task_without_end_uses_duration():
    task = timed_task(end=None, duration_minutes=90)
    event = calendar_mapper.task_to_event(task)
    assert event.end == task.start + timedelta(minutes=90)


def test_all_day_task_maps_to_date_event_with_exclusive_end():
    event = calendar_mapper.task_to_event(all_day_task())
    assert event.is_all_day is True
    # семантика date/date: оба конца — «голые» даты, не datetime
    assert isinstance(event.start, date) and not isinstance(event.start, datetime)
    assert isinstance(event.end, date) and not isinstance(event.end, datetime)
    assert event.start == date(2026, 7, 10)
    assert event.end == date(2026, 7, 11)  # эксклюзивный конец


def test_all_day_task_without_end_gets_exclusive_next_day():
    task = all_day_task(end=None)
    event = calendar_mapper.task_to_event(task)
    assert event.start == date(2026, 7, 10)
    assert event.end == date(2026, 7, 11)


def test_completed_task_still_maps_to_event():
    """Продуктовое решение фазы 1: галочка не убирает событие из календаря."""
    event = calendar_mapper.task_to_event(timed_task(completed=True))
    assert event.summary == "Встреча"


def test_undated_task_refused():
    with pytest.raises(ValueError):
        calendar_mapper.task_to_event(Task(title="Без даты"))


def test_deleted_task_refused_delete_is_separate_op():
    task = timed_task()
    task.mark_deleted()
    with pytest.raises(ValueError):
        calendar_mapper.task_to_event(task)
    with pytest.raises(ValueError):
        calendar_mapper.task_to_event_patch(task)


# ---- Task -> патч (update) ----------------------------------------------------

def test_patch_for_plain_task_includes_start_end():
    patch = calendar_mapper.task_to_event_patch(timed_task())
    assert patch["summary"] == "Встреча"
    assert patch["start"] == datetime(2026, 7, 8, 10, 30)
    assert patch["end"] == datetime(2026, 7, 8, 11, 15)
    assert patch["is_all_day"] is False


def test_patch_for_all_day_task_uses_dates():
    patch = calendar_mapper.task_to_event_patch(all_day_task())
    assert patch["start"] == date(2026, 7, 10)
    assert patch["end"] == date(2026, 7, 11)
    assert patch["is_all_day"] is True


def test_patch_for_recurring_instance_omits_start_end():
    """Правило безопасности: перенос экземпляра серии вслепую не пушится."""
    task = all_day_task(
        google_calendar_event_id="evt-5",
        google_calendar_recurring_event_id="rec-1",
        google_calendar_original_start=datetime(2026, 7, 10, tzinfo=timezone.utc),
    )
    # локально экземпляр «передвинули» — патч всё равно без start/end
    task.start = datetime(2026, 7, 12, 0, 0)
    task.end = datetime(2026, 7, 13, 0, 0)
    patch = calendar_mapper.task_to_event_patch(task)
    assert "start" not in patch
    assert "end" not in patch
    assert "is_all_day" not in patch
    assert patch["summary"] == "Отпуск"


# ---- CalendarEvent -> Task -----------------------------------------------------

def test_timed_event_maps_to_task():
    event = CalendarEvent(
        id="evt-1",
        etag='"3"',
        summary="Планёрка",
        description="каждую среду",
        start=datetime(2026, 7, 8, 9, 0),
        end=datetime(2026, 7, 8, 9, 30),
        updated_at=datetime(2026, 7, 8, 8, 0, tzinfo=timezone.utc),
    )
    task = calendar_mapper.event_to_new_task(event)
    assert task.title == "Планёрка"
    assert task.notes == "каждую среду"
    assert task.is_all_day is False
    assert task.start == datetime(2026, 7, 8, 9, 0)
    assert task.end == datetime(2026, 7, 8, 9, 30)
    assert task.duration_minutes == 30
    assert task.google_calendar_event_id == "evt-1"
    assert task.google_calendar_etag == '"3"'
    assert task.updated_at == event.updated_at


def test_all_day_event_maps_to_all_day_task():
    event = CalendarEvent(
        id="evt-2",
        etag='"1"',
        summary="Конференция",
        start=date(2026, 7, 20),
        end=date(2026, 7, 22),  # эксклюзивно: 20 и 21 июля
        is_all_day=True,
    )
    task = calendar_mapper.event_to_new_task(event)
    assert task.is_all_day is True
    assert task.start == datetime(2026, 7, 20, 0, 0)
    assert task.end == datetime(2026, 7, 22, 0, 0)
    assert task.duration_minutes is None


def test_recurring_instance_metadata_preserved():
    event = CalendarEvent(
        id="evt-3",
        etag='"1"',
        summary="Стендап",
        start=date(2026, 7, 13),
        end=date(2026, 7, 14),
        is_all_day=True,
        recurring_event_id="rec-42",
        original_start=datetime(2026, 7, 13, tzinfo=timezone.utc),
    )
    task = calendar_mapper.event_to_new_task(event)
    assert task.google_calendar_recurring_event_id == "rec-42"
    assert task.google_calendar_original_start == event.original_start


def test_cancelled_event_not_mapped_to_task():
    event = CalendarEvent(id="evt-4", status=EVENT_STATUS_CANCELLED)
    with pytest.raises(ValueError):
        calendar_mapper.event_to_new_task(event)
    with pytest.raises(ValueError):
        calendar_mapper.apply_event_to_task(event, timed_task())


def test_apply_event_updates_existing_task():
    task = timed_task(google_calendar_event_id="evt-1", google_calendar_etag='"1"')
    event = CalendarEvent(
        id="evt-1",
        etag='"2"',
        summary="Встреча (перенос)",
        start=datetime(2026, 7, 9, 15, 0),
        end=datetime(2026, 7, 9, 16, 0),
    )
    calendar_mapper.apply_event_to_task(event, task)
    assert task.title == "Встреча (перенос)"
    assert task.start == datetime(2026, 7, 9, 15, 0)
    assert task.duration_minutes == 60
    assert task.google_calendar_etag == '"2"'


def test_apply_all_day_event_converts_timed_task():
    task = timed_task(google_calendar_event_id="evt-1")
    event = CalendarEvent(
        id="evt-1",
        etag='"2"',
        summary="Теперь весь день",
        start=date(2026, 7, 9),
        end=date(2026, 7, 10),
        is_all_day=True,
    )
    calendar_mapper.apply_event_to_task(event, task)
    assert task.is_all_day is True
    assert task.start == datetime(2026, 7, 9, 0, 0)
    assert task.end == datetime(2026, 7, 10, 0, 0)
    assert task.duration_minutes is None


def test_is_syncable_rules():
    assert calendar_mapper.is_syncable(timed_task()) is True
    assert calendar_mapper.is_syncable(all_day_task()) is True
    assert calendar_mapper.is_syncable(Task(title="Без даты")) is False
    dead = timed_task()
    dead.mark_deleted()
    assert calendar_mapper.is_syncable(dead) is False
