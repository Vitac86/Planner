"""Controlled real-Google acceptance pilot for Phase 3.2B3C1.

Deliberately not an application entry point.  One finite, explicitly
confirmed TEST-only remote split scenario against the isolated sibling
profile and the ``primary`` Calendar selector.  It never prints account
information, credentials, token contents, or remote payloads.

Every remote write requires ``--i-confirm-writes``; without it only the
read-only preflight runs.  Cleanup deletes only masters whose Planner
ownership marker matches a series created by this run.
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from planner_desktop.domain.google_series_split import (
    RemoteSeriesSplitProposal,
    RemoteSeriesSplitStatus,
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
    deterministic_remote_event_id,
)
from planner_desktop.storage.calendar_series_occurrence_sync_store import (
    CalendarSeriesOccurrenceSyncStore,
)
from planner_desktop.storage.calendar_series_remote_split_store import (
    CalendarSeriesRemoteSplitStore,
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
from planner_desktop.sync.google_auth import (
    build_calendar_service,
    load_credentials,
)
from planner_desktop.sync.google_calendar_gateway import GoogleCalendarGateway
from planner_desktop.usecases.manual_sync_service import ManualSyncService
from planner_desktop.usecases.recurrence_service import RecurrenceService
from planner_desktop.usecases.remote_series_split_service import (
    RemoteSeriesSplitService,
)
from planner_desktop.usecases.series_calendar_link_service import (
    SeriesCalendarLinkService,
)

EXPECTED_PROFILE = Path(
    r"D:\Users\v.pyatakov\myspace\planner-desktop-google-series-live-pilot"
)
CALENDAR_ID = "primary"
TEST_PREFIX = "[Planner Phase 3.2B3C1 TEST]"
TIMEZONE_NAME = "Europe/Moscow"
REPORT_FILENAME = "phase3_2b3c1_live_pilot_report.json"


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
            "master_full_update": 0,
            "split_successor_insert": 0,
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
            remote_event_id, master_payload, expected_etag=expected_etag
        )

    def delete_recurring_master(self, remote_event_id):
        self.write_counts["master_delete"] += 1
        return super().delete_recurring_master(remote_event_id)

    def update_recurring_master_full(
        self, remote_event_id, complete_master_payload, expected_etag
    ):
        self.write_counts["master_full_update"] += 1
        return super().update_recurring_master_full(
            remote_event_id, complete_master_payload, expected_etag
        )

    def insert_split_successor_master(
        self, remote_event_id, complete_master_payload
    ):
        self.write_counts["split_successor_insert"] += 1
        return super().insert_split_successor_master(
            remote_event_id, complete_master_payload
        )

    def update_recurring_instance(
        self, instance_event_id, complete_instance_payload, expected_etag
    ):
        self.write_counts["occurrence_update"] += 1
        return super().update_recurring_instance(
            instance_event_id, complete_instance_payload, expected_etag
        )

    def cancel_recurring_instance(
        self, instance_event_id, complete_instance_payload, expected_etag
    ):
        self.write_counts["occurrence_cancel"] += 1
        return super().cancel_recurring_instance(
            instance_event_id, complete_instance_payload, expected_etag
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
    service: Any, *, series_uids: list[str] = ()
) -> dict[str, int]:
    resources: dict[str, dict[str, Any]] = {}
    for single_events in (False, True):
        for item in _page_events(
            service, q=TEST_PREFIX, singleEvents=single_events
        ):
            remote_id = str(item.get("id") or "")
            if remote_id:
                resources[remote_id] = item
        for series_uid in series_uids:
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
    ordinary_store, series_store, occurrence_store, split_store
) -> dict[str, int]:
    active_splits = len([
        plan for plan in split_store.list_processable_plans()
    ]) + int(split_store.counts_by_state().get("conflict", 0))
    return {
        "ordinary_pending": ordinary_store.count_pending_ops(),
        "ordinary_terminal": ordinary_store.count_terminal_ops(),
        "master_pending": series_store.count_pending_ops(),
        "master_terminal": series_store.count_terminal_ops(),
        "occurrence_pending": occurrence_store.count_pending_ops(),
        "occurrence_terminal": occurrence_store.count_terminal_ops(),
        "unresolved_quarantine": occurrence_store.count_quarantined(),
        "active_split_plans": active_splits,
    }


def _queue_is_clear(state: Mapping[str, int]) -> bool:
    return all(int(value) == 0 for value in state.values())


def _series(series_uid: str, start_day: date, *, count: int) -> TaskSeries:
    return TaskSeries(
        uid=series_uid,
        title=f"{TEST_PREFIX} controlled split pilot",
        notes="Finite TEST-only Planner remote split pilot.",
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
            occurrence_count=count,
        ),
    )


def _verify_master_owner(gateway, master_id: str, expected_uid: str):
    resource = gateway.get_recurring_master_resource(master_id)
    _require(resource is not None, "owned_master_missing")
    private = (
        (resource.get("extendedProperties") or {}).get("private") or {}
    )
    _require(
        str(private.get(PLANNER_SERIES_UID_PROPERTY) or "") == expected_uid,
        "owned_master_marker_mismatch",
    )
    return resource


def _active_instances(gateway, master_id: str) -> list[dict[str, Any]]:
    return [
        item
        for item in gateway.list_recurring_instances(
            master_id, None, show_deleted=False
        )
        if str(item.get("status") or "") != "cancelled"
    ]


def _safe_delete_owned_master(gateway, master_id, expected_uid) -> bool:
    if not master_id or not expected_uid:
        return True
    resource = gateway.get_recurring_master_resource(master_id)
    if resource is None:
        return True
    private = (
        (resource.get("extendedProperties") or {}).get("private") or {}
    )
    if str(private.get(PLANNER_SERIES_UID_PROPERTY) or "") != expected_uid:
        return False
    gateway.delete_recurring_master(master_id)
    return gateway.get_recurring_master_resource(master_id) is None


def _sanitized_sync(result) -> dict[str, Any]:
    fields = (
        "ok",
        "series_masters_created",
        "series_masters_updated",
        "remote_splits_started",
        "remote_sources_trimmed",
        "remote_successors_created",
        "remote_splits_finalized",
        "remote_split_conflicts",
        "remote_split_rollbacks_completed",
        "remote_split_terminal",
        "remote_split_reconciliation_completions",
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
    split_store = CalendarSeriesRemoteSplitStore(db_path)
    catalog = SQLiteExternalSeriesRepository(db_path)
    recurrence = RecurrenceService(series_repo, tasks)
    links = SeriesCalendarLinkService(series_repo, tasks, series_store)
    recurrence.series_link_service = links
    recurrence.occurrence_sync_store = occurrence_store
    splits = RemoteSeriesSplitService(
        series_repo, tasks, series_store, occurrence_store, split_store,
        external_series_repository=catalog,
    )
    recurrence.remote_split_service = splits
    links.remote_split_service = splits

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
        split_store=split_store,
    )

    source_uid: str | None = None
    race_uid: str | None = None
    successor_uid: str | None = None
    source_master_id: str | None = None
    successor_master_id: str | None = None
    race_master_id: str | None = None
    race_plan_id: int | None = None
    success = False
    failure_type = ""
    sync_results: dict[str, Any] = {}
    report: dict[str, Any] = {}

    def _cleanup_local(series_uid: str | None) -> None:
        if not series_uid:
            return
        for op in series_store.list_ops():
            if op.series_uid == series_uid:
                series_store.remove_op(op.id)
        active = split_store.get_active_plan(series_uid)
        if active is not None:
            split_store.mark_terminal(active.id, "live_pilot_cleanup")
        if links.get_link(series_uid) is not None:
            links.disconnect_keep_remote(series_uid)

    try:
        preflight_queues = _queue_state(
            ordinary_store, series_store, occurrence_store, split_store
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

        # ---- 1. one future COUNT=5 linked TEST master ----------------------
        start_day = date.today() + timedelta(days=14)
        source_uid = "b3c1-live-" + uuid4().hex
        created = recurrence.create_series(
            _series(source_uid, start_day, count=5)
        )
        _require(created.ok, "local_series_create_failed")
        recurrence.ensure_occurrences(start_day, start_day + timedelta(days=6))
        rows = sorted(
            tasks.list_by_series(source_uid),
            key=lambda row: (row.start or datetime.min, row.uid),
        )
        _require(len(rows) == 5, "occurrence_materialization_count_mismatch")
        connected = links.connect_to_google(source_uid)
        _require(connected.ok and connected.link is not None, "series_link_failed")
        source_master_id = connected.link.remote_event_id
        print("first_controlled_write=starting finite_count_5=true")
        initial = manual.run_once()
        _require(initial.ok, "initial_master_sync_failed")
        sync_results["initial"] = _sanitized_sync(initial)
        _require(initial.series_masters_created == 1, "master_create_count_mismatch")
        _require(
            gateway.write_counts["master_insert"] == 1,
            "master_insert_route_mismatch",
        )

        # ---- 2-4. split from the third occurrence; new title and time ------
        target = rows[2]
        plan_result = splits.create_split_plan(
            source_uid,
            str(target.occurrence_key),
            RemoteSeriesSplitProposal(
                title=f"{TEST_PREFIX} split successor",
                local_time=time(11, 30),
            ),
        )
        _require(plan_result.ok and plan_result.record is not None,
                 "split_plan_create_failed")
        plan_id = int(plan_result.record.id)
        successor_uid = plan_result.record.reserved_successor_series_uid
        successor_master_id = plan_result.record.successor_remote_event_id
        _require(
            successor_master_id == deterministic_remote_event_id(successor_uid),
            "successor_id_not_deterministic",
        )
        split_sync = manual.run_once()
        _require(split_sync.ok, "split_manual_sync_failed")
        sync_results["split"] = _sanitized_sync(split_sync)
        _require(split_sync.remote_splits_finalized == 1, "split_not_finalized")
        _require(
            gateway.write_counts["master_full_update"] == 1,
            "source_trim_write_count_mismatch",
        )
        _require(
            gateway.write_counts["split_successor_insert"] == 1,
            "successor_insert_write_count_mismatch",
        )
        _require(
            split_store.get_plan(plan_id).state
            is RemoteSeriesSplitStatus.COMPLETED,
            "split_plan_not_completed",
        )

        # ---- 5-7. exact partition and two deterministic IDs ----------------
        source_resource = _verify_master_owner(
            gateway, source_master_id, source_uid
        )
        successor_resource = _verify_master_owner(
            gateway, successor_master_id, successor_uid
        )
        _require(
            source_master_id != successor_master_id,
            "master_ids_not_distinct",
        )
        _require(
            "COUNT=2" in str(source_resource.get("recurrence")),
            "source_not_trimmed_to_two",
        )
        _require(
            "COUNT=3" in str(successor_resource.get("recurrence")),
            "successor_count_mismatch",
        )
        _require(
            str(successor_resource.get("summary") or "")
            == f"{TEST_PREFIX} split successor",
            "successor_title_mismatch",
        )
        source_instances = _active_instances(gateway, source_master_id)
        successor_instances = _active_instances(gateway, successor_master_id)
        _require(len(source_instances) == 2, "source_instance_count_mismatch")
        _require(
            len(successor_instances) == 3, "successor_instance_count_mismatch"
        )
        source_days = sorted(
            str((item.get("originalStartTime") or {}).get("dateTime") or "")[:10]
            for item in source_instances
        )
        successor_days = sorted(
            str((item.get("originalStartTime") or {}).get("dateTime") or "")[:10]
            for item in successor_instances
        )
        expected_days = [
            (start_day + timedelta(days=offset)).isoformat()
            for offset in range(5)
        ]
        _require(source_days == expected_days[:2], "source_days_mismatch")
        _require(successor_days == expected_days[2:], "successor_days_mismatch")
        _require(
            all("T11:30" in str(
                (item.get("originalStartTime") or {}).get("dateTime") or ""
            ) for item in successor_instances),
            "successor_time_mismatch",
        )

        # ---- 8. no ordinary TEST events ------------------------------------
        mid_remote = _active_test_resources(
            service,
            series_uids=[source_uid, successor_uid],
        )
        _require(mid_remote["ordinary"] == 0, "ordinary_test_event_detected")
        _require(mid_remote["masters"] == 2, "unexpected_master_count")
        _require(
            all(
                row.google_calendar_event_id is None
                for uid in (source_uid, successor_uid)
                for row in tasks.list_by_series(uid)
            ),
            "materialized_occurrence_uploaded_as_ordinary",
        )

        # ---- 9. unchanged sync: zero update/insert -------------------------
        writes_before_idle = dict(gateway.write_counts)
        idle = manual.run_once()
        _require(idle.ok, "idle_sync_failed")
        sync_results["idle"] = _sanitized_sync(idle)
        _require(
            gateway.write_counts == writes_before_idle,
            "idle_sync_performed_write",
        )

        # ---- 10. remote ETag race on a fresh TEST series -------------------
        race_uid = "b3c1-race-" + uuid4().hex
        race_created = recurrence.create_series(
            _series(race_uid, start_day + timedelta(days=10), count=4)
        )
        _require(race_created.ok, "race_series_create_failed")
        recurrence.ensure_occurrences(
            start_day + timedelta(days=10), start_day + timedelta(days=16)
        )
        race_connected = links.connect_to_google(race_uid)
        _require(race_connected.ok, "race_series_link_failed")
        race_master_id = race_connected.link.remote_event_id
        race_master_sync = manual.run_once()
        _require(
            race_master_sync.ok
            and race_master_sync.series_masters_created == 1,
            "race_master_create_failed",
        )
        race_rows = sorted(
            tasks.list_by_series(race_uid),
            key=lambda row: (row.start or datetime.min, row.uid),
        )
        race_plan = splits.create_split_plan(
            race_uid,
            str(race_rows[2].occurrence_key),
            RemoteSeriesSplitProposal(title=f"{TEST_PREFIX} race successor"),
        )
        _require(race_plan.ok, "race_plan_create_failed")
        race_plan_id = int(race_plan.record.id)
        # External foreign edit through the raw service (not a Planner route).
        service.events().patch(
            calendarId=CALENDAR_ID,
            eventId=race_master_id,
            body={"summary": f"{TEST_PREFIX} externally edited race master"},
        ).execute()
        writes_before_race = dict(gateway.write_counts)
        race_sync = manual.run_once()
        _require(race_sync.ok, "race_sync_failed")
        sync_results["race"] = _sanitized_sync(race_sync)
        _require(
            race_sync.remote_split_conflicts >= 1, "race_conflict_not_detected"
        )
        _require(
            gateway.write_counts["master_full_update"]
            == writes_before_race["master_full_update"]
            and gateway.write_counts["split_successor_insert"]
            == writes_before_race["split_successor_insert"],
            "race_performed_split_write",
        )
        _require(
            split_store.get_plan(race_plan_id).state
            is RemoteSeriesSplitStatus.CONFLICT,
            "race_plan_not_conflicted",
        )
        _require(
            gateway.get_recurring_master_resource(
                race_plan.record.successor_remote_event_id
            ) is None,
            "race_successor_unexpectedly_created",
        )

        # ---- 11-12. cleanup and final verification -------------------------
        split_store.mark_terminal(race_plan_id, "live_pilot_cleanup")
        _require(
            _safe_delete_owned_master(gateway, source_master_id, source_uid),
            "source_master_cleanup_failed",
        )
        _require(
            _safe_delete_owned_master(
                gateway, successor_master_id, successor_uid
            ),
            "successor_master_cleanup_failed",
        )
        _require(
            _safe_delete_owned_master(gateway, race_master_id, race_uid),
            "race_master_cleanup_failed",
        )
        for uid in (source_uid, successor_uid, race_uid):
            _cleanup_local(uid)
        final_remote = _active_test_resources(
            service,
            series_uids=[source_uid, successor_uid, race_uid],
        )
        _require(
            all(value == 0 for value in final_remote.values()),
            "active_test_resources_remain",
        )
        final_queues = _queue_state(
            ordinary_store, series_store, occurrence_store, split_store
        )
        _require(_queue_is_clear(final_queues), "final_queues_not_clear")
        _require(
            gateway.write_counts["ordinary_insert"] == 0
            and gateway.write_counts["ordinary_patch"] == 0
            and gateway.write_counts["ordinary_delete"] == 0,
            "ordinary_test_write_detected",
        )
        _require(
            gateway.write_counts["master_patch"] == 0,
            "unexpected_master_patch_write",
        )

        report = {
            "result": "passed",
            "profile_location": "sibling_of_project",
            "calendar_selector": CALENDAR_ID,
            "account_kind": "personal",
            "account_identity": "redacted",
            "token_contents_logged": False,
            "manual_sync_only": True,
            "finite_count": 5,
            "duration_minutes": 15,
            "timezone": TIMEZONE_NAME,
            "guests": 0,
            "sensitive_notes": False,
            "sync_results": sync_results,
            "write_counts": dict(gateway.write_counts),
            "split_partition": {"source": 2, "successor": 3},
            "deterministic_ids_distinct": True,
            "idle_sync_write_delta": 0,
            "etag_race_split_writes": 0,
            "active_test_masters": final_remote["masters"],
            "active_test_instances": final_remote["instances"],
            "ordinary_test_events": final_remote["ordinary"],
            "queues": final_queues,
            "occurrence_event_flood": 0,
            "automatic_sync": False,
            "adoption_tested": False,
            "cleanup_completed": True,
        }
        success = True
    except Exception as exc:
        failure_type = type(exc).__name__
    finally:
        if not preflight_only and not success:
            try:
                for master_id, uid in (
                    (source_master_id, source_uid),
                    (successor_master_id, successor_uid),
                    (race_master_id, race_uid),
                ):
                    _safe_delete_owned_master(gateway, master_id, uid)
                for uid in (source_uid, successor_uid, race_uid):
                    _cleanup_local(uid)
            except Exception:
                pass
            try:
                final_remote = _active_test_resources(
                    service,
                    series_uids=[
                        uid for uid in (source_uid, successor_uid, race_uid)
                        if uid
                    ],
                )
            except Exception:
                final_remote = {"masters": -1, "instances": -1, "ordinary": -1}
            try:
                final_queues = _queue_state(
                    ordinary_store, series_store, occurrence_store, split_store
                )
            except Exception:
                final_queues = {}
            report = {
                "result": "failed",
                "failure_type": failure_type or "PilotFailure",
                "account_identity": "redacted",
                "token_contents_logged": False,
                "active_test_resources_after_cleanup": final_remote,
                "queues_after_cleanup": final_queues,
                "occurrence_event_flood": 0,
            }
        if not preflight_only:
            (profile / REPORT_FILENAME).write_text(
                json.dumps(report, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        catalog.close()
        split_store.close()
        occurrence_store.close()
        series_store.close()
        series_repo.close()
        ordinary_store.close()
        tasks.close()

    if preflight_only:
        return 0

    # Restart persistence: reopened stores stay clear.
    reopened_split = CalendarSeriesRemoteSplitStore(db_path)
    reopened_occurrence = CalendarSeriesOccurrenceSyncStore(db_path)
    reopened_series = CalendarSeriesSyncStore(db_path)
    reopened_ordinary = CalendarSyncStore(db_path)
    try:
        restart_queues = _queue_state(
            reopened_ordinary,
            reopened_series,
            reopened_occurrence,
            reopened_split,
        )
        _require(_queue_is_clear(restart_queues), "restart_queues_not_clear")
    finally:
        reopened_ordinary.close()
        reopened_series.close()
        reopened_occurrence.close()
        reopened_split.close()

    print(f"result={'passed' if success else 'failed'}")
    print(f"report={EXPECTED_PROFILE / REPORT_FILENAME}")
    return 0 if success else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--preflight-only",
        action="store_true",
        help="run only the read-only preflight; zero Google writes",
    )
    parser.add_argument(
        "--i-confirm-writes",
        action="store_true",
        help="explicit confirmation for finite TEST-only Google writes",
    )
    args = parser.parse_args()
    if not args.preflight_only and not args.i_confirm_writes:
        print(
            "refused: pass --i-confirm-writes for the write phase or "
            "--preflight-only for the read-only check"
        )
        return 2
    return run(preflight_only=args.preflight_only)


if __name__ == "__main__":
    raise SystemExit(main())
