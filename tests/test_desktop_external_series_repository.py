"""SQLite and in-memory behavior of the read-only external series catalog."""
from datetime import date, datetime, timezone

from planner_desktop.domain.external_series import ExternalCalendarSeries
from planner_desktop.domain.recurrence import RecurrenceFrequency, RecurrenceRule
from planner_desktop.domain.task import Task
from planner_desktop.repositories.external_series_repository import (
    InMemoryExternalSeriesRepository,
)
from planner_desktop.repositories.fake_task_repository import FakeTaskRepository
from planner_desktop.storage.external_series_repository import (
    SQLiteExternalSeriesRepository,
)
from planner_desktop.storage.sqlite_task_repository import SQLiteTaskRepository


FIRST = datetime(2026, 7, 15, 8, tzinfo=timezone.utc)
SECOND = datetime(2026, 7, 15, 9, tzinfo=timezone.utc)


def series(**kwargs):
    defaults = dict(
        provider="google",
        calendar_id="primary",
        remote_event_id="master-1",
        etag='"1"',
        title="Еженедельная встреча",
        start_kind="timed",
        start_value="2026-07-15T09:00:00",
        end_value="2026-07-15T09:30:00",
        timezone_name="Europe/Moscow",
        recurrence_lines=("RRULE:FREQ=WEEKLY;INTERVAL=1;BYDAY=WE",),
        parsed_rule=RecurrenceRule(RecurrenceFrequency.WEEKLY, weekdays=(2,)),
        support_status="supported",
        first_seen_at=FIRST,
        last_seen_at=FIRST,
        remote_updated_at=FIRST,
    )
    defaults.update(kwargs)
    return ExternalCalendarSeries(**defaults)


def test_upsert_key_refresh_and_first_seen_are_stable(tmp_path):
    repo = SQLiteExternalSeriesRepository(tmp_path / "desktop.db")
    first = repo.upsert(series())
    updated = repo.upsert(series(
        etag='"2"', title="Обновлённая", last_seen_at=SECOND,
        remote_updated_at=SECOND,
    ))
    stored = repo.get("google", "primary", "master-1")
    assert updated.id == first.id == stored.id
    assert stored.title == "Обновлённая"
    assert stored.etag == '"2"'
    assert stored.first_seen_at == FIRST
    assert stored.last_seen_at == SECOND
    repo.close()


def test_unsupported_raw_rule_persists_after_reopen(tmp_path):
    db_path = tmp_path / "desktop.db"
    raw = "RRULE:FREQ=MONTHLY;BYDAY=MO;BYSETPOS=1"
    repo = SQLiteExternalSeriesRepository(db_path)
    repo.upsert(series(
        remote_event_id="unsupported-1",
        recurrence_lines=(raw, "EXDATE:20261224"),
        parsed_rule=None,
        support_status="unsupported",
        unsupported_reason="BYSETPOS пока не поддерживается.",
    ))
    repo.close()

    reopened = SQLiteExternalSeriesRepository(db_path)
    stored = reopened.get("google", "primary", "unsupported-1")
    assert stored.recurrence_lines == (raw, "EXDATE:20261224")
    assert stored.parsed_rule is None
    assert stored.unsupported_reason == "BYSETPOS пока не поддерживается."
    reopened.close()


def test_cancellation_is_a_catalog_tombstone_only(tmp_path):
    repo = SQLiteExternalSeriesRepository(tmp_path / "desktop.db")
    repo.upsert(series())
    deleted = repo.mark_deleted(
        "google", "primary", "master-1", etag='"3"',
        remote_updated_at=SECOND, seen_at=SECOND,
    )
    assert deleted.is_cancelled
    assert deleted.deleted_at == SECOND
    assert deleted.recurrence_lines == series().recurrence_lines
    assert repo.list_all(include_deleted=False) == []
    repo.close()


def test_instance_count_and_conservative_legacy_master_diagnostic(tmp_path):
    db_path = tmp_path / "desktop.db"
    tasks = SQLiteTaskRepository(db_path)
    tasks.add(Task(
        title="Экземпляр", google_calendar_event_id="instance-1",
        google_calendar_recurring_event_id="master-1",
    ))
    legacy = tasks.add(Task(
        title="Возможный старый импорт мастера",
        google_calendar_event_id="master-1",
    ))
    repo = SQLiteExternalSeriesRepository(db_path)
    repo.upsert(series())
    assert repo.count_imported_instances("master-1") == 1
    assert repo.possible_legacy_master_import_ids() == [legacy.uid]
    # Diagnostic discovery is read-only: the suspected row survives unchanged.
    assert tasks.get_by_uid(legacy.uid).is_deleted is False
    repo.close()
    tasks.close()


def test_in_memory_repository_matches_identity_and_diagnostics():
    tasks = FakeTaskRepository(seed=False)
    instance = tasks.add(Task(
        title="Instance", google_calendar_event_id="i-1",
        google_calendar_recurring_event_id="master-1",
    ))
    legacy = tasks.add(Task(title="Legacy", google_calendar_event_id="master-1"))
    repo = InMemoryExternalSeriesRepository(tasks)
    repo.upsert(series())
    repo.upsert(series(etag='"2"', last_seen_at=SECOND))
    assert len(repo.list_all()) == 1
    assert repo.count_imported_instances("master-1") == 1
    assert repo.possible_legacy_master_import_ids() == [legacy.uid]
    assert tasks.get_by_uid(instance.uid) is not None

