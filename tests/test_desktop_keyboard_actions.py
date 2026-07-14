"""Маршрутизация клавиатурных сокращений и выбор задачи из ViewModel.

Политика — чистый Python (domain/keyboard.py): «голые» клавиши уступают
текстовому вводу и открытым диалогам, Ctrl-сочетания не мешают набору.
QML спрашивает разрешение через UiStateViewModel.allowShortcut.
"""
import pytest

from planner_desktop.domain.keyboard import (
    RESERVED_SHORTCUTS,
    allow_shortcut,
    known_shortcuts,
)
from planner_desktop.domain.task import Task
from planner_desktop.repositories.fake_task_repository import FakeTaskRepository
from planner_desktop.usecases.task_service import DesktopTaskService
from planner_desktop.viewmodels.today_viewmodel import TodayViewModel
from planner_desktop.viewmodels.ui_state import UiStateViewModel

BARE = ["open_selected", "toggle_selected", "delete_selected",
        "clear_selection", "calendar_prev_day", "calendar_next_day",
        "calendar_prev_period", "calendar_next_period", "calendar_today",
        "calendar_prev_event", "calendar_next_event",
        "calendar_move_slot", "calendar_move_day", "calendar_resize",
        "calendar_to_all_day", "calendar_unschedule",
        "quick_add_slash"]
CTRL = ["new_task", "new_scheduled_task", "quick_add", "refresh"]


# ---- политика ---------------------------------------------------------------------

@pytest.mark.parametrize("name", BARE)
def test_bare_keys_do_not_fire_while_typing(name):
    assert allow_shortcut(name, typing=True, dialog_open=False) is False
    assert allow_shortcut(name, typing=False, dialog_open=False) is True


@pytest.mark.parametrize("name", CTRL)
def test_ctrl_shortcuts_work_while_typing(name):
    assert allow_shortcut(name, typing=True, dialog_open=False) is True


@pytest.mark.parametrize("name", BARE + CTRL)
def test_nothing_fires_over_open_dialog(name):
    assert allow_shortcut(name, typing=False, dialog_open=True) is False


def test_unknown_shortcut_is_refused():
    assert allow_shortcut("nonsense", typing=False, dialog_open=False) is False


def test_search_is_reserved_for_phase_3():
    assert "search" in RESERVED_SHORTCUTS
    assert allow_shortcut("search", typing=False, dialog_open=False) is False


def test_known_shortcuts_cover_documented_set():
    assert set(BARE + CTRL) == set(known_shortcuts())


# ---- мост в QML -----------------------------------------------------------------------

def test_ui_state_viewmodel_mirrors_policy():
    ui = UiStateViewModel()
    assert ui.allowShortcut("toggle_selected", False, False) is True
    assert ui.allowShortcut("toggle_selected", True, False) is False
    assert ui.allowShortcut("new_task", True, False) is True
    assert ui.allowShortcut("new_task", True, True) is False
    assert ui.allowShortcut("search", False, False) is False


# ---- выбор задачи в ViewModel (цель Enter/Space/Delete) ----------------------------------

@pytest.fixture()
def vm():
    service = DesktopTaskService(FakeTaskRepository())
    return TodayViewModel(service=service)


def test_selection_exposes_selected_task_row(vm):
    task = vm.service.create_task(Task(title="Выбранная"))
    signals = []
    vm.selectedTaskChanged.connect(lambda: signals.append(1))

    vm.selectTask(task.uid)
    assert vm.selectedUid == task.uid
    assert vm.selectedTask["title"] == "Выбранная"
    assert signals

    vm.clearSelection()
    assert vm.selectedUid == ""
    assert vm.selectedTask is None


def test_selected_task_actions_route_through_slots(vm):
    """Space/Enter/Delete в QML зовут те же слоты по selectedUid."""
    task = vm.service.create_task(Task(title="Клавиатура"))
    vm.selectTask(task.uid)

    assert vm.toggleCompleted(vm.selectedUid) is True
    assert vm.selectedTask["completed"] is True

    assert vm.deleteTask(vm.selectedUid) is True
    assert vm.selectedUid == ""  # выбор очищается вместе с задачей
    assert vm.selectedTask is None


def test_selection_of_missing_task_yields_none(vm):
    vm.selectTask("no-such-uid")
    assert vm.selectedTask is None
