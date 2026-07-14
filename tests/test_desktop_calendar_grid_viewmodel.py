"""Display modes and normalized grid contract of CalendarViewModel."""
from datetime import datetime, timedelta

from planner_desktop.domain.task import Task
from planner_desktop.repositories.fake_task_repository import FakeTaskRepository
from planner_desktop.usecases.task_service import DesktopTaskService
from planner_desktop.viewmodels.calendar_viewmodel import (
    CalendarViewModel,
    DISPLAY_DAY,
    DISPLAY_WEEK,
    DISPLAY_WORK_WEEK,
)


NOW = datetime(2026, 7, 14, 10, 30)  # Tuesday


def make_vm(now=NOW):
    service = DesktopTaskService(FakeTaskRepository(seed=False))
    return CalendarViewModel(service=service, now_provider=lambda: now)


def add(vm, uid, start, *, minutes=60, all_day=False, end=None):
    return vm.service.create_task(Task(
        uid=uid,
        title=uid,
        start=start,
        end=end or (start + timedelta(minutes=minutes)),
        duration_minutes=minutes,
        is_all_day=all_day,
    ))


def dates(vm):
    return [row["dateText"] for row in vm.visibleDates]


def test_day_work_week_and_week_visible_dates():
    vm = make_vm()
    assert dates(vm) == [
        "2026-07-13", "2026-07-14", "2026-07-15", "2026-07-16",
        "2026-07-17", "2026-07-18", "2026-07-19",
    ]

    vm.setDisplayMode(DISPLAY_WORK_WEEK)
    assert dates(vm) == [
        "2026-07-13", "2026-07-14", "2026-07-15", "2026-07-16",
        "2026-07-17",
    ]

    vm.setDisplayMode(DISPLAY_DAY)
    assert dates(vm) == ["2026-07-14"]


def test_previous_next_period_follow_display_mode():
    vm = make_vm()
    vm.nextPeriod()
    assert vm.weekStartText == "2026-07-20"
    vm.previousPeriod()
    assert vm.weekStartText == "2026-07-13"

    vm.setDisplayMode(DISPLAY_DAY)
    vm.nextPeriod()
    assert vm.selectedDateText == "2026-07-15"
    vm.previousPeriod()
    assert vm.selectedDateText == "2026-07-14"


def test_go_to_today_uses_injected_clock():
    vm = make_vm()
    vm.nextWeek()
    vm.goToToday()
    assert vm.selectedDateText == "2026-07-14"
    assert vm.isCurrentWeek is True


def test_current_time_indicator_visible_only_when_today_and_hour_are_visible():
    vm = make_vm()
    indicator = vm.currentTimeIndicator
    assert indicator["visible"] is True
    assert indicator["dayIndex"] == 1
    assert indicator["minute"] == 630

    vm.nextWeek()
    assert vm.currentTimeIndicator["visible"] is False

    early = make_vm(datetime(2026, 7, 14, 2, 0))
    assert early.currentTimeIndicator["visible"] is False


def test_grid_rows_expose_geometry_overlap_and_all_day_lane():
    vm = make_vm()
    add(vm, "a", datetime(2026, 7, 14, 9), minutes=120)
    add(vm, "b", datetime(2026, 7, 14, 10), minutes=120)
    add(vm, "all", datetime(2026, 7, 14), all_day=True)

    tuesday = vm.gridDays[1]
    assert [row["uid"] for row in tuesday["allDayEvents"]] == ["all"]
    assert {row["overlapColumnCount"] for row in tuesday["timedEvents"]} == {2}
    assert {row["overlapColumnIndex"] for row in tuesday["timedEvents"]} == {0, 1}
    assert all(0 <= row["topRatio"] < 1 for row in tuesday["timedEvents"])


def test_multi_day_all_day_appears_on_each_covered_visible_date():
    vm = make_vm()
    add(vm, "trip", datetime(2026, 7, 13), all_day=True,
        end=datetime(2026, 7, 16))
    assert [
        row["dateText"] for row in vm.gridDays if row["allDayEvents"]
    ] == ["2026-07-13", "2026-07-14", "2026-07-15"]


def test_selected_event_survives_refresh_while_present():
    vm = make_vm()
    task = add(vm, "selected", datetime(2026, 7, 14, 9))
    vm.selectEvent(task.uid)
    vm.refresh()
    assert vm.selectedUid == task.uid
    assert vm.selectedTask["title"] == "selected"


def test_selection_clears_when_task_disappears():
    vm = make_vm()
    task = add(vm, "gone", datetime(2026, 7, 14, 9))
    vm.selectEvent(task.uid)
    vm.repository.delete(task.id)
    vm.refresh()
    assert vm.selectedUid == ""
    assert vm.selectedTask is None


def test_compact_responsive_mode_defaults_to_day_without_forcing_expand():
    vm = make_vm()
    assert vm.displayMode == DISPLAY_WEEK
    vm.setResponsiveMode("compact")
    assert vm.displayMode == DISPLAY_DAY
    assert len(vm.visibleDates) == 1
    vm.setResponsiveMode("wide")
    assert vm.displayMode == DISPLAY_DAY


def test_keyboard_event_selection_follows_visible_geometry_order():
    vm = make_vm()
    add(vm, "first", datetime(2026, 7, 14, 9))
    add(vm, "second", datetime(2026, 7, 14, 11))
    vm.selectNextEvent()
    assert vm.selectedUid == "first"
    vm.selectNextEvent()
    assert vm.selectedUid == "second"
    vm.selectPreviousEvent()
    assert vm.selectedUid == "first"
