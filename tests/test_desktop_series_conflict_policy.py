import re
from datetime import date, time

import pytest

from planner_desktop.domain.recurrence import (
    RecurrenceFrequency,
    RecurrenceRule,
    SeriesSchedule,
    TaskSeries,
)
from planner_desktop.domain.series_calendar_link import (
    PendingSeriesSyncOp,
    SeriesCalendarLink,
    SeriesLinkStatus,
    SeriesSyncOpKind,
    deterministic_remote_event_id,
)
from planner_desktop.domain.series_conflict_resolution import (
    RemoteDeletedRecoveryKind,
    deterministic_remote_event_id_for_generation,
    evaluate_use_google,
    next_link_generation_proposal,
    validate_disconnect,
    validate_keep_planner,
    validate_remote_deleted_recovery,
)


def _series(uid="s1"):
    return TaskSeries(
        uid=uid, title="Local",
        schedule=SeriesSchedule(
            date(2026, 7, 15), False, time(9), 30, "Europe/Moscow"
        ),
        rule=RecurrenceRule(RecurrenceFrequency.DAILY),
    )


def _conflict_link(uid="s1"):
    return SeriesCalendarLink(
        series_uid=uid,
        remote_event_id="plrabc",
        link_status=SeriesLinkStatus.CONFLICT,
        conflict_remote_etag='"7"',
        id=1,
    )


def _snapshot(uid="s1", **overrides):
    snapshot = {
        "id": "plrabc",
        "etag": '"7"',
        "status": "confirmed",
        "summary": "Remote title",
        "description": "",
        "start": {"dateTime": "2026-07-15T09:00:00+03:00",
                  "timeZone": "Europe/Moscow"},
        "end": {"dateTime": "2026-07-15T09:30:00+03:00",
                "timeZone": "Europe/Moscow"},
        "recurrence": ["RRULE:FREQ=DAILY;INTERVAL=1"],
        "updated_at": "2026-07-15T07:00:00+00:00",
        "private": {"planner_series_uid": uid, "planner_payload_hash": "h1"},
    }
    snapshot.update(overrides)
    return snapshot


# ---- deterministic generation ids -------------------------------------------

def test_generation_zero_keeps_phase_b2_formula():
    assert deterministic_remote_event_id_for_generation("s1", 0) == (
        deterministic_remote_event_id("s1")
    )


def test_generations_are_stable_distinct_and_google_valid():
    gen1_a = deterministic_remote_event_id_for_generation("s1", 1)
    gen1_b = deterministic_remote_event_id_for_generation("s1", 1)
    gen2 = deterministic_remote_event_id_for_generation("s1", 2)
    gen0 = deterministic_remote_event_id_for_generation("s1", 0)
    other = deterministic_remote_event_id_for_generation("s2", 1)
    assert gen1_a == gen1_b
    assert len({gen0, gen1_a, gen2, other}) == 4
    for value in (gen0, gen1_a, gen2, other):
        assert re.fullmatch(r"plr[0-9a-v]+", value)
        assert 5 <= len(value) <= 1024


def test_generation_rejects_invalid_input():
    with pytest.raises(ValueError):
        deterministic_remote_event_id_for_generation("", 1)
    with pytest.raises(ValueError):
        deterministic_remote_event_id_for_generation("s1", -1)


def test_next_generation_proposal_is_max_plus_one():
    proposal = next_link_generation_proposal("s1", [0, 3, 1])
    assert proposal.generation == 4
    assert proposal.remote_event_id == (
        deterministic_remote_event_id_for_generation("s1", 4)
    )
    assert next_link_generation_proposal("s1", []).generation == 1


# ---- keep planner ------------------------------------------------------------

def test_keep_planner_ok_with_snapshot_and_etag():
    validation = validate_keep_planner(
        series=_series(), link=_conflict_link(), snapshot=_snapshot(),
        acknowledged_remote_etag='"7"',
    )
    assert validation.ok


def test_keep_planner_requires_conflict_state():
    link = _conflict_link()
    link.link_status = SeriesLinkStatus.SYNCED
    validation = validate_keep_planner(
        series=_series(), link=link, snapshot=_snapshot(),
        acknowledged_remote_etag='"7"',
    )
    assert any(i.code == "not_in_conflict" for i in validation.issues)


def test_keep_planner_requires_snapshot_and_acknowledged_etag():
    validation = validate_keep_planner(
        series=_series(), link=_conflict_link(), snapshot=None,
        acknowledged_remote_etag=None,
    )
    codes = {i.code for i in validation.issues}
    assert "missing_snapshot" in codes
    assert "missing_acknowledged_etag" in codes


def test_keep_planner_rejects_foreign_and_mismatched_master():
    foreign = validate_keep_planner(
        series=_series(), link=_conflict_link(),
        snapshot=_snapshot(private={}), acknowledged_remote_etag='"7"',
    )
    assert any(i.code == "foreign_master" for i in foreign.issues)
    mismatch = validate_keep_planner(
        series=_series(), link=_conflict_link(),
        snapshot=_snapshot(private={"planner_series_uid": "other"}),
        acknowledged_remote_etag='"7"',
    )
    assert any(i.code == "series_uid_mismatch" for i in mismatch.issues)


def test_keep_planner_rejects_competing_delete_or_create():
    for kind in (SeriesSyncOpKind.DELETE, SeriesSyncOpKind.CREATE):
        validation = validate_keep_planner(
            series=_series(), link=_conflict_link(), snapshot=_snapshot(),
            acknowledged_remote_etag='"7"',
            pending_op=PendingSeriesSyncOp(id=1, series_uid="s1", op=kind),
        )
        assert any(i.code == "competing_operation" for i in validation.issues)


# ---- use google ----------------------------------------------------------------

def test_use_google_accepts_supported_timed_daily():
    validation, accepted = evaluate_use_google(
        series=_series(), link=_conflict_link(), snapshot=_snapshot()
    )
    assert validation.ok
    assert accepted.title == "Remote title"
    assert accepted.schedule.all_day is False
    assert accepted.schedule.local_time == time(9, 0)
    assert accepted.schedule.duration_minutes == 30
    assert accepted.schedule.timezone_name == "Europe/Moscow"
    assert accepted.rule.frequency is RecurrenceFrequency.DAILY
    assert accepted.remote_etag == '"7"'
    assert accepted.remote_payload_hash == "h1"


def test_use_google_accepts_all_day_weekly():
    snapshot = _snapshot(
        start={"date": "2026-07-13"},
        end={"date": "2026-07-14"},
        recurrence=["RRULE:FREQ=WEEKLY;INTERVAL=1;BYDAY=MO,WE"],
    )
    validation, accepted = evaluate_use_google(
        series=_series(), link=_conflict_link(), snapshot=snapshot
    )
    assert validation.ok
    assert accepted.schedule.all_day is True
    assert accepted.rule.frequency is RecurrenceFrequency.WEEKLY
    assert accepted.rule.weekdays == (0, 2)


def test_use_google_accepts_monthly_and_yearly():
    monthly = _snapshot(recurrence=["RRULE:FREQ=MONTHLY;BYMONTHDAY=15"])
    yearly = _snapshot(
        recurrence=["RRULE:FREQ=YEARLY;BYMONTHDAY=15;BYMONTH=7;COUNT=5"]
    )
    ok_m, accepted_m = evaluate_use_google(
        series=_series(), link=_conflict_link(), snapshot=monthly
    )
    ok_y, accepted_y = evaluate_use_google(
        series=_series(), link=_conflict_link(), snapshot=yearly
    )
    assert ok_m.ok and accepted_m.rule.month_day == 15
    assert ok_y.ok and accepted_y.rule.yearly_month == 7
    assert accepted_y.rule.occurrence_count == 5


def test_use_google_rejects_unsupported_recurrence():
    snapshot = _snapshot(recurrence=["RRULE:FREQ=WEEKLY;BYDAY=2MO"])
    validation, accepted = evaluate_use_google(
        series=_series(), link=_conflict_link(), snapshot=snapshot
    )
    assert not validation.ok
    assert accepted is None
    assert any(i.code == "unsupported_recurrence" for i in validation.issues)


def test_use_google_rejects_exdate_and_rdate_instead_of_discarding():
    for extra in ("EXDATE;TZID=Europe/Moscow:20260716T090000",
                  "RDATE;TZID=Europe/Moscow:20260720T090000"):
        snapshot = _snapshot(
            recurrence=["RRULE:FREQ=DAILY;INTERVAL=1", extra]
        )
        validation, accepted = evaluate_use_google(
            series=_series(), link=_conflict_link(), snapshot=snapshot
        )
        assert accepted is None
        assert any(
            i.code == "unsupported_exceptions" for i in validation.issues
        ), extra


def test_use_google_rejects_invalid_forms():
    mixed = _snapshot(start={"date": "2026-07-15"})
    multi_day = _snapshot(
        start={"date": "2026-07-15"}, end={"date": "2026-07-18"}
    )
    bad_zone = _snapshot(
        start={"dateTime": "2026-07-15T09:00:00", "timeZone": "Nope/Zone"},
        end={"dateTime": "2026-07-15T09:30:00", "timeZone": "Nope/Zone"},
    )
    zero_duration = _snapshot(
        end={"dateTime": "2026-07-15T09:00:00+03:00",
             "timeZone": "Europe/Moscow"},
    )
    for snapshot, code in (
        (mixed, "invalid_start_form"),
        (multi_day, "multi_day_all_day"),
        (bad_zone, "invalid_timezone"),
        (zero_duration, "invalid_duration"),
    ):
        validation, accepted = evaluate_use_google(
            series=_series(), link=_conflict_link(), snapshot=snapshot
        )
        assert accepted is None
        assert any(i.code == code for i in validation.issues), code


def test_use_google_requires_matching_ownership():
    validation, accepted = evaluate_use_google(
        series=_series(), link=_conflict_link(),
        snapshot=_snapshot(private={"planner_series_uid": "other"}),
    )
    assert accepted is None
    assert any(i.code == "series_uid_mismatch" for i in validation.issues)


# ---- disconnect and remote-deleted recovery -----------------------------------

def test_disconnect_allowed_only_for_conflict_or_remote_deleted():
    assert validate_disconnect(link=_conflict_link()).ok
    dead = _conflict_link()
    dead.link_status = SeriesLinkStatus.REMOTE_DELETED
    assert validate_disconnect(link=dead).ok
    synced = _conflict_link()
    synced.link_status = SeriesLinkStatus.SYNCED
    assert not validate_disconnect(link=synced).ok
    assert not validate_disconnect(link=None).ok


def test_remote_deleted_recovery_validation():
    dead = _conflict_link()
    dead.link_status = SeriesLinkStatus.REMOTE_DELETED
    for kind in RemoteDeletedRecoveryKind:
        assert validate_remote_deleted_recovery(
            kind=kind, series=_series(), link=dead
        ).ok, kind
    conflict = _conflict_link()
    validation = validate_remote_deleted_recovery(
        kind=RemoteDeletedRecoveryKind.RECREATE,
        series=_series(), link=conflict,
    )
    assert any(i.code == "not_remote_deleted" for i in validation.issues)
    inactive = _series()
    inactive.active = False
    validation = validate_remote_deleted_recovery(
        kind=RemoteDeletedRecoveryKind.RECREATE, series=inactive, link=dead
    )
    assert any(i.code == "series_inactive" for i in validation.issues)
