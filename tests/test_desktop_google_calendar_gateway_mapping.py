"""Тесты чистого маппинга GoogleCalendarGateway: CalendarEvent <-> тела
Calendar API v3. Без сети, без OAuth, без googleapiclient.
"""
from datetime import date, datetime, timezone

from planner_desktop.sync.google_calendar_gateway import (
    event_to_insert_body,
    patch_to_body,
    payload_to_event,
    times_to_body,
)
from planner_desktop.sync.sync_types import CalendarEvent


# ---- CalendarEvent -> тело insert ------------------------------------------------

def test_timed_event_body_uses_datetime_form_only():
    event = CalendarEvent(
        summary="Встреча", description="Заметки",
        start=datetime(2026, 7, 14, 10, 0),
        end=datetime(2026, 7, 14, 11, 0),
        is_all_day=False,
    )
    body = event_to_insert_body(event)
    assert body["summary"] == "Встреча"
    assert body["description"] == "Заметки"
    assert "dateTime" in body["start"] and "dateTime" in body["end"]
    assert body["start"]["timeZone"] == "UTC"
    assert "date" not in body["start"] and "date" not in body["end"]
    # dateTime сериализован в UTC (оканчивается смещением +00:00)
    assert body["start"]["dateTime"].endswith("+00:00")


def test_all_day_event_body_uses_date_form_with_exclusive_end():
    event = CalendarEvent(
        summary="Конференция",
        start=date(2026, 7, 14), end=date(2026, 7, 15), is_all_day=True,
    )
    body = event_to_insert_body(event)
    assert body["start"] == {"date": "2026-07-14"}
    assert body["end"] == {"date": "2026-07-15"}  # эксклюзивный конец как есть


def test_all_day_end_never_collapses_below_one_day():
    event = CalendarEvent(
        summary="Однодневное",
        start=date(2026, 7, 14), end=date(2026, 7, 14), is_all_day=True,
    )
    body = event_to_insert_body(event)
    assert body["end"]["date"] == "2026-07-15"


def test_timed_naive_local_datetime_is_converted_to_utc():
    naive_local = datetime(2026, 7, 14, 12, 0)
    start_body, _ = times_to_body(naive_local, datetime(2026, 7, 14, 13, 0), False)
    parsed = datetime.fromisoformat(start_body["dateTime"])
    assert parsed.tzinfo is not None
    assert parsed.utcoffset().total_seconds() == 0
    # тот же момент времени, что и локальный naive
    assert parsed == naive_local.astimezone(timezone.utc)


# ---- патч -------------------------------------------------------------------------

def test_text_only_patch_has_no_start_end():
    body = patch_to_body({"summary": "Новое", "description": "Текст"})
    assert body == {"summary": "Новое", "description": "Текст"}


def test_all_day_patch_nulls_datetime_form_explicitly():
    """Урок исторической петли HTTP 400: при PATCH дат противоположная
    форма всегда явно null-ится, формы не смешиваются."""
    body = patch_to_body({
        "summary": "х", "start": date(2026, 7, 20),
        "end": date(2026, 7, 21), "is_all_day": True,
    })
    assert body["start"]["date"] == "2026-07-20"
    assert body["start"]["dateTime"] is None
    assert body["end"]["date"] == "2026-07-21"
    assert body["end"]["dateTime"] is None


def test_timed_patch_nulls_date_form_explicitly():
    body = patch_to_body({
        "start": datetime(2026, 7, 20, 9, 0),
        "end": datetime(2026, 7, 20, 10, 0), "is_all_day": False,
    })
    assert body["start"]["date"] is None
    assert "dateTime" in body["start"]
    assert body["end"]["date"] is None


# ---- тело Calendar API -> CalendarEvent ---------------------------------------------

def test_timed_payload_parses_to_local_naive_times():
    item = {
        "id": "evt-1",
        "etag": '"7"',
        "status": "confirmed",
        "summary": "Звонок",
        "description": "тема",
        "start": {"dateTime": "2026-07-14T09:00:00Z"},
        "end": {"dateTime": "2026-07-14T09:30:00Z"},
        "updated": "2026-07-14T08:00:00.123Z",
    }
    event = payload_to_event(item)
    assert event.id == "evt-1"
    assert event.etag == '"7"'
    assert event.is_all_day is False
    # start/end — локальные naive (так задачи хранятся в БД)
    assert event.start.tzinfo is None and event.end.tzinfo is None
    expected_local = datetime(2026, 7, 14, 9, 0, tzinfo=timezone.utc) \
        .astimezone().replace(tzinfo=None)
    assert event.start == expected_local
    # updated — aware UTC (сравнивается с Task.updated_at)
    assert event.updated_at.tzinfo is not None
    assert event.updated_at.utcoffset().total_seconds() == 0


def test_all_day_payload_parses_to_dates_with_exclusive_end():
    item = {
        "id": "evt-2",
        "start": {"date": "2026-07-14"},
        "end": {"date": "2026-07-16"},
        "summary": "Отпуск",
    }
    event = payload_to_event(item)
    assert event.is_all_day is True
    assert event.start == date(2026, 7, 14)
    assert event.end == date(2026, 7, 16)  # эксклюзивный конец сохранён как есть


def test_cancelled_stub_payload_parses_without_times():
    """Отменённые события приходят усечёнными — только id/status/etag."""
    event = payload_to_event({"id": "evt-3", "status": "cancelled", "etag": '"9"'})
    assert event.is_cancelled is True
    assert event.start is None and event.end is None


def test_recurring_instance_metadata_preserved():
    item = {
        "id": "evt-4_20260714",
        "recurringEventId": "evt-4",
        "originalStartTime": {"dateTime": "2026-07-14T10:00:00Z"},
        "start": {"dateTime": "2026-07-14T11:00:00Z"},
        "end": {"dateTime": "2026-07-14T12:00:00Z"},
        "summary": "Еженедельная",
    }
    event = payload_to_event(item)
    assert event.is_recurring_instance is True
    assert event.recurring_event_id == "evt-4"
    assert event.original_start is not None


def test_recurring_all_day_instance_original_start_from_date():
    item = {
        "id": "evt-5_20260714",
        "recurringEventId": "evt-5",
        "originalStartTime": {"date": "2026-07-14"},
        "start": {"date": "2026-07-15"},
        "end": {"date": "2026-07-16"},
    }
    event = payload_to_event(item)
    assert event.is_all_day is True
    # All-day Google identity is a date, never a synthetic midnight datetime.
    assert event.original_start == date(2026, 7, 14)
