from datetime import date, time

from planner_desktop.domain.recurrence import (
    RecurrenceEndMode,
    RecurrenceFrequency,
    RecurrenceRule,
    SeriesSchedule,
    TaskSeries,
)
from planner_desktop.domain.series_calendar_link import (
    PLANNER_PAYLOAD_HASH_PROPERTY,
    PLANNER_SERIES_UID_PROPERTY,
    canonical_master_payload_fingerprint,
    deterministic_remote_event_id,
)
from planner_desktop.sync.calendar_series_mapper import (
    master_event_to_owned_payload,
    master_payload_hash,
    series_to_master_event,
)


def test_timed_master_is_canonical_private_and_excludes_local_metadata():
    series = TaskSeries(
        uid="series-fixed",
        title="Standup",
        notes="Notes",
        priority=3,
        tags=("work",),
        schedule=SeriesSchedule(
            date(2026, 7, 15), False, time(9, 30), 45, "Europe/Moscow"
        ),
        rule=RecurrenceRule(
            RecurrenceFrequency.WEEKLY,
            weekdays=(0, 2),
            end_mode=RecurrenceEndMode.COUNT,
            occurrence_count=5,
        ),
    )
    event = series_to_master_event(series)
    body = master_event_to_owned_payload(event)
    assert body["start"]["timeZone"] == "Europe/Moscow"
    assert body["end"]["timeZone"] == "Europe/Moscow"
    assert body["recurrence"] == [
        "RRULE:FREQ=WEEKLY;INTERVAL=1;BYDAY=MO,WE;COUNT=5"
    ]
    private = body["extendedProperties"]["private"]
    assert private[PLANNER_SERIES_UID_PROPERTY] == "series-fixed"
    assert private[PLANNER_PAYLOAD_HASH_PROPERTY] == master_payload_hash(event)
    serialized = str(body).lower()
    assert "work" not in serialized and "priority" not in serialized
    assert "completed" not in serialized and "history" not in serialized


def test_all_day_end_is_exclusive_and_ids_hashes_are_stable():
    series = TaskSeries(
        uid="same-series",
        title="All day",
        schedule=SeriesSchedule(date(2026, 7, 20), True, timezone_name="UTC"),
        rule=RecurrenceRule(RecurrenceFrequency.DAILY),
    )
    event = series_to_master_event(series)
    body = master_event_to_owned_payload(event)
    assert body["start"] == {"date": "2026-07-20"}
    assert body["end"] == {"date": "2026-07-21"}
    first_id = deterministic_remote_event_id(series.uid)
    assert first_id == deterministic_remote_event_id(series.uid)
    assert len(first_id) >= 5 and set(first_id) <= set("0123456789abcdefghijklmnopqrstuv")
    assert master_payload_hash(event) == canonical_master_payload_fingerprint(body)
