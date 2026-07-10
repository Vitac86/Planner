"""Лёгкий разбор естественного ввода Quick Add.

Сознательно консервативный, чтобы не «угадывать» лишнего и не мешать
пользователю: распознаём только небольшой набор ключевых слов даты
(«сегодня», «завтра», «послезавтра») и время в формате ЧЧ:ММ. Всё
остальное остаётся в названии задачи как есть.

Примеры:
    «Позвонить Ивану завтра»   -> title=«Позвонить Ивану», завтра, весь день
    «Отчет 15:00»              -> title=«Отчет», сегодня 15:00
    «Встреча сегодня 18:30»    -> title=«Встреча», сегодня 18:30
    «Купить хлеб»              -> title=«Купить хлеб», без даты

Возвращает готовую :class:`QuickAddCommand`, которую дальше валидирует и
превращает в задачу общий ``execute_quick_add`` — отдельной логики создания
здесь нет.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

from .commands import DATE_FORMAT, QuickAddCommand

# День недели/относительная дата: слово -> смещение в днях от «сегодня».
_DATE_KEYWORDS = {
    "сегодня": 0,
    "завтра": 1,
    "послезавтра": 2,
}

# Время ЧЧ:ММ (24 часа). \b работает и с кириллицей (re.UNICODE по умолчанию).
_TIME_RE = re.compile(r"\b([01]?\d|2[0-3]):([0-5]\d)\b")
# Предлог «в»/«к» непосредственно перед временем — убираем вместе со временем.
_TIME_WITH_PREP_RE = re.compile(r"\b(?:в|к)\s+([01]?\d|2[0-3]):([0-5]\d)\b", re.IGNORECASE)


@dataclass
class ParsedQuickAdd:
    """Результат разбора: поля для QuickAddCommand + флаг, что что-то распознано."""

    title: str
    add_to_calendar: bool = False
    is_all_day: bool = False
    date_text: str = ""
    time_text: str = ""
    matched_date: bool = False
    matched_time: bool = False

    @property
    def matched(self) -> bool:
        return self.matched_date or self.matched_time

    def to_command(self, notes: str = "") -> QuickAddCommand:
        return QuickAddCommand(
            title=self.title,
            notes=notes,
            add_to_calendar=self.add_to_calendar,
            is_all_day=self.is_all_day,
            date_text=self.date_text,
            time_text=self.time_text,
            duration_text="",
        )


def _collapse_spaces(text: str) -> str:
    return re.sub(r"\s{2,}", " ", text).strip(" \t,")


def parse_natural(text: str, *, today: Optional[date] = None) -> ParsedQuickAdd:
    """Разбирает строку Quick Add в структурированные поля.

    Ничего не «додумывает» сверх ключевых слов даты и времени ЧЧ:ММ.
    Если после вырезания токенов название пустеет (ввели только «завтра»),
    расписание сбрасывается, а исходный текст остаётся названием —
    задача без даты, чем неожиданно созданная календарная запись без имени.
    """
    reference = today or date.today()
    raw = (text or "").strip()

    working = raw
    matched_date = False
    matched_time = False
    day: date = reference
    time_text = ""

    # 1) дата по ключевому слову
    for keyword, offset in _DATE_KEYWORDS.items():
        pattern = re.compile(r"\b" + keyword + r"\b", re.IGNORECASE)
        if pattern.search(working):
            day = reference + timedelta(days=offset)
            working = pattern.sub(" ", working)
            matched_date = True
            break  # первое совпадение задаёт дату

    # 2) время ЧЧ:ММ (с предлогом «в/к» или без него)
    prep_match = _TIME_WITH_PREP_RE.search(working)
    time_match = prep_match or _TIME_RE.search(working)
    if time_match:
        hour = int(time_match.group(1))
        minute = int(time_match.group(2))
        time_text = f"{hour:02d}:{minute:02d}"
        working = (working[: time_match.start()] + " " + working[time_match.end():])
        matched_time = True

    title = _collapse_spaces(working)

    # Пустое имя после вырезания токенов -> не создаём безымянную дату.
    if not title:
        return ParsedQuickAdd(title=raw)

    if matched_time:
        return ParsedQuickAdd(
            title=title,
            add_to_calendar=True,
            is_all_day=False,
            date_text=day.strftime(DATE_FORMAT),
            time_text=time_text,
            matched_date=matched_date,
            matched_time=True,
        )
    if matched_date:
        return ParsedQuickAdd(
            title=title,
            add_to_calendar=True,
            is_all_day=True,
            date_text=day.strftime(DATE_FORMAT),
            matched_date=True,
        )
    return ParsedQuickAdd(title=title)
