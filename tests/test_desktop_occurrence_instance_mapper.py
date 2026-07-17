from copy import deepcopy
from datetime import datetime

import pytest

from planner_desktop.domain.google_occurrence import (
    canonical_occurrence_payload_data,
)
from planner_desktop.domain.task import Task
from planner_desktop.sync.calendar_series_occurrence_mapper import (
    build_desired_occurrence_payload,
    merge_complete_instance_payload,
    task_to_occurrence_owned_payload,
)
from tests.occurrence_sync_testkit import timed_series


def _task(series):
    return Task(
        title="Moved title",
        notes="Planner notes",
        start=datetime(2026, 7, 20, 10),
        end=datetime(2026, 7, 20, 10, 45),
        duration_minutes=45,
        is_all_day=False,
        series_uid=series.uid,
        occurrence_key="2026-07-20T09:00@Europe/Moscow",
    )


def test_mapper_emits_only_owned_fields_and_private_markers():
    series = timed_series()
    payload = build_desired_occurrence_payload(_task(series), series, 3)
    assert set(canonical_occurrence_payload_data(payload)) == {
        "summary", "description", "start", "end", "status"
    }
    private = payload["extendedProperties"]["private"]
    assert private["planner_series_uid"] == series.uid
    assert private["planner_occurrence_key"].endswith("@Europe/Moscow")
    assert private["planner_link_generation"] == "3"
    assert "tags" not in payload and "priority" not in payload
    assert "recurrence" not in payload


def test_full_resource_merge_preserves_unowned_google_fields():
    series = timed_series()
    desired = build_desired_occurrence_payload(_task(series), series, 0)
    complete = {
        "id": "instance-1",
        "etag": '"7"',
        "summary": "remote",
        "description": "remote",
        "start": {"dateTime": "old"},
        "end": {"dateTime": "old"},
        "status": "confirmed",
        "recurrence": ["RRULE:FREQ=DAILY"],
        "attendees": [{"email": "kept@example.test"}],
        "location": "Keep this",
        "extendedProperties": {
            "private": {"foreign_private": "keep"},
            "shared": {"foreign_shared": "keep"},
        },
    }
    original = deepcopy(complete)
    merged = merge_complete_instance_payload(complete, desired)
    assert complete == original
    assert merged["attendees"] == original["attendees"]
    assert merged["location"] == "Keep this"
    assert merged["extendedProperties"]["private"]["foreign_private"] == "keep"
    assert merged["extendedProperties"]["shared"]["foreign_shared"] == "keep"
    assert "recurrence" not in merged


def test_timed_to_all_day_and_multi_day_timed_are_rejected():
    series = timed_series()
    task = _task(series)
    task.is_all_day = True
    with pytest.raises(ValueError, match="3.2B3B"):
        task_to_occurrence_owned_payload(task, series)
    task = _task(series)
    task.end = datetime(2026, 7, 21, 10)
    with pytest.raises(ValueError, match="3.2B3C"):
        task_to_occurrence_owned_payload(task, series)
