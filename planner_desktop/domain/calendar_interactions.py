"""Pure Calendar drag, drop and resize proposals.

The module intentionally knows nothing about Qt, repositories or Google.  Pointer
coordinates are converted to dates/minutes here and every unsupported operation
is represented by a rejected proposal instead of an exception.

Snapping is deterministic "half up": the midpoint belongs to the later slot.
Normal interaction uses 15 minute slots; holding Shift selects 5 minute slots.
Timed drops are clamped so the complete event stays inside the visible grid.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from enum import Enum
from typing import Optional, Sequence

from planner_desktop.domain.commands import MAX_DURATION_MINUTES
from planner_desktop.domain.task import Task


DEFAULT_SNAP_MINUTES = 15
FINE_SNAP_MINUTES = 5
MIN_TIMED_DURATION_MINUTES = 15
DEFAULT_TIMED_DURATION_MINUTES = 60
DEFAULT_VISIBLE_START_MINUTE = 6 * 60
DEFAULT_VISIBLE_END_MINUTE = 23 * 60

RECURRING_INTERACTION_ERROR = (
    "Перенос экземпляров повторяющихся событий пока не поддерживается"
)
SERIES_INTERACTION_ERROR = (
    "Измените экземпляр серии через редактор и выберите область изменений"
)


class DropZoneKind(str, Enum):
    TIMED_GRID = "timed_grid"
    ALL_DAY_LANE = "all_day_lane"
    UNDATED_PANEL = "undated_panel"


class ResizeEdge(str, Enum):
    START = "start"
    END = "end"


@dataclass(frozen=True)
class InteractionValidationResult:
    valid: bool
    reason: str = ""
    code: str = ""

    @classmethod
    def accept(cls) -> "InteractionValidationResult":
        return cls(True)

    @classmethod
    def reject(cls, reason: str, code: str) -> "InteractionValidationResult":
        return cls(False, reason, code)


@dataclass(frozen=True)
class CalendarDropTarget:
    kind: DropZoneKind
    target_date: Optional[date] = None
    minute_of_day: Optional[int] = None
    visible_start_minute: int = DEFAULT_VISIBLE_START_MINUTE
    visible_end_minute: int = DEFAULT_VISIBLE_END_MINUTE


@dataclass(frozen=True)
class CalendarDragProposal:
    task_uid: str
    source_kind: DropZoneKind
    target: CalendarDropTarget
    proposed_start: Optional[datetime]
    proposed_end: Optional[datetime]
    proposed_duration_minutes: Optional[int]
    proposed_all_day: bool
    validation: InteractionValidationResult
    changed: bool = False

    @property
    def valid(self) -> bool:
        return self.validation.valid

    @property
    def message(self) -> str:
        return self.validation.reason


@dataclass(frozen=True)
class CalendarResizeProposal:
    task_uid: str
    edge: ResizeEdge
    proposed_start: Optional[datetime]
    proposed_end: Optional[datetime]
    proposed_duration_minutes: Optional[int]
    validation: InteractionValidationResult
    changed: bool = False

    @property
    def valid(self) -> bool:
        return self.validation.valid

    @property
    def message(self) -> str:
        return self.validation.reason


def snapping_increment(*, shift: bool = False) -> int:
    return FINE_SNAP_MINUTES if shift else DEFAULT_SNAP_MINUTES


def snap_minute(
    minute: float,
    *,
    shift: bool = False,
    lower: int = 0,
    upper: int = 24 * 60,
) -> int:
    """Snap a minute value half-up, then clamp it to inclusive bounds."""
    if upper < lower:
        lower, upper = upper, lower
    increment = snapping_increment(shift=shift)
    rounded = int((float(minute) + increment / 2) // increment) * increment
    return max(lower, min(upper, rounded))


def minute_from_mouse_y(
    y: float,
    height: float,
    *,
    visible_start_minute: int = DEFAULT_VISIBLE_START_MINUTE,
    visible_end_minute: int = DEFAULT_VISIBLE_END_MINUTE,
    shift: bool = False,
) -> int:
    """Map a vertical pointer coordinate to a snapped visible-grid minute."""
    if height <= 0 or visible_end_minute <= visible_start_minute:
        return visible_start_minute
    ratio = max(0.0, min(1.0, float(y) / float(height)))
    raw = visible_start_minute + ratio * (
        visible_end_minute - visible_start_minute
    )
    return snap_minute(
        raw,
        shift=shift,
        lower=visible_start_minute,
        upper=visible_end_minute,
    )


def target_from_mouse(
    x: float,
    y: float,
    width: float,
    height: float,
    visible_dates: Sequence[date],
    *,
    kind: DropZoneKind = DropZoneKind.TIMED_GRID,
    shift: bool = False,
    visible_start_minute: int = DEFAULT_VISIBLE_START_MINUTE,
    visible_end_minute: int = DEFAULT_VISIBLE_END_MINUTE,
) -> CalendarDropTarget:
    """Return the clamped day and minute under a pointer.

    Empty date ranges produce a target with no date; validation later returns a
    structured rejection.  All-day and undated zones intentionally carry no
    minute value.
    """
    target_date: Optional[date] = None
    if visible_dates:
        if width <= 0:
            day_index = 0
        else:
            clamped_x = max(0.0, min(float(width), float(x)))
            day_index = min(
                len(visible_dates) - 1,
                int(clamped_x / float(width) * len(visible_dates)),
            )
        target_date = visible_dates[day_index]
    minute = None
    if kind == DropZoneKind.TIMED_GRID:
        minute = minute_from_mouse_y(
            y,
            height,
            visible_start_minute=visible_start_minute,
            visible_end_minute=visible_end_minute,
            shift=shift,
        )
    return CalendarDropTarget(
        kind=kind,
        target_date=target_date,
        minute_of_day=minute,
        visible_start_minute=visible_start_minute,
        visible_end_minute=visible_end_minute,
    )


def _source_kind(task: Task) -> DropZoneKind:
    if task.start is None:
        return DropZoneKind.UNDATED_PANEL
    if task.is_all_day:
        return DropZoneKind.ALL_DAY_LANE
    return DropZoneKind.TIMED_GRID


def _is_recurring_instance(task: Task) -> bool:
    return bool(
        task.google_calendar_recurring_event_id
        or task.google_calendar_original_start
    )


def _timed_duration(task: Task) -> int:
    if task.start is not None and task.end is not None and task.end > task.start:
        return int((task.end - task.start).total_seconds() // 60)
    if task.duration_minutes is not None and task.duration_minutes > 0:
        return int(task.duration_minutes)
    return DEFAULT_TIMED_DURATION_MINUTES


def _all_day_span(task: Task) -> int:
    if task.start is None:
        return 1
    if task.end is None:
        return 1
    return max(1, (task.end.date() - task.start.date()).days)


def _at_minute(day: date, minute: int, tzinfo=None) -> datetime:
    return datetime.combine(day, time.min, tzinfo=tzinfo) + timedelta(minutes=minute)


def _drag_rejection(
    task: Task,
    target: CalendarDropTarget,
    reason: str,
    code: str,
) -> CalendarDragProposal:
    return CalendarDragProposal(
        task_uid=task.uid,
        source_kind=_source_kind(task),
        target=target,
        proposed_start=task.start,
        proposed_end=task.end,
        proposed_duration_minutes=task.duration_minutes,
        proposed_all_day=task.is_all_day,
        validation=InteractionValidationResult.reject(reason, code),
        changed=False,
    )


def validate_drop_target(target: CalendarDropTarget) -> InteractionValidationResult:
    if target.kind == DropZoneKind.UNDATED_PANEL:
        return InteractionValidationResult.accept()
    if target.target_date is None:
        return InteractionValidationResult.reject(
            "Не удалось определить дату назначения", "missing_date"
        )
    if target.kind == DropZoneKind.TIMED_GRID:
        if target.minute_of_day is None:
            return InteractionValidationResult.reject(
                "Не удалось определить время назначения", "missing_time"
            )
        if target.visible_end_minute <= target.visible_start_minute:
            return InteractionValidationResult.reject(
                "Некорректные границы календарной сетки", "invalid_grid"
            )
    return InteractionValidationResult.accept()


def propose_drag(
    task: Task,
    target: CalendarDropTarget,
    *,
    default_timed_duration: int = DEFAULT_TIMED_DURATION_MINUTES,
    minimum_timed_duration: int = MIN_TIMED_DURATION_MINUTES,
    maximum_timed_duration: int = MAX_DURATION_MINUTES,
) -> CalendarDragProposal:
    """Calculate a move/conversion/schedule proposal without mutating ``task``."""
    target_validation = validate_drop_target(target)
    if not target_validation.valid:
        return _drag_rejection(
            task, target, target_validation.reason, target_validation.code
        )
    if _is_recurring_instance(task):
        return _drag_rejection(
            task, target, RECURRING_INTERACTION_ERROR, "recurring_instance"
        )
    if task.series_uid is not None:
        # Прямой drag экземпляра локальной серии запрещён в Phase 3.2A:
        # область изменений («только этот» / «этот и все будущие»)
        # выбирается только явно в редакторе.
        return _drag_rejection(
            task, target, SERIES_INTERACTION_ERROR, "local_series_occurrence"
        )

    source = _source_kind(task)
    tzinfo = task.start.tzinfo if task.start is not None else None

    if target.kind == DropZoneKind.UNDATED_PANEL:
        if task.start is None:
            start = end = None
            duration = None
            all_day = False
        else:
            start = end = None
            duration = None
            all_day = False
        return CalendarDragProposal(
            task.uid, source, target, start, end, duration, all_day,
            InteractionValidationResult.accept(), changed=task.start is not None,
        )

    assert target.target_date is not None  # validated above
    if target.kind == DropZoneKind.ALL_DAY_LANE:
        span = _all_day_span(task) if task.is_all_day else 1
        start = datetime.combine(target.target_date, time.min, tzinfo=tzinfo)
        end = start + timedelta(days=span)
        changed = not (
            task.is_all_day and task.start == start and task.end == end
        )
        return CalendarDragProposal(
            task.uid, source, target, start, end, None, True,
            InteractionValidationResult.accept(), changed=changed,
        )

    duration = (
        default_timed_duration if task.is_all_day or task.start is None
        else _timed_duration(task)
    )
    if duration < minimum_timed_duration:
        duration = minimum_timed_duration
    if duration > maximum_timed_duration:
        return _drag_rejection(
            task,
            target,
            f"Длительность не может превышать {maximum_timed_duration} минут",
            "duration_too_long",
        )
    grid_span = target.visible_end_minute - target.visible_start_minute
    if duration > grid_span:
        return _drag_rejection(
            task,
            target,
            "Событие не помещается в видимый диапазон календаря",
            "outside_grid",
        )
    assert target.minute_of_day is not None
    start_minute = max(
        target.visible_start_minute,
        min(target.visible_end_minute - duration, target.minute_of_day),
    )
    start = _at_minute(target.target_date, start_minute, tzinfo)
    end = start + timedelta(minutes=duration)
    changed = not (
        not task.is_all_day
        and task.start == start
        and task.end == end
        and task.duration_minutes == duration
    )
    return CalendarDragProposal(
        task.uid, source, target, start, end, duration, False,
        InteractionValidationResult.accept(), changed=changed,
    )


def _resize_rejection(
    task: Task,
    edge: ResizeEdge,
    reason: str,
    code: str,
) -> CalendarResizeProposal:
    return CalendarResizeProposal(
        task.uid,
        edge,
        task.start,
        task.end,
        task.duration_minutes,
        InteractionValidationResult.reject(reason, code),
        changed=False,
    )


def propose_resize(
    task: Task,
    edge: ResizeEdge,
    target: CalendarDropTarget,
    *,
    minimum_timed_duration: int = MIN_TIMED_DURATION_MINUTES,
    maximum_timed_duration: int = MAX_DURATION_MINUTES,
) -> CalendarResizeProposal:
    """Calculate a single-day timed resize proposal."""
    if _is_recurring_instance(task):
        return _resize_rejection(
            task, edge, RECURRING_INTERACTION_ERROR, "recurring_instance"
        )
    if task.series_uid is not None:
        return _resize_rejection(
            task, edge, SERIES_INTERACTION_ERROR, "local_series_occurrence"
        )
    if task.start is None or task.is_all_day:
        return _resize_rejection(
            task, edge, "Изменять размер можно только у задач со временем",
            "not_timed",
        )
    target_validation = validate_drop_target(target)
    if not target_validation.valid or target.kind != DropZoneKind.TIMED_GRID:
        reason = target_validation.reason or "Изменение размера возможно только в сетке"
        return _resize_rejection(task, edge, reason, "invalid_resize_target")
    if target.target_date != task.start.date():
        return _resize_rejection(
            task,
            edge,
            "Изменение многодневных событий пока не поддерживается",
            "multi_day_resize",
        )
    current_end = task.end or task.start + timedelta(minutes=_timed_duration(task))
    if current_end.date() != task.start.date():
        return _resize_rejection(
            task,
            edge,
            "Изменение многодневных событий пока не поддерживается",
            "multi_day_resize",
        )
    assert target.minute_of_day is not None
    tzinfo = task.start.tzinfo
    if edge == ResizeEdge.START:
        latest = int(
            (current_end - datetime.combine(
                task.start.date(), time.min, tzinfo=tzinfo
            )).total_seconds() // 60
        ) - minimum_timed_duration
        minute = max(target.visible_start_minute, min(latest, target.minute_of_day))
        start = _at_minute(task.start.date(), minute, tzinfo)
        end = current_end
    else:
        start_minute = task.start.hour * 60 + task.start.minute
        earliest = start_minute + minimum_timed_duration
        minute = max(earliest, min(target.visible_end_minute, target.minute_of_day))
        start = task.start
        end = _at_minute(task.start.date(), minute, tzinfo)
    duration = int((end - start).total_seconds() // 60)
    if duration > maximum_timed_duration:
        return _resize_rejection(
            task,
            edge,
            f"Длительность не может превышать {maximum_timed_duration} минут",
            "duration_too_long",
        )
    return CalendarResizeProposal(
        task.uid,
        edge,
        start,
        end,
        duration,
        InteractionValidationResult.accept(),
        changed=not (
            task.start == start
            and task.end == end
            and task.duration_minutes == duration
        ),
    )


# Readable aliases for callers/tests that prefer explicit builder names.
calculate_drop_target = target_from_mouse
build_drag_proposal = propose_drag
build_resize_proposal = propose_resize


__all__ = [
    "CalendarDragProposal",
    "CalendarDropTarget",
    "CalendarResizeProposal",
    "DEFAULT_SNAP_MINUTES",
    "DEFAULT_TIMED_DURATION_MINUTES",
    "DropZoneKind",
    "FINE_SNAP_MINUTES",
    "InteractionValidationResult",
    "MIN_TIMED_DURATION_MINUTES",
    "RECURRING_INTERACTION_ERROR",
    "SERIES_INTERACTION_ERROR",
    "ResizeEdge",
    "build_drag_proposal",
    "build_resize_proposal",
    "calculate_drop_target",
    "minute_from_mouse_y",
    "propose_drag",
    "propose_resize",
    "snap_minute",
    "snapping_increment",
    "target_from_mouse",
    "validate_drop_target",
]
