from datetime import date, time

import pytest

from planner_desktop.domain.google_occurrence import (
    google_original_start_to_occurrence_key,
    local_occurrence_to_google_original_start,
)
from planner_desktop.domain.recurrence import (
    RecurrenceFrequency,
    RecurrenceRule,
    SeriesSchedule,
    TaskSeries,
)
from tests.occurrence_sync_testkit import all_day_series, timed_series


def test_timed_identity_round_trip_is_original_wall_clock_slot():
    series = timed_series()
    key = "2026-07-20T09:00@Europe/Moscow"
    identity = local_occurrence_to_google_original_start(series, key)
    assert identity.kind == "datetime"
    assert identity.timezone_name == "Europe/Moscow"
    assert identity.value.startswith("2026-07-20T09:00:00+03:00")
    assert google_original_start_to_occurrence_key(series, identity) == key


def test_all_day_identity_round_trip_uses_date_only():
    series = all_day_series()
    identity = local_occurrence_to_google_original_start(series, "2026-07-22")
    assert identity.to_google() == {"date": "2026-07-22"}
    assert google_original_start_to_occurrence_key(series, identity) == "2026-07-22"


def test_moved_instance_start_does_not_participate_in_identity():
    series = timed_series()
    original = {
        "dateTime": "2026-07-20T09:00:00+03:00",
        "timeZone": "Europe/Moscow",
    }
    moved_current_start = "2026-07-21T15:30:00+03:00"
    assert google_original_start_to_occurrence_key(series, original) == (
        "2026-07-20T09:00@Europe/Moscow"
    )
    assert moved_current_start not in str(original)


def test_timezone_and_kind_mismatch_are_rejected():
    series = timed_series()
    with pytest.raises(ValueError, match="timezone"):
        google_original_start_to_occurrence_key(
            series,
            {
                "dateTime": "2026-07-20T09:00:00+02:00",
                "timeZone": "Europe/Paris",
            },
        )
    with pytest.raises(ValueError, match="all-day"):
        google_original_start_to_occurrence_key(series, {"date": "2026-07-20"})


def test_dst_nonexistent_and_fold_follow_recurrence_policy():
    spring = TaskSeries(
        uid="spring",
        title="DST spring",
        schedule=SeriesSchedule(
            date(2026, 3, 8), False, time(2, 30), 30, "America/New_York"
        ),
        rule=RecurrenceRule(RecurrenceFrequency.DAILY),
    )
    spring_key = "2026-03-08T02:30@America/New_York"
    spring_identity = local_occurrence_to_google_original_start(
        spring, spring_key
    )
    assert "T03:30:00-04:00" in spring_identity.value
    assert google_original_start_to_occurrence_key(
        spring, spring_identity
    ) == spring_key

    fold = TaskSeries(
        uid="fold",
        title="DST fold",
        schedule=SeriesSchedule(
            date(2026, 11, 1), False, time(1, 30), 30, "America/New_York"
        ),
        rule=RecurrenceRule(RecurrenceFrequency.DAILY),
    )
    identity = local_occurrence_to_google_original_start(
        fold, "2026-11-01T01:30@America/New_York"
    )
    assert identity.value.endswith("-04:00")
