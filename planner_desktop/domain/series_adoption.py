"""Pure domain model for explicit external Google-series adoption.

Phase 3.2B3C2 supports one deliberately narrow direction:

    clean external Google recurring master -> new local TaskSeries

This module contains no repositories, Qt objects, Google clients, or network
access.  Eligibility is intentionally conservative: a field that cannot be
represented losslessly blocks adoption instead of being approximated.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from enum import Enum
from typing import Any, Mapping, Optional, Sequence, Tuple

from planner_desktop.domain.external_series import ExternalCalendarSeries
from planner_desktop.domain.recurrence import generate_occurrences, is_valid_timezone
from planner_desktop.domain.series_calendar_link import (
    PLANNER_LINK_VERSION_PROPERTY,
    PLANNER_PAYLOAD_HASH_PROPERTY,
    PLANNER_SERIES_REVISION_PROPERTY,
    PLANNER_SERIES_UID_PROPERTY,
)
from planner_desktop.domain.task import utc_now


PLANNER_LINK_ORIGIN_PROPERTY = "planner_link_origin"
PLANNER_ADOPTION_VERSION_PROPERTY = "planner_adoption_version"
PLANNER_ADOPTION_ID_PROPERTY = "planner_adoption_id"
PLANNER_ADOPTION_VERSION = "1"
ADOPTED_EXTERNAL_ORIGIN = "adopted_external"
PLANNER_CREATED_ORIGIN = "planner_created"
EXTERNAL_EXISTING_ID_STRATEGY = "external_existing"
PLANNER_DETERMINISTIC_ID_STRATEGY = "planner_deterministic"

PLANNER_OWNERSHIP_PROPERTIES = frozenset(
    {
        PLANNER_SERIES_UID_PROPERTY,
        PLANNER_LINK_VERSION_PROPERTY,
        PLANNER_SERIES_REVISION_PROPERTY,
        PLANNER_PAYLOAD_HASH_PROPERTY,
        PLANNER_LINK_ORIGIN_PROPERTY,
        PLANNER_ADOPTION_VERSION_PROPERTY,
        PLANNER_ADOPTION_ID_PROPERTY,
    }
)


class SeriesAdoptionState(str, Enum):
    PENDING = "pending"
    REMOTE_CLAIMED = "remote_claimed"
    LOCAL_FINALIZE_PENDING = "local_finalize_pending"
    COMPLETED = "completed"
    CONFLICT = "conflict"
    REMOTE_DELETED = "remote_deleted"
    ROLLBACK_PENDING = "rollback_pending"
    ROLLED_BACK = "rolled_back"
    RELEASE_PENDING = "release_pending"
    RELEASE_CONFLICT = "release_conflict"
    RELEASED = "released"
    TERMINAL_ERROR = "terminal_error"


ACTIVE_ADOPTION_STATES = frozenset(
    {
        SeriesAdoptionState.PENDING,
        SeriesAdoptionState.REMOTE_CLAIMED,
        SeriesAdoptionState.LOCAL_FINALIZE_PENDING,
        SeriesAdoptionState.CONFLICT,
        SeriesAdoptionState.ROLLBACK_PENDING,
        SeriesAdoptionState.RELEASE_PENDING,
        SeriesAdoptionState.RELEASE_CONFLICT,
    }
)
PROCESSABLE_ADOPTION_STATES = frozenset(
    {
        SeriesAdoptionState.PENDING,
        SeriesAdoptionState.REMOTE_CLAIMED,
        SeriesAdoptionState.LOCAL_FINALIZE_PENDING,
        SeriesAdoptionState.ROLLBACK_PENDING,
        SeriesAdoptionState.RELEASE_PENDING,
    }
)


@dataclass(frozen=True)
class AdoptionBlockedReason:
    code: str
    message: str


@dataclass(frozen=True)
class SeriesAdoptionEligibility:
    remote_event_id: str
    eligible: bool
    blocked_reasons: Tuple[AdoptionBlockedReason, ...] = ()
    affected_exception_dates: Tuple[str, ...] = ()
    organizer_self: bool = False
    calendar_access_role: str = ""
    recurrence_summary: str = ""
    remote_only_fields: Tuple[str, ...] = ()

    @property
    def errors(self) -> Tuple[str, ...]:
        return tuple(item.message for item in self.blocked_reasons)

    def to_dict(self) -> dict[str, Any]:
        return {
            "remoteEventId": self.remote_event_id,
            "eligible": self.eligible,
            "blockedReasons": [
                {"code": item.code, "message": item.message}
                for item in self.blocked_reasons
            ],
            "affectedExceptionDates": list(self.affected_exception_dates),
            "organizerSelf": self.organizer_self,
            "calendarAccessRole": self.calendar_access_role,
            "recurrenceSummary": self.recurrence_summary,
            "remoteOnlyFields": list(self.remote_only_fields),
        }


@dataclass
class CalendarSeriesAdoption:
    provider: str
    calendar_id: str
    remote_event_id: str
    reserved_series_uid: str
    remote_etag_base: str
    remote_snapshot_json: str
    canonical_remote_payload_hash: str
    preexisting_private_properties_json: str
    external_catalog_id: Optional[int] = None
    claimed_private_properties_json: Optional[str] = None
    claimed_remote_etag: Optional[str] = None
    local_link_id: Optional[int] = None
    state: SeriesAdoptionState = SeriesAdoptionState.PENDING
    attempts: int = 0
    last_error: Optional[str] = None
    conflict_snapshot_json: Optional[str] = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    completed_at: Optional[datetime] = None
    rolled_back_at: Optional[datetime] = None
    release_requested_at: Optional[datetime] = None
    released_at: Optional[datetime] = None
    id: Optional[int] = None

    @property
    def is_active(self) -> bool:
        return self.state in ACTIVE_ADOPTION_STATES

    @property
    def snapshot(self) -> dict[str, Any]:
        try:
            value = json.loads(self.remote_snapshot_json)
        except (TypeError, ValueError):
            return {}
        return value if isinstance(value, dict) else {}

    @property
    def preexisting_private_properties(self) -> dict[str, str]:
        try:
            value = json.loads(self.preexisting_private_properties_json)
        except (TypeError, ValueError):
            return {}
        if not isinstance(value, dict):
            return {}
        return {str(key): str(item) for key, item in value.items()}

    @property
    def claimed_private_properties(self) -> dict[str, str]:
        if not self.claimed_private_properties_json:
            return {}
        try:
            value = json.loads(self.claimed_private_properties_json)
        except (TypeError, ValueError):
            return {}
        if not isinstance(value, dict):
            return {}
        return {str(key): str(item) for key, item in value.items()}

    def clone(self) -> "CalendarSeriesAdoption":
        return replace(self)


def planner_adoption_private_properties(
    *,
    adoption_id: int,
    series_uid: str,
    revision: int,
    payload_hash: str,
) -> dict[str, str]:
    return {
        PLANNER_SERIES_UID_PROPERTY: str(series_uid),
        PLANNER_LINK_VERSION_PROPERTY: "1",
        PLANNER_SERIES_REVISION_PROPERTY: str(int(revision)),
        PLANNER_PAYLOAD_HASH_PROPERTY: str(payload_hash),
        PLANNER_LINK_ORIGIN_PROPERTY: ADOPTED_EXTERNAL_ORIGIN,
        PLANNER_ADOPTION_VERSION_PROPERTY: PLANNER_ADOPTION_VERSION,
        PLANNER_ADOPTION_ID_PROPERTY: str(int(adoption_id)),
    }


def _raw_payload(series: ExternalCalendarSeries) -> dict[str, Any]:
    value = series.raw_snapshot
    return value if isinstance(value, dict) else {}


def _remote_only_fields(raw: Mapping[str, Any]) -> Tuple[str, ...]:
    labels = (
        ("location", "Место"),
        ("reminders", "Напоминания"),
        ("colorId", "Цвет"),
        ("visibility", "Видимость"),
        ("transparency", "Занятость"),
        ("attachments", "Вложения"),
    )
    return tuple(label for key, label in labels if raw.get(key) not in (None, "", [], {}))


def _has_future_occurrence(series: ExternalCalendarSeries, today: date) -> bool:
    schedule = series.schedule()
    if schedule is None or series.parsed_rule is None:
        return False
    try:
        return bool(
            generate_occurrences(
                schedule,
                series.parsed_rule,
                today,
                date(9999, 12, 31),
                limit=1,
            )
        )
    except (OverflowError, TypeError, ValueError):
        return False


def evaluate_series_adoption(
    series: ExternalCalendarSeries,
    *,
    today: Optional[date] = None,
    has_active_link: bool = False,
    has_active_adoption: bool = False,
    legacy_master_task_count: int = 0,
    imported_instance_task_count: int = 0,
    unresolved_quarantine_count: int = 0,
    pending_operation_count: int = 0,
    exception_dates: Sequence[str] = (),
    live_calendar_access_role: Optional[str] = None,
) -> SeriesAdoptionEligibility:
    """Return a complete, stable, structured conservative preflight result."""
    raw = _raw_payload(series)
    reasons: list[AdoptionBlockedReason] = []

    def block(code: str, message: str) -> None:
        if not any(item.code == code for item in reasons):
            reasons.append(AdoptionBlockedReason(code, message))

    if series.provider != "google":
        block("provider", "Поддерживается только Google Calendar.")
    if series.is_cancelled or series.remote_status != "confirmed":
        block("cancelled", "Отменённую или удалённую серию импортировать нельзя.")
    if str(series.event_type or raw.get("eventType") or "default") != "default":
        block("event_type", "Этот тип события Google не поддерживается.")
    if bool(series.locked or raw.get("locked")):
        block("locked", "Заблокированную серию Google изменять нельзя.")
    access = str(live_calendar_access_role or series.calendar_access_role or "")
    if access not in ("writer", "owner"):
        block("access_role", "Нужен доступ writer или owner к календарю.")
    organizer_self = bool(
        series.organizer_self
        if series.organizer_self is not None
        else (raw.get("organizer") or {}).get("self")
    )
    if not organizer_self:
        block("organizer", "Импорт доступен только организатору серии.")
    if not series.is_supported or series.parsed_rule is None:
        block(
            "unsupported_recurrence",
            series.unsupported_reason or "Правило повторения не поддерживается без потерь.",
        )
    rrules = [
        line for line in series.recurrence_lines
        if str(line).upper().startswith("RRULE:")
    ]
    if len(rrules) != 1:
        block("rrule_count", "Требуется ровно одно поддерживаемое RRULE.")
    if any(
        str(line).upper().startswith(("EXDATE:", "RDATE:", "EXRULE:"))
        for line in series.recurrence_lines
    ):
        block("recurrence_exclusions", "EXDATE, RDATE и EXRULE не импортируются.")
    schedule = series.schedule()
    if schedule is None:
        block("schedule", "Расписание Google не удалось прочитать без потерь.")
    elif schedule.all_day:
        if series.duration_minutes not in (None, 24 * 60):
            block("all_day_duration", "All-day серия должна длиться ровно один день.")
    else:
        if not series.timezone_name or not is_valid_timezone(series.timezone_name):
            block("timezone", "Timed-серия должна содержать корректную IANA timezone.")
        if not series.duration_minutes or series.duration_minutes <= 0:
            block("duration", "Длительность timed-серии должна быть положительной.")
    if not _has_future_occurrence(series, today or date.today()):
        block("no_future_occurrence", "У серии не осталось будущих экземпляров.")
    if any(key in series.private_properties for key in PLANNER_OWNERSHIP_PROPERTIES):
        block("planner_owned", "Серия уже содержит маркеры владения Planner.")
    if series.planner_owned:
        block("planner_owned", "Серия уже принадлежит Planner.")
    if has_active_link:
        block("active_link", "Удалённый master уже связан с локальной серией.")
    if has_active_adoption:
        block("active_adoption", "Для master уже существует активный план импорта.")
    if legacy_master_task_count:
        block("legacy_master_task", "Master уже использован устаревшей Task-строкой.")
    if imported_instance_task_count:
        block(
            "imported_instances",
            "Экземпляры этой серии уже импортированы как обычные задачи.",
        )
    if unresolved_quarantine_count:
        block("quarantine", "Для master существует неразрешённый quarantine.")
    all_exception_dates = tuple(
        dict.fromkeys(
            [str(item) for item in exception_dates if str(item)]
            + [str(item) for item in series.exception_dates if str(item)]
        )
    )
    if series.exception_count or all_exception_dates:
        block("exceptions", "Серии с изменёнными или отменёнными экземплярами запрещены.")
    if pending_operation_count:
        block("pending_operation", "Master участвует в другой операции Planner.")
    if raw.get("attendees"):
        block("attendees", "Серии с участниками не импортируются на этом этапе.")
    if raw.get("conferenceData") or raw.get("hangoutLink"):
        block("conference", "Серии с конференцией не импортируются на этом этапе.")
    if raw.get("privateCopy"):
        block("private_copy", "Недоступную частную копию серии импортировать нельзя.")

    return SeriesAdoptionEligibility(
        remote_event_id=series.remote_event_id,
        eligible=not reasons,
        blocked_reasons=tuple(reasons),
        affected_exception_dates=all_exception_dates,
        organizer_self=organizer_self,
        calendar_access_role=access,
        recurrence_summary=series.recurrence_summary(),
        remote_only_fields=_remote_only_fields(raw),
    )


__all__ = [
    "ACTIVE_ADOPTION_STATES",
    "ADOPTED_EXTERNAL_ORIGIN",
    "AdoptionBlockedReason",
    "CalendarSeriesAdoption",
    "EXTERNAL_EXISTING_ID_STRATEGY",
    "PLANNER_ADOPTION_ID_PROPERTY",
    "PLANNER_ADOPTION_VERSION",
    "PLANNER_ADOPTION_VERSION_PROPERTY",
    "PLANNER_CREATED_ORIGIN",
    "PLANNER_DETERMINISTIC_ID_STRATEGY",
    "PLANNER_LINK_ORIGIN_PROPERTY",
    "PLANNER_OWNERSHIP_PROPERTIES",
    "PROCESSABLE_ADOPTION_STATES",
    "SeriesAdoptionEligibility",
    "SeriesAdoptionState",
    "evaluate_series_adoption",
    "planner_adoption_private_properties",
]
