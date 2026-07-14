"""CalendarViewModel drag/resize state, commit and rollback behavior."""
from datetime import datetime, timedelta

from planner_desktop.domain.task import Task
from planner_desktop.repositories.fake_task_repository import FakeTaskRepository
from planner_desktop.usecases.task_service import DesktopTaskService
from planner_desktop.viewmodels.calendar_viewmodel import CalendarViewModel


START = datetime(2026, 7, 14, 9)


def make_vm():
    repo = FakeTaskRepository(seed=False)
    service = DesktopTaskService(repo)
    vm = CalendarViewModel(service=service, now_provider=lambda: START)
    task = service.create_task(Task(
        uid="task",
        title="Task",
        start=START,
        end=START + timedelta(hours=1),
        duration_minutes=60,
    ))
    return vm, service, repo, task


def test_begin_update_cancel_drag_exposes_preview_state():
    vm, _, _, task = make_vm()
    assert vm.beginDrag(task.uid, "timed_grid")
    vm.updateDragTarget("timed_grid", "2026-07-14", 10 * 60, 0, False)
    assert vm.dragging
    assert vm.draggedTaskUid == task.uid
    assert vm.proposalValid
    assert vm.proposedStartTime == "10:00"
    assert vm.dropPreviewGeometry["visible"] is True
    vm.cancelDrag()
    assert not vm.dragging
    assert vm.dropPreviewGeometry == {"visible": False}


def test_commit_drop_preserves_selection_and_emits_mutation():
    vm, service, _, task = make_vm()
    mutations = []
    vm.tasksMutated.connect(lambda: mutations.append(True))
    vm.beginDrag(task.uid, "timed_grid")
    vm.updateDragTarget("timed_grid", "2026-07-15", 11 * 60, 0, False)
    assert vm.commitDrop()
    assert service.get_task(task.uid).start == datetime(2026, 7, 15, 11)
    assert vm.selectedUid == task.uid
    assert mutations == [True]
    assert not vm.interactionBusy and not vm.dragging


def test_busy_guard_and_duplicate_commit():
    vm, _, _, task = make_vm()
    vm._interaction_busy = True
    assert not vm.beginDrag(task.uid, "timed_grid")
    vm._interaction_busy = False
    vm.beginDrag(task.uid, "timed_grid")
    vm.updateDragTarget("timed_grid", "2026-07-14", 10 * 60, 0, False)
    assert vm.commitDrop()
    assert not vm.commitDrop()


def test_invalid_recurring_commit_resets_busy_and_keeps_geometry():
    vm, service, repo, task = make_vm()
    task.google_calendar_recurring_event_id = "series"
    task.google_calendar_original_start = START
    repo.update(task)
    errors = []
    vm.toastError.connect(errors.append)
    vm.beginDrag(task.uid, "timed_grid")
    vm.updateDragTarget("timed_grid", "2026-07-15", 10 * 60, 0, False)
    assert not vm.proposalValid
    assert not vm.commitDrop()
    assert service.get_task(task.uid).start == START
    assert not vm.interactionBusy and not vm.dragging
    assert errors and "повторяющихся" in errors[-1]


def test_resize_begin_update_cancel_and_commit():
    vm, service, _, task = make_vm()
    assert vm.beginResize(task.uid, "end")
    vm.updateResize("2026-07-14", 10 * 60 + 30, 0, False)
    assert vm.resizing and vm.proposalValid
    assert vm.resizePreview["durationMinutes"] == 90
    assert vm.commitResize()
    assert service.get_task(task.uid).duration_minutes == 90
    assert not vm.resizing and not vm.interactionBusy


def test_disappeared_task_cancels_active_interaction():
    vm, _, repo, task = make_vm()
    vm.beginDrag(task.uid, "timed_grid")
    repo.delete(task.id)
    vm.refresh()
    assert not vm.dragging
    assert vm.draggedTaskUid == ""


def test_keyboard_move_resize_convert_and_unschedule():
    vm, service, _, task = make_vm()
    vm.selectTask(task.uid)
    assert vm.moveSelectedByMinutes(15)
    assert service.get_task(task.uid).start == datetime(2026, 7, 14, 9, 15)

    # Let the duplicate window pass deterministically for the next distinct action.
    assert vm.resizeSelectedByMinutes(15)
    assert service.get_task(task.uid).duration_minutes == 75
    assert vm.convertSelectedToAllDay()
    assert service.get_task(task.uid).is_all_day
    assert vm.unscheduleSelected()
    assert service.get_task(task.uid).start is None
    assert vm.selectedUid == task.uid
