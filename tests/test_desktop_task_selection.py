from datetime import datetime

from planner_desktop.domain.task import Task
from planner_desktop.repositories.fake_task_repository import FakeTaskRepository
from planner_desktop.viewmodels.task_selection import TaskSelection
from planner_desktop.viewmodels.today_viewmodel import TodayViewModel


def test_ctrl_toggle_and_shift_contiguous_range():
    selection = TaskSelection()
    selection.set_visible(["a", "b", "c", "d"])
    selection.select("b")
    selection.select("d", shift=True)
    assert selection.selected == ("b", "c", "d")
    selection.select("c", ctrl=True)
    assert selection.selected == ("b", "d")


def test_select_all_uses_visible_order_only_and_prunes_filter_change():
    selection = TaskSelection()
    selection.set_visible(["a", "b", "c"])
    selection.select_all_visible()
    assert selection.selected == ("a", "b", "c")
    selection.set_visible(["c", "d"])
    assert selection.selected == ("c",)


def test_today_selection_excludes_tasks_not_visible_on_today_or_undated():
    repo = FakeTaskRepository(seed=False)
    visible = repo.add(Task(title="Без даты", uid="visible"))
    hidden = repo.add(Task(
        title="Другой день", uid="hidden",
        start=datetime(2030, 1, 1, 9),
    ))
    vm = TodayViewModel(repo)
    vm.selectTaskWithModifiers(visible.uid, False, False)
    vm.selectAllVisible()
    assert vm.selectedUids == [visible.uid]
    repo.delete(visible.id)
    vm.refresh()
    assert vm.selectedCount == 0
    assert vm.selectedUid == ""
