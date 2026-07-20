"""Isolated fake Phase 3.2B3C1 remote-split acceptance smoke.

FakeCalendarGateway only: no OAuth, no network.  Leaves durable split-plan
states in SQLite for the screenshot capture script.
"""
from __future__ import annotations

import json
import os
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

from planner_desktop.domain.commands import TaskEditorCommand
from planner_desktop.domain.google_occurrence import (
    local_occurrence_to_google_original_start,
)
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
from planner_desktop.sync.calendar_series_occurrence_mapper import (
    task_to_occurrence_owned_payload,
)
from planner_desktop.sync.calendar_series_remote_split_engine import (
    merge_split_master_resource,
)
from planner_desktop.sync.calendar_sync_engine import CalendarSyncEngine
from planner_desktop.sync.fake_calendar_gateway import FakeCalendarGateway
from planner_desktop.usecases.manual_sync_service import ManualSyncService
from planner_desktop.usecases.recurrence_service import RecurrenceService
from planner_desktop.usecases.remote_series_split_service import (
    RemoteSeriesSplitService,
)
from planner_desktop.usecases.series_calendar_link_service import (
    SeriesCalendarLinkService,
)

TODAY = date(2026, 8, 1)
START = date(2026, 8, 3)
BASE = datetime(2026, 8, 1, 8, tzinfo=timezone.utc)


def _series(uid, title, *, freq=RecurrenceFrequency.DAILY, weekdays=(),
            month_day=None, end=RecurrenceEndMode.COUNT, count=5, until=None,
            start=START, all_day=False):
    return TaskSeries(
        uid=uid,
        title=title,
        notes="Fake Phase 3.2B3C1 remote split smoke",
        priority=1,
        schedule=SeriesSchedule(
            start_date=start,
            all_day=all_day,
            local_time=None if all_day else time(9),
            duration_minutes=None if all_day else 30,
            timezone_name="Europe/Moscow",
        ),
        rule=RecurrenceRule(
            frequency=freq,
            weekdays=weekdays,
            month_day=month_day,
            end_mode=end,
            occurrence_count=count if end is RecurrenceEndMode.COUNT else None,
            until_date=until,
        ),
    )


def _live(tasks, uid):
    return sorted(
        (row for row in tasks.list_by_series(uid) if not row.is_deleted),
        key=lambda row: (row.start or datetime.min, row.uid),
    )


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
    split_store = CalendarSeriesRemoteSplitStore(db_path)
    catalog = SQLiteExternalSeriesRepository(db_path)

    recurrence = RecurrenceService(series_repo, tasks)
    links = SeriesCalendarLinkService(
        series_repo, tasks, series_store, today_provider=lambda: TODAY
    )
    recurrence.series_link_service = links
    recurrence.occurrence_sync_store = occurrence_store
    splits = RemoteSeriesSplitService(
        series_repo, tasks, series_store, occurrence_store, split_store,
        external_series_repository=catalog, today_provider=lambda: TODAY,
    )
    recurrence.remote_split_service = splits
    links.remote_split_service = splits

    gateway = FakeCalendarGateway(base_time=BASE)
    manual = ManualSyncService(
        tasks, ordinary_store, gateway_provider=lambda: gateway,
        external_series_repository=catalog, series_store=series_store,
        series_repository=series_repo, occurrence_store=occurrence_store,
        split_store=split_store,
    )
    pull = CalendarSyncEngine(
        tasks, ordinary_store, gateway, catalog,
        series_link_store=series_store, occurrence_sync_store=occurrence_store,
        series_repository=series_repo, split_store=split_store,
    )

    if series_repo.get_by_uid("b3c1-count") is not None:
        raise SystemExit("Smoke data already exists; use a fresh profile.")

    definitions = [
        _series("b3c1-count", "TEST B3C1 COUNT=5 series"),
        _series("b3c1-until", "TEST B3C1 UNTIL series",
                end=RecurrenceEndMode.UNTIL, count=None,
                until=date(2026, 8, 20)),
        _series("b3c1-never", "TEST B3C1 weekly never-ending series",
                freq=RecurrenceFrequency.WEEKLY, weekdays=(0, 2, 4),
                end=RecurrenceEndMode.NEVER, count=None),
        _series("b3c1-monthly", "TEST B3C1 monthly 31st series",
                freq=RecurrenceFrequency.MONTHLY, month_day=31,
                start=date(2026, 8, 31), count=6),
        _series("b3c1-past-exc", "TEST B3C1 series with past exception",
                start=TODAY - timedelta(days=5), count=20),
        _series("b3c1-future-exc", "TEST B3C1 blocked by future exception",
                count=9),
        _series("b3c1-race", "TEST B3C1 ETag race series"),
        _series("b3c1-trim-partial", "TEST B3C1 trim partial failure"),
        _series("b3c1-finalize-fail", "TEST B3C1 finalize retry"),
        _series("b3c1-rollback", "TEST B3C1 rollback series"),
        _series("b3c1-dialog", "TEST B3C1 eligible dialog series"),
    ]
    for definition in definitions:
        result = recurrence.create_series(definition)
        assert result.ok, result.errors
    recurrence.ensure_occurrences(TODAY - timedelta(days=6), START + timedelta(days=90))
    for definition in definitions:
        assert links.connect_to_google(definition.uid).ok
    boot = manual.run_once()
    assert boot.ok and boot.series_masters_created == len(definitions)
    pull.pull_remote_changes()

    def target_key(uid, index=2):
        return str(_live(tasks, uid)[index].occurrence_key)

    def plan(uid, index=2, proposal=None):
        result = splits.create_split_plan(
            uid, target_key(uid, index),
            proposal or RemoteSeriesSplitProposal(
                title=f"TEST successor of {uid}", local_time=time(11)
            ),
        )
        assert result.ok, (uid, result.error)
        return result.record

    report = {"profile": str(Path(data_dir)), "db": str(db_path)}

    # ---- 1. clean COUNT split: exact partition, 1 update + 1 insert -------
    count_plan = plan("b3c1-count")
    gateway.reset_call_counts()
    s = manual.run_once()
    assert s.ok and s.remote_splits_finalized == 1
    assert gateway.write_call_count == 2  # one update + one insert
    count_rec = split_store.get_plan(count_plan.id)
    src = gateway.get_recurring_master_resource(count_rec.source_remote_event_id)
    succ = gateway.get_recurring_master_resource(count_rec.successor_remote_event_id)
    assert "COUNT=2" in src["recurrence"][0]
    assert "COUNT=3" in succ["recurrence"][0]
    recurrence.ensure_occurrences(TODAY, START + timedelta(days=30))
    assert len(_live(tasks, "b3c1-count")) == 2
    assert len(_live(tasks, count_rec.reserved_successor_series_uid)) == 3
    report["count_split"] = {
        "state": count_rec.state.value,
        "writes": 2,
        "source_recurrence": src["recurrence"],
        "successor_recurrence": succ["recurrence"],
    }

    # ---- 2. UNTIL split: successor keeps the lossless UNTIL ----------------
    until_plan = plan("b3c1-until")
    assert manual.run_once().ok
    until_rec = split_store.get_plan(until_plan.id)
    assert until_rec.state is RemoteSeriesSplitStatus.COMPLETED
    succ_until = gateway.get_recurring_master_resource(
        until_rec.successor_remote_event_id
    )
    assert "UNTIL=" in succ_until["recurrence"][0]
    successor_series = series_repo.get_by_uid(
        until_rec.reserved_successor_series_uid
    )
    assert successor_series.rule.until_date == date(2026, 8, 20)

    # ---- 3. weekly never-ending split --------------------------------------
    never_plan = plan("b3c1-never", index=4)
    assert manual.run_once().ok
    never_rec = split_store.get_plan(never_plan.id)
    assert never_rec.state is RemoteSeriesSplitStatus.COMPLETED
    succ_never = gateway.get_recurring_master_resource(
        never_rec.successor_remote_event_id
    )
    assert "COUNT" not in succ_never["recurrence"][0]
    assert "UNTIL" not in succ_never["recurrence"][0]

    # ---- 5. past exception does not block ----------------------------------
    past_rows = _live(tasks, "b3c1-past-exc")
    past_target = past_rows[1]
    past_link = series_store.get_link("b3c1-past-exc")
    gateway.seed_recurring_instance({
        "id": "b3c1-past-instance",
        "etag": '"1"',
        **task_to_occurrence_owned_payload(
            past_target, series_repo.get_by_uid("b3c1-past-exc")
        ),
        "recurringEventId": past_link.remote_event_id,
        "originalStartTime": local_occurrence_to_google_original_start(
            series_repo.get_by_uid("b3c1-past-exc"),
            str(past_target.occurrence_key),
        ).to_google(),
        "extendedProperties": {"private": {}},
    })
    assert recurrence.edit_occurrence(past_target.uid, TaskEditorCommand(
        title="TEST past exception",
        notes=past_target.notes,
        priority=past_target.priority,
        completed=False,
        add_to_calendar=True,
        is_all_day=False,
        date_text=past_target.start.date().isoformat(),
        time_text=past_target.start.strftime("%H:%M"),
        duration_text="30",
    )).ok
    assert manual.run_once().ok
    future_target = next(
        row for row in _live(tasks, "b3c1-past-exc")
        if row.start is not None
        and row.start.date() >= TODAY + timedelta(days=2)
    )
    past_result = splits.create_split_plan(
        "b3c1-past-exc", str(future_target.occurrence_key),
        RemoteSeriesSplitProposal(title="TEST successor of b3c1-past-exc"),
    )
    assert past_result.ok, past_result.error
    assert manual.run_once().ok
    assert split_store.get_plan(past_result.record.id).state is (
        RemoteSeriesSplitStatus.COMPLETED
    )
    # The past exception row stays with the source series.
    kept = tasks.get_by_uid(past_target.uid)
    assert kept is not None and kept.is_series_exception

    # ---- 6. future exception blocks (never silently discarded) -------------
    fut_rows = _live(tasks, "b3c1-future-exc")
    fut_exc = fut_rows[5]
    fut_link = series_store.get_link("b3c1-future-exc")
    gateway.seed_recurring_instance({
        "id": "b3c1-future-instance",
        "etag": '"1"',
        **task_to_occurrence_owned_payload(
            fut_exc, series_repo.get_by_uid("b3c1-future-exc")
        ),
        "recurringEventId": fut_link.remote_event_id,
        "originalStartTime": local_occurrence_to_google_original_start(
            series_repo.get_by_uid("b3c1-future-exc"),
            str(fut_exc.occurrence_key),
        ).to_google(),
        "extendedProperties": {"private": {}},
    })
    assert recurrence.edit_occurrence(fut_exc.uid, TaskEditorCommand(
        title="TEST future exception",
        notes=fut_exc.notes,
        priority=fut_exc.priority,
        completed=False,
        add_to_calendar=True,
        is_all_day=False,
        date_text=fut_exc.start.date().isoformat(),
        time_text=(fut_exc.start + timedelta(hours=1)).strftime("%H:%M"),
        duration_text="30",
    )).ok
    assert manual.run_once().ok
    blocked = splits.create_split_plan(
        "b3c1-future-exc", target_key("b3c1-future-exc", 3),
        RemoteSeriesSplitProposal(title="TEST blocked successor"),
    )
    assert not blocked.ok
    assert any(
        "future" in code for code in blocked.validation.codes
    ), blocked.validation.codes
    # The future exception itself is untouched.
    assert tasks.get_by_uid(fut_exc.uid).is_series_exception
    report["blocked_codes"] = list(blocked.validation.codes)
    report["blocked_task_uid"] = fut_exc.uid
    report["blocked_target_key"] = target_key("b3c1-future-exc", 3)
    report["blocked_dialog_task_uid"] = _live(tasks, "b3c1-future-exc")[3].uid

    # ---- 7. ETag race: zero split writes ------------------------------------
    race_plan = plan("b3c1-race")
    current = gateway.get_recurring_master_resource(
        race_plan.source_remote_event_id
    )
    foreign = dict(current)
    foreign["summary"] = "TEST foreign race edit"
    gateway.update_recurring_master_full(
        race_plan.source_remote_event_id, foreign,
        expected_etag=str(current.get("etag")),
    )
    writes_before_race = gateway.write_call_count
    race_sync = manual.run_once()
    assert race_sync.ok and race_sync.remote_split_conflicts >= 1
    assert gateway.write_call_count == writes_before_race
    race_rec = split_store.get_plan(race_plan.id)
    assert race_rec.state is RemoteSeriesSplitStatus.CONFLICT
    report["race"] = {"state": race_rec.state.value, "split_writes": 0}

    # ---- 8. source-trim partial failure -> retry reconciliation ------------
    partial_plan = plan("b3c1-trim-partial")
    source = gateway.get_recurring_master_resource(
        partial_plan.source_remote_event_id
    )
    gateway.update_recurring_master_full(
        partial_plan.source_remote_event_id,
        merge_split_master_resource(
            source, partial_plan.trimmed_source_payload
        ),
        expected_etag=partial_plan.source_remote_etag_base,
    )
    writes = gateway.write_call_count
    partial_sync = manual.run_once()
    assert partial_sync.ok
    assert partial_sync.remote_split_reconciliation_completions >= 1
    assert gateway.write_call_count == writes + 1  # only the insert remained
    assert split_store.get_plan(partial_plan.id).state is (
        RemoteSeriesSplitStatus.COMPLETED
    )

    # ---- 9. successor-created local-finalize failure -> local-only retry ---
    finalize_plan = plan("b3c1-finalize-fail")
    original_finalize = split_store.finalize_linked_remote_split_atomic
    state = {"failed": False}

    def failing_once(*args, **kwargs):
        if not state["failed"]:
            state["failed"] = True
            raise RuntimeError("simulated finalize failure")
        return original_finalize(*args, **kwargs)

    split_store.finalize_linked_remote_split_atomic = failing_once
    first = manual.run_once()
    assert first.ok and first.remote_splits_finalized == 0
    assert split_store.get_plan(finalize_plan.id).state is (
        RemoteSeriesSplitStatus.SUCCESSOR_CREATED
    )
    remote_writes = gateway.write_call_count
    second = manual.run_once()
    assert second.ok and second.remote_splits_finalized == 1
    assert gateway.write_call_count == remote_writes  # local retry only
    split_store.finalize_linked_remote_split_atomic = original_finalize

    # ---- 10. rollback after trim -------------------------------------------
    rollback_plan = plan("b3c1-rollback")
    source = gateway.get_recurring_master_resource(
        rollback_plan.source_remote_event_id
    )
    trimmed = gateway.update_recurring_master_full(
        rollback_plan.source_remote_event_id,
        merge_split_master_resource(
            source, rollback_plan.trimmed_source_payload
        ),
        expected_etag=rollback_plan.source_remote_etag_base,
    )
    split_store.mark_source_trimmed(
        rollback_plan.id, remote_etag=str(trimmed.get("etag"))
    )
    assert splits.request_split_rollback(rollback_plan.id).ok
    rollback_sync = manual.run_once()
    assert rollback_sync.ok
    assert rollback_sync.remote_split_rollbacks_completed == 1
    restored = gateway.get_recurring_master_resource(
        rollback_plan.source_remote_event_id
    )
    assert "COUNT=5" in restored["recurrence"][0]
    assert gateway.get_recurring_master_resource(
        rollback_plan.successor_remote_event_id
    ) is None
    report["rollback"] = {
        "state": split_store.get_plan(rollback_plan.id).state.value,
        "source_restored": True,
        "successor_absent": True,
    }

    # ---- 4/11. monthly plan created LAST so it stays pending for the UI ----
    # Monthly day-31 slots skip short months: Aug 31 is index 0, Oct 31 is
    # index 1 inside the materialized horizon.
    monthly_plan = plan("b3c1-monthly", index=1)
    assert split_store.get_plan(monthly_plan.id).state is (
        RemoteSeriesSplitStatus.PENDING
    )

    # ---- master duplication / ordinary flood checks ------------------------
    masters = [
        event for event in gateway.events
        if event.is_recurring_master and not event.is_cancelled
    ]
    master_ids = [event.id for event in masters]
    assert len(master_ids) == len(set(master_ids))
    ordinary_events = [
        event for event in gateway.events
        if event.is_ordinary_event and not event.is_cancelled
    ]
    assert ordinary_events == []
    assert ordinary_store.count_pending_ops() == 0
    report["masters_total"] = len(masters)
    report["ordinary_event_flood"] = 0

    # ---- diagnostics + report ----------------------------------------------
    diag = splits.diagnostics()
    report["diagnostics"] = diag
    report["dialog_task_uid"] = _live(tasks, "b3c1-dialog")[2].uid
    report["recovery_plan_id"] = race_rec.id
    report["real_google_calls"] = 0
    (Path(data_dir) / "phase3_2b3c1_smoke_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )

    for closable in (catalog, split_store, occurrence_store, series_store,
                     series_repo, ordinary_store, tasks):
        closable.close()

    # ---- restart persistence -----------------------------------------------
    reopened = CalendarSeriesRemoteSplitStore(db_path)
    assert reopened.get_plan(count_plan.id).state is (
        RemoteSeriesSplitStatus.COMPLETED
    )
    assert reopened.get_plan(monthly_plan.id).state is (
        RemoteSeriesSplitStatus.PENDING
    )
    assert reopened.get_plan(race_plan.id).state is (
        RemoteSeriesSplitStatus.CONFLICT
    )
    assert reopened.get_plan(rollback_plan.id).state is (
        RemoteSeriesSplitStatus.ROLLED_BACK
    )
    reopened.close()

    print(f"profile={data_dir}")
    print(f"db={db_path}")
    print("count_split=2/3 until_split=UNTIL-kept never_split=never")
    print("one_source_update=true one_successor_insert=true")
    print("ordinary_event_flood=0 master_duplication=0")
    print("etag_race_split_writes=0 retry_reconciliation=true rollback=true")
    print("restart_persistence=true real_google_calls=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
