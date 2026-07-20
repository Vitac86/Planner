"""Pure Google Calendar recurrence transport for Phase 3.2B1.

The module deliberately has no Qt, SQLite, Google client, or network imports.
It preserves every recurrence line verbatim and only exposes a Planner
``RecurrenceRule`` when the Google rule can be represented without changing
its meaning.  Unsupported constructs remain diagnostic transport data; they
are never simplified or materialized as a local ``TaskSeries``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timezone
from enum import Enum
from typing import Iterable, Mapping, Optional, Sequence, Tuple, Union
from zoneinfo import ZoneInfo

from planner_desktop.domain.recurrence import (
    MAX_INTERVAL,
    MAX_OCCURRENCE_COUNT,
    RecurrenceEndMode,
    RecurrenceFrequency,
    RecurrenceRule,
    SeriesSchedule,
    describe_rule,
    resolve_wall_clock,
)


class GoogleRecurrenceSupport(str, Enum):
    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"


class GoogleWeekday(str, Enum):
    MO = "MO"
    TU = "TU"
    WE = "WE"
    TH = "TH"
    FR = "FR"
    SA = "SA"
    SU = "SU"

    @property
    def planner_index(self) -> int:
        return _WEEKDAYS.index(self)

    @classmethod
    def from_planner_index(cls, value: int) -> "GoogleWeekday":
        if value < 0 or value > 6:
            raise ValueError(f"Invalid Planner weekday: {value}")
        return _WEEKDAYS[value]


_WEEKDAYS = tuple(GoogleWeekday)


class GoogleUntilKind(str, Enum):
    DATE = "date"
    UTC_DATETIME = "utc_datetime"
    LOCAL_DATETIME = "local_datetime"


@dataclass(frozen=True)
class GoogleUntilValue:
    raw: str
    kind: GoogleUntilKind
    date_value: Optional[date] = None
    datetime_value: Optional[datetime] = None


class UnsupportedRecurrenceCode(str, Enum):
    MISSING_RRULE = "missing_rrule"
    MULTIPLE_RRULE = "multiple_rrule"
    EXRULE = "exrule"
    UNKNOWN_LINE = "unknown_line"
    INVALID_LINE = "invalid_line"
    INVALID_PROPERTY = "invalid_property"
    DUPLICATE_PROPERTY = "duplicate_property"
    UNSUPPORTED_PROPERTY = "unsupported_property"
    INVALID_INTEGER = "invalid_integer"
    INVALID_VALUE = "invalid_value"
    COUNT_AND_UNTIL = "count_and_until"
    ORDINAL_BYDAY = "ordinal_byday"
    UNSUPPORTED_COMBINATION = "unsupported_combination"
    NOT_LOSSLESS = "not_lossless"


@dataclass(frozen=True)
class UnsupportedRecurrenceReason:
    """Structured diagnostic retained alongside the readable message."""

    code: UnsupportedRecurrenceCode
    message: str
    line: str = ""
    property_name: str = ""


RecurrenceValue = Union[date, datetime]


@dataclass(frozen=True)
class GoogleRecurrenceDateValues:
    """A parsed EXDATE/RDATE line. ``raw_line`` is never canonicalized."""

    property_name: str
    raw_line: str
    values: Tuple[RecurrenceValue, ...] = ()
    value_kind: str = "date"
    tzid: Optional[str] = None
    parameters: Tuple[Tuple[str, str], ...] = ()


@dataclass(frozen=True)
class ParsedGoogleRule:
    raw_line: str
    frequency: Optional[RecurrenceFrequency] = None
    interval: int = 1
    byday: Tuple[GoogleWeekday, ...] = ()
    bymonthday: Tuple[int, ...] = ()
    bymonth: Tuple[int, ...] = ()
    count: Optional[int] = None
    until: Optional[GoogleUntilValue] = None
    wkst: Optional[GoogleWeekday] = None
    properties: Tuple[Tuple[str, str], ...] = ()


@dataclass(frozen=True)
class GoogleRecurrenceSet:
    """Lossless transport envelope for the Calendar ``recurrence`` array."""

    raw_lines: Tuple[str, ...] = ()
    rrule_lines: Tuple[str, ...] = ()
    exdates: Tuple[GoogleRecurrenceDateValues, ...] = ()
    rdates: Tuple[GoogleRecurrenceDateValues, ...] = ()
    other_lines: Tuple[str, ...] = ()


@dataclass(frozen=True)
class GoogleRecurrenceParseResult:
    recurrence_set: GoogleRecurrenceSet
    support: GoogleRecurrenceSupport
    parsed_rule: Optional[ParsedGoogleRule] = None
    planner_rule: Optional[RecurrenceRule] = None
    reasons: Tuple[UnsupportedRecurrenceReason, ...] = ()
    canonical_rrule: Optional[str] = None

    @property
    def supported(self) -> bool:
        return self.support is GoogleRecurrenceSupport.SUPPORTED

    @property
    def raw_lines(self) -> Tuple[str, ...]:
        return self.recurrence_set.raw_lines

    @property
    def exdates(self) -> Tuple[GoogleRecurrenceDateValues, ...]:
        return self.recurrence_set.exdates

    @property
    def rdates(self) -> Tuple[GoogleRecurrenceDateValues, ...]:
        return self.recurrence_set.rdates

    @property
    def readable_reason(self) -> str:
        return "; ".join(reason.message for reason in self.reasons)


_INTEGER = re.compile(r"^[+-]?\d+$")
_DATE = re.compile(r"^\d{8}$")
_DATETIME = re.compile(r"^\d{8}T\d{6}Z?$", re.IGNORECASE)
_ORDINAL_WEEKDAY = re.compile(r"^[+-]?\d+(MO|TU|WE|TH|FR|SA|SU)$", re.IGNORECASE)
_SUPPORTED_PROPERTIES = {
    "FREQ", "INTERVAL", "BYDAY", "BYMONTHDAY", "BYMONTH", "COUNT",
    "UNTIL", "WKST",
}


def _reason(
    code: UnsupportedRecurrenceCode,
    message: str,
    line: str = "",
    property_name: str = "",
) -> UnsupportedRecurrenceReason:
    return UnsupportedRecurrenceReason(code, message, line, property_name)


def _parse_compact_date(value: str) -> date:
    return datetime.strptime(value, "%Y%m%d").date()


def _parse_compact_datetime(value: str) -> datetime:
    fmt = "%Y%m%dT%H%M%SZ" if value.upper().endswith("Z") else "%Y%m%dT%H%M%S"
    parsed = datetime.strptime(value.upper(), fmt)
    return parsed.replace(tzinfo=timezone.utc) if value.upper().endswith("Z") else parsed


def _parse_until(value: str) -> GoogleUntilValue:
    raw = value
    if _DATE.fullmatch(value):
        return GoogleUntilValue(raw, GoogleUntilKind.DATE, date_value=_parse_compact_date(value))
    if _DATETIME.fullmatch(value):
        parsed = _parse_compact_datetime(value)
        kind = (GoogleUntilKind.UTC_DATETIME
                if parsed.tzinfo is not None else GoogleUntilKind.LOCAL_DATETIME)
        return GoogleUntilValue(raw, kind, datetime_value=parsed)
    raise ValueError("UNTIL должен быть YYYYMMDD или YYYYMMDDTHHMMSSZ.")


def _parse_positive_int(
    properties: Mapping[str, str], name: str, default: Optional[int] = None
) -> Optional[int]:
    if name not in properties:
        return default
    raw = properties[name]
    if not _INTEGER.fullmatch(raw):
        raise ValueError(f"{name} должен быть целым числом.")
    value = int(raw)
    if value < 1:
        raise ValueError(f"{name} должен быть не меньше 1.")
    return value


def _parse_int_list(value: str, name: str) -> Tuple[int, ...]:
    parts = value.split(",")
    if not parts or any(not part or not _INTEGER.fullmatch(part) for part in parts):
        raise ValueError(f"{name} должен содержать целые числа через запятую.")
    result = tuple(int(part) for part in parts)
    if len(set(result)) != len(result):
        raise ValueError(f"{name} содержит повторяющиеся значения.")
    return result


def _parse_byday(value: str) -> Tuple[GoogleWeekday, ...]:
    parts = value.split(",")
    result = []
    for raw in parts:
        upper = raw.upper()
        if _ORDINAL_WEEKDAY.fullmatch(upper):
            raise RuntimeError("ordinal")
        try:
            weekday = GoogleWeekday(upper)
        except ValueError as exc:
            raise ValueError(f"Неизвестный день недели BYDAY: {raw}.") from exc
        if weekday in result:
            raise ValueError("BYDAY содержит повторяющиеся дни недели.")
        result.append(weekday)
    return tuple(result)


def _parse_date_values_line(
    raw_line: str, property_and_params: str, value_text: str
) -> GoogleRecurrenceDateValues:
    parts = property_and_params.split(";")
    property_name = parts[0].upper()
    parameters = []
    seen = set()
    for raw_param in parts[1:]:
        if "=" not in raw_param:
            raise ValueError(f"Некорректный параметр {property_name}: {raw_param}.")
        key, value = raw_param.split("=", 1)
        key = key.upper()
        if key in seen:
            raise ValueError(f"Повторяющийся параметр {key} в {property_name}.")
        seen.add(key)
        parameters.append((key, value))
    parameter_map = dict(parameters)
    unknown = set(parameter_map) - {"TZID", "VALUE"}
    if unknown:
        raise ValueError(f"Неподдерживаемые параметры {property_name}: {', '.join(sorted(unknown))}.")
    tzid = parameter_map.get("TZID")
    if tzid:
        try:
            ZoneInfo(tzid)
        except Exception as exc:
            raise ValueError(f"Неизвестный TZID: {tzid}.") from exc

    raw_values = value_text.split(",")
    if not raw_values or any(not value for value in raw_values):
        raise ValueError(f"{property_name} не содержит значений.")
    declared = parameter_map.get("VALUE", "").upper()
    values = []
    kinds = set()
    for raw in raw_values:
        if declared == "DATE" or (not declared and _DATE.fullmatch(raw)):
            if not _DATE.fullmatch(raw):
                raise ValueError(f"Некорректная дата {property_name}: {raw}.")
            values.append(_parse_compact_date(raw))
            kinds.add("date")
        elif declared in ("", "DATE-TIME") and _DATETIME.fullmatch(raw):
            parsed = _parse_compact_datetime(raw)
            if parsed.tzinfo is not None and tzid:
                raise ValueError(f"{property_name}: UTC-значение нельзя сочетать с TZID.")
            values.append(parsed)
            kinds.add("utc_datetime" if parsed.tzinfo is not None else "date_time")
        else:
            raise ValueError(f"Некорректное значение {property_name}: {raw}.")
    if len(kinds) != 1:
        raise ValueError(f"{property_name} смешивает даты и date-time значения.")
    return GoogleRecurrenceDateValues(
        property_name=property_name,
        raw_line=raw_line,
        values=tuple(values),
        value_kind=next(iter(kinds)),
        tzid=tzid,
        parameters=tuple(parameters),
    )


def _parse_rrule_line(
    line: str,
) -> tuple[Optional[ParsedGoogleRule], list[UnsupportedRecurrenceReason]]:
    reasons: list[UnsupportedRecurrenceReason] = []
    if ":" not in line:
        return None, [_reason(
            UnsupportedRecurrenceCode.INVALID_LINE,
            "Строка RRULE не содержит двоеточие.", line,
        )]
    prefix, body = line.split(":", 1)
    if prefix.upper() != "RRULE":
        return None, [_reason(
            UnsupportedRecurrenceCode.INVALID_LINE,
            "У RRULE не поддерживаются параметры свойства.", line,
        )]
    properties: dict[str, str] = {}
    ordered_properties = []
    for token in body.split(";"):
        if "=" not in token:
            reasons.append(_reason(
                UnsupportedRecurrenceCode.INVALID_PROPERTY,
                f"Некорректное свойство RRULE: {token or '(пусто)' }.", line,
            ))
            continue
        name, value = token.split("=", 1)
        name = name.upper()
        value = value.upper()
        if name in properties:
            reasons.append(_reason(
                UnsupportedRecurrenceCode.DUPLICATE_PROPERTY,
                f"Свойство {name} указано в RRULE несколько раз.", line, name,
            ))
            continue
        properties[name] = value
        ordered_properties.append((name, value))
    for name in sorted(set(properties) - _SUPPORTED_PROPERTIES):
        reasons.append(_reason(
            UnsupportedRecurrenceCode.UNSUPPORTED_PROPERTY,
            f"Свойство {name} пока не поддерживается Planner.", line, name,
        ))

    frequency = None
    raw_frequency = properties.get("FREQ")
    if raw_frequency is None:
        reasons.append(_reason(
            UnsupportedRecurrenceCode.INVALID_PROPERTY,
            "В RRULE отсутствует обязательное свойство FREQ.", line, "FREQ",
        ))
    else:
        try:
            frequency = RecurrenceFrequency(raw_frequency.lower())
        except ValueError:
            reasons.append(_reason(
                UnsupportedRecurrenceCode.INVALID_VALUE,
                f"Частота FREQ={raw_frequency} не поддерживается.", line, "FREQ",
            ))

    interval = 1
    count = None
    until = None
    byday: Tuple[GoogleWeekday, ...] = ()
    bymonthday: Tuple[int, ...] = ()
    bymonth: Tuple[int, ...] = ()
    wkst = None
    try:
        interval = int(_parse_positive_int(properties, "INTERVAL", 1) or 1)
        if interval > MAX_INTERVAL:
            raise ValueError(f"INTERVAL не может превышать {MAX_INTERVAL} в Planner.")
    except ValueError as exc:
        reasons.append(_reason(
            UnsupportedRecurrenceCode.INVALID_INTEGER, str(exc), line, "INTERVAL"
        ))
    try:
        count = _parse_positive_int(properties, "COUNT")
        if count is not None and count > MAX_OCCURRENCE_COUNT:
            raise ValueError(f"COUNT не может превышать {MAX_OCCURRENCE_COUNT} в Planner.")
    except ValueError as exc:
        reasons.append(_reason(
            UnsupportedRecurrenceCode.INVALID_INTEGER, str(exc), line, "COUNT"
        ))
    if "COUNT" in properties and "UNTIL" in properties:
        reasons.append(_reason(
            UnsupportedRecurrenceCode.COUNT_AND_UNTIL,
            "COUNT и UNTIL нельзя использовать одновременно.", line,
        ))
    if "UNTIL" in properties:
        try:
            until = _parse_until(properties["UNTIL"])
            if until.kind is GoogleUntilKind.LOCAL_DATETIME:
                reasons.append(_reason(
                    UnsupportedRecurrenceCode.NOT_LOSSLESS,
                    "UNTIL с date-time должен быть указан в UTC и оканчиваться на Z.",
                    line, "UNTIL",
                ))
        except ValueError as exc:
            reasons.append(_reason(
                UnsupportedRecurrenceCode.INVALID_VALUE, str(exc), line, "UNTIL"
            ))
    if "BYDAY" in properties:
        try:
            byday = _parse_byday(properties["BYDAY"])
        except RuntimeError:
            reasons.append(_reason(
                UnsupportedRecurrenceCode.ORDINAL_BYDAY,
                "Порядковые дни BYDAY (например 2MO или -1FR) пока не поддерживаются.",
                line, "BYDAY",
            ))
        except ValueError as exc:
            reasons.append(_reason(
                UnsupportedRecurrenceCode.INVALID_VALUE, str(exc), line, "BYDAY"
            ))
    for name in ("BYMONTHDAY", "BYMONTH"):
        if name in properties:
            try:
                parsed = _parse_int_list(properties[name], name)
                if name == "BYMONTHDAY":
                    bymonthday = parsed
                else:
                    bymonth = parsed
            except ValueError as exc:
                reasons.append(_reason(
                    UnsupportedRecurrenceCode.INVALID_INTEGER, str(exc), line, name
                ))
    if "WKST" in properties:
        try:
            wkst = GoogleWeekday(properties["WKST"])
        except ValueError:
            reasons.append(_reason(
                UnsupportedRecurrenceCode.INVALID_VALUE,
                f"Неизвестный день начала недели WKST={properties['WKST']}.", line, "WKST",
            ))

    return ParsedGoogleRule(
        raw_line=line,
        frequency=frequency,
        interval=interval,
        byday=byday,
        bymonthday=bymonthday,
        bymonth=bymonth,
        count=count,
        until=until,
        wkst=wkst,
        properties=tuple(ordered_properties),
    ), reasons


def _until_to_planner_date(
    until: GoogleUntilValue, schedule: Optional[SeriesSchedule]
) -> date:
    if until.kind is GoogleUntilKind.DATE:
        if schedule is not None and not schedule.all_day:
            raise ValueError(
                "UNTIL в форме даты не совпадает с формой DTSTART timed-серии."
            )
        return until.date_value  # type: ignore[return-value]
    if until.kind is not GoogleUntilKind.UTC_DATETIME:
        raise ValueError("UNTIL date-time без UTC нельзя перенести без потерь.")
    if schedule is None or schedule.all_day or schedule.local_time is None:
        raise ValueError(
            "Для UTC UNTIL нужны время и часовой пояс timed-серии."
        )
    instant = until.datetime_value
    try:
        zone = ZoneInfo(schedule.timezone_name)
    except Exception as exc:
        raise ValueError(f"Неизвестный часовой пояс: {schedule.timezone_name}.") from exc
    local = instant.astimezone(zone)  # type: ignore[union-attr]
    if local.replace(tzinfo=None).time() != schedule.local_time:
        raise ValueError(
            "UTC UNTIL не совпадает со временем экземпляра; дата Planner была бы неточной."
        )
    expected = resolve_wall_clock(
        datetime.combine(local.date(), schedule.local_time), schedule.timezone_name
    ).astimezone(timezone.utc)
    if expected != instant.astimezone(timezone.utc):  # type: ignore[union-attr]
        raise ValueError("UTC UNTIL неоднозначен относительно локального расписания.")
    return local.date()


def google_rrule_to_planner_rule(
    parsed: ParsedGoogleRule,
    *,
    schedule: Optional[SeriesSchedule] = None,
) -> RecurrenceRule:
    """Convert only the lossless Phase 3.2A subset; otherwise raise ValueError."""
    frequency = parsed.frequency
    if frequency is None:
        raise ValueError("RRULE не содержит поддерживаемую частоту.")
    if parsed.interval < 1 or parsed.interval > MAX_INTERVAL:
        raise ValueError("INTERVAL не представим в Planner.")
    if parsed.count is not None and parsed.until is not None:
        raise ValueError("COUNT и UNTIL нельзя использовать одновременно.")
    if parsed.wkst is not None and frequency is RecurrenceFrequency.WEEKLY:
        if parsed.interval > 1 and parsed.wkst is not GoogleWeekday.MO:
            raise ValueError(
                "WKST меняет границы многонедельного интервала Planner."
            )

    kwargs: dict[str, object] = {
        "frequency": frequency,
        "interval": parsed.interval,
    }
    if frequency is RecurrenceFrequency.DAILY:
        if parsed.byday or parsed.bymonthday or parsed.bymonth:
            raise ValueError("DAILY поддерживается без BYDAY/BYMONTHDAY/BYMONTH.")
    elif frequency is RecurrenceFrequency.WEEKLY:
        if parsed.bymonthday or parsed.bymonth:
            raise ValueError("WEEKLY не поддерживает BYMONTHDAY/BYMONTH в Planner.")
        days = parsed.byday
        if not days and schedule is not None:
            days = (GoogleWeekday.from_planner_index(schedule.start_date.weekday()),)
        if not days:
            raise ValueError("Для WEEKLY нужен BYDAY или дата начала серии.")
        kwargs["weekdays"] = tuple(sorted(day.planner_index for day in days))
    elif frequency is RecurrenceFrequency.MONTHLY:
        if parsed.byday or parsed.bymonth:
            raise ValueError("MONTHLY поддерживается только с одним BYMONTHDAY.")
        values = parsed.bymonthday
        if not values and schedule is not None:
            values = (schedule.start_date.day,)
        if len(values) != 1 or not 1 <= values[0] <= 31:
            raise ValueError("MONTHLY требует один положительный BYMONTHDAY 1..31.")
        kwargs["month_day"] = values[0]
    elif frequency is RecurrenceFrequency.YEARLY:
        if parsed.byday:
            raise ValueError("YEARLY с BYDAY пока не поддерживается.")
        months, days = parsed.bymonth, parsed.bymonthday
        if schedule is not None:
            months = months or (schedule.start_date.month,)
            days = days or (schedule.start_date.day,)
        if len(months) != 1 or len(days) != 1:
            raise ValueError("YEARLY требует по одному BYMONTH и BYMONTHDAY.")
        try:
            date(2000 if months[0] == 2 and days[0] == 29 else 2001,
                 months[0], days[0])
        except ValueError as exc:
            raise ValueError("YEARLY содержит недопустимую дату.") from exc
        kwargs["yearly_month"] = months[0]
        kwargs["yearly_day"] = days[0]

    if parsed.count is not None:
        if parsed.count > MAX_OCCURRENCE_COUNT:
            raise ValueError("COUNT не представим в Planner.")
        kwargs["end_mode"] = RecurrenceEndMode.COUNT
        kwargs["occurrence_count"] = parsed.count
    elif parsed.until is not None:
        kwargs["end_mode"] = RecurrenceEndMode.UNTIL
        kwargs["until_date"] = _until_to_planner_date(parsed.until, schedule)
    return RecurrenceRule(**kwargs)


def _canonical_parsed_rrule(parsed: ParsedGoogleRule) -> str:
    if parsed.frequency is None:
        raise ValueError("Нельзя сериализовать RRULE без FREQ.")
    properties = [
        ("FREQ", parsed.frequency.value.upper()),
        ("INTERVAL", str(parsed.interval)),
    ]
    if parsed.byday:
        properties.append(("BYDAY", ",".join(
            day.value for day in sorted(parsed.byday, key=lambda item: item.planner_index)
        )))
    if parsed.bymonthday:
        properties.append(("BYMONTHDAY", ",".join(map(str, parsed.bymonthday))))
    if parsed.bymonth:
        properties.append(("BYMONTH", ",".join(map(str, parsed.bymonth))))
    if parsed.count is not None:
        properties.append(("COUNT", str(parsed.count)))
    if parsed.until is not None:
        properties.append(("UNTIL", parsed.until.raw.upper()))
    if parsed.wkst is not None:
        properties.append(("WKST", parsed.wkst.value))
    return "RRULE:" + ";".join(f"{name}={value}" for name, value in properties)


def canonicalize_rrule_line(line: str) -> str:
    """Canonical serialization of one RRULE line for content comparison.

    Google normalizes stored RRULEs (for example it drops ``INTERVAL=1``),
    so byte-exact comparison of a written line against the returned one is
    wrong.  Parsing and re-serializing both sides yields one canonical form
    for the supported subset.  Non-RRULE, unparseable or unsupported lines
    are returned unchanged — a failed canonicalization must degrade to a
    mismatch, never to a silent success.
    """
    raw = str(line)
    if not raw.upper().startswith("RRULE"):
        return raw
    parsed, reasons = _parse_rrule_line(raw)
    if parsed is None or reasons:
        return raw
    try:
        return _canonical_parsed_rrule(parsed)
    except ValueError:
        return raw


def parse_google_recurrence(
    lines: Iterable[str],
    *,
    schedule: Optional[SeriesSchedule] = None,
) -> GoogleRecurrenceParseResult:
    """Parse Calendar recurrence lines while preserving the exact input."""
    raw_lines = tuple(str(line) for line in lines)
    rrules = []
    exdates = []
    rdates = []
    other = []
    reasons: list[UnsupportedRecurrenceReason] = []

    for raw_line in raw_lines:
        if ":" not in raw_line:
            other.append(raw_line)
            reasons.append(_reason(
                UnsupportedRecurrenceCode.INVALID_LINE,
                "Строка повторения не содержит двоеточие.", raw_line,
            ))
            continue
        property_and_params, value_text = raw_line.split(":", 1)
        property_name = property_and_params.split(";", 1)[0].upper()
        if property_name == "RRULE":
            rrules.append(raw_line)
        elif property_name in ("EXDATE", "RDATE"):
            try:
                parsed_values = _parse_date_values_line(
                    raw_line, property_and_params, value_text
                )
                (exdates if property_name == "EXDATE" else rdates).append(parsed_values)
            except ValueError as exc:
                other.append(raw_line)
                reasons.append(_reason(
                    UnsupportedRecurrenceCode.INVALID_VALUE, str(exc), raw_line,
                    property_name,
                ))
        elif property_name == "EXRULE":
            other.append(raw_line)
            reasons.append(_reason(
                UnsupportedRecurrenceCode.EXRULE,
                "EXRULE пока не поддерживается; исходная строка сохранена.", raw_line,
                "EXRULE",
            ))
        else:
            other.append(raw_line)
            reasons.append(_reason(
                UnsupportedRecurrenceCode.UNKNOWN_LINE,
                f"Свойство повторения {property_name} пока не поддерживается.",
                raw_line, property_name,
            ))

    parsed_rule = None
    planner_rule = None
    canonical = None
    if not rrules:
        reasons.append(_reason(
            UnsupportedRecurrenceCode.MISSING_RRULE,
            "В наборе повторения отсутствует RRULE.",
        ))
    elif len(rrules) > 1:
        reasons.append(_reason(
            UnsupportedRecurrenceCode.MULTIPLE_RRULE,
            "Несколько RRULE в одной серии пока не поддерживаются.",
            "\n".join(rrules), "RRULE",
        ))
    else:
        parsed_rule, parse_reasons = _parse_rrule_line(rrules[0])
        reasons.extend(parse_reasons)
        if parsed_rule is not None and not parse_reasons:
            try:
                planner_rule = google_rrule_to_planner_rule(
                    parsed_rule, schedule=schedule
                )
                canonical = _canonical_parsed_rrule(parsed_rule)
            except ValueError as exc:
                reasons.append(_reason(
                    UnsupportedRecurrenceCode.UNSUPPORTED_COMBINATION,
                    str(exc), rrules[0], "RRULE",
                ))

    recurrence_set = GoogleRecurrenceSet(
        raw_lines=raw_lines,
        rrule_lines=tuple(rrules),
        exdates=tuple(exdates),
        rdates=tuple(rdates),
        other_lines=tuple(other),
    )
    support = (GoogleRecurrenceSupport.SUPPORTED
               if not reasons and planner_rule is not None
               else GoogleRecurrenceSupport.UNSUPPORTED)
    return GoogleRecurrenceParseResult(
        recurrence_set=recurrence_set,
        support=support,
        parsed_rule=parsed_rule,
        planner_rule=planner_rule,
        reasons=tuple(reasons),
        canonical_rrule=canonical,
    )


def planner_rule_to_google_rrule(
    rule: RecurrenceRule,
    *,
    schedule: Optional[SeriesSchedule] = None,
) -> str:
    """Canonical deterministic RRULE for the Planner-supported subset."""
    if rule.interval < 1 or rule.interval > MAX_INTERVAL:
        raise ValueError("INTERVAL не представим в Google RRULE Planner.")
    properties = [
        ("FREQ", rule.frequency.value.upper()),
        ("INTERVAL", str(rule.interval)),
    ]
    if rule.frequency is RecurrenceFrequency.WEEKLY:
        if not rule.weekdays:
            raise ValueError("Weekly Planner rule requires weekdays.")
        days = tuple(sorted(set(int(day) for day in rule.weekdays)))
        if any(day < 0 or day > 6 for day in days):
            raise ValueError("Invalid Planner weekday.")
        properties.append(("BYDAY", ",".join(
            GoogleWeekday.from_planner_index(day).value for day in days
        )))
    elif rule.frequency is RecurrenceFrequency.MONTHLY:
        if rule.month_day is None or not 1 <= rule.month_day <= 31:
            raise ValueError("Monthly Planner rule requires month_day 1..31.")
        properties.append(("BYMONTHDAY", str(rule.month_day)))
    elif rule.frequency is RecurrenceFrequency.YEARLY:
        if rule.yearly_month is None or rule.yearly_day is None:
            raise ValueError("Yearly Planner rule requires month and day.")
        properties.extend((
            ("BYMONTHDAY", str(rule.yearly_day)),
            ("BYMONTH", str(rule.yearly_month)),
        ))

    if rule.end_mode is RecurrenceEndMode.COUNT:
        if (rule.occurrence_count is None or rule.occurrence_count < 1
                or rule.occurrence_count > MAX_OCCURRENCE_COUNT):
            raise ValueError("COUNT не представим в Google RRULE Planner.")
        properties.append(("COUNT", str(rule.occurrence_count)))
    elif rule.end_mode is RecurrenceEndMode.UNTIL:
        if rule.until_date is None:
            raise ValueError("UNTIL rule requires until_date.")
        if schedule is not None and not schedule.all_day:
            if schedule.local_time is None:
                raise ValueError("Timed UNTIL serialization requires local_time.")
            instant = resolve_wall_clock(
                datetime.combine(rule.until_date, schedule.local_time),
                schedule.timezone_name,
            ).astimezone(timezone.utc)
            value = instant.strftime("%Y%m%dT%H%M%SZ")
        else:
            value = rule.until_date.strftime("%Y%m%d")
        properties.append(("UNTIL", value))
    return "RRULE:" + ";".join(f"{name}={value}" for name, value in properties)


def recurrence_to_google_lines(
    rule: RecurrenceRule,
    *,
    schedule: Optional[SeriesSchedule] = None,
    extra_lines: Sequence[str] = (),
) -> Tuple[str, ...]:
    """Future-write helper. B1 production gateways intentionally do not call it."""
    return (planner_rule_to_google_rrule(rule, schedule=schedule), *tuple(extra_lines))


def recurrence_round_trip_support(
    rule: RecurrenceRule, *, schedule: Optional[SeriesSchedule] = None
) -> GoogleRecurrenceParseResult:
    return parse_google_recurrence(
        recurrence_to_google_lines(rule, schedule=schedule), schedule=schedule
    )


def readable_google_recurrence_summary(
    result: GoogleRecurrenceParseResult,
    *,
    schedule: Optional[SeriesSchedule] = None,
) -> str:
    if not result.supported or result.planner_rule is None:
        return result.readable_reason or "Правило повторения не поддерживается."
    display_schedule = schedule or SeriesSchedule(
        start_date=date(2000, 1, 1), all_day=True, timezone_name="UTC"
    )
    return describe_rule(result.planner_rule, display_schedule)


# Clear aliases for callers/tests that prefer transport-oriented names.
parse_recurrence_lines = parse_google_recurrence
serialize_google_rrule = planner_rule_to_google_rrule
supported_google_rrule_to_planner_rule = google_rrule_to_planner_rule


__all__ = [
    "GoogleRecurrenceDateValues",
    "GoogleRecurrenceParseResult",
    "GoogleRecurrenceSet",
    "GoogleRecurrenceSupport",
    "GoogleUntilKind",
    "GoogleUntilValue",
    "GoogleWeekday",
    "ParsedGoogleRule",
    "UnsupportedRecurrenceCode",
    "UnsupportedRecurrenceReason",
    "canonicalize_rrule_line",
    "google_rrule_to_planner_rule",
    "parse_google_recurrence",
    "parse_recurrence_lines",
    "planner_rule_to_google_rrule",
    "readable_google_recurrence_summary",
    "recurrence_round_trip_support",
    "recurrence_to_google_lines",
    "serialize_google_rrule",
    "supported_google_rrule_to_planner_rule",
]
