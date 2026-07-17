"""Pure Phase 3.2B3A policy for explicit series conflict resolution.

No repository, SQLite, Qt, Google client, or network access belongs here.
Everything operates on plain values: the local ``TaskSeries``, the stored
``SeriesCalendarLink``, and the durable remote-master snapshot ``dict`` that
pull/push persisted at conflict time.  The module decides *whether* an action
is allowed and *what* it would produce; services own persistence.
"""
from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from enum import Enum
from typing import Any, Iterable, Mapping, Optional, Tuple
from zoneinfo import ZoneInfo

from planner_desktop.domain.google_recurrence import parse_google_recurrence
from planner_desktop.domain.recurrence import (
    RecurrenceRule,
    SeriesSchedule,
    TaskSeries,
    is_valid_timezone,
)
from planner_desktop.domain.series_calendar_link import (
    PLANNER_PAYLOAD_HASH_PROPERTY,
    PLANNER_SERIES_UID_PROPERTY,
    PendingSeriesSyncOp,
    SeriesCalendarLink,
    SeriesLinkStatus,
    SeriesSyncOpKind,
    deterministic_remote_event_id,
)


class ConflictResolutionKind(str, Enum):
    KEEP_PLANNER = "keep_planner"
    USE_GOOGLE = "use_google"
    DISCONNECT = "disconnect"
    # Remote-deleted recovery kinds share the audit table.
    KEEP_LOCAL = "keep_local"
    RECREATE = "recreate"
    DELETE_LOCAL = "delete_local"


class RemoteDeletedRecoveryKind(str, Enum):
    KEEP_LOCAL = "keep_local"
    RECREATE = "recreate"
    DELETE_LOCAL = "delete_local"


class ConflictResolutionStatus(str, Enum):
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"
    SUPERSEDED = "superseded"


#: Stable separator for generation-specific deterministic remote ids.  The
#: value is part of the persistent id contract and must never change.
LINK_GENERATION_SEPARATOR = "::planner-link-generation::"

KEEP_PLANNER_ACTION_RU = "Оставить версию Planner"
USE_GOOGLE_ACTION_RU = "Использовать версию Google"
DISCONNECT_ACTION_RU = "Отключить и сохранить обе"
KEEP_LOCAL_ACTION_RU = "Оставить локальной"
RECREATE_ACTION_RU = "Создать серию в Google заново"
DELETE_LOCAL_ACTION_RU = "Удалить локальную серию"

RESOLUTION_KIND_TEXT_RU = {
    ConflictResolutionKind.KEEP_PLANNER: KEEP_PLANNER_ACTION_RU,
    ConflictResolutionKind.USE_GOOGLE: USE_GOOGLE_ACTION_RU,
    ConflictResolutionKind.DISCONNECT: DISCONNECT_ACTION_RU,
    ConflictResolutionKind.KEEP_LOCAL: KEEP_LOCAL_ACTION_RU,
    ConflictResolutionKind.RECREATE: RECREATE_ACTION_RU,
    ConflictResolutionKind.DELETE_LOCAL: DELETE_LOCAL_ACTION_RU,
}

RESOLUTION_STATUS_TEXT_RU = {
    ConflictResolutionStatus.PENDING: "Ожидает ручной синхронизации",
    ConflictResolutionStatus.COMPLETED: "Завершено",
    ConflictResolutionStatus.FAILED: "Ошибка",
    ConflictResolutionStatus.SUPERSEDED: "Устарело (мастер изменился снова)",
}


def readable_resolution_kind(kind: str | None) -> str:
    try:
        return RESOLUTION_KIND_TEXT_RU[ConflictResolutionKind(str(kind))]
    except (KeyError, ValueError):
        return str(kind or "")


def readable_resolution_status(status: str | None) -> str:
    try:
        return RESOLUTION_STATUS_TEXT_RU[ConflictResolutionStatus(str(status))]
    except (KeyError, ValueError):
        return str(status or "")


def deterministic_remote_event_id_for_generation(
    series_uid: str, generation: int
) -> str:
    """Stable Google-valid id for one (series, link generation) pair.

    Generation 0 keeps the exact Phase 3.2B2 formula so existing links stay
    addressable.  Generation N > 0 hashes ``series_uid + separator + N`` so
    the id never collides with generation 0, is identical across retries and
    restarts, and uses no randomness, time, or Python ``hash()``.
    """
    normalized = str(series_uid).strip()
    if not normalized:
        raise ValueError("series_uid is required")
    generation = int(generation)
    if generation < 0:
        raise ValueError("link generation must be >= 0")
    if generation == 0:
        return deterministic_remote_event_id(normalized)
    seed = f"{normalized}{LINK_GENERATION_SEPARATOR}{generation}"
    digest = hashlib.sha256(seed.encode("utf-8")).digest()
    encoded = base64.b32hexencode(digest).decode("ascii").rstrip("=").lower()
    return f"plr{encoded}"


@dataclass
class SeriesConflictResolution:
    """One durable audit row of series_conflict_resolutions."""

    series_uid: str
    link_id: int
    resolution_kind: str
    status: str = ConflictResolutionStatus.PENDING.value
    local_revision_before: int = 0
    local_revision_after: Optional[int] = None
    remote_etag_before: Optional[str] = None
    remote_etag_after: Optional[str] = None
    remote_payload_hash: Optional[str] = None
    acknowledged_remote_etag: Optional[str] = None
    created_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    error: Optional[str] = None
    id: Optional[int] = None

    @property
    def is_pending(self) -> bool:
        return self.status == ConflictResolutionStatus.PENDING.value

    @property
    def kind_text(self) -> str:
        return readable_resolution_kind(self.resolution_kind)

    @property
    def status_text(self) -> str:
        return readable_resolution_status(self.status)


@dataclass(frozen=True)
class ConflictResolutionIssue:
    code: str
    message: str


@dataclass(frozen=True)
class ConflictResolutionValidation:
    series_uid: str
    kind: ConflictResolutionKind
    issues: Tuple[ConflictResolutionIssue, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.issues

    @property
    def errors(self) -> Tuple[str, ...]:
        return tuple(item.message for item in self.issues)


@dataclass(frozen=True)
class AcceptedRemoteSeriesState:
    """Lossless local TaskSeries state derived from the remote snapshot."""

    title: str
    notes: str
    schedule: SeriesSchedule
    rule: RecurrenceRule
    remote_etag: Optional[str] = None
    remote_updated_at: Optional[datetime] = None
    remote_payload_hash: Optional[str] = None


@dataclass(frozen=True)
class ConflictResolutionProposal:
    series_uid: str
    kind: ConflictResolutionKind
    validation: ConflictResolutionValidation
    acknowledged_remote_etag: Optional[str] = None
    desired_revision: Optional[int] = None
    desired_payload_hash: Optional[str] = None
    accepted_state: Optional[AcceptedRemoteSeriesState] = None

    @property
    def ok(self) -> bool:
        return self.validation.ok


@dataclass(frozen=True)
class LinkGenerationProposal:
    series_uid: str
    generation: int
    remote_event_id: str


def next_link_generation_proposal(
    series_uid: str, existing_generations: Iterable[int]
) -> LinkGenerationProposal:
    """Exactly max(existing) + 1; identical inputs always propose the same id."""
    highest = max((int(item) for item in existing_generations), default=0)
    generation = highest + 1
    return LinkGenerationProposal(
        series_uid=series_uid,
        generation=generation,
        remote_event_id=deterministic_remote_event_id_for_generation(
            series_uid, generation
        ),
    )


# ---- remote snapshot readers -------------------------------------------------
#
# The snapshot is deterministic JSON persisted on the link at conflict time:
# {"id","etag","summary","description","status","updated_at",
#  "start": {"date"|"dateTime","timeZone"}, "end": {...},
#  "recurrence": [...], "private": {planner markers}}.


def snapshot_private_properties(snapshot: Mapping[str, Any]) -> dict[str, str]:
    private = snapshot.get("private") or {}
    if not isinstance(private, Mapping):
        return {}
    return {str(key): str(value) for key, value in private.items()}


def snapshot_series_uid_marker(snapshot: Mapping[str, Any]) -> Optional[str]:
    return snapshot_private_properties(snapshot).get(PLANNER_SERIES_UID_PROPERTY)


def snapshot_payload_hash_marker(snapshot: Mapping[str, Any]) -> Optional[str]:
    return snapshot_private_properties(snapshot).get(PLANNER_PAYLOAD_HASH_PROPERTY)


def snapshot_recurrence_lines(snapshot: Mapping[str, Any]) -> Tuple[str, ...]:
    lines = snapshot.get("recurrence") or ()
    return tuple(str(item) for item in lines)


def snapshot_is_all_day(snapshot: Mapping[str, Any]) -> Optional[bool]:
    start = snapshot.get("start") or {}
    end = snapshot.get("end") or {}
    if not isinstance(start, Mapping) or not isinstance(end, Mapping):
        return None
    start_date, start_datetime = start.get("date"), start.get("dateTime")
    end_date, end_datetime = end.get("date"), end.get("dateTime")
    if start_date and end_date and not start_datetime and not end_datetime:
        return True
    if start_datetime and end_datetime and not start_date and not end_date:
        return False
    return None  # mixed or missing form


def _parse_snapshot_datetime(raw: Any) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(str(raw))
    except (TypeError, ValueError):
        return None


def _parse_snapshot_date(raw: Any) -> Optional[date]:
    try:
        return date.fromisoformat(str(raw))
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class SnapshotScheduleResult:
    schedule: Optional[SeriesSchedule] = None
    issues: Tuple[ConflictResolutionIssue, ...] = ()


def snapshot_schedule(snapshot: Mapping[str, Any]) -> SnapshotScheduleResult:
    """Lossless SeriesSchedule from the snapshot, or explicit issues."""
    issues: list[ConflictResolutionIssue] = []
    form = snapshot_is_all_day(snapshot)
    if form is None:
        return SnapshotScheduleResult(issues=(ConflictResolutionIssue(
            "invalid_start_form",
            "У мастера Google недопустимая форма start/end "
            "(смешаны date и dateTime или значения отсутствуют).",
        ),))
    start = snapshot.get("start") or {}
    end = snapshot.get("end") or {}
    if form:
        start_day = _parse_snapshot_date(start.get("date"))
        end_day = _parse_snapshot_date(end.get("date"))
        if start_day is None or end_day is None:
            issues.append(ConflictResolutionIssue(
                "invalid_all_day_dates",
                "Даты all-day мастера Google не разбираются.",
            ))
            return SnapshotScheduleResult(issues=tuple(issues))
        # Planner all-day занимает один день; end.date у Google эксклюзивен.
        if end_day - start_day != timedelta(days=1):
            issues.append(ConflictResolutionIssue(
                "multi_day_all_day",
                "Многодневные all-day серии нельзя перенести в Planner без потерь.",
            ))
            return SnapshotScheduleResult(issues=tuple(issues))
        timezone_name = str(start.get("timeZone") or "UTC")
        if not is_valid_timezone(timezone_name):
            timezone_name = "UTC"
        return SnapshotScheduleResult(schedule=SeriesSchedule(
            start_date=start_day,
            all_day=True,
            timezone_name=timezone_name,
        ))

    timezone_name = str(start.get("timeZone") or end.get("timeZone") or "")
    if not is_valid_timezone(timezone_name):
        issues.append(ConflictResolutionIssue(
            "invalid_timezone",
            "У timed-мастера Google нет действительного IANA timezone.",
        ))
        return SnapshotScheduleResult(issues=tuple(issues))
    start_dt = _parse_snapshot_datetime(start.get("dateTime"))
    end_dt = _parse_snapshot_datetime(end.get("dateTime"))
    if start_dt is None or end_dt is None:
        issues.append(ConflictResolutionIssue(
            "invalid_timed_bounds",
            "Время начала/окончания мастера Google не разбирается.",
        ))
        return SnapshotScheduleResult(issues=tuple(issues))
    zone = ZoneInfo(timezone_name)
    if start_dt.tzinfo is not None:
        start_dt = start_dt.astimezone(zone)
    if end_dt.tzinfo is not None:
        end_dt = end_dt.astimezone(zone)
    duration = int((end_dt - start_dt).total_seconds() // 60)
    if duration <= 0:
        issues.append(ConflictResolutionIssue(
            "invalid_duration",
            "Длительность мастера Google должна быть больше нуля.",
        ))
        return SnapshotScheduleResult(issues=tuple(issues))
    return SnapshotScheduleResult(schedule=SeriesSchedule(
        start_date=start_dt.date(),
        all_day=False,
        local_time=time(start_dt.hour, start_dt.minute),
        duration_minutes=duration,
        timezone_name=timezone_name,
    ))


# ---- pure validation ----------------------------------------------------------


def _series_issues(series: Optional[TaskSeries]) -> list[ConflictResolutionIssue]:
    if series is None or series.is_deleted or not series.active:
        return [ConflictResolutionIssue(
            "series_inactive", "Локальная серия не найдена или неактивна."
        )]
    return []


def validate_keep_planner(
    *,
    series: Optional[TaskSeries],
    link: Optional[SeriesCalendarLink],
    snapshot: Optional[Mapping[str, Any]],
    acknowledged_remote_etag: Optional[str],
    pending_op: Optional[PendingSeriesSyncOp] = None,
) -> ConflictResolutionValidation:
    series_uid = series.uid if series is not None else (
        link.series_uid if link is not None else ""
    )
    issues = _series_issues(series)
    if link is None or link.link_status is not SeriesLinkStatus.CONFLICT:
        issues.append(ConflictResolutionIssue(
            "not_in_conflict", "Связь серии не находится в состоянии конфликта."
        ))
    if snapshot is None:
        issues.append(ConflictResolutionIssue(
            "missing_snapshot",
            "Нет сохранённого снимка мастера Google; выполните ручную "
            "синхронизацию, чтобы обновить конфликт.",
        ))
    else:
        marker = snapshot_series_uid_marker(snapshot)
        if not marker:
            issues.append(ConflictResolutionIssue(
                "foreign_master",
                "Мастер Google не содержит маркеров Planner; перезапись "
                "чужого события запрещена.",
            ))
        elif series is not None and marker != series.uid:
            issues.append(ConflictResolutionIssue(
                "series_uid_mismatch",
                "Маркер серии в мастере Google принадлежит другой серии; "
                "перезапись запрещена.",
            ))
    if not acknowledged_remote_etag:
        issues.append(ConflictResolutionIssue(
            "missing_acknowledged_etag",
            "Не зафиксирован etag конфликтной версии Google; выполните "
            "ручную синхронизацию.",
        ))
    if pending_op is not None and pending_op.op in (
        SeriesSyncOpKind.DELETE, SeriesSyncOpKind.CREATE
    ):
        issues.append(ConflictResolutionIssue(
            "competing_operation",
            "Для серии уже ожидает конкурирующая операция "
            f"{pending_op.op.value}; сначала завершите или отмените её.",
        ))
    return ConflictResolutionValidation(
        series_uid, ConflictResolutionKind.KEEP_PLANNER, tuple(issues)
    )


def evaluate_use_google(
    *,
    series: Optional[TaskSeries],
    link: Optional[SeriesCalendarLink],
    snapshot: Optional[Mapping[str, Any]],
) -> tuple[ConflictResolutionValidation, Optional[AcceptedRemoteSeriesState]]:
    """Allow acceptance only when the remote maps losslessly to Planner."""
    series_uid = series.uid if series is not None else (
        link.series_uid if link is not None else ""
    )
    issues = _series_issues(series)
    if link is None or link.link_status is not SeriesLinkStatus.CONFLICT:
        issues.append(ConflictResolutionIssue(
            "not_in_conflict", "Связь серии не находится в состоянии конфликта."
        ))
    if snapshot is None:
        issues.append(ConflictResolutionIssue(
            "missing_snapshot",
            "Нет сохранённого снимка мастера Google; выполните ручную "
            "синхронизацию, чтобы обновить конфликт.",
        ))
        return (
            ConflictResolutionValidation(
                series_uid, ConflictResolutionKind.USE_GOOGLE, tuple(issues)
            ),
            None,
        )
    marker = snapshot_series_uid_marker(snapshot)
    if series is not None and marker != series.uid:
        issues.append(ConflictResolutionIssue(
            "series_uid_mismatch",
            "Мастер Google не принадлежит этой серии Planner.",
        ))

    schedule_result = snapshot_schedule(snapshot)
    issues.extend(schedule_result.issues)
    schedule = schedule_result.schedule

    lines = snapshot_recurrence_lines(snapshot)
    parsed = parse_google_recurrence(lines, schedule=schedule)
    if not parsed.supported or parsed.planner_rule is None:
        reason = parsed.readable_reason or "Правило повторения не поддерживается."
        issues.append(ConflictResolutionIssue("unsupported_recurrence", reason))
    # EXDATE/RDATE являются валидным транспортом, но B3A не умеет применять
    # исключения; молча отбросить их нельзя.
    if parsed.exdates or parsed.rdates:
        issues.append(ConflictResolutionIssue(
            "unsupported_exceptions",
            "Мастер Google содержит EXDATE/RDATE; перенос исключений "
            "появится в Phase 3.2B3B.",
        ))

    if issues or schedule is None:
        return (
            ConflictResolutionValidation(
                series_uid, ConflictResolutionKind.USE_GOOGLE, tuple(issues)
            ),
            None,
        )

    updated_at = _parse_snapshot_datetime(snapshot.get("updated_at"))
    accepted = AcceptedRemoteSeriesState(
        title=str(snapshot.get("summary") or "").strip() or "(без названия)",
        notes=str(snapshot.get("description") or ""),
        schedule=schedule,
        rule=parsed.planner_rule,
        remote_etag=(str(snapshot["etag"]) if snapshot.get("etag") else None),
        remote_updated_at=updated_at,
        remote_payload_hash=snapshot_payload_hash_marker(snapshot),
    )
    return (
        ConflictResolutionValidation(
            series_uid, ConflictResolutionKind.USE_GOOGLE, ()
        ),
        accepted,
    )


def validate_disconnect(
    *, link: Optional[SeriesCalendarLink]
) -> ConflictResolutionValidation:
    series_uid = link.series_uid if link is not None else ""
    issues: list[ConflictResolutionIssue] = []
    if link is None or link.link_status not in (
        SeriesLinkStatus.CONFLICT, SeriesLinkStatus.REMOTE_DELETED
    ):
        issues.append(ConflictResolutionIssue(
            "not_disconnectable",
            "Отключить можно только связь в конфликте или после удаления "
            "мастера в Google.",
        ))
    return ConflictResolutionValidation(
        series_uid, ConflictResolutionKind.DISCONNECT, tuple(issues)
    )


def validate_remote_deleted_recovery(
    *,
    kind: RemoteDeletedRecoveryKind,
    series: Optional[TaskSeries],
    link: Optional[SeriesCalendarLink],
) -> ConflictResolutionValidation:
    series_uid = series.uid if series is not None else (
        link.series_uid if link is not None else ""
    )
    issues: list[ConflictResolutionIssue] = []
    if link is None or link.link_status is not SeriesLinkStatus.REMOTE_DELETED:
        issues.append(ConflictResolutionIssue(
            "not_remote_deleted",
            "Связь серии не находится в состоянии «удалена в Google».",
        ))
    if kind is RemoteDeletedRecoveryKind.RECREATE:
        issues.extend(_series_issues(series))
    elif kind is RemoteDeletedRecoveryKind.DELETE_LOCAL:
        if series is None or series.is_deleted:
            issues.append(ConflictResolutionIssue(
                "series_missing", "Локальная серия уже удалена."
            ))
    return ConflictResolutionValidation(
        series_uid, ConflictResolutionKind(kind.value), tuple(issues)
    )


__all__ = [
    "AcceptedRemoteSeriesState",
    "ConflictResolutionIssue",
    "ConflictResolutionKind",
    "ConflictResolutionProposal",
    "ConflictResolutionStatus",
    "ConflictResolutionValidation",
    "DELETE_LOCAL_ACTION_RU",
    "DISCONNECT_ACTION_RU",
    "KEEP_LOCAL_ACTION_RU",
    "KEEP_PLANNER_ACTION_RU",
    "LINK_GENERATION_SEPARATOR",
    "LinkGenerationProposal",
    "RECREATE_ACTION_RU",
    "RemoteDeletedRecoveryKind",
    "SeriesConflictResolution",
    "SnapshotScheduleResult",
    "USE_GOOGLE_ACTION_RU",
    "deterministic_remote_event_id_for_generation",
    "evaluate_use_google",
    "next_link_generation_proposal",
    "readable_resolution_kind",
    "readable_resolution_status",
    "snapshot_is_all_day",
    "snapshot_payload_hash_marker",
    "snapshot_private_properties",
    "snapshot_recurrence_lines",
    "snapshot_schedule",
    "snapshot_series_uid_marker",
    "validate_disconnect",
    "validate_keep_planner",
    "validate_remote_deleted_recovery",
]
