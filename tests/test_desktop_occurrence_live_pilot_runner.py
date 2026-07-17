from datetime import date, time, timedelta

from planner_desktop.domain.recurrence import (
    RecurrenceEndMode,
    RecurrenceFrequency,
    RecurrenceRule,
    SeriesSchedule,
    TaskSeries,
)
from planner_desktop.domain.series_calendar_link import SeriesLinkStatus
from planner_desktop.scripts.run_phase3_2b3b_live_pilot import (
    _cleanup_local_pilot_rows,
)
from planner_desktop.storage.calendar_series_occurrence_sync_store import (
    CalendarSeriesOccurrenceSyncStore,
)
from planner_desktop.storage.calendar_series_sync_store import (
    CalendarSeriesSyncStore,
)
from planner_desktop.storage.series_repository import SQLiteSeriesRepository
from planner_desktop.storage.sqlite_task_repository import SQLiteTaskRepository
from planner_desktop.usecases.series_calendar_link_service import (
    SeriesCalendarLinkService,
)


def test_live_pilot_cleanup_uses_real_master_store_api(tmp_path):
    db_path = tmp_path / "desktop.db"
    tasks = SQLiteTaskRepository(db_path)
    series_repo = SQLiteSeriesRepository(db_path)
    series_store = CalendarSeriesSyncStore(db_path)
    occurrence_store = CalendarSeriesOccurrenceSyncStore(db_path)
    start_day = date.today() + timedelta(days=14)
    series = series_repo.add(TaskSeries(
        uid="live-pilot-cleanup-test",
        title="[Planner Phase 3.2B3B TEST] cleanup",
        schedule=SeriesSchedule(
            start_date=start_day,
            all_day=False,
            local_time=time(10),
            duration_minutes=15,
            timezone_name="Europe/Moscow",
        ),
        rule=RecurrenceRule(
            RecurrenceFrequency.DAILY,
            end_mode=RecurrenceEndMode.COUNT,
            occurrence_count=3,
        ),
    ))
    links = SeriesCalendarLinkService(
        series_repo,
        tasks,
        series_store,
        today_provider=lambda: start_day - timedelta(days=1),
    )
    assert links.connect_to_google(series.uid).ok
    assert series_store.count_pending_ops() == 1

    _cleanup_local_pilot_rows(
        series.uid,
        [],
        links,
        series_store,
        occurrence_store,
    )

    assert series_store.count_pending_ops() == 0
    assert series_store.count_terminal_ops() == 0
    assert links.get_link(series.uid) is None
    detached = series_store.get_link(series.uid, include_detached=True)
    assert detached is not None
    assert detached.link_status is SeriesLinkStatus.DETACHED
    occurrence_store.close()
    series_store.close()
    series_repo.close()
    tasks.close()
