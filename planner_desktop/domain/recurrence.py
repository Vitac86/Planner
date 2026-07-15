"""Чистый движок правил повторения локальных серий (Phase 3.2A).

Правила и генерация экземпляров — детерминированный Python без Qt,
SQLite и Google. Семантика зафиксирована в docs/RECURRENCE_ARCHITECTURE.md:

- частоты daily/weekly/monthly/yearly, interval >= 1;
- weekly — выбранные дни недели (0 = понедельник, как date.weekday());
- monthly day 29/30/31 — RRULE-подобно: месяцы без такого числа
  пропускаются, ничего не переносится на последний день месяца;
- yearly 29 февраля — только високосные годы;
- окончание: never / until (включительно) / count (от начала серии);
- timed-экземпляры сохраняют локальное wall-clock время; таймзона серии
  хранится явным IANA-именем; DST-политика: неоднозначное время — fold=0,
  несуществующее — сдвиг вперёд на величину разрыва (resolve_wall_clock);
- генерация ограничена жёсткими пределами и стабильно отсортирована;
- occurrence_key — неизменяемая идентичность слота ИСХОДНОГО расписания
  (правка экземпляра ключ не меняет).
"""
from __future__ import annotations

import calendar
import os
import uuid
from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timedelta, timezone
from enum import Enum
from typing import List, Optional, Tuple
from zoneinfo import ZoneInfo

from planner_desktop.domain.task import utc_now

# ---- пределы безопасности ---------------------------------------------------

#: Максимум экземпляров, которые один вызов генерации может вернуть.
MAX_OCCURRENCES_PER_CALL = 366
#: Максимум шагов-кандидатов внутри генератора (защита от патологических
#: правил/диапазонов: дальше этого предела экземпляры не ищутся).
MAX_GENERATION_STEPS = 20000
#: Максимальное значение interval и occurrence_count в валидном правиле.
MAX_INTERVAL = 999
MAX_OCCURRENCE_COUNT = 999
#: Длительность timed-экземпляра по умолчанию (минуты).
DEFAULT_OCCURRENCE_DURATION_MINUTES = 60
#: Документированный fallback, когда платформа не сообщает IANA-имя зоны.
FALLBACK_TIMEZONE_NAME = "UTC"


class RecurrenceFrequency(str, Enum):
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    YEARLY = "yearly"


class RecurrenceEndMode(str, Enum):
    NEVER = "never"
    UNTIL = "until"
    COUNT = "count"


class SeriesEditScope(str, Enum):
    THIS_OCCURRENCE = "this_occurrence"
    THIS_AND_FUTURE = "this_and_future"
    ENTIRE_SERIES = "entire_series"


# ---- пресеты ----------------------------------------------------------------

PRESET_EVERY_DAY = "every_day"
PRESET_WEEKDAYS = "weekdays"
PRESET_WEEKLY = "weekly_same_day"
PRESET_MONTHLY = "monthly_same_day"
PRESET_YEARLY = "yearly"
PRESET_CUSTOM = "custom"

_WEEKDAY_SHORT = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
_MONTHS_GENITIVE = [
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]


def recurrence_presets() -> List[dict]:
    """Пресеты для панели редактора (id + русская подпись)."""
    return [
        {"id": PRESET_EVERY_DAY, "label": "Каждый день"},
        {"id": PRESET_WEEKDAYS, "label": "По будням"},
        {"id": PRESET_WEEKLY, "label": "Каждую неделю"},
        {"id": PRESET_MONTHLY, "label": "Каждый месяц"},
        {"id": PRESET_YEARLY, "label": "Каждый год"},
        {"id": PRESET_CUSTOM, "label": "Настроить…"},
    ]


# ---- модели -----------------------------------------------------------------

@dataclass(frozen=True)
class RecurrenceRule:
    """Правило повторения. Неиспользуемые для частоты поля игнорируются."""

    frequency: RecurrenceFrequency = RecurrenceFrequency.DAILY
    interval: int = 1
    #: weekly: выбранные дни недели, 0 = понедельник.
    weekdays: Tuple[int, ...] = field(default_factory=tuple)
    #: monthly: число месяца 1..31.
    month_day: Optional[int] = None
    #: yearly: месяц 1..12 и число 1..31.
    yearly_month: Optional[int] = None
    yearly_day: Optional[int] = None
    end_mode: RecurrenceEndMode = RecurrenceEndMode.NEVER
    #: until: последняя допустимая дата экземпляра (включительно).
    until_date: Optional[date] = None
    #: count: всего экземпляров от начала серии.
    occurrence_count: Optional[int] = None


@dataclass(frozen=True)
class SeriesSchedule:
    """Расписание серии: якорная дата + время/длительность + таймзона."""

    start_date: date
    all_day: bool = True
    #: Локальное wall-clock время timed-серии (None для all-day).
    local_time: Optional[time] = None
    duration_minutes: Optional[int] = None
    timezone_name: str = FALLBACK_TIMEZONE_NAME


@dataclass(frozen=True)
class OccurrenceSpec:
    """Один сгенерированный слот серии (naive-локальные datetime)."""

    occurrence_key: str
    local_date: date
    start: datetime
    end: datetime
    all_day: bool


@dataclass(frozen=True)
class RecurrenceValidationResult:
    errors: Tuple[str, ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        return not self.errors


# ---- таймзоны и DST ---------------------------------------------------------

def is_valid_timezone(name: str) -> bool:
    if not name:
        return False
    try:
        ZoneInfo(name)
        return True
    except Exception:
        return False


def default_timezone_name() -> str:
    """Лучшее доступное IANA-имя локальной зоны.

    Порядок: tzlocal (если установлен) -> переменная TZ -> /etc/timezone
    (POSIX) -> документированный fallback "UTC". Wall-clock семантика
    генерации от fallback-а не страдает: экземпляры всегда сохраняют
    локальное время слота.
    """
    try:  # pragma: no cover - зависит от наличия пакета
        import tzlocal  # type: ignore

        key = getattr(tzlocal, "get_localzone_key", None)
        name = str(key() if callable(key) else tzlocal.get_localzone())
        if is_valid_timezone(name):
            return name
    except Exception:
        pass
    env_tz = (os.environ.get("TZ") or "").strip()
    if is_valid_timezone(env_tz):
        return env_tz
    try:  # pragma: no cover - только POSIX
        with open("/etc/timezone", encoding="utf-8") as handle:
            name = handle.read().strip()
        if is_valid_timezone(name):
            return name
    except OSError:
        pass
    return FALLBACK_TIMEZONE_NAME


def resolve_wall_clock(local_dt: datetime, timezone_name: str) -> datetime:
    """Aware-datetime слота по документированной DST-политике.

    - неоднозначное локальное время -> первое прохождение (fold=0);
    - несуществующее локальное время -> сдвиг вперёд на величину разрыва;
    - неизвестная зона -> детерминированный fallback UTC.
    """
    if local_dt.tzinfo is not None:
        local_dt = local_dt.replace(tzinfo=None)
    try:
        tz = ZoneInfo(timezone_name)
    except Exception:
        tz = timezone.utc
    candidate = local_dt.replace(fold=0, tzinfo=tz)
    # Несуществующее время выявляется по round-trip через UTC: если
    # wall-clock изменился, зона «протолкнула» время через разрыв вперёд.
    round_trip = candidate.astimezone(timezone.utc).astimezone(tz)
    if round_trip.replace(tzinfo=None) != local_dt:
        return round_trip
    return candidate


# ---- валидация ----------------------------------------------------------------

def validate_rule(
    rule: RecurrenceRule, schedule: SeriesSchedule
) -> RecurrenceValidationResult:
    """Человекочитаемые ошибки правила и расписания; пусто — валидно."""
    errors: List[str] = []

    if not isinstance(rule.frequency, RecurrenceFrequency):
        errors.append("Неизвестная частота повторения.")
        return RecurrenceValidationResult(tuple(errors))

    if not isinstance(rule.interval, int) or rule.interval < 1:
        errors.append("Интервал повторения должен быть не меньше 1.")
    elif rule.interval > MAX_INTERVAL:
        errors.append(f"Интервал повторения не может превышать {MAX_INTERVAL}.")

    if rule.frequency == RecurrenceFrequency.WEEKLY:
        if not rule.weekdays:
            errors.append("Выберите хотя бы один день недели.")
        elif any(day < 0 or day > 6 for day in rule.weekdays):
            errors.append("Дни недели должны быть в диапазоне Пн..Вс.")

    if rule.frequency == RecurrenceFrequency.MONTHLY:
        day = rule.month_day
        if day is None or not 1 <= int(day) <= 31:
            errors.append("Число месяца должно быть от 1 до 31.")

    if rule.frequency == RecurrenceFrequency.YEARLY:
        month, day = rule.yearly_month, rule.yearly_day
        if month is None or not 1 <= int(month) <= 12:
            errors.append("Месяц годового повторения должен быть от 1 до 12.")
        elif day is None or not 1 <= int(day) <= _max_month_day(int(month)):
            errors.append("Недопустимое число для выбранного месяца.")

    if rule.end_mode == RecurrenceEndMode.UNTIL:
        if rule.until_date is None:
            errors.append("Укажите дату окончания серии.")
        elif rule.until_date < schedule.start_date:
            errors.append("Дата окончания раньше начала серии.")
    elif rule.end_mode == RecurrenceEndMode.COUNT:
        count = rule.occurrence_count
        if count is None or int(count) < 1:
            errors.append("Число повторений должно быть не меньше 1.")
        elif int(count) > MAX_OCCURRENCE_COUNT:
            errors.append(
                f"Число повторений не может превышать {MAX_OCCURRENCE_COUNT}."
            )

    if not schedule.all_day and schedule.local_time is None:
        errors.append("Для серии со временем укажите время.")
    if schedule.duration_minutes is not None and schedule.duration_minutes <= 0:
        errors.append("Длительность должна быть больше нуля.")
    if not is_valid_timezone(schedule.timezone_name):
        errors.append("Неизвестный часовой пояс серии.")

    return RecurrenceValidationResult(tuple(errors))


def rule_from_preset(preset: str, start_date: date) -> RecurrenceRule:
    """Правило из пресета относительно якорной даты серии."""
    if preset == PRESET_EVERY_DAY:
        return RecurrenceRule(RecurrenceFrequency.DAILY)
    if preset == PRESET_WEEKDAYS:
        return RecurrenceRule(
            RecurrenceFrequency.WEEKLY, weekdays=(0, 1, 2, 3, 4)
        )
    if preset == PRESET_WEEKLY:
        return RecurrenceRule(
            RecurrenceFrequency.WEEKLY, weekdays=(start_date.weekday(),)
        )
    if preset == PRESET_MONTHLY:
        return RecurrenceRule(
            RecurrenceFrequency.MONTHLY, month_day=start_date.day
        )
    if preset == PRESET_YEARLY:
        return RecurrenceRule(
            RecurrenceFrequency.YEARLY,
            yearly_month=start_date.month,
            yearly_day=start_date.day,
        )
    # custom и неизвестные пресеты — консервативный weekly по дню старта.
    return RecurrenceRule(
        RecurrenceFrequency.WEEKLY, weekdays=(start_date.weekday(),)
    )


# ---- человекочитаемая сводка ---------------------------------------------------

def describe_rule(rule: RecurrenceRule, schedule: SeriesSchedule) -> str:
    """Русская сводка правила: «Каждые 2 недели: Пн, Ср, до 31.12.2026»."""
    if rule.frequency == RecurrenceFrequency.DAILY:
        base = "Каждый день" if rule.interval == 1 else f"Каждые {rule.interval} дн."
    elif rule.frequency == RecurrenceFrequency.WEEKLY:
        days = ", ".join(
            _WEEKDAY_SHORT[d] for d in sorted(set(rule.weekdays))
            if 0 <= d <= 6
        )
        if rule.interval == 1 and set(rule.weekdays) == {0, 1, 2, 3, 4}:
            base = "По будням"
        elif rule.interval == 1:
            base = f"Каждую неделю: {days}" if days else "Каждую неделю"
        else:
            base = f"Каждые {rule.interval} нед.: {days}"
    elif rule.frequency == RecurrenceFrequency.MONTHLY:
        day = rule.month_day or schedule.start_date.day
        base = (
            f"Каждый месяц {day}-го числа"
            if rule.interval == 1
            else f"Каждые {rule.interval} мес. {day}-го числа"
        )
    else:
        month = rule.yearly_month or schedule.start_date.month
        day = rule.yearly_day or schedule.start_date.day
        month_name = _MONTHS_GENITIVE[max(1, min(12, month)) - 1]
        base = (
            f"Каждый год {day} {month_name}"
            if rule.interval == 1
            else f"Каждые {rule.interval} г. {day} {month_name}"
        )

    if not schedule.all_day and schedule.local_time is not None:
        base += f" в {schedule.local_time.strftime('%H:%M')}"

    if rule.end_mode == RecurrenceEndMode.UNTIL and rule.until_date is not None:
        base += f", до {rule.until_date.strftime('%d.%m.%Y')}"
    elif (
        rule.end_mode == RecurrenceEndMode.COUNT
        and rule.occurrence_count is not None
    ):
        base += f", всего {rule.occurrence_count} раз"
    return base


# ---- occurrence key -----------------------------------------------------------

def occurrence_key(schedule: SeriesSchedule, local_date: date) -> str:
    """Детерминированная идентичность слота ИСХОДНОГО расписания.

    all-day: ``YYYY-MM-DD``;
    timed:   ``YYYY-MM-DDTHH:MM@<IANA-zone>``.
    """
    if schedule.all_day or schedule.local_time is None:
        return local_date.strftime("%Y-%m-%d")
    return (
        f"{local_date.strftime('%Y-%m-%d')}"
        f"T{schedule.local_time.strftime('%H:%M')}"
        f"@{schedule.timezone_name}"
    )


# ---- генерация ------------------------------------------------------------------

def _max_month_day(month: int, year: Optional[int] = None) -> int:
    if year is None:
        # Максимум по «лучшему» году (для валидации 29 февраля разрешаем).
        return 29 if month == 2 else calendar.monthrange(2024, month)[1]
    return calendar.monthrange(year, month)[1]


def _add_months(year: int, month: int, months: int) -> Tuple[int, int]:
    index = (year * 12 + (month - 1)) + months
    return index // 12, index % 12 + 1


def _candidate_dates(
    rule: RecurrenceRule,
    start: date,
    range_end: date,
    *,
    fast_forward_to: Optional[date],
):
    """Кандидаты-даты по частоте, по возрастанию, начиная с даты старта.

    fast_forward_to — оптимизация для правил без COUNT: генерация может
    начаться с ближайшего interval-выровненного кандидата перед этой датой
    (счётчик экземпляров при этом не нужен).
    """
    interval = max(1, int(rule.interval))
    freq = rule.frequency

    if freq == RecurrenceFrequency.DAILY:
        current = start
        if fast_forward_to is not None and fast_forward_to > start:
            steps = (fast_forward_to - start).days // interval
            current = start + timedelta(days=steps * interval)
        while current <= range_end:
            yield current
            current += timedelta(days=interval)
        return

    if freq == RecurrenceFrequency.WEEKLY:
        selected = sorted(set(d for d in rule.weekdays if 0 <= d <= 6))
        week_anchor = start - timedelta(days=start.weekday())
        if fast_forward_to is not None and fast_forward_to > start:
            target_week = fast_forward_to - timedelta(
                days=fast_forward_to.weekday()
            )
            weeks = ((target_week - week_anchor).days // 7 // interval) * interval
            week_anchor += timedelta(weeks=max(0, weeks))
        while week_anchor <= range_end:
            for day_index in selected:
                candidate = week_anchor + timedelta(days=day_index)
                if candidate < start or candidate > range_end:
                    continue
                yield candidate
            week_anchor += timedelta(weeks=interval)
        return

    if freq == RecurrenceFrequency.MONTHLY:
        day = int(rule.month_day or start.day)
        year, month = start.year, start.month
        if fast_forward_to is not None and fast_forward_to > start:
            months_gap = (
                (fast_forward_to.year * 12 + fast_forward_to.month)
                - (year * 12 + month)
            )
            steps = max(0, months_gap // interval)
            year, month = _add_months(year, month, steps * interval)
        while True:
            if day <= calendar.monthrange(year, month)[1]:
                candidate = date(year, month, day)
                if candidate > range_end:
                    return
                if candidate >= start:
                    yield candidate
            elif date(year, month, 1) > range_end:
                return
            year, month = _add_months(year, month, interval)
        return

    # YEARLY
    month = int(rule.yearly_month or start.month)
    day = int(rule.yearly_day or start.day)
    year = start.year
    if fast_forward_to is not None and fast_forward_to > start:
        steps = max(0, (fast_forward_to.year - year) // interval)
        year += steps * interval
    while year <= range_end.year:
        if day <= calendar.monthrange(year, month)[1]:
            candidate = date(year, month, day)
            if candidate > range_end:
                return
            if candidate >= start:
                yield candidate
        year += interval


def generate_occurrences(
    schedule: SeriesSchedule,
    rule: RecurrenceRule,
    range_start: date,
    range_end: date,
    *,
    limit: int = MAX_OCCURRENCES_PER_CALL,
) -> List[OccurrenceSpec]:
    """Экземпляры серии внутри [range_start, range_end] (обе включительно).

    Детерминированно: стабильный порядок по дате, без дублей ключей,
    не больше ``limit`` экземпляров и не больше ``MAX_GENERATION_STEPS``
    кандидатов за вызов. Для COUNT-режима счёт всегда идёт от начала
    серии, поэтому диапазон не искажает нумерацию.
    """
    if range_end < range_start or range_end < schedule.start_date:
        return []

    effective_end = range_end
    if rule.end_mode == RecurrenceEndMode.UNTIL and rule.until_date is not None:
        effective_end = min(effective_end, rule.until_date)
        if effective_end < range_start and effective_end < schedule.start_date:
            return []

    count_limit = None
    fast_forward: Optional[date] = range_start
    if rule.end_mode == RecurrenceEndMode.COUNT:
        count_limit = int(rule.occurrence_count or 0)
        if count_limit < 1:
            return []
        fast_forward = None  # счёт от начала серии — пропускать нельзя

    limit = max(0, min(int(limit), MAX_OCCURRENCES_PER_CALL))
    results: List[OccurrenceSpec] = []
    seen_keys = set()
    steps = 0
    produced = 0
    for candidate in _candidate_dates(
        rule, schedule.start_date, effective_end, fast_forward_to=fast_forward
    ):
        steps += 1
        if steps > MAX_GENERATION_STEPS:
            break
        produced += 1
        if count_limit is not None and produced > count_limit:
            break
        if candidate < range_start:
            continue
        key = occurrence_key(schedule, candidate)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        results.append(_build_spec(schedule, candidate, key))
        if len(results) >= limit:
            break
    return results


def _build_spec(
    schedule: SeriesSchedule, local_date: date, key: str
) -> OccurrenceSpec:
    if schedule.all_day or schedule.local_time is None:
        start = datetime.combine(local_date, time.min)
        span_days = 1
        if schedule.duration_minutes and schedule.duration_minutes >= 24 * 60:
            span_days = max(1, schedule.duration_minutes // (24 * 60))
        return OccurrenceSpec(
            occurrence_key=key,
            local_date=local_date,
            start=start,
            end=start + timedelta(days=span_days),
            all_day=True,
        )
    # Keep the series' wall-clock semantics while resolving DST edge cases
    # deterministically.  Task rows use the application's existing naive-local
    # datetime representation, so the resolved aware value is converted back
    # to its local wall-clock fields after applying the documented policy:
    # fold=0 for ambiguous times and shift-forward for nonexistent times.
    resolved = resolve_wall_clock(
        datetime.combine(local_date, schedule.local_time),
        schedule.timezone_name,
    )
    start = resolved.replace(tzinfo=None)
    minutes = schedule.duration_minutes or DEFAULT_OCCURRENCE_DURATION_MINUTES
    return OccurrenceSpec(
        occurrence_key=key,
        local_date=local_date,
        start=start,
        end=start + timedelta(minutes=minutes),
        all_day=False,
    )


# ---- сущность серии --------------------------------------------------------------

@dataclass
class TaskSeries:
    """Локальная повторяющаяся серия. Google-полей нет сознательно."""

    title: str
    schedule: SeriesSchedule
    rule: RecurrenceRule
    id: Optional[int] = None
    uid: str = field(default_factory=lambda: str(uuid.uuid4()))
    notes: str = ""
    priority: int = 0
    tags: Tuple[str, ...] = field(default_factory=tuple)
    #: Ревизия расписания/правила: растёт при каждом изменении.
    revision: int = 1
    active: bool = True
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    deleted_at: Optional[datetime] = None

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None

    def touch(self) -> None:
        self.updated_at = utc_now()

    def mark_deleted(self, when: Optional[datetime] = None) -> None:
        self.deleted_at = when or utc_now()
        self.updated_at = self.deleted_at

    def summary(self) -> str:
        return describe_rule(self.rule, self.schedule)

    def with_end_before(self, boundary: date) -> "TaskSeries":
        """Копия серии, завершённой строго ПЕРЕД boundary (для split)."""
        new_rule = replace(
            self.rule,
            end_mode=RecurrenceEndMode.UNTIL,
            until_date=boundary - timedelta(days=1),
            occurrence_count=None,
        )
        clone = replace_series(self, rule=new_rule)
        return clone


def replace_series(series: TaskSeries, **changes) -> TaskSeries:
    """dataclasses.replace для мутабельного TaskSeries (без потери id/uid)."""
    payload = {
        "title": series.title,
        "schedule": series.schedule,
        "rule": series.rule,
        "id": series.id,
        "uid": series.uid,
        "notes": series.notes,
        "priority": series.priority,
        "tags": tuple(series.tags),
        "revision": series.revision,
        "active": series.active,
        "created_at": series.created_at,
        "updated_at": series.updated_at,
        "deleted_at": series.deleted_at,
    }
    payload.update(changes)
    return TaskSeries(**payload)


__all__ = [
    "DEFAULT_OCCURRENCE_DURATION_MINUTES",
    "FALLBACK_TIMEZONE_NAME",
    "MAX_GENERATION_STEPS",
    "MAX_INTERVAL",
    "MAX_OCCURRENCE_COUNT",
    "MAX_OCCURRENCES_PER_CALL",
    "OccurrenceSpec",
    "PRESET_CUSTOM",
    "PRESET_EVERY_DAY",
    "PRESET_MONTHLY",
    "PRESET_WEEKDAYS",
    "PRESET_WEEKLY",
    "PRESET_YEARLY",
    "RecurrenceEndMode",
    "RecurrenceFrequency",
    "RecurrenceRule",
    "RecurrenceValidationResult",
    "SeriesEditScope",
    "SeriesSchedule",
    "TaskSeries",
    "default_timezone_name",
    "describe_rule",
    "generate_occurrences",
    "is_valid_timezone",
    "occurrence_key",
    "recurrence_presets",
    "replace_series",
    "resolve_wall_clock",
    "rule_from_preset",
    "validate_rule",
]
