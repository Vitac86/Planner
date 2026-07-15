"""Seed and verify the isolated Phase 3.2B1 read-only discovery smoke.

The helper requires ``PLANNER_DESKTOP_DATA_DIR``.  It uses only the injected
``FakeCalendarGateway`` and the new desktop database; no OAuth, network,
legacy database, or legacy token is opened.
"""
from __future__ import annotations

import json
import os
from datetime import date, datetime, time, timezone
from pathlib import Path
from types import SimpleNamespace

from planner_desktop.domain.recurrence import (
    RecurrenceFrequency,
    RecurrenceRule,
    SeriesSchedule,
    TaskSeries,
)
from planner_desktop.storage.calendar_sync_store import CalendarSyncStore
from planner_desktop.storage.external_series_repository import (
    SQLiteExternalSeriesRepository,
)
from planner_desktop.storage.paths import get_desktop_db_path
from planner_desktop.storage.series_repository import SQLiteSeriesRepository
from planner_desktop.storage.sqlite_task_repository import SQLiteTaskRepository
from planner_desktop.sync.fake_calendar_gateway import FakeCalendarGateway
from planner_desktop.sync.sync_types import CalendarEvent
from planner_desktop.usecases.external_series_service import ExternalSeriesService
from planner_desktop.usecases.manual_sync_service import ManualSyncService
from planner_desktop.usecases.recurrence_service import RecurrenceService
from planner_desktop.usecases.task_service import DesktopTaskService
from planner_desktop.viewmodels.settings_viewmodel import SettingsViewModel


BASE = datetime(2026, 7, 15, 8, tzinfo=timezone.utc)
DAY = date(2026, 7, 15)


def _all_day_master(gateway, title, *lines):
    return gateway.insert_event(CalendarEvent(
        summary=title,
        description="Синтетические данные visual smoke",
        start=DAY,
        end=date(2026, 7, 16),
        is_all_day=True,
        recurrence_lines=tuple(lines),
    ))


def main() -> int:
    data_dir = os.environ.get("PLANNER_DESKTOP_DATA_DIR")
    if not data_dir:
        raise SystemExit(
            "Set PLANNER_DESKTOP_DATA_DIR to an isolated smoke directory first."
        )
    db_path = get_desktop_db_path()
    tasks = SQLiteTaskRepository(db_path)
    queue = CalendarSyncStore(db_path)
    catalog = SQLiteExternalSeriesRepository(db_path)
    gateway = FakeCalendarGateway(base_time=BASE)

    ordinary_timed = gateway.insert_event(CalendarEvent(
        summary="Обычная встреча",
        start=datetime(2026, 7, 15, 10),
        end=datetime(2026, 7, 15, 11),
    ))
    ordinary_all_day = gateway.insert_event(CalendarEvent(
        summary="Обычное событие на весь день",
        start=DAY, end=date(2026, 7, 16), is_all_day=True,
    ))

    daily = _all_day_master(
        gateway, "Ежедневная поддерживаемая серия",
        "RRULE:FREQ=DAILY;INTERVAL=1",
    )
    unsupported = _all_day_master(
        gateway, "Неподдерживаемый последний понедельник",
        "RRULE:FREQ=MONTHLY;BYDAY=MO;BYSETPOS=1",
    )
    cancelled_master = _all_day_master(
        gateway, "Отменённая серия для истории",
        "RRULE:FREQ=DAILY;INTERVAL=2",
    )
    weekly = _all_day_master(
        gateway, "Планирование по понедельникам и средам",
        "RRULE:FREQ=WEEKLY;INTERVAL=1;BYDAY=MO,WE",
    )
    monthly = _all_day_master(
        gateway, "Ежемесячный отчёт 15-го числа",
        "RRULE:FREQ=MONTHLY;INTERVAL=1;BYMONTHDAY=15",
    )
    yearly = _all_day_master(
        gateway, "Ежегодный обзор 15 июля",
        "RRULE:FREQ=YEARLY;INTERVAL=1;BYMONTHDAY=15;BYMONTH=7",
    )
    counted = _all_day_master(
        gateway, "Пять ежедневных проверок",
        "RRULE:FREQ=DAILY;INTERVAL=1;COUNT=5",
    )
    until = _all_day_master(
        gateway, "Ежедневно до конца июля",
        "RRULE:FREQ=DAILY;INTERVAL=1;UNTIL=20260731",
    )
    with_exdate = _all_day_master(
        gateway, "Ежедневно кроме 20 июля",
        "RRULE:FREQ=DAILY;INTERVAL=1",
        "EXDATE:20260720",
    )

    changed_instance = gateway.insert_event(CalendarEvent(
        summary="Изменённый экземпляр Google-серии",
        start=date(2026, 7, 16), end=date(2026, 7, 17), is_all_day=True,
        recurring_event_id=weekly.id,
        original_start=datetime(2026, 7, 16),
    ))
    cancelled_instance = gateway.insert_event(CalendarEvent(
        summary="Экземпляр, который будет отменён",
        start=date(2026, 7, 17), end=date(2026, 7, 18), is_all_day=True,
        recurring_event_id=weekly.id,
        original_start=datetime(2026, 7, 17),
    ))

    sync = ManualSyncService(
        tasks,
        queue,
        gateway_provider=lambda: gateway,
        external_series_repository=catalog,
    )
    queue_before = queue.count_pending_ops()
    first = sync.run_once()
    assert first.ok, first.error
    assert first.ordinary_events_pulled == 2
    assert first.recurring_masters_discovered == 9
    assert first.recurring_instances_pulled == 2
    assert first.unsupported_masters == 1
    assert queue.count_pending_ops() == queue_before == 0

    # Remote changes between explicit syncs: one changed instance, one
    # cancelled instance, one cancelled master.  No local history cleanup.
    gateway.patch_event(changed_instance.id, {
        "summary": "Изменённый экземпляр после ручного sync"
    })
    gateway.delete_event(cancelled_instance.id)
    gateway.delete_event(cancelled_master.id)
    queue_before_second = queue.count_pending_ops()
    second = sync.run_once()
    assert second.ok, second.error
    assert second.recurring_instances_pulled == 2
    assert second.cancelled_masters == 1
    assert queue.count_pending_ops() == queue_before_second == 0

    # A third explicit run proves cursor/idempotence; opening Settings below
    # remains a local query and does not call the provider.
    catalog_count = len(catalog.list_all())
    active_task_count = tasks.count_active()
    third = sync.run_once()
    assert third.ok and third.pulled == 0
    assert len(catalog.list_all()) == catalog_count == 9
    assert tasks.count_active() == active_task_count

    master_ids = {
        daily.id, unsupported.id, cancelled_master.id, weekly.id, monthly.id,
        yearly.id, counted.id, until.id, with_exdate.id,
    }
    assert all(tasks.get_by_google_event_id(remote_id) is None for remote_id in master_ids)
    assert tasks.get_by_google_event_id(ordinary_timed.id) is not None
    assert tasks.get_by_google_event_id(ordinary_all_day.id) is not None
    assert tasks.get_by_google_event_id(changed_instance.id) is not None
    assert tasks.get_by_google_event_id(cancelled_instance.id).is_deleted
    assert catalog.get("google", "primary", cancelled_master.id).is_cancelled

    # Local TaskSeries stays local and creates no Calendar queue operations.
    series_repo = SQLiteSeriesRepository(db_path)
    recurrence = RecurrenceService(series_repo, tasks)
    before_local_series = queue.count_pending_ops()
    created_local = recurrence.create_series(TaskSeries(
        title="Локальная серия остаётся локальной",
        schedule=SeriesSchedule(
            start_date=DAY, all_day=False, local_time=time(18),
            duration_minutes=30, timezone_name="Europe/Moscow",
        ),
        rule=RecurrenceRule(RecurrenceFrequency.WEEKLY, weekdays=(2,)),
    ))
    assert created_local.ok
    assert queue.count_pending_ops() == before_local_series
    series_repo.close()

    provider_calls = []
    local_only_manual = ManualSyncService(
        tasks, queue,
        gateway_provider=lambda: provider_calls.append("called") or gateway,
        external_series_repository=catalog,
    )
    desktop_service = DesktopTaskService(tasks, calendar_queue=queue)
    settings = SettingsViewModel(
        desktop_service,
        manual_sync_service=local_only_manual,
        connection_checker=lambda: SimpleNamespace(
            connected=False, has_client_secret=False,
            token_path="", client_secret_path="",
        ),
        external_series_service=ExternalSeriesService(catalog),
    )
    settings.refresh()
    assert len(settings.externalSeriesRows) == 9
    assert provider_calls == []

    report = {
        "db": str(db_path),
        "first_sync": first.__dict__,
        "second_sync": second.__dict__,
        "third_sync": third.__dict__,
        "catalog_count": catalog_count,
        "active_task_count": active_task_count,
        "queue_delta": queue.count_pending_ops() - queue_before,
        "settings_gateway_calls": len(provider_calls),
        "diagnostics": ExternalSeriesService(catalog).diagnostics(),
    }
    # JSON evidence contains only synthetic counts/ids and the isolated path.
    def default(value):
        return value.isoformat() if isinstance(value, (date, datetime)) else list(value)

    report_path = Path(data_dir) / "phase3_2b1_smoke_report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=default),
        encoding="utf-8",
    )

    catalog.close()
    queue.close()
    tasks.close()
    reopened = SQLiteExternalSeriesRepository(db_path)
    assert len(reopened.list_all()) == 9
    reopened.close()

    print(f"db={db_path}")
    print("catalog=9 active=8 unsupported=1 cancelled=1")
    print(f"tasks_active={active_task_count} queue_delta=0")
    print("idempotent_followup_sync=true restart_catalog=9 settings_google_calls=0")
    print(f"report={report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
