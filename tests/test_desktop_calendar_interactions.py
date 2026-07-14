"""Pure Phase 2.2 Calendar interaction proposals."""
from datetime import date, datetime

from planner_desktop.domain.calendar_interactions import (
    CalendarDropTarget,
    DropZoneKind,
    ResizeEdge,
    propose_drag,
    propose_resize,
    snap_minute,
    target_from_mouse,
)
from planner_desktop.domain.task import Task


DAY = date(2026, 7, 14)


def timed(*, start=datetime(2026, 7, 14, 9), minutes=60, **kwargs):
    return Task(
        title="timed",
        start=start,
        end=start.replace(hour=start.hour + minutes // 60,
                          minute=start.minute + minutes % 60),
        duration_minutes=minutes,
        **kwargs,
    )


def target(day=DAY, minute=10 * 60, kind=DropZoneKind.TIMED_GRID):
    return CalendarDropTarget(kind, day, minute)


def test_default_snapping_is_15_minutes_half_up():
    assert snap_minute(9 * 60 + 7) == 9 * 60
    assert snap_minute(9 * 60 + 8) == 9 * 60 + 15


def test_shift_snapping_is_5_minutes():
    assert snap_minute(9 * 60 + 2, shift=True) == 9 * 60
    assert snap_minute(9 * 60 + 3, shift=True) == 9 * 60 + 5


def test_mouse_target_clamps_day_and_visible_time():
    days = [DAY, date(2026, 7, 15)]
    first = target_from_mouse(-50, -20, 200, 1000, days)
    last = target_from_mouse(500, 2000, 200, 1000, days)
    assert (first.target_date, first.minute_of_day) == (DAY, 6 * 60)
    assert (last.target_date, last.minute_of_day) == (days[-1], 23 * 60)


def test_timed_move_preserves_duration_and_clamps_near_grid_end():
    proposal = propose_drag(timed(minutes=90), target(minute=22 * 60 + 45))
    assert proposal.valid
    assert proposal.proposed_start == datetime(2026, 7, 14, 21, 30)
    assert proposal.proposed_end == datetime(2026, 7, 14, 23)
    assert proposal.proposed_duration_minutes == 90


def test_timed_to_all_day_uses_exclusive_end():
    proposal = propose_drag(
        timed(), target(kind=DropZoneKind.ALL_DAY_LANE, minute=None)
    )
    assert proposal.valid and proposal.proposed_all_day
    assert proposal.proposed_start == datetime(2026, 7, 14)
    assert proposal.proposed_end == datetime(2026, 7, 15)
    assert proposal.proposed_duration_minutes is None


def test_all_day_to_timed_uses_default_duration():
    task = Task(
        title="all",
        start=datetime(2026, 7, 14),
        end=datetime(2026, 7, 15),
        is_all_day=True,
    )
    proposal = propose_drag(task, target(minute=11 * 60))
    assert proposal.valid and not proposal.proposed_all_day
    assert proposal.proposed_end == datetime(2026, 7, 14, 12)
    assert proposal.proposed_duration_minutes == 60


def test_multi_day_all_day_move_preserves_exclusive_span():
    task = Task(
        title="trip",
        start=datetime(2026, 7, 14),
        end=datetime(2026, 7, 17),
        is_all_day=True,
    )
    proposal = propose_drag(
        task,
        target(date(2026, 7, 20), None, DropZoneKind.ALL_DAY_LANE),
    )
    assert proposal.proposed_start == datetime(2026, 7, 20)
    assert proposal.proposed_end == datetime(2026, 7, 23)


def test_undated_to_timed_and_scheduled_to_undated():
    undated = Task(title="later")
    scheduled = propose_drag(undated, target(minute=13 * 60))
    assert scheduled.valid
    assert scheduled.proposed_start == datetime(2026, 7, 14, 13)
    assert scheduled.proposed_end == datetime(2026, 7, 14, 14)

    removed = propose_drag(timed(), target(kind=DropZoneKind.UNDATED_PANEL,
                                           day=None, minute=None))
    assert removed.valid and removed.changed
    assert removed.proposed_start is None
    assert removed.proposed_end is None


def test_resize_enforces_minimum_duration_and_grid_bounds():
    task = timed(minutes=60)
    shorter = propose_resize(task, ResizeEdge.END, target(minute=9 * 60 + 2))
    assert shorter.valid
    assert shorter.proposed_end == datetime(2026, 7, 14, 9, 15)
    assert shorter.proposed_duration_minutes == 15


def test_invalid_target_and_recurring_instance_are_structured_rejections():
    invalid = propose_drag(timed(), CalendarDropTarget(DropZoneKind.TIMED_GRID))
    assert not invalid.valid
    assert invalid.validation.code == "missing_date"

    recurring = timed(
        google_calendar_recurring_event_id="series",
        google_calendar_original_start=datetime(2026, 7, 14, 9),
    )
    refused = propose_drag(recurring, target())
    assert not refused.valid
    assert refused.validation.code == "recurring_instance"
    assert "повторяющихся" in refused.message


def test_noop_move_is_valid_but_not_changed():
    proposal = propose_drag(timed(), target(minute=9 * 60))
    assert proposal.valid
    assert proposal.changed is False
