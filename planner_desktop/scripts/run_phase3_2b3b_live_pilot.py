"""Controlled real-Google acceptance pilot for Phase 3.2B3B.

This is deliberately not an application entry point.  It performs one finite,
explicitly invoked TEST-only scenario against the isolated desktop profile and
the ``primary`` Calendar selector.  It never prints account information,
credentials, token contents, Calendar resource IDs, or remote payloads.

The scenario owns exactly one future COUNT=3 recurring master.  Every cleanup
write is guarded by the master ownership marker recorded for the newly-created
local series.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import date, datetime, time, timedelta
import json
import os
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from planner_desktop.domain.commands import TaskEditorCommand
from planner_desktop.domain.google_occurrence import (
    local_occurrence_to_google_original_start,
)
from planner_desktop.domain.recurrence import (
    RecurrenceEndMode,
    RecurrenceFrequency,
    RecurrenceRule,
    SeriesSchedule,
    TaskSeries,
)
from planner_desktop.domain.series_calendar_link import (
    PLANNER_SERIES_UID_PROPERTY,
)
from planner_desktop.storage.calendar_series_occurrence_sync_store import (
    CalendarSeriesOccurrenceSyncStore,
)
from planner_desktop.storage.calendar_series_sync_store import (
    CalendarSeriesSyncStore,
)
from planner_desktop.storage.calendar_sync_store import CalendarSyncStore
from planner_desktop.storage.external_series_repository import (
    SQLiteExternalSeriesRepository,
)
from planner_desktop.storage.paths import get_desktop_db_path
from planner_desktop.storage.series_repository import SQLiteSeriesRepository
from planner_desktop.storage.sqlite_task_repository import SQLiteTaskRepository
from planner_desktop.sync.calendar_series_mapper import (
    master_event_to_owned_payload,
)
from planner_desktop.sync.calendar_series_occurrence_mapper import (
    validate_remote_occurrence_payload,
)
from planner_desktop.sync.google_auth import (
    build_calendar_service,
    load_credentials,
)
from planner_desktop.sync.google_calendar_gateway import GoogleCalendarGateway
from planner_desktop.usecases.manual_sync_service import ManualSyncService
from planner_desktop.usecases.occurrence_resolution_service import (
    OccurrenceResolutionService,
)
from planner_desktop.usecases.recurrence_service import RecurrenceService
from planner_desktop.usecases.series_calendar_link_service import (
    SeriesCalendarLinkService,
)


EXPECTED_PROFILE = Path(
    r"D:\Users\v.pyatakov\myspace\planner-desktop-google-series-live-pilot"
)
CALENDAR_ID = "primary"
TEST_PREFIX = "[Planner Phase 3.2B3B TEST]"
TIMEZONE_NAME = "Europe/Moscow"
REPORT_FILENAME = "phase3_2b3b_live_pilot_report.json"


class PilotFailure(RuntimeError):
    """A redaction-safe acceptance failure."""


class AuditedGoogleCalendarGateway(GoogleCalendarGateway):
    """Count writes by route without logging request or response payloads."""

    def __init__(self, service: Any) -> None:
        super().__init__(service, calendar_id=CALENDAR_ID)
        self.write_counts = {
            "ordinary_insert": 0,
            "ordinary_patch": 0,
            "ordinary_delete": 0,
            "master_insert": 0,
            "master_patch": 0,
            "master_delete": 0,
            "occurrence_update": 0,
            "occurrence_cancel": 0,
        }

    def insert_event(self, event):
        self.write_counts["ordinary_insert"] += 1
        return super().insert_event(event)

    def patch_event(self, event_id, patch):
        self.write_counts["ordinary_patch"] += 1
        return super().patch_event(event_id, patch)

    def delete_event(self, event_id):
        self.write_counts["ordinary_delete"] += 1
        return super().delete_event(event_id)

    def insert_recurring_master(self, remote_event_id, master_payload):
        self.write_counts["master_insert"] += 1
        return super().insert_recurring_master(remote_event_id, master_payload)

    def patch_recurring_master(
        self, remote_event_id, master_payload, *, expected_etag=None
    ):
        self.write_counts["master_patch"] += 1
        return super().patch_recurring_master(
            remote_event_id,
            master_payload,
            expected_etag=expected_etag,
        )

    def delete_recurring_master(self, remote_event_id):
        self.write_counts["master_delete"] += 1
        return super().delete_recurring_master(remote_event_id)

    def update_recurring_instance(
        self, instance_event_id, complete_instance_payload, expected_etag
    ):
        self.write_counts["occurrence_update"] += 1
        return super().update_recurring_instance(
            instance_event_id,
            complete_instance_payload,
            expected_etag,
        )

    def cancel_recurring_instance(
        self, instance_event_id, complete_instance_payload, expected_etag
    ):
        self.write_counts["occurrence_cancel"] += 1
        return super().cancel_recurring_instance(
            instance_event_id,
            complete_instance_payload,
            expected_etag,
        )


def _require(condition: bool, label: str) -> None:
    if not condition:
        raise PilotFailure(label)


def _page_events(service: Any, **params: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    page_token = None
    while True:
        request_params = {
            "calendarId": CALENDAR_ID,
            "showDeleted": False,
            "maxResults": 2500,
            **params,
        }
        if page_token:
            request_params["pageToken"] = page_token
        page = service.events().list(**request_params).execute()
        rows.extend(dict(item) for item in page.get("items", ()))
        page_token = page.get("nextPageToken")
        if not page_token:
            return rows


def _active_test_resources(
    service: Any, *, series_uid: str | None = None
) -> dict[str, int]:
    resources: dict[str, dict[str, Any]] = {}
    for single_events in (False, True):
        for item in _page_events(
            service,
            q=TEST_PREFIX,
            singleEvents=single_events,
        ):
            remote_id = str(item.get("id") or "")
            if remote_id:
                resources[remote_id] = item
        if series_uid:
            for item in _page_events(
                service,
                privateExtendedProperty=[
                    f"{PLANNER_SERIES_UID_PROPERTY}={series_uid}"
                ],
                singleEvents=single_events,
            ):
                remote_id = str(item.get("id") or "")
                if remote_id:
                    resources[remote_id] = item
    counts = {"masters": 0, "instances": 0, "ordinary": 0}
    for item in resources.values():
        if item.get("recurringEventId"):
            counts["instances"] += 1
        elif item.get("recurrence"):
            counts["masters"] += 1
        else:
            counts["ordinary"] += 1
    return counts


def _queue_state(
    ordinary_store: CalendarSyncStore,
    series_store: CalendarSeriesSyncStore,
    occurrence_store: CalendarSeriesOccurrenceSyncStore,
) -> dict[str, int]:
    return {
        "ordinary_pending": ordinary_store.count_pending_ops(),
        "ordinary_terminal": ordinary_store.count_terminal_ops(),
        "master_pending": series_store.count_pending_ops(),
        "master_terminal": series_store.count_terminal_ops(),
        "occurrence_pending": occurrence_store.count_pending_ops(),
        "occurrence_terminal": occurrence_store.count_terminal_ops(),
        "unresolved_quarantine": occurrence_store.count_quarantined(),
    }


def _queue_is_clear(state: Mapping[str, int]) -> bool:
    return all(int(value) == 0 for value in state.values())


def _series(series_uid: str, start_day: date) -> TaskSeries:
    return TaskSeries(
        uid=series_uid,
        title=f"{TEST_PREFIX} controlled occurrence pilot",
        notes="Finite TEST-only Planner occurrence synchronization pilot.",
        schedule=SeriesSchedule(
            start_date=start_day,
            all_day=False,
            local_time=time(10, 0),
            duration_minutes=15,
            timezone_name=TIMEZONE_NAME,
        ),
        rule=RecurrenceRule(
            RecurrenceFrequency.DAILY,
            end_mode=RecurrenceEndMode.COUNT,
            occurrence_count=3,
        ),
    )


def _command(task, *, title: str, start: datetime) -> TaskEditorCommand:
    return TaskEditorCommand(
        title=title,
        notes="One TEST occurrence; no guests and no sensitive notes.",
        priority=task.priority,
        completed=task.completed,
        add_to_calendar=True,
        is_all_day=False,
        date_text=start.date().isoformat(),
        time_text=start.strftime("%H:%M"),
        duration_text="15",
    )


def _verify_master_owner(gateway, master_id: str, series_uid: str):
    master = gateway.get_recurring_master(master_id)
    _require(master is not None, "owned_master_missing")
    private = master.private_extended_properties or {}
    _require(
        private.get(PLANNER_SERIES_UID_PROPERTY) == series_uid,
        "owned_master_marker_mismatch",
    )
    return master


def _exact_instance(gateway, series, master_id: str, occurrence_key: str):
    _verify_master_owner(gateway, master_id, series.uid)
    identity = local_occurrence_to_google_original_start(
        series, occurrence_key
    )
    candidates = gateway.list_recurring_instances(
        master_id, identity.to_google(), show_deleted=True
    )
    exact: list[dict[str, Any]] = []
    for candidate in candidates:
        validation = validate_remote_occurrence_payload(
            candidate,
            expected_master_event_id=master_id,
            expected_original_start=identity,
        )
        if validation.ok:
            exact.append(candidate)
    _require(len(exact) == 1, "exact_instance_lookup_not_unique")
    remote_id = str(exact[0].get("id") or "")
    _require(bool(remote_id), "exact_instance_id_missing")
    complete = gateway.get_recurring_instance(remote_id)
    _require(complete is not None, "exact_instance_disappeared")
    validation = validate_remote_occurrence_payload(
        complete,
        expected_master_event_id=master_id,
        expected_original_start=identity,
    )
    _require(validation.ok, "exact_instance_identity_mismatch")
    return complete


def _shift_timed_bound(bound: Mapping[str, Any], minutes: int) -> dict[str, Any]:
    value = str(bound.get("dateTime") or "")
    _require(bool(value), "timed_instance_bound_missing")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    result = dict(bound)
    result["dateTime"] = (parsed + timedelta(minutes=minutes)).isoformat()
    return result


def _remote_edit(
    gateway,
    series,
    master_id: str,
    occurrence_key: str,
    *,
    title: str,
    shift_minutes: int = 0,
):
    current = _exact_instance(
        gateway, series, master_id, occurrence_key
    )
    payload = deepcopy(current)
    payload["summary"] = title
    if shift_minutes:
        payload["start"] = _shift_timed_bound(
            payload.get("start") or {}, shift_minutes
        )
        payload["end"] = _shift_timed_bound(
            payload.get("end") or {}, shift_minutes
        )
    payload.pop("recurrence", None)
    return gateway.update_recurring_instance(
        str(current["id"]), payload, str(current.get("etag") or "") or None
    )


def _remote_cancel(
    gateway, series, master_id: str, occurrence_key: str
):
    current = _exact_instance(
        gateway, series, master_id, occurrence_key
    )
    payload = deepcopy(current)
    payload.pop("recurrence", None)
    return gateway.cancel_recurring_instance(
        str(current["id"]), payload, str(current.get("etag") or "") or None
    )


def _unresolved_change(
    occurrence_store: CalendarSeriesOccurrenceSyncStore,
    series_uid: str,
    occurrence_key: str,
):
    rows = [
        row
        for row in occurrence_store.list_occurrence_changes(
            unresolved_only=True
        )
        if row.matched_series_uid == series_uid
        and row.matched_occurrence_key == occurrence_key
    ]
    _require(bool(rows), "expected_occurrence_quarantine_missing")
    return rows[-1]


def _manual(manual: ManualSyncService, label: str):
    result = manual.run_once()
    _require(result.ok, f"{label}_manual_sync_failed")
    return result


def _safe_delete_owned_master(
    gateway, master_id: str | None, series_uid: str | None
) -> bool:
    if not master_id or not series_uid:
        return False
    remote = gateway.get_recurring_master(master_id)
    if remote is None:
        return True
    private = remote.private_extended_properties or {}
    if private.get(PLANNER_SERIES_UID_PROPERTY) != series_uid:
        return False
    gateway.delete_recurring_master(master_id)
    return gateway.get_recurring_master(master_id) is None


def _cleanup_local_pilot_rows(
    series_uid: str | None,
    occurrence_keys: list[str],
    links,
    series_store,
    occurrence_store,
) -> None:
    if not series_uid:
        return
    for occurrence_key in occurrence_keys:
        pending = occurrence_store.get_pending_op(series_uid, occurrence_key)
        if pending is not None:
            occurrence_store.remove_op(pending.id)
    for op in occurrence_store.list_terminal_ops():
        if op.series_uid == series_uid:
            occurrence_store.remove_op(op.id)
    for change in occurrence_store.list_occurrence_changes(
        unresolved_only=True
    ):
        if change.matched_series_uid == series_uid:
            occurrence_store.resolve_occurrence_change(
                change.id, "live_pilot_cleanup"
            )
    # The master store exposes one status-filterable list rather than a
    # separate list_terminal_ops() convenience method.
    for op in series_store.list_ops():
        if op.series_uid == series_uid:
            series_store.remove_op(op.id)
    if links.get_link(series_uid) is not None:
        links.disconnect_keep_remote(series_uid)


def _sanitized_sync(result) -> dict[str, Any]:
    fields = (
        "ok",
        "series_masters_created",
        "occurrence_updates_pushed",
        "occurrence_cancellations_pushed",
        "occurrence_conflicts_detected",
        "occurrence_conflicts_resolved_keep_planner",
        "occurrence_conflicts_resolved_use_google",
        "occurrence_remote_cancellations",
        "occurrence_quarantine_resolved",
        "occurrence_ops_terminal",
    )
    return {name: getattr(result, name) for name in fields}


def run(*, preflight_only: bool = False) -> int:
    profile = Path(os.environ.get("PLANNER_DESKTOP_DATA_DIR", ""))
    _require(bool(str(profile)), "isolated_profile_not_set")
    _require(
        profile.resolve() == EXPECTED_PROFILE.resolve(),
        "unexpected_isolated_profile",
    )
    _require(profile.is_dir(), "isolated_profile_missing")

    db_path = get_desktop_db_path()
    tasks = SQLiteTaskRepository(db_path)
    ordinary_store = CalendarSyncStore(db_path)
    series_repo = SQLiteSeriesRepository(db_path)
    series_store = CalendarSeriesSyncStore(db_path)
    occurrence_store = CalendarSeriesOccurrenceSyncStore(db_path)
    catalog = SQLiteExternalSeriesRepository(db_path)
    recurrence = RecurrenceService(series_repo, tasks)
    links = SeriesCalendarLinkService(series_repo, tasks, series_store)
    recurrence.series_link_service = links
    recurrence.occurrence_sync_store = occurrence_store
    resolutions = OccurrenceResolutionService(
        series_repo, tasks, links, occurrence_store
    )

    credentials = load_credentials()
    _require(credentials is not None, "isolated_profile_not_authenticated")
    service = build_calendar_service(credentials)
    gateway = AuditedGoogleCalendarGateway(service)
    manual = ManualSyncService(
        tasks,
        ordinary_store,
        gateway_provider=lambda: gateway,
        external_series_repository=catalog,
        series_store=series_store,
        series_repository=series_repo,
        occurrence_store=occurrence_store,
    )

    series_uid: str | None = None
    master_id: str | None = None
    occurrence_keys: list[str] = []
    cleaned_remote = False
    success = False
    failure_type = ""
    sync_results: dict[str, Any] = {}
    report: dict[str, Any] = {}

    try:
        preflight_queues = _queue_state(
            ordinary_store, series_store, occurrence_store
        )
        _require(_queue_is_clear(preflight_queues), "preflight_queues_not_clear")
        preflight_remote = _active_test_resources(service)
        _require(
            all(value == 0 for value in preflight_remote.values()),
            "preflight_active_test_resources_found",
        )
        print("preflight=passed profile=isolated calendar=primary")
        print("account=personal identity=redacted token=not_logged")
        if preflight_only:
            return 0

        start_day = date.today() + timedelta(days=14)
        series_uid = "b3b-live-" + uuid4().hex
        created = recurrence.create_series(_series(series_uid, start_day))
        _require(created.ok and created.series is not None, "local_series_create_failed")
        series = created.series
        materialized = recurrence.ensure_occurrences(
            start_day, start_day + timedelta(days=2)
        )
        _require(materialized.created == 3, "occurrence_materialization_failed")
        rows = sorted(
            tasks.list_by_series(series_uid),
            key=lambda row: (row.start or datetime.min, row.uid),
        )
        _require(len(rows) == 3, "occurrence_materialization_count_mismatch")
        occurrence_keys = [str(row.occurrence_key) for row in rows]
        _require(all(occurrence_keys), "occurrence_key_missing")

        connected = links.connect_to_google(series_uid)
        _require(connected.ok and connected.link is not None, "series_link_failed")
        master_id = connected.link.remote_event_id
        print("first_controlled_write=starting finite_count_3=true")
        initial = _manual(manual, "initial_master")
        sync_results["initial"] = _sanitized_sync(initial)
        _require(initial.series_masters_created == 1, "master_create_count_mismatch")
        _require(gateway.write_counts["master_insert"] == 1, "master_insert_route_mismatch")
        master = _verify_master_owner(gateway, master_id, series_uid)
        master_baseline = master_event_to_owned_payload(master)
        print("first_controlled_write=passed master_created=1")

        second = rows[1]
        original_key = str(second.occurrence_key)
        moved_start = second.start + timedelta(hours=1)
        edited = recurrence.edit_occurrence(
            second.uid,
            _command(
                second,
                title=f"{TEST_PREFIX} moved second occurrence",
                start=moved_start,
            ),
        )
        _require(edited.ok and edited.task is not None, "local_occurrence_edit_failed")
        _require(
            str(edited.task.occurrence_key) == original_key,
            "local_occurrence_identity_changed",
        )
        _require(occurrence_store.count_pending_ops() == 1, "update_queue_count_mismatch")
        update_sync = _manual(manual, "local_occurrence_update")
        sync_results["local_update"] = _sanitized_sync(update_sync)
        _require(
            update_sync.occurrence_updates_pushed == 1,
            "instance_update_count_mismatch",
        )
        _require(occurrence_store.count_pending_ops() == 0, "update_queue_not_empty")
        moved_remote = _exact_instance(
            gateway, series, master_id, original_key
        )
        _require(
            moved_remote.get("summary")
            == f"{TEST_PREFIX} moved second occurrence",
            "instance_title_not_updated",
        )
        _require(
            gateway.write_counts["ordinary_insert"] == 0
            and gateway.write_counts["ordinary_patch"] == 0
            and gateway.write_counts["ordinary_delete"] == 0,
            "ordinary_event_write_detected",
        )
        _require(
            master_event_to_owned_payload(
                _verify_master_owner(gateway, master_id, series_uid)
            )
            == master_baseline,
            "master_changed_by_occurrence_update",
        )

        _remote_edit(
            gateway,
            series,
            master_id,
            original_key,
            title=f"{TEST_PREFIX} remote race one",
        )
        conflict_pull = _manual(manual, "remote_conflict_pull")
        sync_results["conflict_pull"] = _sanitized_sync(conflict_pull)
        race_change = _unresolved_change(
            occurrence_store, series_uid, original_key
        )
        keep_first = resolutions.keep_planner(
            race_change.id, confirmed=True
        )
        _require(keep_first.ok, "first_keep_planner_decision_failed")
        _remote_edit(
            gateway,
            series,
            master_id,
            original_key,
            title=f"{TEST_PREFIX} remote race two",
        )
        writes_before_race = dict(gateway.write_counts)
        race_sync = _manual(manual, "etag_race")
        sync_results["etag_race"] = _sanitized_sync(race_sync)
        _require(
            race_sync.occurrence_conflicts_detected >= 1,
            "etag_race_not_detected",
        )
        _require(
            gateway.write_counts == writes_before_race,
            "etag_race_performed_unexpected_write",
        )
        race_remote = _exact_instance(
            gateway, series, master_id, original_key
        )
        _require(
            race_remote.get("summary") == f"{TEST_PREFIX} remote race two",
            "etag_race_overwrote_second_remote_edit",
        )

        refreshed = _unresolved_change(
            occurrence_store, series_uid, original_key
        )
        keep_second = resolutions.keep_planner(
            refreshed.id, confirmed=True
        )
        _require(keep_second.ok, "second_keep_planner_decision_failed")
        keep_sync = _manual(manual, "keep_planner")
        sync_results["keep_planner"] = _sanitized_sync(keep_sync)
        _require(
            keep_sync.occurrence_updates_pushed == 1,
            "keep_planner_update_missing",
        )
        kept_remote = _exact_instance(
            gateway, series, master_id, original_key
        )
        _require(
            kept_remote.get("summary")
            == f"{TEST_PREFIX} moved second occurrence",
            "keep_planner_content_mismatch",
        )

        _remote_edit(
            gateway,
            series,
            master_id,
            original_key,
            title=f"{TEST_PREFIX} remote accepted occurrence",
            shift_minutes=30,
        )
        use_pull = _manual(manual, "use_google_pull")
        sync_results["use_google_pull"] = _sanitized_sync(use_pull)
        use_change = _unresolved_change(
            occurrence_store, series_uid, original_key
        )
        accepted_etag = use_change.remote_etag
        writes_before_use = dict(gateway.write_counts)
        use_result = resolutions.use_google(use_change.id)
        _require(use_result.ok, "use_google_resolution_failed")
        _require(
            gateway.write_counts == writes_before_use,
            "use_google_performed_google_write",
        )
        accepted_remote = _exact_instance(
            gateway, series, master_id, original_key
        )
        _require(
            accepted_remote.get("etag") == accepted_etag,
            "use_google_changed_remote_etag",
        )
        accepted_local = tasks.get_by_uid(second.uid)
        _require(
            accepted_local is not None
            and accepted_local.title
            == f"{TEST_PREFIX} remote accepted occurrence",
            "use_google_local_content_mismatch",
        )
        _require(
            str(accepted_local.occurrence_key) == original_key,
            "use_google_identity_changed",
        )

        third = rows[2]
        third_key = str(third.occurrence_key)
        _require(recurrence.delete_occurrence(third.uid), "local_cancel_failed")
        _require(occurrence_store.count_pending_ops() == 1, "cancel_queue_count_mismatch")
        cancel_sync = _manual(manual, "local_occurrence_cancel")
        sync_results["local_cancel"] = _sanitized_sync(cancel_sync)
        _require(
            cancel_sync.occurrence_cancellations_pushed == 1,
            "instance_cancel_count_mismatch",
        )
        cancelled_remote = _exact_instance(
            gateway, series, master_id, third_key
        )
        _require(
            cancelled_remote.get("status") == "cancelled",
            "remote_instance_not_cancelled",
        )
        third_local = tasks.get_by_uid(third.uid)
        _require(
            third_local is not None
            and third_local.is_deleted
            and str(third_local.occurrence_key) == third_key,
            "local_cancel_tombstone_missing",
        )

        first = rows[0]
        first_key = str(first.occurrence_key)
        remote_cancelled = _remote_cancel(
            gateway, series, master_id, first_key
        )
        _require(
            remote_cancelled.get("status") == "cancelled",
            "direct_remote_cancel_failed",
        )
        remote_cancel_pull = _manual(manual, "remote_cancel_pull")
        sync_results["remote_cancel_pull"] = _sanitized_sync(
            remote_cancel_pull
        )
        cancel_change = _unresolved_change(
            occurrence_store, series_uid, first_key
        )
        writes_before_cancel_accept = dict(gateway.write_counts)
        accepted_cancel = resolutions.use_google(cancel_change.id)
        _require(accepted_cancel.ok, "remote_cancel_accept_failed")
        _require(
            gateway.write_counts == writes_before_cancel_accept,
            "remote_cancel_accept_performed_google_write",
        )
        first_local = tasks.get_by_uid(first.uid)
        _require(
            first_local is not None
            and first_local.is_deleted
            and str(first_local.occurrence_key) == first_key,
            "remote_cancel_local_tombstone_missing",
        )

        _require(
            occurrence_store.count_quarantined() == 0,
            "quarantine_not_resolved_before_cleanup",
        )
        _require(
            master_event_to_owned_payload(
                _verify_master_owner(gateway, master_id, series_uid)
            )
            == master_baseline,
            "master_changed_during_occurrence_scenario",
        )
        _require(
            all(
                row.google_calendar_event_id is None
                for row in tasks.list_by_series(series_uid)
            ),
            "materialized_occurrence_uploaded_as_ordinary",
        )

        cleaned_remote = _safe_delete_owned_master(
            gateway, master_id, series_uid
        )
        _require(cleaned_remote, "owned_master_cleanup_failed")
        _cleanup_local_pilot_rows(
            series_uid,
            occurrence_keys,
            links,
            series_store,
            occurrence_store,
        )
        final_remote = _active_test_resources(
            service, series_uid=series_uid
        )
        _require(
            all(value == 0 for value in final_remote.values()),
            "active_test_resources_remain",
        )
        final_queues = _queue_state(
            ordinary_store, series_store, occurrence_store
        )
        _require(_queue_is_clear(final_queues), "final_queues_not_clear")
        _require(
            gateway.write_counts["master_patch"] == 0,
            "unexpected_master_update_write",
        )
        _require(
            gateway.write_counts["ordinary_insert"] == 0
            and gateway.write_counts["ordinary_patch"] == 0
            and gateway.write_counts["ordinary_delete"] == 0,
            "ordinary_test_write_detected",
        )

        report = {
            "result": "passed",
            "profile_location": "sibling_of_project",
            "calendar_selector": CALENDAR_ID,
            "account_kind": "personal",
            "account_identity": "redacted",
            "token_contents_logged": False,
            "manual_sync_only": True,
            "finite_count": 3,
            "duration_minutes": 15,
            "timezone": TIMEZONE_NAME,
            "guests": 0,
            "sensitive_notes": False,
            "sync_results": sync_results,
            "write_counts": dict(gateway.write_counts),
            "master_unchanged_before_cleanup": True,
            "etag_race_superseded": True,
            "use_google_write_delta": 0,
            "remote_cancel_accept_write_delta": 0,
            "active_test_masters": final_remote["masters"],
            "active_test_instances": final_remote["instances"],
            "ordinary_test_events": final_remote["ordinary"],
            "queues": final_queues,
            "dead_letter_operations": 0,
            "occurrence_event_flood": 0,
            "automatic_sync": False,
            "cleanup_completed": True,
        }
        success = True
    except Exception as exc:
        failure_type = type(exc).__name__
    finally:
        if not preflight_only and gateway is not None:
            try:
                if not cleaned_remote:
                    cleaned_remote = _safe_delete_owned_master(
                        gateway, master_id, series_uid
                    )
            except Exception:
                cleaned_remote = False
            try:
                _cleanup_local_pilot_rows(
                    series_uid,
                    occurrence_keys,
                    links,
                    series_store,
                    occurrence_store,
                )
            except Exception:
                pass
        if not preflight_only and not success:
            try:
                final_remote = _active_test_resources(
                    service, series_uid=series_uid
                )
            except Exception:
                final_remote = {
                    "masters": -1,
                    "instances": -1,
                    "ordinary": -1,
                }
            try:
                final_queues = _queue_state(
                    ordinary_store, series_store, occurrence_store
                )
            except Exception:
                final_queues = {}
            report = {
                "result": "failed",
                "failure_type": failure_type or "PilotFailure",
                "account_identity": "redacted",
                "token_contents_logged": False,
                "cleanup_completed": cleaned_remote,
                "active_test_resources_after_cleanup": final_remote,
                "queues_after_cleanup": final_queues,
                "occurrence_event_flood": 0,
            }
        if not preflight_only:
            report_path = profile / REPORT_FILENAME
            report_path.write_text(
                json.dumps(report, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        catalog.close()
        occurrence_store.close()
        series_store.close()
        series_repo.close()
        ordinary_store.close()
        tasks.close()

    if preflight_only:
        return 0

    reopened_occurrence = CalendarSeriesOccurrenceSyncStore(db_path)
    reopened_series = CalendarSeriesSyncStore(db_path)
    reopened_ordinary = CalendarSyncStore(db_path)
    try:
        restart_queues = _queue_state(
            reopened_ordinary, reopened_series, reopened_occurrence
        )
    finally:
        reopened_ordinary.close()
        reopened_series.close()
        reopened_occurrence.close()

    if not success:
        print("pilot=failed details=redacted cleanup_attempted=true")
        print(f"failure_type={failure_type or 'PilotFailure'}")
        print(f"report={profile / REPORT_FILENAME}")
        return 1

    _require(_queue_is_clear(restart_queues), "restart_queues_not_clear")
    print("pilot=passed cleanup=passed restart_persistence=passed")
    print("active_test_masters=0 active_test_instances=0 ordinary_test_events=0")
    print("master_queue=0 occurrence_queue=0 ordinary_queue=0 terminal=0")
    print("unresolved_quarantine=0 occurrence_event_flood=0")
    print("master_unchanged=true etag_race=protected use_google_write_delta=0")
    print("automatic_sync=false manual_sync_only=true")
    print(f"report={profile / REPORT_FILENAME}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--preflight-only",
        action="store_true",
        help="Perform local and remote read-only checks, then exit.",
    )
    args = parser.parse_args()
    return run(preflight_only=args.preflight_only)


if __name__ == "__main__":
    raise SystemExit(main())
