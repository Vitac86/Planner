from datetime import date, time
from types import SimpleNamespace

from planner_desktop.domain.recurrence import (
    RecurrenceFrequency,
    RecurrenceRule,
    SeriesSchedule,
    TaskSeries,
)
from planner_desktop.repositories.external_series_repository import (
    InMemoryExternalSeriesRepository,
)
from planner_desktop.repositories.fake_task_repository import FakeTaskRepository
from planner_desktop.repositories.series_repository import InMemorySeriesRepository
from planner_desktop.storage.calendar_series_sync_store import (
    CalendarSeriesSyncStore,
)
from planner_desktop.storage.calendar_sync_store import CalendarSyncStore
from planner_desktop.sync.calendar_series_sync_engine import (
    CalendarSeriesSyncEngine,
)
from planner_desktop.sync.calendar_sync_engine import CalendarSyncEngine
from planner_desktop.sync.fake_calendar_gateway import FakeCalendarGateway
from planner_desktop.usecases.series_calendar_link_service import (
    SeriesCalendarLinkService,
)
from planner_desktop.usecases.series_conflict_service import (
    CONFIRMATION_REQUIRED_ERROR,
    SeriesConflictService,
)

TODAY = date(2026, 7, 15)


def make_stack(tmp_path):
    db = tmp_path / "desktop.db"
    series_repo = InMemorySeriesRepository()
    tasks = FakeTaskRepository(seed=False)
    series = series_repo.add(TaskSeries(
        uid="s1", title="Local authoritative",
        schedule=SeriesSchedule(TODAY, False, time(9), 30, "Europe/Moscow"),
        rule=RecurrenceRule(RecurrenceFrequency.DAILY),
    ))
    store = CalendarSeriesSyncStore(db)
    ordinary = CalendarSyncStore(db)
    catalog = InMemoryExternalSeriesRepository(tasks)
    gateway = FakeCalendarGateway()
    links = SeriesCalendarLinkService(
        series_repo, tasks, store, today_provider=lambda: TODAY
    )
    assert links.connect_to_google(series.uid).ok
    engine = CalendarSeriesSyncEngine(series_repo, tasks, store, catalog, gateway)
    assert engine.push_pending().created == 1
    pull = CalendarSyncEngine(
        tasks, ordinary, gateway, catalog, series_link_store=store
    )
    pull.pull_remote_changes()
    conflicts = SeriesConflictService(
        series_repo, tasks, store, today_provider=lambda: TODAY
    )
    return SimpleNamespace(
        series_repo=series_repo, tasks=tasks, series=series, store=store,
        ordinary=ordinary, catalog=catalog, gateway=gateway, links=links,
        engine=engine, pull=pull, conflicts=conflicts,
        remote_id=store.get_link("s1").remote_event_id,
    )


def make_conflict(stack, summary="Changed in Google"):
    stack.gateway.patch_event(stack.remote_id, {"summary": summary})
    stack.pull.pull_remote_changes()
    link = stack.store.get_link("s1")
    assert link.link_status.value == "conflict"
    assert link.conflict_remote_snapshot is not None
    return link


def test_get_conflict_reports_comparison_and_actions(tmp_path):
    stack = make_stack(tmp_path)
    make_conflict(stack)
    data = stack.conflicts.get_conflict("s1")
    assert data["available"] and data["status"] == "conflict"
    assert data["local"]["title"] == "Local authoritative"
    assert data["remote"]["title"] == "Changed in Google"
    assert data["remote"]["supported"] is True
    assert data["local"]["timezone"] == "Europe/Moscow"
    assert data["ownershipOk"] is True
    assert data["canKeepPlanner"] and data["canUseGoogle"] and data["canDisconnect"]
    assert data["acknowledgedRemoteEtag"] == stack.store.get_link(
        "s1"
    ).conflict_remote_etag
    stack.store.close(); stack.ordinary.close()


def test_propose_keep_planner_requires_conflict(tmp_path):
    stack = make_stack(tmp_path)
    proposal = stack.conflicts.propose_keep_planner("s1")
    assert not proposal.ok
    make_conflict(stack)
    proposal = stack.conflicts.propose_keep_planner("s1")
    assert proposal.ok
    assert proposal.acknowledged_remote_etag
    assert proposal.desired_payload_hash
    stack.store.close(); stack.ordinary.close()


def test_resolve_keep_planner_requires_explicit_confirmation(tmp_path):
    stack = make_stack(tmp_path)
    make_conflict(stack)
    refused = stack.conflicts.resolve_keep_planner("s1")
    assert not refused.ok
    assert refused.error == CONFIRMATION_REQUIRED_ERROR
    assert stack.store.count_pending_ops() == 0
    assert stack.store.list_resolutions("s1") == []
    stack.store.close(); stack.ordinary.close()


def test_resolve_keep_planner_queues_exactly_one_operation(tmp_path):
    stack = make_stack(tmp_path)
    link = make_conflict(stack)
    first = stack.conflicts.resolve_keep_planner("s1", confirmed=True)
    assert first.ok and first.changed
    duplicate = stack.conflicts.resolve_keep_planner("s1", confirmed=True)
    assert duplicate.ok and not duplicate.changed
    assert duplicate.resolution.id == first.resolution.id
    ops = stack.store.list_ops()
    assert len(ops) == 1
    op = ops[0]
    assert op.resolution_id == first.resolution.id
    assert op.acknowledged_remote_etag == link.conflict_remote_etag
    assert op.payload["summary"] == "Local authoritative"
    audits = stack.store.list_resolutions("s1")
    assert len(audits) == 1 and audits[0].is_pending
    # Conflict is NOT cleared before the remote write succeeds.
    assert stack.store.get_link("s1").link_status.value == "conflict"
    stack.store.close(); stack.ordinary.close()


def test_keep_planner_rejected_for_foreign_master(tmp_path):
    stack = make_stack(tmp_path)
    make_conflict(stack)
    # Poison the stored snapshot ownership marker.
    link = stack.store.get_link("s1")
    link.conflict_remote_snapshot_json = (
        link.conflict_remote_snapshot_json.replace("s1", "sX")
    )
    stack.store.update_link(link)
    refused = stack.conflicts.resolve_keep_planner("s1", confirmed=True)
    assert not refused.ok
    assert "другой серии" in refused.error or "чужого" in refused.error
    assert stack.store.count_pending_ops() == 0
    stack.store.close(); stack.ordinary.close()


def test_keep_planner_requires_acknowledged_etag(tmp_path):
    stack = make_stack(tmp_path)
    make_conflict(stack)
    link = stack.store.get_link("s1")
    link.conflict_remote_etag = None
    stack.store.update_link(link)
    refused = stack.conflicts.resolve_keep_planner("s1", confirmed=True)
    assert not refused.ok
    assert "etag" in refused.error
    stack.store.close(); stack.ordinary.close()


def test_list_resolution_history_orders_and_filters(tmp_path):
    stack = make_stack(tmp_path)
    make_conflict(stack)
    stack.conflicts.resolve_keep_planner("s1", confirmed=True)
    all_rows = stack.conflicts.list_resolution_history()
    s1_rows = stack.conflicts.list_resolution_history("s1")
    assert len(all_rows) == len(s1_rows) == 1
    assert s1_rows[0].resolution_kind == "keep_planner"
    assert stack.conflicts.list_resolution_history("nope") == []
    stack.store.close(); stack.ordinary.close()


def test_get_remote_deleted_reports_recovery_options(tmp_path):
    stack = make_stack(tmp_path)
    stack.gateway.delete_recurring_master(stack.remote_id)
    stack.pull.pull_remote_changes()
    data = stack.conflicts.get_remote_deleted("s1")
    assert data["available"] is True
    assert data["canRecreate"] is True
    assert data["canDeleteLocal"] is True
    assert data["linkGeneration"] == 0
    assert data["nextGeneration"] == 1
    assert data["title"] == "Local authoritative"
    stack.store.close(); stack.ordinary.close()
