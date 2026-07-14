"""Pure calendar time-grid geometry for Planner Desktop.

The module deliberately knows nothing about Qt, pixels, repositories, or sync.
It accepts Task-like objects (attributes or mappings) and returns normalized
geometry which QML can scale to any viewport height.

All-day events are placed in a separate lane.  Timed events are split at day
boundaries, clipped to the visible time window, grouped by interval overlap,
and assigned deterministic side-by-side columns.  Half-open intervals are
used throughout, so an event ending at 10:00 does not overlap one starting at
10:00.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
from typing import Any, Iterable, Mapping, Optional, Sequence, Tuple


MINUTES_PER_DAY = 24 * 60


@dataclass(frozen=True, slots=True)
class CalendarGridConfig:
    """Height-independent configuration for a time grid."""

    visible_start_hour: int = 6
    visible_end_hour: int = 23
    minimum_visual_minutes: int = 15

    def __post_init__(self) -> None:
        if not 0 <= self.visible_start_hour < 24:
            raise ValueError("visible_start_hour must be between 0 and 23")
        if not 1 <= self.visible_end_hour <= 24:
            raise ValueError("visible_end_hour must be between 1 and 24")
        if self.visible_end_hour <= self.visible_start_hour:
            raise ValueError("visible_end_hour must be after visible_start_hour")
        if self.minimum_visual_minutes <= 0:
            raise ValueError("minimum_visual_minutes must be positive")

    @property
    def visible_start_minute(self) -> int:
        return self.visible_start_hour * 60

    @property
    def visible_end_minute(self) -> int:
        return self.visible_end_hour * 60

    @property
    def visible_minutes(self) -> int:
        return self.visible_end_minute - self.visible_start_minute


@dataclass(frozen=True, slots=True)
class CalendarEventBlock:
    """One display block for one event on one visible day.

    ``start_minute`` and ``end_minute`` are the clipped display interval,
    expressed as minutes after local midnight.  A cross-midnight event can
    therefore produce more than one block with the same ``task_uid``.
    """

    task_uid: str
    task_id: Any
    day: date
    day_index: int
    start_minute: int
    end_minute: int
    top_ratio: float
    height_ratio: float
    overlap_column_index: int = 0
    overlap_column_count: int = 1
    clipped_at_start: bool = False
    clipped_at_end: bool = False
    all_day: bool = False

    @property
    def uid(self) -> str:
        """Compatibility alias convenient for callers and tests."""
        return self.task_uid

    @property
    def duration_minutes(self) -> int:
        return self.end_minute - self.start_minute

    @property
    def column_index(self) -> int:
        return self.overlap_column_index

    @property
    def column_count(self) -> int:
        return self.overlap_column_count


@dataclass(frozen=True, slots=True)
class OverlapGroup:
    """A connected group of overlapping half-open timed intervals."""

    day_index: int
    start_minute: int
    end_minute: int
    column_count: int
    blocks: Tuple[CalendarEventBlock, ...]


@dataclass(frozen=True, slots=True)
class CalendarDayColumn:
    """All display data belonging to a single visible date."""

    day: date
    day_index: int
    timed_blocks: Tuple[CalendarEventBlock, ...] = ()
    all_day_blocks: Tuple[CalendarEventBlock, ...] = ()
    overlap_groups: Tuple[OverlapGroup, ...] = ()

    @property
    def events(self) -> Tuple[CalendarEventBlock, ...]:
        return self.timed_blocks

    @property
    def all_day_events(self) -> Tuple[CalendarEventBlock, ...]:
        return self.all_day_blocks


@dataclass(frozen=True, slots=True)
class CalendarLayout:
    """Complete normalized layout for a day, work week, or week."""

    config: CalendarGridConfig
    dates: Tuple[date, ...]
    day_columns: Tuple[CalendarDayColumn, ...]

    @property
    def timed_blocks(self) -> Tuple[CalendarEventBlock, ...]:
        return tuple(
            block for column in self.day_columns for block in column.timed_blocks
        )

    @property
    def all_day_blocks(self) -> Tuple[CalendarEventBlock, ...]:
        return tuple(
            block for column in self.day_columns
            for block in column.all_day_blocks
        )


def _value(event: Any, name: str, default: Any = None) -> Any:
    if isinstance(event, Mapping):
        return event.get(name, default)
    return getattr(event, name, default)


def _uid(event: Any) -> str:
    value = _value(event, "uid")
    if value in (None, ""):
        value = _value(event, "id", "")
    return str(value)


def _positive_duration(value: Any) -> Optional[int]:
    try:
        minutes = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return minutes if minutes > 0 else None


def _comparable_end(start: datetime, end: datetime) -> datetime:
    """Make common naive/aware combinations deterministic.

    Planner normally stores homogeneous datetimes.  This small normalization
    keeps the pure layout function total for imported or hand-built fixtures.
    """
    if start.tzinfo is None and end.tzinfo is not None:
        return end.replace(tzinfo=None)
    if start.tzinfo is not None and end.tzinfo is None:
        return end.replace(tzinfo=start.tzinfo)
    if start.tzinfo is not None and end.tzinfo is not None:
        return end.astimezone(start.tzinfo)
    return end


def _effective_end(event: Any, start: datetime,
                   config: CalendarGridConfig) -> datetime:
    explicit = _value(event, "end")
    if isinstance(explicit, datetime):
        explicit = _comparable_end(start, explicit)
        if explicit > start:
            return explicit
    duration = _positive_duration(_value(event, "duration_minutes"))
    return start + timedelta(
        minutes=duration or config.minimum_visual_minutes
    )


def _midnight(day: date, like: datetime) -> datetime:
    return datetime.combine(day, time.min, tzinfo=like.tzinfo)


def _timed_block(event: Any, day: date, day_index: int,
                 config: CalendarGridConfig) -> Optional[CalendarEventBlock]:
    start = _value(event, "start")
    if not isinstance(start, datetime):
        return None
    end = _effective_end(event, start, config)
    day_start = _midnight(day, start)
    day_end = day_start + timedelta(days=1)

    # Half-open interval intersection with this calendar date.
    if start >= day_end or end <= day_start:
        return None
    segment_start = max(start, day_start)
    segment_end = min(end, day_end)
    visible_start = day_start + timedelta(
        minutes=config.visible_start_minute)
    visible_end = day_start + timedelta(minutes=config.visible_end_minute)
    display_start = max(segment_start, visible_start)
    display_end = min(segment_end, visible_end)
    if display_end <= display_start:
        return None

    start_minute = int((display_start - day_start).total_seconds() // 60)
    end_minute = int((display_end - day_start).total_seconds() // 60)
    visible_minutes = config.visible_minutes
    top = (start_minute - config.visible_start_minute) / visible_minutes
    height = (end_minute - start_minute) / visible_minutes

    return CalendarEventBlock(
        task_uid=_uid(event),
        task_id=_value(event, "id"),
        day=day,
        day_index=day_index,
        start_minute=start_minute,
        end_minute=end_minute,
        top_ratio=top,
        height_ratio=height,
        clipped_at_start=(start < day_start or segment_start < visible_start),
        clipped_at_end=(end > day_end or segment_end > visible_end),
        all_day=False,
    )


def _all_day_span(event: Any) -> Optional[tuple[date, date]]:
    start = _value(event, "start")
    if not isinstance(start, datetime):
        return None
    first = start.date()
    end = _value(event, "end")
    exclusive = end.date() if isinstance(end, datetime) else first + timedelta(days=1)
    if exclusive <= first:
        exclusive = first + timedelta(days=1)
    return first, exclusive


def _all_day_block(event: Any, day: date,
                   day_index: int) -> CalendarEventBlock:
    return CalendarEventBlock(
        task_uid=_uid(event),
        task_id=_value(event, "id"),
        day=day,
        day_index=day_index,
        start_minute=0,
        end_minute=MINUTES_PER_DAY,
        top_ratio=0.0,
        height_ratio=1.0,
        all_day=True,
    )


def _block_sort_key(block: CalendarEventBlock) -> tuple[int, int, str]:
    # Literal stable ordering required by the UI contract: start, duration, uid.
    return (block.start_minute, block.duration_minutes, block.task_uid)


def _layout_overlap_groups(
    blocks: Sequence[CalendarEventBlock], day_index: int
) -> tuple[tuple[CalendarEventBlock, ...], tuple[OverlapGroup, ...]]:
    ordered = sorted(blocks, key=_block_sort_key)
    raw_groups: list[list[CalendarEventBlock]] = []
    current: list[CalendarEventBlock] = []
    current_end = -1
    for block in ordered:
        # ``==`` starts a new group: touching intervals do not overlap.
        if current and block.start_minute >= current_end:
            raw_groups.append(current)
            current = []
            current_end = -1
        current.append(block)
        current_end = max(current_end, block.end_minute)
    if current:
        raw_groups.append(current)

    laid_out: list[CalendarEventBlock] = []
    groups: list[OverlapGroup] = []
    for raw in raw_groups:
        column_ends: list[int] = []
        assignments: list[tuple[CalendarEventBlock, int]] = []
        for block in raw:
            column = next(
                (index for index, last_end in enumerate(column_ends)
                 if last_end <= block.start_minute),
                len(column_ends),
            )
            if column == len(column_ends):
                column_ends.append(block.end_minute)
            else:
                column_ends[column] = block.end_minute
            assignments.append((block, column))

        column_count = max(1, len(column_ends))
        group_blocks = tuple(
            replace(
                block,
                overlap_column_index=column,
                overlap_column_count=column_count,
            )
            for block, column in assignments
        )
        laid_out.extend(group_blocks)
        groups.append(OverlapGroup(
            day_index=day_index,
            start_minute=min(block.start_minute for block in group_blocks),
            end_minute=max(block.end_minute for block in group_blocks),
            column_count=column_count,
            blocks=group_blocks,
        ))
    return tuple(laid_out), tuple(groups)


def layout_calendar_events(
    events: Iterable[Any],
    visible_dates: Sequence[date],
    config: CalendarGridConfig | None = None,
) -> CalendarLayout:
    """Calculate a normalized time-grid layout for ``visible_dates``.

    Input ordering never affects the result.  Dates retain the caller's order,
    which lets the same function serve Day, Work week, and Week modes.
    """
    config = config or CalendarGridConfig()
    dates = tuple(visible_dates)
    if len(set(dates)) != len(dates):
        raise ValueError("visible_dates must not contain duplicates")

    event_list = list(events)
    timed_by_day: list[list[CalendarEventBlock]] = [list() for _ in dates]
    all_day_by_day: list[list[tuple[tuple[Any, ...], CalendarEventBlock]]] = [
        list() for _ in dates
    ]

    for event in event_list:
        if bool(_value(event, "is_all_day", False)):
            span = _all_day_span(event)
            if span is None:
                continue
            first, exclusive = span
            span_days = (exclusive - first).days
            for index, day in enumerate(dates):
                if first <= day < exclusive:
                    key = (first, -span_days, _uid(event))
                    all_day_by_day[index].append(
                        (key, _all_day_block(event, day, index)))
            continue

        for index, day in enumerate(dates):
            block = _timed_block(event, day, index, config)
            if block is not None:
                timed_by_day[index].append(block)

    columns: list[CalendarDayColumn] = []
    for index, day in enumerate(dates):
        timed, groups = _layout_overlap_groups(timed_by_day[index], index)
        all_day = tuple(
            block for _key, block in sorted(
                all_day_by_day[index], key=lambda item: item[0])
        )
        columns.append(CalendarDayColumn(
            day=day,
            day_index=index,
            timed_blocks=timed,
            all_day_blocks=all_day,
            overlap_groups=groups,
        ))
    return CalendarLayout(config=config, dates=dates, day_columns=tuple(columns))


def layout_day_events(
    events: Iterable[Any], day: date,
    config: CalendarGridConfig | None = None,
) -> CalendarDayColumn:
    """Convenience wrapper for a one-day layout."""
    return layout_calendar_events(events, (day,), config).day_columns[0]


# Descriptive aliases keep the public surface friendly for callers.
calculate_calendar_layout = layout_calendar_events
layout_events = layout_calendar_events


__all__ = [
    "CalendarDayColumn",
    "CalendarEventBlock",
    "CalendarGridConfig",
    "CalendarLayout",
    "OverlapGroup",
    "calculate_calendar_layout",
    "layout_calendar_events",
    "layout_day_events",
    "layout_events",
]
