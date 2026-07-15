from datetime import date, time
from threading import Thread
import time as clock

from planner_desktop.domain.recurrence import (
    RecurrenceFrequency,
    RecurrenceRule,
    SeriesSchedule,
    TaskSeries,
)
from planner_desktop.repositories.fake_task_repository import FakeTaskRepository
from planner_desktop.repositories.series_repository import InMemorySeriesRepository
from planner_desktop.usecases.occurrence_materializer import (
    MATERIALIZATION_BUFFER_DAYS,
    MAX_RANGE_DAYS,
    OccurrenceMaterializer,
)
from planner_desktop.usecases.recurrence_service import EnsureResult, RecurrenceService


def test_materializer_adds_documented_buffer_and_deduplicates_covered_range():
    tasks = FakeTaskRepository(seed=False)
    recurrence = RecurrenceService(InMemorySeriesRepository(), tasks)
    recurrence.create_series(TaskSeries(
        title="Daily",
        schedule=SeriesSchedule(date(2026, 7, 1), True, timezone_name="UTC"),
        rule=RecurrenceRule(RecurrenceFrequency.DAILY),
    ))
    materializer = OccurrenceMaterializer(recurrence)
    first = materializer.ensure_day(date(2026, 7, 15))
    assert first.created == 1 + 2 * MATERIALIZATION_BUFFER_DAYS
    assert materializer.covered_start == date(2026, 7, 1)
    assert materializer.covered_end == date(2026, 7, 29)
    second = materializer.ensure_day(date(2026, 7, 15))
    assert (second.created, second.existing, second.skipped) == (0, 0, 0)


def test_materializer_rejects_invalid_and_caps_large_ranges():
    class Stub:
        def __init__(self):
            self.calls = []
        def add_change_listener(self, listener):
            self.listener = listener
        def ensure_occurrences(self, start, end):
            self.calls.append((start, end))
            return EnsureResult()

    stub = Stub()
    materializer = OccurrenceMaterializer(stub, buffer_days=0)
    assert materializer.ensure_range(date(2026, 2, 1), date(2026, 1, 1)).rejected
    materializer.ensure_range(date(2026, 1, 1), date(2030, 1, 1))
    start, end = stub.calls[0]
    assert (end - start).days == MAX_RANGE_DAYS


def test_concurrent_requests_share_one_materialization_call():
    class SlowStub:
        def __init__(self):
            self.calls = 0
        def add_change_listener(self, listener):
            self.listener = listener
        def ensure_occurrences(self, start, end):
            self.calls += 1
            clock.sleep(0.05)
            return EnsureResult(created=1)

    stub = SlowStub()
    materializer = OccurrenceMaterializer(stub, buffer_days=0)
    results = []
    threads = [
        Thread(target=lambda: results.append(
            materializer.ensure_day(date(2026, 7, 15))
        ))
        for _ in range(2)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert stub.calls == 1
    assert sum(result.created for result in results) == 1

