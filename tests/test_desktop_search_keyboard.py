from pathlib import Path

from planner_desktop.domain.keyboard import allow_shortcut
from planner_desktop.domain.task import Task
from planner_desktop.repositories.fake_task_repository import FakeTaskRepository
from planner_desktop.usecases.task_service import DesktopTaskService
from planner_desktop.viewmodels.search_viewmodel import SearchViewModel


def test_search_works_while_typing_but_list_actions_do_not_conflict():
    assert allow_shortcut("search", typing=True, dialog_open=False)
    for action in ("select_all", "duplicate_selected"):
        assert not allow_shortcut(action, typing=True, dialog_open=False)
        assert not allow_shortcut(action, typing=False, dialog_open=True)
    assert not allow_shortcut("toggle_selected", typing=True, dialog_open=False)


def test_search_keyboard_navigation_and_open_signal():
    repo = FakeTaskRepository(seed=False)
    service = DesktopTaskService(repo)
    service.create_task(Task(title="Первый", uid="a"))
    service.create_task(Task(title="Второй", uid="b"))
    vm = SearchViewModel(service)
    opened = []
    vm.editRequested.connect(opened.append)

    vm.moveResultSelection(1)
    selected = vm.selectedUid
    vm.openSelectedResult()
    assert opened == [selected]
    vm.moveResultSelection(1)
    assert vm.selectedUid != selected


def test_qml_binds_required_search_shortcuts_and_avoids_text_conflicts():
    qml_dir = Path(__file__).resolve().parents[1] / "planner_desktop" / "qml"
    main = (qml_dir / "Main.qml").read_text(encoding="utf-8")
    overlay = (qml_dir / "components" / "GlobalSearch.qml").read_text(
        encoding="utf-8"
    )
    assert '"Ctrl+F"' in main
    assert '"Ctrl+D"' in main
    assert 'sequence: "Ctrl+A"' in overlay
    assert "!searchField.activeFocus" in overlay
    assert 'sequence: "Delete"' in overlay
    assert "searchField.activeFocus" not in overlay.split('sequence: "Ctrl+A"', 1)[1].split("}", 1)[0]
