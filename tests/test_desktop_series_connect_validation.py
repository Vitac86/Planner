from datetime import date, datetime, time

from planner_desktop.domain.recurrence import (
    RecurrenceFrequency,
    RecurrenceRule,
    SeriesSchedule,
    TaskSeries,
    replace_series,
)
from planner_desktop.domain.task import Task
from planner_desktop.repositories.fake_task_repository import FakeTaskRepository
from planner_desktop.repositories.series_repository import InMemorySeriesRepository
from planner_desktop.storage.calendar_series_sync_store import CalendarSeriesSyncStore
from planner_desktop.usecases.series_calendar_link_service import SeriesCalendarLinkService


def _series(**changes):
    base = TaskSeries(
        title="Daily",
        schedule=SeriesSchedule(
            start_date=date(2026, 7, 15),
            all_day=False,
            local_time=time(9),
            duration_minutes=30,
            timezone_name="Europe/Moscow",
        ),
        rule=RecurrenceRule(RecurrenceFrequency.DAILY),
    )
    return replace_series(base, **changes)


def _stack(tmp_path, series=None):
    series_repo = InMemorySeriesRepository()
    tasks = FakeTaskRepository(seed=False)
    item = series_repo.add(series or _series())
    store = CalendarSeriesSyncStore(tmp_path / "desktop.db")
    service = SeriesCalendarLinkService(
        series_repo,
        tasks,
        store,
        today_provider=lambda: date(2026, 7, 15),
    )
    return item, series_repo, tasks, store, service


def test_supported_clean_series_and_completed_occurrence_are_allowed(tmp_path):
    item, _, tasks, store, service = _stack(tmp_path)
    tasks.add(Task(
        title="Done",
        start=datetime(2026, 7, 16, 9),
        end=datetime(2026, 7, 16, 9, 30),
        completed=True,
        series_uid=item.uid,
        occurrence_key="2026-07-16T09:00",
    ))
    assert service.validate_connection(item.uid).ok
    store.close()


def test_invalid_timezone_unsupported_rule_and_future_exception_are_structured(tmp_path):
    invalid = _series(schedule=SeriesSchedule(
        date(2026, 7, 15), False, time(9), 30, "Mars/Olympus"
    ))
    item, repo, tasks, store, service = _stack(tmp_path, invalid)
    codes = {x.code for x in service.validate_connection(item.uid).issues}
    assert "invalid_timezone" in codes

    repo.update(replace_series(item, schedule=_series().schedule,
                               rule=RecurrenceRule(RecurrenceFrequency.DAILY, interval=1000)))
    codes = {x.code for x in service.validate_connection(item.uid).issues}
    assert "unsupported_recurrence" in codes

    repo.update(replace_series(repo.get_by_uid(item.uid), rule=_series().rule))
    tasks.add(Task(
        title="Moved", start=datetime(2026, 7, 20, 10),
        end=datetime(2026, 7, 20, 10, 30), series_uid=item.uid,
        occurrence_key="2026-07-20T09:00", is_series_exception=True,
    ))
    codes = {x.code for x in service.validate_connection(item.uid).issues}
    assert "future_exception" in codes
    store.close()


def test_future_tombstone_google_id_and_already_linked_are_rejected(tmp_path):
    item, _, tasks, store, service = _stack(tmp_path)
    row = tasks.add(Task(
        title="Deleted", start=datetime(2026, 7, 21, 9),
        end=datetime(2026, 7, 21, 9, 30), series_uid=item.uid,
        occurrence_key="2026-07-21T09:00",
    ))
    tasks.delete(row.id)
    assert "future_tombstone" in {
        x.code for x in service.validate_connection(item.uid).issues
    }
    # A completed tombstone is local history and no longer blocks.
    tombstone = tasks.get_by_uid(row.uid)
    tombstone.completed = True
    tasks.update(tombstone)
    google_row = tasks.add(Task(
        title="Foreign", start=datetime(2026, 7, 22, 9),
        end=datetime(2026, 7, 22, 9, 30), series_uid=item.uid,
        occurrence_key="2026-07-22T09:00", google_calendar_event_id="evt",
    ))
    assert "occurrence_has_google_id" in {
        x.code for x in service.validate_connection(item.uid).issues
    }
    tasks.hard_delete_by_uid(google_row.uid)
    assert service.connect_to_google(item.uid).ok
    assert "already_linked" in {
        x.code for x in service.validate_connection(item.uid).issues
    }
    store.close()
