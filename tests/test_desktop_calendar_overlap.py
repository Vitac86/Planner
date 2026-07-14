"""Deterministic interval-column tests for Calendar Phase 2.1."""
from datetime import date, datetime
from itertools import permutations

from planner_desktop.domain.calendar_layout import layout_calendar_events
from planner_desktop.domain.task import Task


DAY = date(2026, 7, 14)


def event(uid: str, start_hour: int, start_minute: int,
          end_hour: int, end_minute: int = 0) -> Task:
    return Task(
        uid=uid,
        title=uid,
        start=datetime(2026, 7, 14, start_hour, start_minute),
        end=datetime(2026, 7, 14, end_hour, end_minute),
    )


def geometry(tasks):
    blocks = layout_calendar_events(tasks, [DAY]).timed_blocks
    return {
        block.uid: (
            block.start_minute,
            block.end_minute,
            block.overlap_column_index,
            block.overlap_column_count,
        )
        for block in blocks
    }


def test_touching_events_do_not_overlap():
    result = geometry([event("a", 9, 0, 10), event("b", 10, 0, 11)])
    assert result["a"][2:] == (0, 1)
    assert result["b"][2:] == (0, 1)


def test_two_overlapping_events_are_side_by_side():
    result = geometry([event("a", 9, 0, 11), event("b", 10, 0, 12)])
    assert result["a"][2:] == (0, 2)
    assert result["b"][2:] == (1, 2)


def test_three_way_overlap_uses_three_columns():
    tasks = [
        event("a", 9, 0, 12),
        event("b", 9, 30, 11),
        event("c", 10, 0, 13),
    ]
    result = geometry(tasks)
    assert {result[uid][2] for uid in result} == {0, 1, 2}
    assert {result[uid][3] for uid in result} == {3}


def test_chained_overlap_is_one_group_but_reuses_available_column():
    tasks = [
        event("a", 9, 0, 11),
        event("b", 10, 0, 12),
        event("c", 11, 30, 13),
    ]
    layout = layout_calendar_events(tasks, [DAY])
    result = geometry(tasks)

    assert len(layout.day_columns[0].overlap_groups) == 1
    assert layout.day_columns[0].overlap_groups[0].column_count == 2
    assert result["a"][2] == result["c"][2] == 0
    assert result["b"][2] == 1
    assert {row[3] for row in result.values()} == {2}


def test_layout_is_stable_independent_of_input_order():
    tasks = [
        event("a", 9, 0, 12),
        event("b", 9, 30, 10, 30),
        event("c", 10, 0, 11),
    ]
    expected = geometry(tasks)
    for order in permutations(tasks):
        assert geometry(order) == expected


def test_same_start_uses_duration_then_uid_for_stable_columns():
    tasks = [
        event("z-long", 9, 0, 12),
        event("b-short", 9, 0, 10),
        event("a-short", 9, 0, 10),
    ]
    result = geometry(tasks)
    assert result["a-short"][2] == 0
    assert result["b-short"][2] == 1
    assert result["z-long"][2] == 2
