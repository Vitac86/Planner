"""Run the isolated fake Phase 3.2B3A conflict-resolution smoke.

Everything below uses FakeCalendarGateway only: no OAuth flow, no Google
client import, zero real network calls.  The script exercises every explicit
conflict/remote-deleted action end-to-end against the real SQLite stores of
the isolated profile and leaves deterministic UI states for the screenshot
capture script.
"""
from __future__ import annotations

import json
import os
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
from planner_desktop.domain.series_conflict_resolution import (
    deterministic_remote_event_id_for_generation,
)
from planner_desktop.domain.task import Task
from planner_desktop.storage.calendar_series_sync_store import CalendarSeriesSyncStore
from planner_desktop.storage.calendar_sync_store import CalendarSyncStore
from planner_desktop.storage.external_series_repository import (
    SQLiteExternalSeriesRepository,
)
from planner_desktop.storage.paths import get_desktop_db_path
from planner_desktop.storage.series_repository import SQLiteSeriesRepository
from planner_desktop.storage.sqlite_task_repository import SQLiteTaskRepository
from planner_desktop.storage.tag_repository import SQLiteTagRepository
from planner_desktop.sync.calendar_series_sync_engine import CalendarSeriesSyncEngine
from planner_desktop.sync.calendar_sync_engine import CalendarSyncEngine
from planner_desktop.sync.fake_calendar_gateway import FakeCalendarGateway
from planner_desktop.usecases.external_series_service import ExternalSeriesService
from planner_desktop.usecases.manual_sync_service import ManualSyncService
from planner_desktop.usecases.recurrence_service import RecurrenceService
from planner_desktop.usecases.series_calendar_link_service import (
    SeriesCalendarLinkService,
)
from planner_desktop.usecases.series_conflict_service import SeriesConflictService
from planner_desktop.usecases.tag_service import TagService
from planner_desktop.usecases.task_service import DesktopTaskService
from planner_desktop.viewmodels.settings_viewmodel import SettingsViewModel


DAY = date(2026, 7, 15)
BASE = datetime(2026, 7, 15, 8, tzinfo=timezone.utc)


def _series(uid: str, title: str, *, hour: int = 9) -> TaskSeries:
    return TaskSeries(
        uid=uid,
        title=title,
        notes="Синтетическая серия conflict smoke",
        schedule=SeriesSchedule(DAY, False, time(hour), 30, "Europe/Moscow"),
        rule=RecurrenceRule(
            RecurrenceFrequency.DAILY,
            end_mode=RecurrenceEndMode.COUNT,
            occurrence_count=6,
        ),
    )


def _make_conflict(ctx, uid: str, *, summary: str) -> None:
    remote_id = ctx.series_store.get_link(uid).remote_event_id
    ctx.gateway.patch_event(remote_id, {"summary": summary})
    ctx.pull.pull_remote_changes()
    link = ctx.series_store.get_link(uid)
    assert link.link_status is SeriesLinkStatus.CONFLICT, uid
    assert link.conflict_remote_snapshot_json, uid


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
    conflicts = SeriesConflictService(
        series_repo, tasks, series_store, today_provider=lambda: DAY
    )
    recurrence.series_link_service = links
    recurrence.series_conflict_service = conflicts
    gateway = FakeCalendarGateway(base_time=BASE)
    manual = ManualSyncService.for_db_path(db_path, gateway_provider=lambda: gateway)
    engine = CalendarSeriesSyncEngine(series_repo, tasks, series_store, catalog, gateway)
    pull = CalendarSyncEngine(
        tasks, ordinary_queue, gateway, catalog, series_link_store=series_store
    )
    ctx = SimpleNamespace(
        gateway=gateway, series_store=series_store, pull=pull
    )

    if series_repo.get_by_uid("b3a-keep") is not None:
        raise SystemExit(
            "Smoke data already exists; use a fresh isolated profile directory."
        )

    uids = (
        "b3a-keep", "b3a-race", "b3a-use", "b3a-unsup", "b3a-disc",
        "b3a-keeplocal", "b3a-recreate", "b3a-dellocal",
        "b3a-screen-conflict", "b3a-screen-pending", "b3a-screen-deleted",
    )
    titles = {
        "b3a-keep": "TEST B3A — оставить версию Planner",
        "b3a-race": "TEST B3A — гонка второй правки",
        "b3a-use": "TEST B3A — использовать версию Google",
        "b3a-unsup": "TEST B3A — неподдерживаемое правило",
        "b3a-disc": "TEST B3A — отключить и сохранить обе",
        "b3a-keeplocal": "TEST B3A — оставить локальной",
        "b3a-recreate": "TEST B3A — пересоздать в Google",
        "b3a-dellocal": "TEST B3A — удалить локальную",
        "b3a-screen-conflict": "TEST B3A — конфликт для экрана",
        "b3a-screen-pending": "TEST B3A — решение ожидает синка",
        "b3a-screen-deleted": "TEST B3A — удалена в Google (экран)",
    }
    for index, uid in enumerate(uids):
        created = recurrence.create_series(
            _series(uid, titles[uid], hour=8 + index % 8)
        ).series
        recurrence.ensure_occurrences(DAY, date(2026, 7, 20), series_uid=created.uid)
        assert links.connect_to_google(uid).ok, uid
    first_sync = manual.run_once()
    assert first_sync.ok and first_sync.series_masters_created == len(uids)
    occurrence_rows = sum(
        len([r for r in tasks.list_by_series(uid) if not r.is_deleted])
        for uid in uids
    )
    live_masters = [
        e for e in gateway.events if e.is_recurring_master and not e.is_cancelled
    ]
    assert len(live_masters) == len(uids)  # no occurrence flood
    assert all(
        not row.google_calendar_event_id
        for uid in uids for row in tasks.list_by_series(uid)
    )

    # 1. Keep Planner success.
    _make_conflict(ctx, "b3a-keep", summary="Изменено в Google (keep)")
    assert conflicts.resolve_keep_planner("b3a-keep", confirmed=True).ok
    keep_sync = manual.run_once()
    assert keep_sync.ok and keep_sync.conflicts_resolved_keep_planner == 1
    keep_link = series_store.get_link("b3a-keep")
    assert keep_link.link_status is SeriesLinkStatus.SYNCED
    keep_remote = gateway.get_recurring_master(keep_link.remote_event_id)
    assert keep_remote.summary == titles["b3a-keep"]

    # 2. Second remote edit race: the stale decision must not overwrite.
    _make_conflict(ctx, "b3a-race", summary="Первая внешняя правка")
    race = conflicts.resolve_keep_planner("b3a-race", confirmed=True)
    race_remote_id = series_store.get_link("b3a-race").remote_event_id
    gateway.patch_event(race_remote_id, {"summary": "Вторая внешняя правка"})
    race_sync = manual.run_once()
    assert race_sync.ok and race_sync.resolution_attempts_superseded == 1
    assert gateway.get_recurring_master(race_remote_id).summary == (
        "Вторая внешняя правка"
    )
    race_link = series_store.get_link("b3a-race")
    assert race_link.link_status is SeriesLinkStatus.CONFLICT
    assert series_store.get_resolution(race.resolution.id).status == "superseded"

    # 3. Use Google success (schedule + title change, lossless).
    use_remote_id = series_store.get_link("b3a-use").remote_event_id
    use_event = gateway._events[use_remote_id]
    use_event.start = datetime(2026, 7, 15, 12)
    use_event.end = datetime(2026, 7, 15, 12, 45)
    use_event.recurrence_start = use_event.start
    use_event.recurrence_lines = ("RRULE:FREQ=WEEKLY;INTERVAL=1;BYDAY=TU,TH",)
    gateway.patch_event(use_remote_id, {"summary": "Версия Google (use)"})
    pull.pull_remote_changes()
    writes_before_use = gateway.write_call_count
    assert conflicts.resolve_use_google("b3a-use", confirmed=True).ok
    assert gateway.write_call_count == writes_before_use  # no Google write
    use_series = series_repo.get_by_uid("b3a-use")
    assert use_series.title == "Версия Google (use)"
    assert use_series.schedule.local_time == time(12, 0)
    assert use_series.rule.frequency is RecurrenceFrequency.WEEKLY
    assert series_store.get_link("b3a-use").link_status is SeriesLinkStatus.SYNCED
    use_echo = manual.run_once()
    assert use_echo.ok and use_echo.conflicts_resolved_use_google == 1
    assert series_store.get_link("b3a-use").link_status is SeriesLinkStatus.SYNCED

    # 4. Unsupported remote recurrence: Use Google disabled, raw lines visible.
    unsup_remote_id = series_store.get_link("b3a-unsup").remote_event_id
    gateway._events[unsup_remote_id].recurrence_lines = (
        "RRULE:FREQ=WEEKLY;BYDAY=-1FR",
    )
    gateway.patch_event(unsup_remote_id, {"summary": "Неподдерживаемое правило"})
    pull.pull_remote_changes()
    unsup = conflicts.get_conflict("b3a-unsup")
    assert unsup["canUseGoogle"] is False
    assert unsup["canKeepPlanner"] is True
    assert unsup["canDisconnect"] is True
    assert unsup["remote"]["rawRecurrence"] == ["RRULE:FREQ=WEEKLY;BYDAY=-1FR"]
    assert unsup["remote"]["unsupportedReason"]

    # 5. Disconnect and keep both.
    _make_conflict(ctx, "b3a-disc", summary="Изменено в Google (disc)")
    disc_remote_id = series_store.get_link("b3a-disc").remote_event_id
    disc_etag = gateway.get_recurring_master(disc_remote_id).etag
    assert conflicts.resolve_disconnect("b3a-disc").ok
    assert gateway.get_recurring_master(disc_remote_id).etag == disc_etag
    disc_link = series_store.get_link("b3a-disc", include_detached=True)
    assert disc_link.link_status is SeriesLinkStatus.DETACHED
    assert not series_repo.get_by_uid("b3a-disc").is_deleted
    assert catalog.get("google", "primary", disc_remote_id) is not None

    # 6. Remote deletion -> keep local.
    for uid in ("b3a-keeplocal", "b3a-recreate", "b3a-dellocal",
                "b3a-screen-deleted"):
        gateway.delete_recurring_master(series_store.get_link(uid).remote_event_id)
    pull.pull_remote_changes()
    assert conflicts.recover_remote_deleted_keep_local("b3a-keeplocal").ok
    assert not series_repo.get_by_uid("b3a-keeplocal").is_deleted

    # 7. Recreate with generation 1 and a different stable id.
    gen0_id = series_store.get_link("b3a-recreate").remote_event_id
    first_try = conflicts.recover_remote_deleted_recreate(
        "b3a-recreate", confirmed=True
    )
    second_try = conflicts.recover_remote_deleted_recreate(
        "b3a-recreate", confirmed=True
    )
    assert first_try.ok and first_try.changed
    assert second_try.ok and not second_try.changed  # no extra generation
    assert series_store.max_link_generation("b3a-recreate") == 1
    recreate_sync = manual.run_once()
    assert recreate_sync.ok and recreate_sync.remote_deleted_recreated == 1
    new_link = series_store.get_link("b3a-recreate")
    gen1_expected = deterministic_remote_event_id_for_generation("b3a-recreate", 1)
    assert new_link.link_generation == 1
    assert new_link.remote_event_id == gen1_expected != gen0_id
    assert new_link.link_status is SeriesLinkStatus.SYNCED
    assert gateway.get_recurring_master(gen0_id) is None
    assert gateway.get_recurring_master(gen1_expected) is not None

    # 8. Delete local series: no Google op, completed history preserved.
    done_row = next(
        row for row in tasks.list_by_series("b3a-dellocal") if not row.is_deleted
    )
    tasks.complete(done_row.id, True)
    writes_before_delete = gateway.write_call_count
    assert conflicts.delete_remote_deleted_local_series(
        "b3a-dellocal", confirmed=True
    ).ok
    assert gateway.write_call_count == writes_before_delete
    assert series_repo.get_by_uid("b3a-dellocal").is_deleted
    assert tasks.get_by_uid(done_row.uid).completed

    # 9. Screenshot states: open conflict and remote-deleted stay unresolved.
    _make_conflict(ctx, "b3a-screen-conflict", summary="Изменено в Google (экран)")
    _make_conflict(ctx, "b3a-screen-pending", summary="Ожидает решения (экран)")

    # 10. Ordinary Task sync remains operational.
    desktop = DesktopTaskService(tasks, ordinary_queue)
    ordinary = desktop.create_task(Task(
        title="TEST B3A — обычная задача",
        start=datetime(2026, 7, 15, 14),
        end=datetime(2026, 7, 15, 15),
    ))
    ordinary_sync = manual.run_once()
    assert ordinary_sync.ok
    assert tasks.get_by_uid(ordinary.uid).google_calendar_event_id
    # The unresolved screenshot conflicts survived the full cycle untouched.
    assert series_store.get_link("b3a-screen-conflict").link_status is (
        SeriesLinkStatus.CONFLICT
    )

    # The pending keep-planner decision is created AFTER the last sync so the
    # capture shows a real queued resolution waiting for the next manual sync.
    assert conflicts.resolve_keep_planner(
        "b3a-screen-pending", confirmed=True
    ).ok

    # 11. Settings/page reads stay local: zero gateway calls.
    desktop.recurrence_service = recurrence
    settings = SettingsViewModel(
        desktop,
        connection_checker=lambda: SimpleNamespace(
            connected=False, has_client_secret=False,
            token_path="", client_secret_path="",
        ),
        external_series_service=ExternalSeriesService(catalog),
        series_link_service=links,
        series_sync_store=series_store,
        series_conflict_service=conflicts,
    )
    calls_before = (gateway.write_call_count, gateway.list_call_count)
    settings.refresh()
    _ = settings.resolutionHistoryRows
    _ = settings.pendingResolutionCount
    _ = conflicts.get_conflict("b3a-screen-conflict")
    _ = conflicts.get_remote_deleted("b3a-screen-deleted")
    assert (gateway.write_call_count, gateway.list_call_count) == calls_before

    diagnostics = series_store.diagnostics()
    history = series_store.list_resolutions()
    report = {
        "profile": str(Path(data_dir)),
        "db": str(db_path),
        "keep_planner": keep_sync.__dict__,
        "race": race_sync.__dict__,
        "use_google_echo": use_echo.__dict__,
        "recreate": recreate_sync.__dict__,
        "ordinary": ordinary_sync.__dict__,
        "recreate_gen0_id": gen0_id,
        "recreate_gen1_id": gen1_expected,
        "diagnostics": diagnostics,
        "resolution_history_kinds": [
            f"{item.resolution_kind}:{item.status}" for item in history
        ],
        "occurrence_rows": occurrence_rows,
        "live_masters_after_seed": len(live_masters),
        "settings_gateway_call_delta": [0, 0],
    }
    report_path = Path(data_dir) / "phase3_2b3a_smoke_report.json"
    report_path.write_text(
        json.dumps(
            report, ensure_ascii=False, indent=2,
            default=lambda value: value.isoformat()
            if isinstance(value, (date, datetime)) else str(value),
        ),
        encoding="utf-8",
    )

    tag_repo.close(); catalog.close(); series_store.close()
    series_repo.close(); ordinary_queue.close(); tasks.close()

    # 12. Restart persistence: reopen and verify the durable states.
    reopened = CalendarSeriesSyncStore(db_path)
    assert reopened.get_link("b3a-screen-conflict").link_status is (
        SeriesLinkStatus.CONFLICT
    )
    assert reopened.get_link("b3a-screen-conflict").conflict_remote_snapshot_json
    pending = reopened.get_pending_resolution("b3a-screen-pending")
    assert pending is not None and pending.resolution_kind == "keep_planner"
    assert reopened.get_pending_op("b3a-screen-pending").resolution_id == pending.id
    assert reopened.get_link("b3a-recreate").link_generation == 1
    assert reopened.get_link("b3a-screen-deleted").link_status is (
        SeriesLinkStatus.REMOTE_DELETED
    )
    assert len(reopened.list_resolutions()) == len(history)
    reopened.close()

    print(f"profile={data_dir}")
    print(f"db={db_path}")
    print("keep_planner=success race_superseded=true use_google=local_only")
    print("unsupported_rule_blocks_use_google=true disconnect_keeps_both=true")
    print(f"recreate_generation=1 gen0={gen0_id[:16]}... gen1={gen1_expected[:16]}...")
    print("keep_local=true delete_local_history_preserved=true")
    print("ordinary_sync=true occurrence_flood=0 settings_gateway_calls=0")
    print("restart_persistence=true real_google_calls=0")
    print(f"report={report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
