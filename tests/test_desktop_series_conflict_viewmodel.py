import sys

from PySide6.QtCore import QCoreApplication

from planner_desktop.usecases.recurrence_service import RecurrenceService
from planner_desktop.usecases.task_service import DesktopTaskService
from planner_desktop.viewmodels.settings_viewmodel import SettingsViewModel
from planner_desktop.viewmodels.today_viewmodel import TodayViewModel
from tests.test_desktop_series_conflict_service import make_conflict, make_stack


def _app():
    return QCoreApplication.instance() or QCoreApplication(sys.argv)


def _view_stack(tmp_path):
    _app()
    stack = make_stack(tmp_path)
    service = DesktopTaskService(stack.tasks)
    recurrence = RecurrenceService(stack.series_repo, stack.tasks)
    recurrence.series_link_service = stack.links
    recurrence.series_conflict_service = stack.conflicts
    service.recurrence_service = recurrence
    vm = TodayViewModel(service=service)
    return stack, service, vm


def test_conflict_dialog_data_and_actions_flow(tmp_path):
    stack, service, vm = _view_stack(tmp_path)
    make_conflict(stack)
    data = vm.seriesConflictData("s1")
    assert data["available"] and data["status"] == "conflict"
    assert data["local"]["title"] == "Local authoritative"
    assert data["remote"]["title"] == "Changed in Google"
    assert data["canKeepPlanner"] and data["canUseGoogle"] and data["canDisconnect"]
    link_data = vm.seriesGoogleLinkData("s1")
    assert link_data["status"] == "conflict"

    assert vm.resolveConflictKeepPlanner("s1")
    assert stack.store.count_pending_ops() == 1
    refreshed = vm.seriesConflictData("s1")
    assert refreshed["pendingResolutionKind"] == "keep_planner"
    stack.store.close(); stack.ordinary.close()


def test_use_google_and_disconnect_slots_are_local(tmp_path):
    stack, service, vm = _view_stack(tmp_path)
    make_conflict(stack)
    writes = stack.gateway.write_call_count
    lists = stack.gateway.list_call_count
    assert vm.resolveConflictUseGoogle("s1")
    assert stack.gateway.write_call_count == writes
    assert stack.gateway.list_call_count == lists
    assert stack.series_repo.get_by_uid("s1").title == "Changed in Google"

    make_conflict(stack, summary="Second conflict")
    assert vm.resolveConflictDisconnect("s1")
    assert stack.store.get_link("s1") is None
    stack.store.close(); stack.ordinary.close()


def test_remote_deleted_recovery_slots(tmp_path):
    stack, service, vm = _view_stack(tmp_path)
    stack.gateway.delete_recurring_master(stack.remote_id)
    stack.pull.pull_remote_changes()
    data = vm.seriesRemoteDeletedData("s1")
    assert data["available"] and data["canRecreate"]
    assert vm.recoverRemoteDeletedRecreate("s1")
    link = stack.store.get_link("s1")
    assert link.link_generation == 1
    # Rapid duplicate press through the ViewModel does not add generations.
    vm.recoverRemoteDeletedRecreate("s1")
    assert stack.store.max_link_generation("s1") == 1
    stack.store.close(); stack.ordinary.close()


def test_keep_local_and_delete_local_slots(tmp_path):
    stack, service, vm = _view_stack(tmp_path)
    stack.gateway.delete_recurring_master(stack.remote_id)
    stack.pull.pull_remote_changes()
    assert vm.recoverRemoteDeletedKeepLocal("s1")
    assert stack.store.get_link("s1") is None
    assert not stack.series_repo.get_by_uid("s1").is_deleted
    stack.store.close(); stack.ordinary.close()


def test_settings_viewmodel_exposes_resolution_diagnostics(tmp_path):
    stack, service, vm = _view_stack(tmp_path)
    make_conflict(stack)
    assert vm.resolveConflictKeepPlanner("s1")
    settings = SettingsViewModel(
        service,
        series_link_service=stack.links,
        series_sync_store=stack.store,
        series_conflict_service=stack.conflicts,
    )
    assert settings.conflictedSeriesCount == 1
    assert settings.pendingResolutionCount == 1
    assert settings.failedResolutionCount == 0
    assert settings.supersededResolutionCount == 0
    rows = settings.resolutionHistoryRows
    assert len(rows) == 1
    assert rows[0]["kind"] == "keep_planner"
    assert rows[0]["statusText"]
    link_rows = settings.linkedSeriesRows
    assert link_rows[0]["generation"] == 0
    assert "явными действиями" in settings.conflictResolutionNote
    stack.store.close(); stack.ordinary.close()


def test_missing_conflict_service_degrades_gracefully(tmp_path):
    _app()
    stack = make_stack(tmp_path)
    service = DesktopTaskService(stack.tasks)
    vm = TodayViewModel(service=service)
    data = vm.seriesConflictData("s1")
    assert data["available"] is False
    assert not vm.resolveConflictKeepPlanner("s1")
    assert not vm.recoverRemoteDeletedRecreate("s1")
    stack.store.close(); stack.ordinary.close()
