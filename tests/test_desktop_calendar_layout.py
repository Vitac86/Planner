"""Pure geometry tests for the Phase 2.1 hourly Calendar grid."""
from datetime import date, datetime, timedelta

import pytest

from planner_desktop.domain.calendar_layout import (
    CalendarGridConfig,
    layout_calendar_events,
)
from planner_desktop.domain.task import Task


DAY = date(2026, 7, 14)
CONFIG = CalendarGridConfig(visible_start_hour=6, visible_end_hour=23,
                            minimum_visual_minutes=15)


def timed(uid: str, start: datetime, *, end: datetime | None = None,
          duration: int | None = None) -> Task:
    return Task(uid=uid, title=uid, start=start, end=end,
                duration_minutes=duration)


def test_event_top_and_height_are_normalized():
    event = timed("a", datetime(2026, 7, 14, 8), duration=60)
    block = layout_calendar_events([event], [DAY], CONFIG).timed_blocks[0]

    assert block.start_minute == 8 * 60
    assert block.end_minute == 9 * 60
    assert block.top_ratio == pytest.approx(2 / 17)
    assert block.height_ratio == pytest.approx(1 / 17)


def test_clips_before_and_after_visible_hours():
    before = timed("before", datetime(2026, 7, 14, 5),
                   end=datetime(2026, 7, 14, 7))
    after = timed("after", datetime(2026, 7, 14, 22),
                  end=datetime(2026, 7, 15, 1))
    blocks = {b.uid: b for b in
              layout_calendar_events([before, after], [DAY], CONFIG).timed_blocks}

    assert (blocks["before"].start_minute, blocks["before"].end_minute) == (360, 420)
    assert blocks["before"].clipped_at_start is True
    assert blocks["before"].clipped_at_end is False
    assert (blocks["after"].start_minute, blocks["after"].end_minute) == (1320, 1380)
    assert blocks["after"].clipped_at_end is True


def test_event_crossing_midnight_is_split_per_day():
    config = CalendarGridConfig(visible_start_hour=0, visible_end_hour=24)
    event = timed("night", datetime(2026, 7, 14, 23, 30),
                  end=datetime(2026, 7, 15, 1, 0))
    layout = layout_calendar_events([event], [DAY, DAY + timedelta(days=1)], config)

    assert [(b.day_index, b.start_minute, b.end_minute) for b in layout.timed_blocks] == [
        (0, 1410, 1440), (1, 0, 60),
    ]
    assert layout.timed_blocks[0].clipped_at_end is True
    assert layout.timed_blocks[1].clipped_at_start is True


@pytest.mark.parametrize("end,duration", [
    (datetime(2026, 7, 14, 10), None),
    (datetime(2026, 7, 14, 9), 0),
    (None, -20),
    (None, None),
])
def test_zero_or_invalid_duration_uses_deterministic_minimum(end, duration):
    event = timed("minimum", datetime(2026, 7, 14, 10),
                  end=end, duration=duration)
    block = layout_calendar_events([event], [DAY], CONFIG).timed_blocks[0]
    assert block.duration_minutes == CONFIG.minimum_visual_minutes


def test_events_completely_outside_visible_hours_are_excluded():
    early = timed("early", datetime(2026, 7, 14, 1), duration=30)
    late = timed("late", datetime(2026, 7, 14, 23, 30), duration=30)
    assert layout_calendar_events([early, late], [DAY], CONFIG).timed_blocks == ()


def test_all_day_is_excluded_from_timed_grid():
    event = Task(uid="all", title="all", start=datetime(2026, 7, 14),
                 is_all_day=True)
    layout = layout_calendar_events([event], [DAY], CONFIG)
    assert layout.timed_blocks == ()
    assert [b.uid for b in layout.all_day_blocks] == ["all"]
    assert layout.all_day_blocks[0].all_day is True


def test_multi_day_all_day_uses_exclusive_end_and_visible_dates_only():
    event = Task(uid="trip", title="trip", start=datetime(2026, 7, 13),
                 end=datetime(2026, 7, 16), is_all_day=True)
    dates = [DAY, DAY + timedelta(days=1), DAY + timedelta(days=2)]
    layout = layout_calendar_events([event], dates, CONFIG)
    assert [(b.day, b.day_index) for b in layout.all_day_blocks] == [
        (DAY, 0), (DAY + timedelta(days=1), 1),
    ]


def test_events_outside_selected_date_range_are_excluded():
    event = timed("elsewhere", datetime(2026, 7, 20, 10), duration=30)
    assert layout_calendar_events([event], [DAY], CONFIG).timed_blocks == ()
