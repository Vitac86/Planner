from planner_desktop.usecases.recurrence_service import RecurrenceService
from planner_desktop.usecases.task_service import DesktopTaskService
from planner_desktop.viewmodels.settings_viewmodel import SettingsViewModel
from planner_desktop.viewmodels.task_actions import TaskActionsViewModel
from tests.test_desktop_occurrence_resolution_service import (
    make_resolution_stack,
)


def test_editor_exposes_linked_occurrence_identity_and_status(tmp_path):
    (
        series, task, _change, series_repo, tasks, master, store, links, _service
    ) = make_resolution_stack(tmp_path)
    recurrence = RecurrenceService(series_repo, tasks)
    recurrence.series_link_service = links
    recurrence.occurrence_sync_store = store
    desktop = DesktopTaskService(tasks)
    desktop.recurrence_service = recurrence
    vm = TaskActionsViewModel(desktop)
    data = vm.editorDataFor(task.uid)
    assert data["seriesLinkedToGoogle"]
    assert data["occurrenceSyncStatus"] == "remote_changed"
    assert data["occurrenceOriginalSlot"].startswith("2026-07-20T09:00")
    assert data["occurrenceRemoteStatus"] == "confirmed"
    store.close()
    master.close()


def test_settings_exposes_occurrence_counts_and_quarantine_actions(tmp_path):
    (
        series, task, change, _series_repo, tasks, master, store, _links, resolver
    ) = make_resolution_stack(tmp_path)
    desktop = DesktopTaskService(tasks)
    vm = SettingsViewModel(
        desktop,
        occurrence_sync_store=store,
        occurrence_resolution_service=resolver,
    )
    assert vm.unresolvedOccurrenceQuarantineCount == 1
    rows = vm.quarantinedOccurrenceRows
    assert rows[0]["id"] == change.id
    assert rows[0]["occurrenceKey"] == task.occurrence_key
    assert rows[0]["canUseGoogle"]
    assert vm.keepPlannerOccurrence(change.id, True)
    assert vm.pendingOccurrenceUpdateCount == 1
    assert store.get_occurrence_change(change.id).resolution_status == "pending"
    store.close()
    master.close()


def test_opening_read_only_viewmodels_performs_no_gateway_calls(tmp_path):
    (
        _series, task, _change, series_repo, tasks, master, store, links, resolver
    ) = make_resolution_stack(tmp_path)
    recurrence = RecurrenceService(series_repo, tasks)
    recurrence.series_link_service = links
    recurrence.occurrence_sync_store = store
    desktop = DesktopTaskService(tasks)
    desktop.recurrence_service = recurrence
    task_vm = TaskActionsViewModel(desktop)
    settings_vm = SettingsViewModel(
        desktop,
        occurrence_sync_store=store,
        occurrence_resolution_service=resolver,
    )
    task_vm.editorDataFor(task.uid)
    _ = settings_vm.quarantinedOccurrenceRows
    _ = settings_vm.pendingOccurrenceUpdateCount
    # The stack has no gateway at all: read-only UI state is purely local.
    assert store.count_pending_ops() == 0
    store.close()
    master.close()
