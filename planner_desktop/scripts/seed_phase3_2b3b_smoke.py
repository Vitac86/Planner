"""Run the isolated fake Phase 3.2B3B occurrence-sync acceptance smoke.

This script uses FakeCalendarGateway only. It performs no OAuth or network
operation and leaves durable SQLite states for the screenshot capture script.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, time, timedelta, timezone
import json
import os
from pathlib import Path
from types import SimpleNamespace

from planner_desktop.domain.commands import TaskEditorCommand
from planner_desktop.domain.google_occurrence import (
    OccurrenceSyncStatus,
    local_occurrence_to_google_original_start,
)
from planner_desktop.domain.recurrence import (
    RecurrenceEndMode,
    RecurrenceFrequency,
    RecurrenceRule,
    SeriesSchedule,
    TaskSeries,
)
from planner_desktop.domain.task import Task
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
from planner_desktop.storage.tag_repository import SQLiteTagRepository
from planner_desktop.sync.calendar_series_occurrence_mapper import (
    task_to_occurrence_owned_payload,
)
from planner_desktop.sync.calendar_sync_engine import CalendarSyncEngine
from planner_desktop.sync.fake_calendar_gateway import FakeCalendarGateway
from planner_desktop.usecases.external_series_service import ExternalSeriesService
from planner_desktop.usecases.manual_sync_service import ManualSyncService
from planner_desktop.usecases.occurrence_resolution_service import (
    OccurrenceResolutionService,
)
from planner_desktop.usecases.recurrence_service import RecurrenceService
from planner_desktop.usecases.series_calendar_link_service import (
    SeriesCalendarLinkService,
)
from planner_desktop.usecases.tag_service import TagService
from planner_desktop.usecases.task_service import DesktopTaskService
from planner_desktop.viewmodels.settings_viewmodel import SettingsViewModel


DAY = date(2026, 8, 3)
BASE = datetime(2026, 8, 1, 8, tzinfo=timezone.utc)
TIMED_UID = "b3b-timed"
ALL_DAY_UID = "b3b-all-day"


def _series(uid: str, title: str, *, all_day: bool) -> TaskSeries:
    return TaskSeries(
        uid=uid,
        title=title,
        notes="Fake Phase 3.2B3B acceptance series",
        priority=2,
        schedule=SeriesSchedule(
            start_date=DAY,
            all_day=all_day,
            local_time=None if all_day else time(9),
            duration_minutes=None if all_day else 30,
            timezone_name="Europe/Moscow",
        ),
        rule=RecurrenceRule(
            RecurrenceFrequency.DAILY,
            end_mode=RecurrenceEndMode.COUNT,
            occurrence_count=9,
        ),
    )


def _live_rows(tasks, series_uid: str):
    return sorted(
        (row for row in tasks.list_by_series(series_uid) if not row.is_deleted),
        key=lambda row: (row.start or datetime.min, row.uid),
    )


def _instance_payload(task, series, master_id: str, instance_id: str):
    owned = task_to_occurrence_owned_payload(task, series)
    return {
        "id": instance_id,
        "etag": '"1"',
        **owned,
        "recurringEventId": master_id,
        "originalStartTime": local_occurrence_to_google_original_start(
            series, str(task.occurrence_key)
        ).to_google(),
        "extendedProperties": {
            "private": {},
            "shared": {"fake_smoke_field": "preserved"},
        },
        "attendees": [{"email": "preserved@example.test"}],
        "location": "Preserved fake location",
    }


def _remote_edit(gateway, instance_id: str, **fields):
    payload = gateway.get_recurring_instance(instance_id)
    assert payload is not None
    revision = int(str(payload.get("etag") or '"0"').strip('"')) + 1
    payload.update(deepcopy(fields))
    payload["etag"] = f'"{revision}"'
    payload.pop("updated", None)
    return gateway.seed_recurring_instance(payload)


def _command(
    task,
    *,
    title: str | None = None,
    notes: str | None = None,
    start: datetime | None = None,
    duration: int | None = None,
    priority: int | None = None,
    completed: bool | None = None,
):
    actual_start = start or task.start
    assert actual_start is not None
    return TaskEditorCommand(
        title=title if title is not None else task.title,
        notes=notes if notes is not None else task.notes,
        priority=task.priority if priority is None else priority,
        completed=task.completed if completed is None else completed,
        add_to_calendar=True,
        is_all_day=task.is_all_day,
        date_text=actual_start.date().isoformat(),
        time_text="" if task.is_all_day else actual_start.strftime("%H:%M"),
        duration_text=(
            ""
            if task.is_all_day
            else str(duration or task.duration_minutes or 30)
        ),
    )


def _unresolved_for(store, occurrence_key: str):
    rows = [
        row
        for row in store.list_occurrence_changes(unresolved_only=True)
        if row.matched_occurrence_key == occurrence_key
    ]
    assert rows, occurrence_key
    return rows[-1]


def main() -> int:
    data_dir = os.environ.get("PLANNER_DESKTOP_DATA_DIR")
    if not data_dir:
        raise SystemExit("Set PLANNER_DESKTOP_DATA_DIR to an isolated directory.")
    db_path = get_desktop_db_path()
    tasks = SQLiteTaskRepository(db_path)
    ordinary_store = CalendarSyncStore(db_path)
    series_repo = SQLiteSeriesRepository(db_path)
    series_store = CalendarSeriesSyncStore(db_path)
    occurrence_store = CalendarSeriesOccurrenceSyncStore(db_path)
    catalog = SQLiteExternalSeriesRepository(db_path)
    tag_repo = SQLiteTagRepository(db_path)
    tags = TagService(tag_repo, tasks)
    recurrence = RecurrenceService(series_repo, tasks, tag_service=tags)
    links = SeriesCalendarLinkService(
        series_repo, tasks, series_store, today_provider=lambda: DAY
    )
    recurrence.series_link_service = links
    recurrence.occurrence_sync_store = occurrence_store
    resolutions = OccurrenceResolutionService(
        series_repo, tasks, links, occurrence_store
    )
    gateway = FakeCalendarGateway(base_time=BASE)
    manual = ManualSyncService(
        tasks,
        ordinary_store,
        gateway_provider=lambda: gateway,
        external_series_repository=catalog,
        series_store=series_store,
        series_repository=series_repo,
        occurrence_store=occurrence_store,
    )
    pull = CalendarSyncEngine(
        tasks,
        ordinary_store,
        gateway,
        catalog,
        series_link_store=series_store,
        occurrence_sync_store=occurrence_store,
        series_repository=series_repo,
    )

    if series_repo.get_by_uid(TIMED_UID) is not None:
        raise SystemExit(
            "Smoke data already exists; use a fresh isolated profile directory."
        )

    timed = recurrence.create_series(
        _series(TIMED_UID, "TEST B3B timed linked series", all_day=False)
    ).series
    all_day = recurrence.create_series(
        _series(ALL_DAY_UID, "TEST B3B all-day linked series", all_day=True)
    ).series
    assert timed is not None and all_day is not None
    recurrence.ensure_occurrences(DAY, DAY + timedelta(days=8))
    assert links.connect_to_google(TIMED_UID).ok
    assert links.connect_to_google(ALL_DAY_UID).ok
    master_sync = manual.run_once()
    assert master_sync.ok and master_sync.series_masters_created == 2

    timed_master = series_store.get_link(TIMED_UID)
    all_day_master = series_store.get_link(ALL_DAY_UID)
    assert timed_master is not None and all_day_master is not None
    master_bases = {
        TIMED_UID: gateway.get_recurring_master(timed_master.remote_event_id).etag,
        ALL_DAY_UID: gateway.get_recurring_master(all_day_master.remote_event_id).etag,
    }
    timed_rows = _live_rows(tasks, TIMED_UID)
    all_day_rows = _live_rows(tasks, ALL_DAY_UID)
    assert len(timed_rows) == 9 and len(all_day_rows) == 9

    instance_ids = {}
    for index, task in enumerate(timed_rows):
        instance_id = f"b3b-timed-instance-{index + 1}"
        instance_ids[task.occurrence_key] = instance_id
        gateway.seed_recurring_instance(
            _instance_payload(
                task, timed, timed_master.remote_event_id, instance_id
            )
        )
    for index, task in enumerate(all_day_rows):
        instance_id = f"b3b-all-day-instance-{index + 1}"
        instance_ids[task.occurrence_key] = instance_id
        gateway.seed_recurring_instance(
            _instance_payload(
                task, all_day, all_day_master.remote_event_id, instance_id
            )
        )
    # Establish a pull cursor after installing the baseline instances.
    pull.pull_remote_changes()
    assert ordinary_store.count_pending_ops() == 0
    gateway.reset_call_counts()

    # Coalesced move + resize => exactly one recurring-instance UPDATE.
    moved = timed_rows[0]
    assert recurrence.edit_occurrence(
        moved.uid,
        _command(
            moved,
            title="TEST B3B moved local exception",
            notes="Moved without changing the original occurrence key",
            start=moved.start + timedelta(hours=1),
        ),
    ).ok
    moved = tasks.get_by_uid(moved.uid)
    assert recurrence.edit_occurrence(
        moved.uid, _command(moved, duration=45)
    ).ok
    assert occurrence_store.count_pending_ops() == 1
    occurrence_update = manual.run_once()
    assert occurrence_update.ok
    assert occurrence_update.occurrence_updates_pushed == 1
    assert occurrence_update.occurrence_cancellations_pushed == 0

    # One local tombstone => exactly one recurring-instance cancellation.
    cancelled_local = timed_rows[1]
    assert recurrence.delete_occurrence(cancelled_local.uid)
    occurrence_cancel = manual.run_once()
    assert occurrence_cancel.ok
    assert occurrence_cancel.occurrence_cancellations_pushed == 1
    assert tasks.get_by_uid(cancelled_local.uid).is_deleted
    occurrence_write_count = gateway.write_call_count
    assert occurrence_write_count == 2
    assert gateway.get_recurring_instance(
        instance_ids[cancelled_local.occurrence_key]
    )["status"] == "cancelled"

    # Completion, priority and tags stay local and enqueue no instance op.
    completed = timed_rows[2]
    assert recurrence.edit_occurrence(
        completed.uid,
        _command(completed, priority=3, completed=True),
    ).ok
    local_tag = tags.create("b3b-local-only")
    tags.set_task_tags(completed.uid, [local_tag.id])
    assert occurrence_store.count_pending_ops() == 0
    assert tasks.get_by_uid(completed.uid).completed

    # Use Google: supported remote move/title, zero Google writes.
    use_google_task = timed_rows[3]
    use_id = instance_ids[use_google_task.occurrence_key]
    remote = gateway.get_recurring_instance(use_id)
    remote_start = datetime.fromisoformat(remote["start"]["dateTime"])
    remote_end = datetime.fromisoformat(remote["end"]["dateTime"])
    _remote_edit(
        gateway,
        use_id,
        summary="TEST B3B remote occurrence accepted",
        start={
            "dateTime": (remote_start + timedelta(hours=2)).isoformat(),
            "timeZone": "Europe/Moscow",
        },
        end={
            "dateTime": (remote_end + timedelta(hours=2)).isoformat(),
            "timeZone": "Europe/Moscow",
        },
    )
    pull.pull_remote_changes()
    use_change = _unresolved_for(
        occurrence_store, str(use_google_task.occurrence_key)
    )
    before_use = gateway.write_call_count
    assert resolutions.use_google(use_change.id).ok
    assert gateway.write_call_count == before_use

    # Keep both: independent ordinary local Task, no automatic Google event.
    keep_both_task = all_day_rows[0]
    keep_both_id = instance_ids[keep_both_task.occurrence_key]
    _remote_edit(
        gateway,
        keep_both_id,
        summary="TEST B3B remote kept as local copy",
    )
    pull.pull_remote_changes()
    keep_both_change = _unresolved_for(
        occurrence_store, str(keep_both_task.occurrence_key)
    )
    before_keep_both = gateway.write_call_count
    keep_both_result = resolutions.keep_both_as_local_copy(
        keep_both_change.id
    )
    assert keep_both_result.ok
    assert keep_both_result.task.series_uid is None
    assert keep_both_result.task.google_calendar_event_id is None
    assert gateway.write_call_count == before_keep_both

    # Accept one remote cancellation as a local tombstone.
    accepted_cancel = timed_rows[4]
    accepted_cancel_id = instance_ids[accepted_cancel.occurrence_key]
    _remote_edit(gateway, accepted_cancel_id, status="cancelled")
    pull.pull_remote_changes()
    accepted_change = _unresolved_for(
        occurrence_store, str(accepted_cancel.occurrence_key)
    )
    assert resolutions.use_google(accepted_change.id).ok
    assert tasks.get_by_uid(accepted_cancel.uid).is_deleted

    # Second-edit race: acknowledged ETag is superseded; no overwrite.
    race_task = timed_rows[5]
    race_id = instance_ids[race_task.occurrence_key]
    _remote_edit(gateway, race_id, summary="TEST B3B remote race one")
    pull.pull_remote_changes()
    race_change = _unresolved_for(
        occurrence_store, str(race_task.occurrence_key)
    )
    assert resolutions.keep_planner(race_change.id, confirmed=True).ok
    _remote_edit(gateway, race_id, summary="TEST B3B remote race two")
    before_race = gateway.write_call_count
    race_sync = manual.run_once()
    assert race_sync.ok and race_sync.occurrence_conflicts_detected >= 1
    assert gateway.write_call_count == before_race
    assert gateway.get_recurring_instance(race_id)["summary"] == (
        "TEST B3B remote race two"
    )

    # Leave one changed and one cancelled instance unresolved for Settings UI.
    unresolved_changed = all_day_rows[1]
    _remote_edit(
        gateway,
        instance_ids[unresolved_changed.occurrence_key],
        summary="TEST B3B unresolved changed all-day occurrence",
    )
    unresolved_cancelled = timed_rows[6]
    _remote_edit(
        gateway,
        instance_ids[unresolved_cancelled.occurrence_key],
        status="cancelled",
    )
    pull.pull_remote_changes()
    assert _unresolved_for(
        occurrence_store, str(unresolved_changed.occurrence_key)
    )
    assert _unresolved_for(
        occurrence_store, str(unresolved_cancelled.occurrence_key)
    )

    # Master resources were never touched by any occurrence operation.
    assert gateway.get_recurring_master(timed_master.remote_event_id).etag == (
        master_bases[TIMED_UID]
    )
    assert gateway.get_recurring_master(all_day_master.remote_event_id).etag == (
        master_bases[ALL_DAY_UID]
    )
    assert gateway.write_call_count == occurrence_write_count
    assert all(
        row.google_calendar_event_id is None
        for uid in (TIMED_UID, ALL_DAY_UID)
        for row in tasks.list_by_series(uid)
    )

    # Ordinary Task sync and master sync remain operational and isolated.
    desktop = DesktopTaskService(tasks, ordinary_store)
    ordinary = desktop.create_task(
        Task(
            title="TEST B3B ordinary task",
            start=datetime(2026, 8, 3, 15),
            end=datetime(2026, 8, 3, 15, 30),
        )
    )
    ordinary_sync = manual.run_once()
    assert ordinary_sync.ok
    assert tasks.get_by_uid(ordinary.uid).google_calendar_event_id
    ordinary_remote = [
        event
        for event in gateway.events
        if not event.is_recurring_master and not event.is_recurring_instance
        and not event.is_cancelled
    ]
    assert len(ordinary_remote) == 1

    # Settings/UI reads are local and do not call the fake gateway.
    desktop.recurrence_service = recurrence
    settings = SettingsViewModel(
        desktop,
        connection_checker=lambda: SimpleNamespace(
            connected=False,
            has_client_secret=False,
            token_path="",
            client_secret_path="",
        ),
        external_series_service=ExternalSeriesService(catalog),
        series_link_service=links,
        series_sync_store=series_store,
        occurrence_sync_store=occurrence_store,
        occurrence_resolution_service=resolutions,
    )
    calls_before_ui = (gateway.write_call_count, gateway.list_call_count)
    settings.refresh()
    quarantine_rows = settings.quarantinedOccurrenceRows
    _ = settings.pendingOccurrenceUpdateCount
    _ = settings.remoteCancelledOccurrenceCount
    assert (gateway.write_call_count, gateway.list_call_count) == calls_before_ui
    assert len(quarantine_rows) >= 2

    diagnostics = occurrence_store.diagnostics()
    report = {
        "profile": str(Path(data_dir)),
        "db": str(db_path),
        "master_sync": master_sync.__dict__,
        "occurrence_update": occurrence_update.__dict__,
        "occurrence_cancel": occurrence_cancel.__dict__,
        "race_sync": race_sync.__dict__,
        "ordinary_sync": ordinary_sync.__dict__,
        "occurrence_writes": occurrence_write_count,
        "master_etags_unchanged": True,
        "ordinary_occurrence_event_flood": 0,
        "settings_gateway_call_delta": [0, 0],
        "diagnostics": diagnostics,
        "moved_task_uid": moved.uid,
        "completed_task_uid": completed.uid,
        "unresolved_changed_key": unresolved_changed.occurrence_key,
        "unresolved_cancelled_key": unresolved_cancelled.occurrence_key,
        "quarantine_row_count": len(quarantine_rows),
        "real_google_calls": 0,
    }
    report_path = Path(data_dir) / "phase3_2b3b_smoke_report.json"
    report_path.write_text(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=2,
            default=lambda value: (
                value.isoformat()
                if isinstance(value, (date, datetime))
                else str(value)
            ),
        ),
        encoding="utf-8",
    )

    tag_repo.close()
    catalog.close()
    occurrence_store.close()
    series_store.close()
    series_repo.close()
    ordinary_store.close()
    tasks.close()

    # Reopen verifies durable occurrence links, cancellation and quarantine.
    reopened = CalendarSeriesOccurrenceSyncStore(db_path)
    moved_link = reopened.get_occurrence_link(
        TIMED_UID, str(moved.occurrence_key)
    )
    cancelled_link = reopened.get_occurrence_link(
        TIMED_UID, str(cancelled_local.occurrence_key)
    )
    assert moved_link.sync_status is OccurrenceSyncStatus.SYNCED_EXCEPTION
    assert cancelled_link.sync_status is OccurrenceSyncStatus.CANCELLED
    assert cancelled_link.remote_instance_event_id
    assert len(reopened.list_occurrence_changes(unresolved_only=True)) >= 2
    reopened.close()

    print(f"profile={data_dir}")
    print(f"db={db_path}")
    print("instance_update=1 instance_cancel=1 master_unchanged=true")
    print("occurrence_event_flood=0 ordinary_sync=true master_sync=true")
    print("use_google=local_only keep_both=local_only remote_cancel_accept=true")
    print("etag_race=superseded restart_persistence=true")
    print("settings_gateway_calls=0 real_google_calls=0")
    print(f"report={report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
