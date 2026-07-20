"""ViewModel layer: preflight, plan creation, Settings rows; all local."""
from __future__ import annotations

import sys

from PySide6.QtCore import QCoreApplication

from planner_desktop.usecases.task_service import DesktopTaskService
from planner_desktop.viewmodels.settings_viewmodel import SettingsViewModel
from planner_desktop.viewmodels.today_viewmodel import TodayViewModel
from tests.remote_split_testkit import (
    build_env,
    link_series,
    make_series,
    plan_split,
)


def _app():
    return QCoreApplication.instance() or QCoreApplication(sys.argv)


def _today_vm(env) -> TodayViewModel:
    service = DesktopTaskService(env.tasks)
    service.recurrence_service = env.recurrence
    return TodayViewModel(service=service)


def _payload(target, **overrides):
    values = {
        "title": "TEST split successor",
        "notes": target.notes,
        "priority": target.priority,
        "dateText": target.start.date().isoformat(),
        "timeText": "" if target.is_all_day else "11:00",
        "durationText": "" if target.is_all_day else "45",
    }
    values.update(overrides)
    return values


def test_editor_data_reports_split_eligibility_and_lock(tmp_path):
    _app()
    env = build_env(tmp_path)
    link_series(env, make_series())
    vm = _today_vm(env)
    rows = env.live_rows("src-1")
    target = rows[2]

    data = vm.editorDataFor(target.uid)
    assert data["seriesLinkedToGoogle"] is True
    assert data["remoteSplitEligible"] is True
    assert data["remoteSplitPending"] is False

    plan_split(env, "src-1")
    data = vm.editorDataFor(target.uid)
    assert data["remoteSplitEligible"] is False
    assert data["remoteSplitPending"] is True
    assert data["remoteSplitStatusText"] == "Ожидает разделения"
    env.close()


def test_preflight_and_create_plan_slots(tmp_path):
    _app()
    env = build_env(tmp_path)
    link_series(env, make_series())
    vm = _today_vm(env)
    rows = env.live_rows("src-1")
    target = rows[2]
    env.gateway.reset_call_counts()

    preflight = vm.remoteSplitPreflight(target.uid, _payload(target))
    assert preflight["ok"] is True
    assert preflight["occurrencesBeforeTarget"] == 2
    assert preflight["targetSlot"] == str(target.occurrence_key)
    assert "время начала" in preflight["changedFields"]
    assert "длительность" in preflight["changedFields"]
    assert "ДВА" in preflight["twoMastersWarning"]
    # Opening the dialog performs zero Google calls.
    assert env.gateway.write_call_count == 0
    assert env.gateway.list_call_count == 0

    # First-occurrence preflight routes the user to the whole-series edit.
    first = rows[0]
    routed = vm.remoteSplitPreflight(first.uid, _payload(first))
    assert routed["ok"] is False
    assert routed["routeToEntireSeries"] is True

    assert vm.createRemoteSplitPlan(target.uid, _payload(target))
    active = env.split_store.get_active_plan("src-1")
    assert active is not None
    assert env.gateway.write_call_count == 0
    env.close()


def test_split_role_badges_after_completion(tmp_path):
    _app()
    env = build_env(tmp_path)
    link_series(env, make_series())
    record, target = plan_split(env, "src-1")
    assert env.manual.run_once().ok
    plan = env.split_store.get_plan(record.id)
    from datetime import timedelta

    from tests.remote_split_testkit import START

    env.recurrence.ensure_occurrences(START, START + timedelta(days=10))
    vm = _today_vm(env)

    source_row = env.live_rows("src-1")[0]
    successor_row = env.live_rows(plan.reserved_successor_series_uid)[0]
    assert vm.editorDataFor(source_row.uid)["remoteSplitRole"] == "source"
    assert vm.editorDataFor(successor_row.uid)["remoteSplitRole"] == (
        "successor"
    )
    env.close()


def test_settings_rows_counts_and_actions_are_local(tmp_path):
    _app()
    env = build_env(tmp_path)
    link_series(env, make_series())
    record, _ = plan_split(env, "src-1")
    service = DesktopTaskService(env.tasks)
    service.recurrence_service = env.recurrence
    settings = SettingsViewModel(
        service,
        series_link_service=env.links,
        series_sync_store=env.series_store,
        occurrence_sync_store=env.occurrence_store,
        remote_split_service=env.splits,
    )
    env.gateway.reset_call_counts()

    rows = settings.remoteSplitRows
    assert len(rows) == 1
    assert rows[0]["state"] == "pending"
    assert rows[0]["statusText"] == "Ожидает разделения"
    assert rows[0]["canCancel"] is True
    assert rows[0]["canRollback"] is False
    assert settings.activeRemoteSplitCount == 1
    assert settings.conflictRemoteSplitCount == 0
    assert settings.completedRemoteSplitCount == 0
    # Opening the Settings UI created zero Google calls.
    assert env.gateway.write_call_count == 0
    assert env.gateway.list_call_count == 0

    assert settings.cancelRemoteSplit(record.id)
    assert settings.activeRemoteSplitCount == 0
    assert settings.completedRemoteSplitCount == 1
    assert env.gateway.write_call_count == 0

    # Conflict counter and rollback affordance.
    record2, _ = plan_split(env, "src-1")
    env.split_store.mark_source_trimmed(record2.id, remote_etag="x")
    env.split_store.mark_conflict(record2.id, "test conflict")
    rows = settings.remoteSplitRows
    conflicted = next(row for row in rows if row["id"] == record2.id)
    assert conflicted["state"] == "conflict"
    assert conflicted["canRollback"] is True
    assert settings.conflictRemoteSplitCount == 1
    assert settings.rollbackRemoteSplit(record2.id)
    assert env.split_store.get_plan(record2.id).state.value == (
        "rollback_pending"
    )
    env.close()
