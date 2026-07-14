from planner_desktop.domain.task import Task
from planner_desktop.repositories.fake_task_repository import FakeTaskRepository
from planner_desktop.usecases.task_service import DesktopTaskService
from planner_desktop.viewmodels.search_viewmodel import SearchViewModel


def build_vm():
    repo = FakeTaskRepository(seed=False)
    service = DesktopTaskService(repo)
    first = service.create_task(Task(title="Русский отчёт", uid="first"))
    second = service.create_task(Task(title="English report", uid="second"))
    return repo, service, SearchViewModel(service), first, second


def test_results_update_and_selected_result_survives_refresh():
    repo, service, vm, first, _ = build_vm()
    vm.setQuery("отчёт")
    assert vm.resultCount == 1
    vm.selectTask(first.uid)
    vm.refresh()
    assert vm.selectedUid == first.uid
    assert vm.results[0]["title"] == "Русский отчёт"


def test_selected_result_disappears_cleanly_when_deleted():
    repo, service, vm, first, _ = build_vm()
    vm.selectTask(first.uid)
    assert service.delete_task_by_uid(first.uid)
    vm.refresh()
    assert vm.selectedUid == ""
    assert all(row["uid"] != first.uid for row in vm.results)


def test_empty_query_with_filters_is_valid_and_result_navigation_wraps():
    _, _, vm, first, second = build_vm()
    vm.setStatusFilter("active")
    assert vm.resultCount == 2
    visible = [row["uid"] for row in vm.results]
    vm.moveResultSelection(1)
    assert vm.selectedUid == visible[0]
    vm.moveResultSelection(-1)
    assert vm.selectedUid == visible[-1]


def test_open_search_reopens_focus_without_resetting_query():
    _, _, vm, *_ = build_vm()
    focus_events = []
    vm.focusSearchRequested.connect(lambda: focus_events.append(True))
    vm.setQuery("report")
    vm.openSearch()
    vm.openSearch()
    assert vm.isOpen is True
    assert vm.query == "report"
    assert len(focus_events) == 2
