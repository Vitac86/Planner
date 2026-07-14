"""Undated Calendar panel data and responsive behavior."""
from datetime import datetime, timedelta
from pathlib import Path

from planner_desktop.domain.task import Task
from planner_desktop.repositories.fake_task_repository import FakeTaskRepository
from planner_desktop.usecases.task_service import DesktopTaskService
from planner_desktop.viewmodels.calendar_viewmodel import CalendarViewModel


NOW = datetime(2026, 7, 14, 9)


def make_vm():
    repo = FakeTaskRepository(seed=False)
    service = DesktopTaskService(repo)
    vm = CalendarViewModel(service=service, now_provider=lambda: NOW)
    return vm, service, repo


def test_panel_lists_active_undated_with_priority_and_notes():
    vm, service, _ = make_vm()
    service.create_task(Task(uid="low", title="Low", notes="note", priority=1))
    service.create_task(Task(uid="high", title="High", notes="important", priority=3))
    service.create_task(Task(uid="done", title="Done", completed=True))
    service.create_task(Task(
        uid="dated", title="Dated", start=NOW,
        end=NOW + timedelta(hours=1), duration_minutes=60,
    ))
    rows = vm.undatedTasks
    assert [row["uid"] for row in rows] == ["high", "low"]
    assert rows[0]["notes"] == "important"
    assert rows[0]["priority"] == 3


def test_panel_mode_matches_compact_normal_and_wide():
    vm, _, _ = make_vm()
    assert vm.undatedPanelMode == "drawer"
    vm.setResponsiveMode("wide")
    assert vm.undatedPanelMode == "persistent"
    vm.setResponsiveMode("compact")
    assert vm.undatedPanelMode == "bottom_sheet"


def test_undated_drag_to_timed_and_all_day():
    vm, service, _ = make_vm()
    task = service.create_task(Task(uid="later", title="Later"))
    vm.beginDrag(task.uid, "undated_panel")
    vm.updateDragTarget("timed_grid", "2026-07-14", 10 * 60, 0, False)
    assert vm.commitDrop()
    assert service.get_task(task.uid).start == datetime(2026, 7, 14, 10)

    vm.beginDrag(task.uid, "timed_grid")
    vm.updateDragTarget("all_day_lane", "2026-07-15", 0, 0, False)
    assert vm.commitDrop()
    assert service.get_task(task.uid).is_all_day
    assert service.get_task(task.uid).end == datetime(2026, 7, 16)


def test_scheduled_drop_into_panel_unschedules_and_keeps_selection():
    vm, service, _ = make_vm()
    task = service.create_task(Task(
        uid="scheduled", title="Scheduled", start=NOW,
        end=NOW + timedelta(hours=1), duration_minutes=60,
    ))
    vm.beginDrag(task.uid, "timed_grid")
    vm.updateDragTarget("undated_panel", "", 0, 0, False)
    assert vm.commitDrop()
    assert service.get_task(task.uid).start is None
    assert vm.selectedUid == task.uid


def test_panel_data_refreshes_after_schedule():
    vm, service, _ = make_vm()
    task = service.create_task(Task(uid="later", title="Later"))
    assert vm.undatedTaskCount == 1
    service.schedule_undated_task(task.uid, NOW)
    vm.refresh()
    assert vm.undatedTaskCount == 0


def test_drag_auto_scroll_is_bounded_focus_aware_and_interaction_only():
    source = (
        Path(__file__).parents[1]
        / "planner_desktop/qml/components/CalendarTimeGrid.qml"
    ).read_text(encoding="utf-8")
    assert 'objectName: "calendarDragAutoScroll"' in source
    assert "(grid.dragging || grid.resizing)" in source
    assert "grid.Window.window.active" in source
    assert "Math.min(18," in source
    assert "Math.max(0, Math.min(" in source
