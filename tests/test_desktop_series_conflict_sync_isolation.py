import sys
from datetime import datetime

from PySide6.QtCore import QCoreApplication

from planner_desktop.domain.series_calendar_link import (
    LINKED_OCCURRENCE_CHANGE_ERROR,
)
from planner_desktop.domain.task import Task
from planner_desktop.usecases.manual_sync_service import ManualSyncService
from planner_desktop.usecases.recurrence_service import RecurrenceService
from planner_desktop.usecases.task_service import DesktopTaskService
from planner_desktop.viewmodels.settings_viewmodel import SettingsViewModel
from planner_desktop.viewmodels.today_viewmodel import TodayViewModel
from tests.test_desktop_series_conflict_service import make_conflict, make_stack


def _app():
    return QCoreApplication.instance() or QCoreApplication(sys.argv)


def test_opening_conflict_ui_data_makes_zero_gateway_calls(tmp_path):
    _app()
    stack = make_stack(tmp_path)
    make_conflict(stack)
    service = DesktopTaskService(stack.tasks)
    recurrence = RecurrenceService(stack.series_repo, stack.tasks)
    recurrence.series_link_service = stack.links
    recurrence.series_conflict_service = stack.conflicts
    service.recurrence_service = recurrence
    vm = TodayViewModel(service=service)
    settings = SettingsViewModel(
        service,
        series_link_service=stack.links,
        series_sync_store=stack.store,
        series_conflict_service=stack.conflicts,
    )
    stack.gateway.reset_call_counts()

    vm.seriesConflictData("s1")
    vm.seriesRemoteDeletedData("s1")
    vm.seriesGoogleLinkData("s1")
    settings.refresh()
    _ = settings.resolutionHistoryRows
    _ = settings.pendingResolutionCount
    _ = settings.linkedSeriesRows
    stack.conflicts.get_conflict("s1")
    stack.conflicts.list_resolution_history()

    assert stack.gateway.write_call_count == 0
    assert stack.gateway.list_call_count == 0
    stack.store.close(); stack.ordinary.close()


def test_local_metadata_and_occurrences_stay_out_of_queues(tmp_path):
    stack = make_stack(tmp_path)
    make_conflict(stack)
    occurrence = stack.tasks.add(Task(
        title="Local authoritative", start=datetime(2026, 7, 16, 9),
        end=datetime(2026, 7, 16, 9, 30), series_uid="s1",
        occurrence_key="2026-07-16T09:00@Europe/Moscow",
    ))
    # Completion/priority stay local: no series op appears while in conflict.
    stack.tasks.complete(occurrence.id, True)
    occurrence.priority = 2
    stack.tasks.update(occurrence)
    assert stack.store.count_pending_ops() == 0
    # Materialized occurrences never enqueue ordinary Calendar ops either.
    assert stack.ordinary.count_pending_ops() == 0
    stack.store.close(); stack.ordinary.close()


def test_individual_occurrence_writes_remain_blocked(tmp_path):
    stack = make_stack(tmp_path)
    make_conflict(stack)
    service = DesktopTaskService(stack.tasks)
    recurrence = RecurrenceService(stack.series_repo, stack.tasks)
    recurrence.series_link_service = stack.links
    recurrence.series_conflict_service = stack.conflicts
    service.recurrence_service = recurrence
    occurrence = stack.tasks.add(Task(
        title="Local authoritative", start=datetime(2026, 7, 16, 9),
        end=datetime(2026, 7, 16, 9, 30), series_uid="s1",
        occurrence_key="2026-07-16T09:00@Europe/Moscow",
    ))
    blocked = service.postpone_task(occurrence.uid, "tomorrow")
    assert blocked.errors == [LINKED_OCCURRENCE_CHANGE_ERROR]
    stack.store.close(); stack.ordinary.close()


def test_quarantine_untouched_by_resolution_actions(tmp_path):
    from planner_desktop.sync.sync_types import CalendarEvent
    from datetime import date as date_type

    stack = make_stack(tmp_path)
    make_conflict(stack)
    stack.gateway.insert_event(CalendarEvent(
        summary="Changed instance",
        start=date_type(2026, 7, 16), end=date_type(2026, 7, 17),
        is_all_day=True,
        recurring_event_id=stack.remote_id,
        original_start=datetime(2026, 7, 16),
    ))
    stack.pull.pull_remote_changes()
    assert stack.store.count_quarantined() == 1
    assert stack.conflicts.resolve_use_google("s1", confirmed=True).ok
    # Quarantined linked-instance changes remain unresolved and visible.
    assert stack.store.count_quarantined() == 1
    changes = stack.store.list_occurrence_changes()
    assert changes[0].resolved_at is None
    stack.store.close(); stack.ordinary.close()


def test_ordinary_task_sync_remains_operational(tmp_path):
    stack = make_stack(tmp_path)
    make_conflict(stack)
    service = DesktopTaskService(stack.tasks, stack.ordinary)
    ordinary = service.create_task(Task(
        title="Ordinary", start=datetime(2026, 7, 20, 10),
        end=datetime(2026, 7, 20, 11),
    ))
    assert stack.ordinary.count_pending_ops() == 1
    manual = ManualSyncService(
        stack.tasks, stack.ordinary, gateway_provider=lambda: stack.gateway
    )
    result = manual.run_once()
    assert result.ok
    assert stack.tasks.get_by_uid(ordinary.uid).google_calendar_event_id
    # The unresolved conflict stayed untouched by the ordinary cycle.
    assert stack.store.get_link("s1").link_status.value == "conflict"
    stack.store.close(); stack.ordinary.close()


def test_manual_sync_reports_resolution_counters(tmp_path):
    stack = make_stack(tmp_path)
    make_conflict(stack)
    assert stack.conflicts.resolve_keep_planner("s1", confirmed=True).ok

    manual = ManualSyncService(
        stack.tasks, stack.ordinary, gateway_provider=lambda: stack.gateway
    )
    result = manual._run_cycle(
        stack.tasks, stack.ordinary, stack.catalog,
        series_store=stack.store, series_repository=stack.series_repo,
    )
    assert result.ok
    assert result.conflicts_resolved_keep_planner == 1
    assert result.resolution_failures == 0
    assert result.resolution_attempts_superseded == 0
    assert "конфликтов решено (Planner) 1" in result.summary

    # Local Use-Google/disconnect resolutions surface in the NEXT summary.
    make_conflict(stack, summary="Again different")
    assert stack.conflicts.resolve_use_google("s1", confirmed=True).ok
    second = manual._run_cycle(
        stack.tasks, stack.ordinary, stack.catalog,
        series_store=stack.store, series_repository=stack.series_repo,
    )
    assert second.ok
    assert second.conflicts_resolved_use_google == 1
    stack.store.close(); stack.ordinary.close()


def test_no_automatic_sync_is_registered_anywhere():
    """The Phase 1 guarantee stands: no timer/startup Google sync exists."""
    import inspect
    import planner_desktop.usecases.manual_sync_service as manual_module

    source = inspect.getsource(manual_module)
    assert "QTimer" not in source
    assert "singleShot" not in source
    import planner_desktop.main_window as main_window_module

    window_source = inspect.getsource(main_window_module)
    # No code path in the window triggers a sync cycle by itself; the only
    # trigger remains the explicit Settings button (SettingsViewModel.syncNow).
    assert ".run_once(" not in window_source
    assert ".sync_once(" not in window_source
    assert "QTimer" not in window_source
