"""Тесты лёгкого разбора естественного ввода Quick Add (quick_parse).

Разбор консервативный: только «сегодня/завтра/послезавтра» и время ЧЧ:ММ.
Фиксированная опорная дата делает тесты детерминированными.
"""
from datetime import date

from planner_desktop.domain.quick_parse import parse_natural

TODAY = date(2026, 7, 6)       # понедельник
TOMORROW = "2026-07-07"
DAY_AFTER = "2026-07-08"
TODAY_TEXT = "2026-07-06"


def test_plain_text_stays_undated():
    parsed = parse_natural("Купить хлеб", today=TODAY)
    assert parsed.title == "Купить хлеб"
    assert parsed.add_to_calendar is False
    assert parsed.matched is False


def test_time_only_schedules_today():
    parsed = parse_natural("Отчет 15:00", today=TODAY)
    assert parsed.title == "Отчет"
    assert parsed.add_to_calendar is True
    assert parsed.is_all_day is False
    assert parsed.date_text == TODAY_TEXT
    assert parsed.time_text == "15:00"


def test_tomorrow_keyword_is_all_day():
    parsed = parse_natural("Позвонить Ивану завтра", today=TODAY)
    assert parsed.title == "Позвонить Ивану"
    assert parsed.add_to_calendar is True
    assert parsed.is_all_day is True
    assert parsed.date_text == TOMORROW
    assert parsed.time_text == ""


def test_today_keyword_with_time():
    parsed = parse_natural("Встреча сегодня 18:30", today=TODAY)
    assert parsed.title == "Встреча"
    assert parsed.is_all_day is False
    assert parsed.date_text == TODAY_TEXT
    assert parsed.time_text == "18:30"


def test_day_after_tomorrow():
    parsed = parse_natural("Сдать отчёт послезавтра", today=TODAY)
    assert parsed.date_text == DAY_AFTER
    assert parsed.is_all_day is True


def test_time_with_preposition_v():
    parsed = parse_natural("Созвон в 9:05", today=TODAY)
    assert parsed.title == "Созвон"
    assert parsed.time_text == "09:05"       # нормализуется до ЧЧ:ММ
    assert parsed.date_text == TODAY_TEXT


def test_keyword_and_time_combined_order_free():
    parsed = parse_natural("14:00 завтра тренировка", today=TODAY)
    assert parsed.title == "тренировка"
    assert parsed.date_text == TOMORROW
    assert parsed.time_text == "14:00"
    assert parsed.is_all_day is False


def test_only_keyword_becomes_undated_named_task():
    # Одно ключевое слово без названия не создаёт безымянную дату.
    parsed = parse_natural("завтра", today=TODAY)
    assert parsed.title == "завтра"
    assert parsed.add_to_calendar is False


def test_invalid_time_left_in_title():
    # 25:00 — не валидное время, остаётся в названии, задача без даты.
    parsed = parse_natural("Дело 25:00", today=TODAY)
    assert parsed.add_to_calendar is False
    assert "25:00" in parsed.title


def test_to_command_maps_fields():
    command = parse_natural("Отчет 15:00", today=TODAY).to_command(notes="важно")
    assert command.title == "Отчет"
    assert command.add_to_calendar is True
    assert command.date_text == TODAY_TEXT
    assert command.time_text == "15:00"
    assert command.notes == "важно"
