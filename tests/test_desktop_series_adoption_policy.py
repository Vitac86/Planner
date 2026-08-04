from dataclasses import replace
from datetime import date, timedelta

import pytest

from planner_desktop.domain.external_series import ExternalCalendarSeries
from planner_desktop.domain.google_recurrence import GoogleRecurrenceSupport
from planner_desktop.domain.recurrence import (
    RecurrenceEndMode,
    RecurrenceFrequency,
    RecurrenceRule,
)
from planner_desktop.domain.series_adoption import evaluate_series_adoption
from tests.series_adoption_testkit import build_env


def _codes(result):
    return {item.code for item in result.blocked_reasons}


def test_supported_clean_organizer_owned_master_is_eligible(tmp_path):
    env = build_env(tmp_path)
    try:
        row = env.catalog.get("google", "primary", env.remote_id)
        result = evaluate_series_adoption(row)
        assert result.eligible
        assert result.organizer_self
        assert result.calendar_access_role == "owner"
        assert "Место" not in result.remote_only_fields
    finally:
        env.close()


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        ({"support_status": "unsupported", "unsupported_reason": "bad"}, "unsupported_recurrence"),
        ({"recurrence_lines": ("RRULE:FREQ=DAILY", "EXDATE:20260701")}, "recurrence_exclusions"),
        ({"exception_count": 1, "exception_dates_json": '["2026-07-01"]'}, "exceptions"),
        ({"calendar_access_role": "reader"}, "access_role"),
        ({"organizer_self": False}, "organizer"),
        ({"locked": True}, "locked"),
        ({"event_type": "outOfOffice"}, "event_type"),
        ({"planner_owned": True}, "planner_owned"),
    ],
)
def test_conservative_catalog_blocks(tmp_path, mutation, code):
    env = build_env(tmp_path)
    try:
        row = env.catalog.get("google", "primary", env.remote_id)
        for name, value in mutation.items():
            setattr(row, name, value)
        assert code in _codes(evaluate_series_adoption(row))
    finally:
        env.close()


@pytest.mark.parametrize(
    ("extra", "code"),
    [
        ({"attendees": [{"email": "guest@example.invalid"}]}, "attendees"),
        ({"conferenceData": {"entryPoints": []}}, "conference"),
    ],
)
def test_attendees_and_conference_are_rejected(tmp_path, extra, code):
    env = build_env(tmp_path, extras=extra)
    try:
        row = env.catalog.get("google", "primary", env.remote_id)
        assert code in _codes(evaluate_series_adoption(row))
    finally:
        env.close()


def test_local_identity_and_operation_guards(tmp_path):
    env = build_env(tmp_path)
    try:
        row = env.catalog.get("google", "primary", env.remote_id)
        assert "active_link" in _codes(
            evaluate_series_adoption(row, has_active_link=True)
        )
        assert "active_adoption" in _codes(
            evaluate_series_adoption(row, has_active_adoption=True)
        )
        assert "legacy_master_task" in _codes(
            evaluate_series_adoption(row, legacy_master_task_count=1)
        )
        assert "imported_instances" in _codes(
            evaluate_series_adoption(row, imported_instance_task_count=1)
        )
        assert "quarantine" in _codes(
            evaluate_series_adoption(row, unresolved_quarantine_count=1)
        )
        assert "pending_operation" in _codes(
            evaluate_series_adoption(row, pending_operation_count=1)
        )
    finally:
        env.close()


def test_no_future_occurrence_is_rejected(tmp_path):
    env = build_env(tmp_path)
    try:
        row = env.catalog.get("google", "primary", env.remote_id)
        row.start_value = (date.today() - timedelta(days=30)).isoformat()
        row.parsed_rule = RecurrenceRule(
            frequency=RecurrenceFrequency.DAILY,
            end_mode=RecurrenceEndMode.COUNT,
            occurrence_count=1,
        )
        assert "no_future_occurrence" in _codes(
            evaluate_series_adoption(row)
        )
    finally:
        env.close()
