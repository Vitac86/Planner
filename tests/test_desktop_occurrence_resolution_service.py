import json
from datetime import datetime

from planner_desktop.domain.google_occurrence import (
    OccurrenceOperationKind,
    OccurrenceSyncStatus,
    local_occurrence_to_google_original_start,
)
from planner_desktop.domain.series_calendar_link import RemoteOccurrenceChange
from planner_desktop.domain.task import Task, utc_now
from planner_desktop.repositories.fake_task_repository import FakeTaskRepository
from planner_desktop.repositories.series_repository import (
    InMemorySeriesRepository,
)
from planner_desktop.usecases.occurrence_resolution_service import (
    OccurrenceResolutionService,
)
from planner_desktop.usecases.series_calendar_link_service import (
    SeriesCalendarLinkService,
)
from tests.occurrence_sync_testkit import linked_occurrence_store, timed_series


def remote_payload(*, title="Google version", status="confirmed"):
    return {
        "id": "instance-1",
        "etag": '"5"',
        "summary": title,
        "description": "Remote notes",
        "start": {
            "dateTime": "2026-07-20T11:00:00+03:00",
            "timeZone": "Europe/Moscow",
        },
        "end": {
            "dateTime": "2026-07-20T11:45:00+03:00",
            "timeZone": "Europe/Moscow",
        },
        "status": status,
        "recurringEventId": "master-1",
        "originalStartTime": {
            "dateTime": "2026-07-20T09:00:00+03:00",
            "timeZone": "Europe/Moscow",
        },
    }


def make_resolution_stack(tmp_path, *, status="confirmed"):
    db = tmp_path / "desktop.db"
    series = timed_series()
    series_repo = InMemorySeriesRepository()
    series_repo.add(series)
    tasks = FakeTaskRepository(seed=False)
    key = "2026-07-20T09:00@Europe/Moscow"
    task = tasks.add(Task(
        title="Planner version",
        notes="Local notes",
        start=datetime(2026, 7, 20, 9),
        end=datetime(2026, 7, 20, 9, 30),
        duration_minutes=30,
        series_uid=series.uid,
        occurrence_key=key,
    ))
    master_store, occurrence_store, link = linked_occurrence_store(db, series)
    links = SeriesCalendarLinkService(
        series_repo, tasks, master_store
    )
    identity = local_occurrence_to_google_original_start(series, key)
    occurrence_store.ensure_occurrence_link(
        series.uid, key, link, identity
    )
    payload = remote_payload(status=status)
    now = utc_now()
    change = occurrence_store.upsert_occurrence_change(
        RemoteOccurrenceChange(
            provider="google",
            calendar_id="primary",
            remote_master_event_id="master-1",
            remote_instance_event_id="instance-1",
            original_start_value=identity.value,
            status=status,
            payload_json=json.dumps(payload),
            remote_etag='"5"',
            remote_updated_at=now,
            first_seen_at=now,
            last_seen_at=now,
            matched_series_uid=series.uid,
            matched_occurrence_key=key,
        )
    )
    occurrence_store.record_remote_conflict(
        series.uid,
        key,
        reason="remote change",
        snapshot=payload,
        remote_instance_event_id="instance-1",
        remote_etag='"5"',
        remote_updated_at=now,
        cancelled=status == "cancelled",
    )
    service = OccurrenceResolutionService(
        series_repo, tasks, links, occurrence_store
    )
    return (
        series, task, change, series_repo, tasks, master_store,
        occurrence_store, links, service,
    )


def test_use_google_is_transactional_local_only_and_preserves_identity(tmp_path):
    (
        series, task, change, _series_repo, tasks, master, store, _links, service
    ) = make_resolution_stack(tmp_path)
    result = service.use_google(change.id)
    assert result.ok
    updated = tasks.get_by_uid(task.uid)
    assert updated.title == "Google version"
    assert updated.start == datetime(2026, 7, 20, 11)
    assert updated.occurrence_key == task.occurrence_key
    assert updated.series_uid == series.uid
    assert updated.google_calendar_event_id is None
    assert store.get_pending_op(series.uid, task.occurrence_key) is None
    assert store.get_occurrence_change(change.id).resolution_kind == "use_google"
    store.close()
    master.close()


def test_keep_planner_requires_confirmation_and_queues_one_conditional_op(tmp_path):
    (
        series, task, change, _series_repo, _tasks, master, store, _links, service
    ) = make_resolution_stack(tmp_path)
    assert not service.keep_planner(change.id).ok
    assert store.get_pending_op(series.uid, task.occurrence_key) is None
    result = service.keep_planner(change.id, confirmed=True)
    assert result.ok
    op = store.get_pending_op(series.uid, task.occurrence_key)
    assert op.op is OccurrenceOperationKind.UPDATE
    assert op.acknowledged_remote_etag == '"5"'
    pending = store.get_occurrence_change(change.id)
    assert pending.resolution_status == "pending"
    assert pending.resolved_at is None
    store.close()
    master.close()


def test_keep_both_creates_independent_local_task_without_google_linkage(tmp_path):
    (
        series, task, change, _series_repo, tasks, master, store, _links, service
    ) = make_resolution_stack(tmp_path)
    result = service.keep_both_as_local_copy(change.id)
    assert result.ok
    duplicate = result.task
    assert duplicate.uid != task.uid
    assert duplicate.series_uid is None
    assert duplicate.occurrence_key is None
    assert duplicate.google_calendar_event_id is None
    assert duplicate.title == "Google version"
    assert len(tasks.all()) == 2
    assert store.get_occurrence_change(
        change.id
    ).resolution_kind == "duplicated_local_copy"
    store.close()
    master.close()


def test_accept_cancelled_remote_keeps_local_tombstone(tmp_path):
    (
        series, task, change, _series_repo, tasks, master, store, _links, service
    ) = make_resolution_stack(tmp_path, status="cancelled")
    result = service.use_google(change.id)
    assert result.ok
    assert tasks.get_by_uid(task.uid).is_deleted
    assert tasks.get_by_uid(task.uid).occurrence_key == task.occurrence_key
    link = store.get_occurrence_link(series.uid, task.occurrence_key)
    assert link.sync_status is OccurrenceSyncStatus.CANCELLED
    store.close()
    master.close()


def test_use_google_rolls_back_task_and_link_if_quarantine_close_fails(
    tmp_path, monkeypatch
):
    (
        series, task, change, _series_repo, tasks, master, store, _links, service
    ) = make_resolution_stack(tmp_path)
    original_link = store.get_occurrence_link(series.uid, task.occurrence_key)
    monkeypatch.setattr(store, "resolve_occurrence_change", lambda *a, **k: False)
    result = service.use_google(change.id)
    assert not result.ok
    assert tasks.get_by_uid(task.uid).title == "Planner version"
    restored = store.get_occurrence_link(series.uid, task.occurrence_key)
    assert restored.sync_status == original_link.sync_status
    store.close()
    master.close()
