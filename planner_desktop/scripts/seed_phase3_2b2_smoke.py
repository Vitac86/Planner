"""Run the isolated fake Phase 3.2B2 master-write smoke and seed UI states."""
from __future__ import annotations

import json
import os
from dataclasses import replace
from datetime import date, datetime, time, timezone
from pathlib import Path
from types import SimpleNamespace

from planner_desktop.domain.recurrence import (
    RecurrenceEndMode,
    RecurrenceFrequency,
    RecurrenceRule,
    SeriesSchedule,
    TaskSeries,
)
from planner_desktop.domain.series_calendar_link import SeriesLinkStatus
from planner_desktop.domain.task import Task
from planner_desktop.storage.calendar_series_sync_store import CalendarSeriesSyncStore
from planner_desktop.storage.calendar_sync_store import CalendarSyncStore
from planner_desktop.storage.external_series_repository import SQLiteExternalSeriesRepository
from planner_desktop.storage.paths import get_desktop_db_path
from planner_desktop.storage.series_repository import SQLiteSeriesRepository
from planner_desktop.storage.sqlite_task_repository import SQLiteTaskRepository
from planner_desktop.storage.tag_repository import SQLiteTagRepository
from planner_desktop.sync.calendar_series_sync_engine import CalendarSeriesSyncEngine
from planner_desktop.sync.fake_calendar_gateway import FakeCalendarGateway
from planner_desktop.sync.sync_types import CalendarEvent
from planner_desktop.usecases.external_series_service import ExternalSeriesService
from planner_desktop.usecases.manual_sync_service import ManualSyncService
from planner_desktop.usecases.recurrence_service import RecurrenceService
from planner_desktop.usecases.series_calendar_link_service import SeriesCalendarLinkService
from planner_desktop.usecases.tag_service import TagService
from planner_desktop.usecases.task_service import DesktopTaskService
from planner_desktop.viewmodels.settings_viewmodel import SettingsViewModel


DAY = date(2026, 7, 15)
BASE = datetime(2026, 7, 15, 8, tzinfo=timezone.utc)


def _series(uid: str, title: str, *, hour: int = 9) -> TaskSeries:
    return TaskSeries(
        uid=uid,
        title=title,
        notes="Синтетическая серия visual smoke",
        schedule=SeriesSchedule(
            DAY, False, time(hour), 30, "Europe/Moscow"
        ),
        rule=RecurrenceRule(
            RecurrenceFrequency.DAILY,
            end_mode=RecurrenceEndMode.COUNT,
            occurrence_count=5,
        ),
    )


def main() -> int:
    data_dir = os.environ.get("PLANNER_DESKTOP_DATA_DIR")
    if not data_dir:
        raise SystemExit("Set PLANNER_DESKTOP_DATA_DIR to an isolated directory.")
    db_path = get_desktop_db_path()
    tasks = SQLiteTaskRepository(db_path)
    ordinary_queue = CalendarSyncStore(db_path)
    series_repo = SQLiteSeriesRepository(db_path)
    series_store = CalendarSeriesSyncStore(db_path)
    catalog = SQLiteExternalSeriesRepository(db_path)
    tag_repo = SQLiteTagRepository(db_path)
    tags = TagService(tag_repo, tasks)
    recurrence = RecurrenceService(series_repo, tasks, tag_service=tags)
    links = SeriesCalendarLinkService(
        series_repo, tasks, series_store, today_provider=lambda: DAY
    )
    recurrence.series_link_service = links
    gateway = FakeCalendarGateway(base_time=BASE)
    manual = ManualSyncService.for_db_path(
        db_path, gateway_provider=lambda: gateway
    )

    if series_repo.get_by_uid("b2-functional") is not None:
        raise SystemExit(
            "Smoke data already exists; use a fresh isolated profile directory."
        )

    functional = recurrence.create_series(
        _series("b2-functional", "TEST B2 — функциональная серия")
    ).series
    recurrence.ensure_occurrences(DAY, date(2026, 7, 19), series_uid=functional.uid)
    ordinary_before = ordinary_queue.count_pending_ops()
    assert links.connect_to_google(functional.uid).ok
    assert series_store.count_pending_ops() == 1
    first = manual.run_once()
    assert first.ok and first.series_masters_created == 1, first
    functional_link = series_store.get_link(functional.uid)
    remote_id = functional_link.remote_event_id
    assert len([event for event in gateway.events if event.is_recurring_master]) == 1
    assert all(
        not row.google_calendar_event_id
        for row in tasks.list_by_series(functional.uid)
    )
    assert ordinary_queue.count_pending_ops() == ordinary_before

    new_schedule = SeriesSchedule(
        DAY, False, time(10), 45, "Europe/Moscow"
    )
    changed = recurrence.update_series(
        functional.uid,
        title="TEST B2 — серия обновлена",
        schedule=new_schedule,
        rule=RecurrenceRule(
            RecurrenceFrequency.WEEKLY,
            weekdays=(2, 4),
            end_mode=RecurrenceEndMode.COUNT,
            occurrence_count=6,
        ),
    )
    assert changed.ok and series_store.get_pending_op(functional.uid).op.value == "update"
    second = manual.run_once()
    assert second.ok and second.series_masters_updated == 1
    assert series_store.get_link(functional.uid).remote_event_id == remote_id
    assert gateway.get_recurring_master(remote_id).summary.endswith("обновлена")

    recurrence.ensure_occurrences(DAY, date(2026, 7, 31), series_uid=functional.uid)
    occurrence = next(
        row for row in tasks.list_by_series(functional.uid) if not row.is_deleted
    )
    tag = tags.create("Только локально")
    assert recurrence.update_series(functional.uid, tag_ids=[tag.id]).ok
    pending_after_tag = series_store.count_pending_ops()
    assert pending_after_tag == 0
    tasks.complete(occurrence.id, True)
    assert series_store.count_pending_ops() == 0

    assert links.disconnect_keep_remote(functional.uid).ok
    assert gateway.get_recurring_master(remote_id) is not None
    reconnect = links.connect_to_google(functional.uid)
    assert reconnect.ok and reconnect.link.remote_event_id == remote_id
    reconciled = manual.run_once()
    assert reconciled.ok and reconciled.series_masters_created == 1
    assert len([event for event in gateway.events if event.is_recurring_master]) == 1
    assert links.request_remote_delete_keep_local(functional.uid).ok
    removed = manual.run_once()
    assert removed.ok and removed.series_masters_deleted == 1
    assert gateway.get_recurring_master(remote_id) is None
    assert series_repo.get_by_uid(functional.uid).is_deleted is False

    # A real ordinary task still uses the independent queue and gateway path.
    desktop = DesktopTaskService(tasks, ordinary_queue)
    ordinary = desktop.create_task(Task(
        title="TEST B2 — обычная задача",
        start=datetime(2026, 7, 15, 14),
        end=datetime(2026, 7, 15, 15),
    ))
    ordinary_sync = manual.run_once()
    assert ordinary_sync.ok
    assert tasks.get_by_uid(ordinary.uid).google_calendar_event_id

    # Concurrent screenshot states, built through the same real link/engine path.
    screen_local = recurrence.create_series(
        _series("b2-screen-local", "TEST B2 — локальная для подключения", hour=8)
    ).series
    screen_pending = recurrence.create_series(
        _series("b2-screen-pending", "TEST B2 — ожидает синхронизации", hour=9)
    ).series
    screen_linked = recurrence.create_series(
        _series("b2-screen-linked", "TEST B2 — связана с Google", hour=10)
    ).series
    screen_conflict = recurrence.create_series(
        _series("b2-screen-conflict", "TEST B2 — конфликт Google", hour=11)
    ).series
    screen_deleted = recurrence.create_series(
        _series("b2-screen-deleted", "TEST B2 — удалена в Google", hour=12)
    ).series
    for item in (screen_local, screen_pending, screen_linked, screen_conflict, screen_deleted):
        recurrence.ensure_occurrences(DAY, DAY, series_uid=item.uid)

    for item in (screen_linked, screen_conflict, screen_deleted):
        links.connect_to_google(item.uid)
    engine = CalendarSeriesSyncEngine(series_repo, tasks, series_store, catalog, gateway)
    engine.push_pending()
    # Leave this one unsynchronized so the UI capture shows the real
    # pending-create state and its queued operation survives a restart.
    links.connect_to_google(screen_pending.uid)
    conflict_link = series_store.get_link(screen_conflict.uid)
    conflict_remote = gateway.get_recurring_master(conflict_link.remote_event_id)
    gateway.patch_event(conflict_remote.id, {"summary": "TEST external conflict"})
    from planner_desktop.sync.calendar_sync_engine import CalendarSyncEngine
    pull = CalendarSyncEngine(
        tasks, ordinary_queue, gateway, catalog, series_link_store=series_store
    )
    pull.pull_remote_changes()
    deleted_link = series_store.get_link(screen_deleted.uid)
    gateway.delete_recurring_master(deleted_link.remote_event_id)
    pull.pull_remote_changes()
    assert series_store.get_link(screen_conflict.uid).link_status is SeriesLinkStatus.CONFLICT
    assert series_store.get_link(screen_deleted.uid).link_status is SeriesLinkStatus.REMOTE_DELETED

    changed_instance = gateway.insert_event(CalendarEvent(
        summary="TEST changed linked instance",
        start=date(2026, 7, 16), end=date(2026, 7, 17), is_all_day=True,
        recurring_event_id=conflict_link.remote_event_id,
        original_start=datetime(2026, 7, 16),
    ))
    pull.pull_remote_changes()
    assert tasks.get_by_google_event_id(changed_instance.id) is None
    assert series_store.count_quarantined() == 1

    # Settings/page reads remain local: no provider calls are added here.
    calls_before_settings = (gateway.write_call_count, gateway.list_call_count)
    recurrence_for_ui = recurrence
    desktop.recurrence_service = recurrence_for_ui
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
    )
    settings.refresh()
    assert (gateway.write_call_count, gateway.list_call_count) == calls_before_settings

    # Explicit duplicate-insert reconciliation leaves one master.
    linked_event = gateway.get_recurring_master(
        series_store.get_link(screen_linked.uid).remote_event_id
    )
    master_count = len([event for event in gateway.events if event.is_recurring_master])
    gateway.insert_recurring_master(linked_event.id, linked_event)
    assert len([event for event in gateway.events if event.is_recurring_master]) == master_count

    report = {
        "profile": str(Path(data_dir)),
        "db": str(db_path),
        "first": first.__dict__,
        "update": second.__dict__,
        "reconnect": reconciled.__dict__,
        "delete": removed.__dict__,
        "ordinary": ordinary_sync.__dict__,
        "functional_remote_id": remote_id,
        "functional_local_series_kept": not series_repo.get_by_uid(functional.uid).is_deleted,
        "master_count_after_duplicate_reconcile": master_count,
        "materialized_occurrence_google_ids": sum(
            bool(row.google_calendar_event_id)
            for row in tasks.list_by_series(functional.uid)
        ),
        "series_queue_pending": series_store.count_pending_ops(),
        "ordinary_queue_pending": ordinary_queue.count_pending_ops(),
        "quarantined": series_store.count_quarantined(),
        "settings_gateway_call_delta": [0, 0],
        "states": series_store.diagnostics(),
    }
    report_path = Path(data_dir) / "phase3_2b2_smoke_report.json"
    report_path.write_text(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=2,
            default=lambda value: value.isoformat()
            if isinstance(value, (date, datetime))
            else str(value),
        ),
        encoding="utf-8",
    )

    tag_repo.close(); catalog.close(); series_store.close()
    series_repo.close(); ordinary_queue.close(); tasks.close()
    reopened = CalendarSeriesSyncStore(db_path)
    assert reopened.get_link(screen_linked.uid) is not None
    assert reopened.count_quarantined() == 1
    reopened.close()

    print(f"profile={data_dir}")
    print(f"db={db_path}")
    print(f"functional_remote_id={remote_id}")
    print("one_master_create_update_delete=true")
    print("occurrence_event_flood=0 tag_completion_master_delta=0")
    print("disconnect_reconnect_reconcile=true conflict=true remote_deleted=true")
    print("linked_instance_quarantined=1 ordinary_sync=true settings_google_calls=0")
    print("restart_persistence=true")
    print(f"report={report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
