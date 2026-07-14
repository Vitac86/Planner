"""Детерминированные правила пресетов планирования и снуза.

Единственное место, где считаются даты для быстрых действий «Сегодня» /
«Завтра» / «Следующий понедельник» / «На вечер» / «+1 час» и пунктов меню
снуза («Позже сегодня» / «Завтра» / «Следующая неделя» / «Без даты»).
QML дат не считает: ViewModel вызывает эти функции и отдаёт форме уже
готовые строки. Чистый Python без Qt — правила тестируются без окна.

Зафиксированная семантика (см. тесты test_desktop_scheduling.py):

- «Сегодня»/«Завтра»/«Следующий понедельник» меняют ТОЛЬКО дату.
  Существующее время сохраняется; если режим «Со временем», а времени ещё
  нет — подставляется DEFAULT_START_TIME (09:00). Задача «Без даты»
  становится задачей «Весь день» (время не выдумываем).
- «Завтра» — это календарное завтра (сегодня + 1 день), а не «дата задачи
  + 1»: пресет отвечает на вопрос «когда делать», глядя из сегодняшнего дня.
- «Следующий понедельник» — всегда БУДУЩИЙ понедельник: из понедельника
  он ведёт в понедельник через неделю, никогда «в сегодня».
- «На вечер» — режим «Со временем», время EVENING_TIME (19:00);
  дата сохраняется, для недатированной задачи берётся сегодня.
- «+1 час» — только для режима «Со временем» с валидным временем:
  старт сдвигается на час, переход через полночь сдвигает и дату.
- «Без даты» — снятие расписания; сами операции с Calendar-очередью
  выполняет DesktopTaskService.unschedule_task (правила не дублируются).

Снуз (перенос существующей задачи):

- «Позже сегодня» — сегодня, время = (сейчас + LATER_TODAY_DELTA),
  округлённое ВВЕРХ до получаса; позже 23:30 не уезжает (день тот же).
  Задача без времени становится задачей со временем (длительность
  сохраняется, иначе DEFAULT_DURATION_MINUTES).
- «Завтра» — календарное завтра; время и режим сохраняются,
  задача без даты становится all-day.
- «Следующая неделя» — следующий будущий понедельник; время и режим
  сохраняются, задача без даты становится all-day.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import List, Optional, Tuple

from .commands import DATE_FORMAT, DEFAULT_DURATION_MINUTES, TIME_FORMAT

# ---- зафиксированные константы поведения ---------------------------------------

#: Время по умолчанию, когда пресет даты требует время, а его ещё нет.
DEFAULT_START_TIME = time(9, 0)

#: «На вечер» — задокументированный вечерний час.
EVENING_TIME = time(19, 0)

#: «Позже сегодня» — насколько отодвигаем от «сейчас» (до округления вверх).
LATER_TODAY_DELTA = timedelta(hours=2)

#: Позже этого времени «Позже сегодня» не уезжает (остаётся сегодняшним днём).
LATER_TODAY_MAX = time(23, 30)

#: Пресеты длительности в редакторе (минуты).
DURATION_PRESETS = (15, 30, 45, 60, 90, 120)

# Режимы планирования формы редактора (значения сегментов QML).
MODE_NONE = "none"        # «Без даты»
MODE_ALL_DAY = "allday"   # «Весь день»
MODE_TIMED = "timed"      # «Со временем»
EDITOR_MODES = (MODE_NONE, MODE_ALL_DAY, MODE_TIMED)

# Идентификаторы пресетов редактора.
PRESET_TODAY = "today"
PRESET_TOMORROW = "tomorrow"
PRESET_NEXT_MONDAY = "next_monday"
PRESET_UNSCHEDULE = "unschedule"
PRESET_PLUS_HOUR = "plus_hour"
PRESET_EVENING = "evening"

EDITOR_PRESETS: Tuple[Tuple[str, str], ...] = (
    (PRESET_TODAY, "Сегодня"),
    (PRESET_TOMORROW, "Завтра"),
    (PRESET_NEXT_MONDAY, "Следующий понедельник"),
    (PRESET_UNSCHEDULE, "Без даты"),
    (PRESET_PLUS_HOUR, "+1 час"),
    (PRESET_EVENING, "На вечер"),
)

# Действия меню снуза (карточка задачи и инспектор).
SNOOZE_LATER_TODAY = "later_today"
SNOOZE_TOMORROW = "tomorrow"
SNOOZE_NEXT_WEEK = "next_week"
SNOOZE_PICK = "pick"          # «Выбрать дату и время» — открывает редактор
SNOOZE_UNSCHEDULE = "unschedule"

SNOOZE_ACTIONS: Tuple[Tuple[str, str], ...] = (
    (SNOOZE_LATER_TODAY, "Позже сегодня"),
    (SNOOZE_TOMORROW, "Завтра"),
    (SNOOZE_NEXT_WEEK, "Следующая неделя"),
    (SNOOZE_PICK, "Выбрать дату и время…"),
    (SNOOZE_UNSCHEDULE, "Без даты"),
)

PLUS_HOUR_NEEDS_TIME_ERROR = (
    "«+1 час» доступен только задаче со временем: выберите режим "
    "«Со временем» и время начала."
)


# ---- базовые расчёты дат ---------------------------------------------------------

def next_monday(today: date) -> date:
    """Ближайший БУДУЩИЙ понедельник: из понедельника — через неделю."""
    days_ahead = (7 - today.weekday()) % 7
    return today + timedelta(days=days_ahead or 7)


def round_up_to_half_hour(moment: datetime) -> datetime:
    """Округляет момент вверх до ближайших :00/:30 (ровные не двигаются)."""
    moment = moment.replace(second=0, microsecond=0)
    remainder = moment.minute % 30
    if remainder:
        moment += timedelta(minutes=30 - remainder)
    return moment


def later_today_start(now: datetime) -> datetime:
    """Старт «Позже сегодня»: сейчас + LATER_TODAY_DELTA, вверх до получаса,
    но не позже LATER_TODAY_MAX, пока этот предел ещё в будущем.

    После 23:30 двухчасового окна в текущем дне уже нет. Тогда выбирается
    ближайшая следующая минута этого же дня; в 23:59 остаётся 23:59. Это
    сохраняет обещание «сегодня» и никогда не переносит задачу назад на 23:30.
    """
    candidate = round_up_to_half_hour(now + LATER_TODAY_DELTA)
    latest = now.replace(
        hour=LATER_TODAY_MAX.hour,
        minute=LATER_TODAY_MAX.minute,
        second=0,
        microsecond=0,
    )
    if now < latest:
        return min(candidate, latest)

    current_minute = now.replace(second=0, microsecond=0)
    end_of_day = now.replace(hour=23, minute=59, second=0, microsecond=0)
    if current_minute < end_of_day:
        return current_minute + timedelta(minutes=1)
    return end_of_day


def next_full_hour(now: datetime) -> time:
    """Время ближайшего непрошедшего ровного часа.

    Переход через полночь возвращает 00:00; дату для этого случая сохраняет
    :func:`new_scheduled_defaults`.
    """
    if now.minute == 0 and now.second == 0 and now.microsecond == 0:
        return time(now.hour, 0)
    return time((now.hour + 1) % 24, 0)


def _next_full_hour_moment(now: datetime) -> datetime:
    """Полный момент ближайшего непрошедшего ровного часа."""
    moment = now.replace(minute=0, second=0, microsecond=0)
    if now != moment:
        moment += timedelta(hours=1)
    return moment


def _parse_date(text: str) -> Optional[date]:
    try:
        return datetime.strptime(text.strip(), DATE_FORMAT).date()
    except ValueError:
        return None


def _parse_time(text: str) -> Optional[time]:
    try:
        return datetime.strptime(text.strip(), TIME_FORMAT).time()
    except ValueError:
        return None


# ---- пресеты формы редактора ------------------------------------------------------

@dataclass(frozen=True)
class EditorState:
    """Состояние полей планирования формы редактора (строки — как в QML)."""

    mode: str = MODE_NONE
    date_text: str = ""
    time_text: str = ""

    def normalized_mode(self) -> str:
        return self.mode if self.mode in EDITOR_MODES else MODE_NONE


@dataclass(frozen=True)
class PresetResult:
    """Новые значения формы после пресета (или отказ с причиной)."""

    ok: bool
    mode: str
    date_text: str
    time_text: str
    error: str = ""


def _dated_result(state: EditorState, new_date: date) -> PresetResult:
    """Общий случай «пресет меняет только дату» (Сегодня/Завтра/Понедельник)."""
    mode = state.normalized_mode()
    if mode == MODE_TIMED:
        current_time = _parse_time(state.time_text)
        time_text = (
            state.time_text.strip()
            if current_time is not None
            else DEFAULT_START_TIME.strftime(TIME_FORMAT)
        )
        return PresetResult(True, MODE_TIMED, new_date.strftime(DATE_FORMAT), time_text)
    # «Без даты» и «Весь день» получают дату без времени.
    return PresetResult(True, MODE_ALL_DAY, new_date.strftime(DATE_FORMAT), "")


def apply_editor_preset(
    preset: str, state: EditorState, today: date, now: Optional[datetime] = None
) -> PresetResult:
    """Применяет пресет к полям формы. Ничего не сохраняет и не валидирует
    название — только детерминированно пересчитывает режим/дату/время."""
    if preset == PRESET_TODAY:
        return _dated_result(state, today)
    if preset == PRESET_TOMORROW:
        return _dated_result(state, today + timedelta(days=1))
    if preset == PRESET_NEXT_MONDAY:
        return _dated_result(state, next_monday(today))
    if preset == PRESET_UNSCHEDULE:
        return PresetResult(True, MODE_NONE, "", "")
    if preset == PRESET_EVENING:
        base_date = _parse_date(state.date_text) or today
        return PresetResult(
            True,
            MODE_TIMED,
            base_date.strftime(DATE_FORMAT),
            EVENING_TIME.strftime(TIME_FORMAT),
        )
    if preset == PRESET_PLUS_HOUR:
        current_time = _parse_time(state.time_text)
        if state.normalized_mode() != MODE_TIMED or current_time is None:
            return PresetResult(
                False, state.mode, state.date_text, state.time_text,
                error=PLUS_HOUR_NEEDS_TIME_ERROR,
            )
        base_date = _parse_date(state.date_text) or today
        moved = datetime.combine(base_date, current_time) + timedelta(hours=1)
        return PresetResult(
            True,
            MODE_TIMED,
            moved.strftime(DATE_FORMAT),
            moved.strftime(TIME_FORMAT),
        )
    return PresetResult(
        False, state.mode, state.date_text, state.time_text,
        error=f"Неизвестный пресет: {preset}",
    )


def new_scheduled_defaults(now: datetime) -> PresetResult:
    """Заготовка «новой запланированной задачи» (Ctrl+Shift+N):
    сегодня, ближайший ровный час, режим «Со временем»."""
    start = _next_full_hour_moment(now)
    return PresetResult(
        True,
        MODE_TIMED,
        start.date().strftime(DATE_FORMAT),
        start.strftime(TIME_FORMAT),
    )


# ---- снуз существующей задачи ------------------------------------------------------

@dataclass(frozen=True)
class PostponePlan:
    """Куда переносится задача: новое расписание для schedule_task."""

    start: datetime
    is_all_day: bool
    duration_minutes: Optional[int]


def compute_postpone(
    action: str,
    *,
    start: Optional[datetime],
    is_all_day: bool,
    duration_minutes: Optional[int],
    now: datetime,
) -> PostponePlan:
    """Новое расписание для снуза. SNOOZE_UNSCHEDULE и SNOOZE_PICK сюда не
    попадают: первый идёт через unschedule_task, второй открывает редактор."""
    keep_duration = (
        duration_minutes
        if duration_minutes is not None and duration_minutes > 0
        else DEFAULT_DURATION_MINUTES
    )

    if action == SNOOZE_LATER_TODAY:
        return PostponePlan(later_today_start(now), False, keep_duration)

    if action == SNOOZE_TOMORROW:
        target_day = now.date() + timedelta(days=1)
    elif action == SNOOZE_NEXT_WEEK:
        target_day = next_monday(now.date())
    else:
        raise ValueError(f"Неизвестное действие снуза: {action}")

    if start is not None and not is_all_day:
        # Задача со временем: сохраняем время, меняем день.
        return PostponePlan(
            datetime.combine(target_day, start.time()), False, keep_duration
        )
    # All-day и недатированные становятся all-day на целевой день.
    return PostponePlan(datetime.combine(target_day, time.min), True, None)


# ---- справочники для ViewModel/QML -------------------------------------------------

def editor_presets() -> List[dict]:
    return [{"id": pid, "label": label} for pid, label in EDITOR_PRESETS]


def snooze_actions() -> List[dict]:
    return [{"id": aid, "label": label} for aid, label in SNOOZE_ACTIONS]


def duration_presets() -> List[dict]:
    """Пресеты длительности с человеческими подписями («1,5 ч»)."""
    labels = {15: "15 мин", 30: "30 мин", 45: "45 мин",
              60: "1 час", 90: "1,5 часа", 120: "2 часа"}
    return [
        {"minutes": m, "label": labels.get(m, f"{m} мин")}
        for m in DURATION_PRESETS
    ]
