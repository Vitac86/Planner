"""Pure planning for the remote "this and future" split (Phase 3.2B3C1).

The module computes everything a durable split plan needs — exact
occurrence partition, trimmed source rule, successor definition, canonical
payloads and hashes — without any Qt, SQLite, Google client or network
import.  Counting always uses the deterministic recurrence generator;
"one second before the target" calendar arithmetic is never used.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from enum import Enum
from typing import Any, Mapping, Optional, Sequence, Tuple

from planner_desktop.domain.google_occurrence import (
    GoogleOccurrenceIdentity,
    local_occurrence_to_google_original_start,
)
from planner_desktop.domain.google_recurrence import recurrence_round_trip_support
from planner_desktop.domain.recurrence import (
    MAX_OCCURRENCE_COUNT,
    RecurrenceEndMode,
    RecurrenceRule,
    SeriesSchedule,
    TaskSeries,
    generate_occurrences,
    is_valid_timezone,
    validate_rule,
)
from planner_desktop.domain.series_calendar_link import (
    canonical_master_payload_fingerprint,
    deterministic_remote_event_id,
    planner_private_properties,
)
from planner_desktop.sync.calendar_series_mapper import (
    master_event_to_owned_payload,
    series_to_master_event,
)

PLANNER_SPLIT_SCHEMA_VERSION = "1"
PLANNER_SPLIT_SCHEMA_VERSION_PROPERTY = "planner_split_schema_version"
PLANNER_SPLIT_SOURCE_SERIES_UID_PROPERTY = "planner_split_source_series_uid"
PLANNER_SPLIT_TARGET_OCCURRENCE_KEY_PROPERTY = (
    "planner_split_target_occurrence_key"
)
PLANNER_SPLIT_PREDECESSOR_EVENT_ID_PROPERTY = (
    "planner_split_predecessor_event_id"
)


class RemoteSeriesSplitStatus(str, Enum):
    PENDING = "pending"
    SOURCE_TRIMMED = "source_trimmed"
    SUCCESSOR_CREATED = "successor_created"
    LOCAL_FINALIZE_PENDING = "local_finalize_pending"
    COMPLETED = "completed"
    CONFLICT = "conflict"
    ROLLBACK_PENDING = "rollback_pending"
    SUCCESSOR_REMOVED_FOR_ROLLBACK = "successor_removed_for_rollback"
    ROLLED_BACK = "rolled_back"
    TERMINAL_ERROR = "terminal_error"


#: States in which a plan still owns its source series exclusively.
ACTIVE_SPLIT_STATES: Tuple[RemoteSeriesSplitStatus, ...] = (
    RemoteSeriesSplitStatus.PENDING,
    RemoteSeriesSplitStatus.SOURCE_TRIMMED,
    RemoteSeriesSplitStatus.SUCCESSOR_CREATED,
    RemoteSeriesSplitStatus.LOCAL_FINALIZE_PENDING,
    RemoteSeriesSplitStatus.CONFLICT,
    RemoteSeriesSplitStatus.ROLLBACK_PENDING,
    RemoteSeriesSplitStatus.SUCCESSOR_REMOVED_FOR_ROLLBACK,
)

#: States that still require remote work during manual sync.
PROCESSABLE_SPLIT_STATES: Tuple[RemoteSeriesSplitStatus, ...] = (
    RemoteSeriesSplitStatus.PENDING,
    RemoteSeriesSplitStatus.SOURCE_TRIMMED,
    RemoteSeriesSplitStatus.SUCCESSOR_CREATED,
    RemoteSeriesSplitStatus.LOCAL_FINALIZE_PENDING,
    RemoteSeriesSplitStatus.ROLLBACK_PENDING,
    RemoteSeriesSplitStatus.SUCCESSOR_REMOVED_FOR_ROLLBACK,
)


_STATUS_TEXT_RU = {
    RemoteSeriesSplitStatus.PENDING: "Ожидает разделения",
    RemoteSeriesSplitStatus.SOURCE_TRIMMED: "Исходная серия сокращена",
    RemoteSeriesSplitStatus.SUCCESSOR_CREATED: "Новая серия создана",
    RemoteSeriesSplitStatus.LOCAL_FINALIZE_PENDING: "Завершается локально",
    RemoteSeriesSplitStatus.COMPLETED: "Завершено",
    RemoteSeriesSplitStatus.CONFLICT: "Конфликт",
    RemoteSeriesSplitStatus.ROLLBACK_PENDING: "Требуется откат",
    RemoteSeriesSplitStatus.SUCCESSOR_REMOVED_FOR_ROLLBACK: "Требуется откат",
    RemoteSeriesSplitStatus.ROLLED_BACK: "Откат завершён",
    RemoteSeriesSplitStatus.TERMINAL_ERROR: "Ошибка",
}


def readable_split_status(status: RemoteSeriesSplitStatus | str | None) -> str:
    if status is None:
        return "Нет плана разделения"
    try:
        key = (
            status
            if isinstance(status, RemoteSeriesSplitStatus)
            else RemoteSeriesSplitStatus(status)
        )
    except ValueError:
        return "Неизвестное состояние"
    return _STATUS_TEXT_RU[key]


class RemoteSeriesSplitRecoveryKind(str, Enum):
    """How an already-performed remote step was reconciled on retry."""

    NONE = "none"
    SOURCE_TRIM_RECONCILED = "source_trim_reconciled"
    SUCCESSOR_INSERT_RECONCILED = "successor_insert_reconciled"
    LOCAL_FINALIZE_RETRIED = "local_finalize_retried"
    ROLLBACK_RESTORE_RECONCILED = "rollback_restore_reconciled"
    ROLLBACK_DELETE_RECONCILED = "rollback_delete_reconciled"


@dataclass(frozen=True)
class RemoteSeriesSplitIssue:
    code: str
    message: str


@dataclass(frozen=True)
class RemoteSeriesSplitValidation:
    series_uid: str
    target_occurrence_key: str = ""
    issues: Tuple[RemoteSeriesSplitIssue, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.issues

    @property
    def errors(self) -> Tuple[str, ...]:
        return tuple(item.message for item in self.issues)

    @property
    def codes(self) -> Tuple[str, ...]:
        return tuple(item.code for item in self.issues)


@dataclass(frozen=True)
class RemoteSeriesSplitProposal:
    """Requested successor definition, relative to the target slot.

    ``None`` fields inherit the source series value.  The successor kind
    always equals the source kind (timed -> timed, all-day -> all-day);
    a kind conversion is rejected during planning.
    """

    title: Optional[str] = None
    notes: Optional[str] = None
    priority: Optional[int] = None
    #: Timed successor wall-clock start time (ignored for all-day).
    local_time: Optional[Any] = None
    duration_minutes: Optional[int] = None
    timezone_name: Optional[str] = None
    #: All-day successor start date; defaults to the target slot date.
    start_date: Optional[date] = None
    #: Replacement recurrence rule.  Its end condition is recomputed from
    #: the source end semantics unless ``keep_rule_end`` is True.
    rule: Optional[RecurrenceRule] = None
    keep_rule_end: bool = False


@dataclass(frozen=True)
class FutureExceptionSummary:
    """Exact local/remote state at or after the target slot.

    The caller (use-case layer) gathers these; the pure planner only
    turns non-empty entries into blocking validation issues.  Nothing is
    ever deleted, moved or silently reset.
    """

    local_exception_dates: Tuple[str, ...] = ()
    local_tombstone_dates: Tuple[str, ...] = ()
    pending_occurrence_op_dates: Tuple[str, ...] = ()
    terminal_occurrence_op_dates: Tuple[str, ...] = ()
    remote_exception_dates: Tuple[str, ...] = ()
    remote_cancelled_dates: Tuple[str, ...] = ()
    unresolved_quarantine_dates: Tuple[str, ...] = ()
    exdate_rdate_lines: Tuple[str, ...] = ()

    @property
    def clean(self) -> bool:
        return not any((
            self.local_exception_dates,
            self.local_tombstone_dates,
            self.pending_occurrence_op_dates,
            self.terminal_occurrence_op_dates,
            self.remote_exception_dates,
            self.remote_cancelled_dates,
            self.unresolved_quarantine_dates,
            self.exdate_rdate_lines,
        ))


@dataclass(frozen=True)
class RemoteSeriesSplitPlan:
    """Complete deterministic outcome of one split computation."""

    source_series_uid: str
    target_occurrence_key: str
    target_original_start: GoogleOccurrenceIdentity
    occurrences_before_target: int
    trimmed_source_series: TaskSeries
    successor_series: TaskSeries
    reserved_successor_series_uid: str
    successor_remote_event_id: str
    source_before_payload: Mapping[str, Any]
    source_before_hash: str
    trimmed_source_payload: Mapping[str, Any]
    trimmed_source_hash: str
    successor_payload: Mapping[str, Any]
    successor_hash: str


@dataclass
class RemoteSeriesSplitResult:
    """Per-plan engine outcome for one manual sync cycle."""

    plan_id: int
    series_uid: str
    status: RemoteSeriesSplitStatus
    started: bool = False
    source_trimmed: bool = False
    successor_created: bool = False
    finalized: bool = False
    conflict: bool = False
    rollback_completed: bool = False
    terminal: bool = False
    recovery: RemoteSeriesSplitRecoveryKind = RemoteSeriesSplitRecoveryKind.NONE
    error: str = ""


@dataclass
class RemoteSeriesSplitPlanRecord:
    """Durable ``calendar_series_remote_splits`` row (schema v11)."""

    source_series_uid: str
    source_link_id: int
    source_link_generation: int
    source_remote_event_id: str
    target_occurrence_key: str
    target_original_start_kind: str
    target_original_start_value: str
    source_local_revision: int
    source_remote_etag_base: str
    source_original_snapshot_json: str
    source_original_payload_hash: str
    source_trimmed_payload_json: str
    source_trimmed_payload_hash: str
    reserved_successor_series_uid: str
    successor_remote_event_id: str
    successor_series_snapshot_json: str
    successor_payload_json: str
    successor_payload_hash: str
    target_original_start_timezone: Optional[str] = None
    state: RemoteSeriesSplitStatus = RemoteSeriesSplitStatus.PENDING
    source_trimmed_remote_etag: Optional[str] = None
    successor_remote_etag: Optional[str] = None
    attempts: int = 0
    last_error: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    id: Optional[int] = None

    @property
    def is_active(self) -> bool:
        return self.state in ACTIVE_SPLIT_STATES

    @property
    def status_text(self) -> str:
        return readable_split_status(self.state)

    @property
    def successor_payload(self) -> dict[str, Any]:
        return _loads(self.successor_payload_json)

    @property
    def trimmed_source_payload(self) -> dict[str, Any]:
        return _loads(self.source_trimmed_payload_json)

    @property
    def source_original_snapshot(self) -> dict[str, Any]:
        return _loads(self.source_original_snapshot_json)

    @property
    def successor_series_snapshot(self) -> dict[str, Any]:
        return _loads(self.successor_series_snapshot_json)


def _loads(raw: Optional[str]) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def canonical_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


# ---- deterministic slot counting -------------------------------------------

#: Generation page size when paging occurrence windows by date.
_COUNT_PAGE_LIMIT = 366


def _slot_date_from_key(key: str) -> Optional[date]:
    try:
        return date.fromisoformat(str(key)[:10])
    except (TypeError, ValueError):
        return None


def count_occurrences_before(
    schedule: SeriesSchedule, rule: RecurrenceRule, target_date: date
) -> Optional[int]:
    """Count actual generated slots strictly before ``target_date``.

    Returns ``None`` when the count exceeds ``MAX_OCCURRENCE_COUNT`` and is
    therefore not representable as a Google COUNT.
    """
    if target_date <= schedule.start_date:
        return 0
    total = 0
    cursor = schedule.start_date
    end = target_date - timedelta(days=1)
    while cursor <= end:
        page = generate_occurrences(
            schedule, rule, cursor, end, limit=_COUNT_PAGE_LIMIT
        )
        if not page:
            break
        total += len(page)
        if total > MAX_OCCURRENCE_COUNT:
            return None
        if len(page) < _COUNT_PAGE_LIMIT:
            break
        cursor = page[-1].local_date + timedelta(days=1)
    return total


def is_generated_slot(
    schedule: SeriesSchedule, rule: RecurrenceRule, occurrence_key: str
) -> bool:
    """True only when the key is a real original-slot of the rule."""
    slot = _slot_date_from_key(occurrence_key)
    if slot is None:
        return False
    page = generate_occurrences(schedule, rule, slot, slot, limit=2)
    return any(spec.occurrence_key == occurrence_key for spec in page)


def first_generated_slot_key(
    schedule: SeriesSchedule, rule: RecurrenceRule
) -> Optional[str]:
    horizon = schedule.start_date + timedelta(days=366 * 3)
    page = generate_occurrences(
        schedule, rule, schedule.start_date, horizon, limit=1
    )
    return page[0].occurrence_key if page else None


# ---- payload construction ---------------------------------------------------

def split_provenance_properties(
    source_series_uid: str,
    target_occurrence_key: str,
    predecessor_remote_event_id: str,
) -> dict[str, str]:
    """Successor split provenance markers; no history or tags are exposed."""
    return {
        PLANNER_SPLIT_SCHEMA_VERSION_PROPERTY: PLANNER_SPLIT_SCHEMA_VERSION,
        PLANNER_SPLIT_SOURCE_SERIES_UID_PROPERTY: str(source_series_uid),
        PLANNER_SPLIT_TARGET_OCCURRENCE_KEY_PROPERTY: str(
            target_occurrence_key
        ),
        PLANNER_SPLIT_PREDECESSOR_EVENT_ID_PROPERTY: str(
            predecessor_remote_event_id
        ),
    }


def series_master_payload(series: TaskSeries) -> tuple[dict[str, Any], str]:
    """Canonical owned payload + hash + ownership markers for one series."""
    event = series_to_master_event(series)
    payload = master_event_to_owned_payload(event)
    return payload, canonical_master_payload_fingerprint(payload)


def master_content_fingerprint(payload: Mapping[str, Any]) -> str:
    """Content hash tolerant of provider-side RRULE normalization.

    Identical to ``canonical_master_payload_fingerprint`` except every RRULE
    line is canonicalized first (Google drops ``INTERVAL=1`` and similar),
    so a written master and its returned echo hash equally.  Planner's own
    generated lines are already canonical, therefore plan-stored hashes are
    unchanged by this normalization.
    """
    import hashlib

    from planner_desktop.domain.google_recurrence import canonicalize_rrule_line
    from planner_desktop.domain.series_calendar_link import (
        canonical_master_payload_data,
    )

    data = canonical_master_payload_data(payload)
    data["recurrence"] = [
        canonicalize_rrule_line(line) for line in data["recurrence"]
    ]
    return hashlib.sha256(
        canonical_json(data).encode("utf-8")
    ).hexdigest()


def _with_private_properties(
    payload: Mapping[str, Any], private: Mapping[str, str]
) -> dict[str, Any]:
    result = dict(payload)
    result["extendedProperties"] = {"private": dict(private)}
    return result


# ---- the planner ------------------------------------------------------------

def _issue(code: str, message: str) -> RemoteSeriesSplitIssue:
    return RemoteSeriesSplitIssue(code, message)


def _future_exception_issues(
    summary: FutureExceptionSummary,
) -> list[RemoteSeriesSplitIssue]:
    issues: list[RemoteSeriesSplitIssue] = []

    def _block(code: str, label: str, dates: Sequence[str]) -> None:
        if not dates:
            return
        listed = ", ".join(str(item) for item in dates[:10])
        suffix = "…" if len(dates) > 10 else ""
        issues.append(_issue(
            code,
            f"{label}: {len(dates)} ({listed}{suffix}). "
            "Разделение заблокировано; сначала разрешите их явно — "
            "они не будут удалены или сброшены автоматически.",
        ))

    _block(
        "future_local_exception",
        "Будущие локальные исключения",
        summary.local_exception_dates,
    )
    _block(
        "future_local_tombstone",
        "Будущие локальные удаления экземпляров",
        summary.local_tombstone_dates,
    )
    _block(
        "future_pending_occurrence_op",
        "Будущие незавершённые операции экземпляров",
        summary.pending_occurrence_op_dates,
    )
    _block(
        "future_terminal_occurrence_op",
        "Будущие dead-letter операции экземпляров",
        summary.terminal_occurrence_op_dates,
    )
    _block(
        "future_remote_exception",
        "Будущие синхронизированные исключения Google",
        summary.remote_exception_dates,
    )
    _block(
        "future_remote_cancelled",
        "Будущие отменённые в Google экземпляры",
        summary.remote_cancelled_dates,
    )
    _block(
        "future_quarantine",
        "Неразрешённые карантинные изменения экземпляров",
        summary.unresolved_quarantine_dates,
    )
    _block(
        "unsupported_exdate_rdate",
        "EXDATE/RDATE в мастере Google",
        summary.exdate_rdate_lines,
    )
    return issues


def _successor_end_rule(
    source_rule: RecurrenceRule,
    proposal_rule: Optional[RecurrenceRule],
    occurrences_before: int,
    *,
    keep_rule_end: bool,
) -> tuple[Optional[RecurrenceRule], Optional[RemoteSeriesSplitIssue]]:
    base = proposal_rule if proposal_rule is not None else source_rule
    if proposal_rule is not None and keep_rule_end:
        # An explicitly requested new ending (frequency/rule editing UI)
        # is honoured verbatim.
        return base, None
    if source_rule.end_mode is RecurrenceEndMode.NEVER:
        return replace(
            base,
            end_mode=RecurrenceEndMode.NEVER,
            until_date=None,
            occurrence_count=None,
        ), None
    if source_rule.end_mode is RecurrenceEndMode.COUNT:
        total = int(source_rule.occurrence_count or 0)
        remaining = total - occurrences_before
        if remaining < 1:
            return None, _issue(
                "target_after_last",
                "Целевой экземпляр находится после последнего экземпляра серии.",
            )
        return replace(
            base,
            end_mode=RecurrenceEndMode.COUNT,
            occurrence_count=remaining,
            until_date=None,
        ), None
    # UNTIL: the successor keeps the original lossless UNTIL date.
    return replace(
        base,
        end_mode=RecurrenceEndMode.UNTIL,
        until_date=source_rule.until_date,
        occurrence_count=None,
    ), None


def plan_remote_series_split(
    source: TaskSeries,
    *,
    source_remote_event_id: str,
    target_occurrence_key: str,
    proposal: RemoteSeriesSplitProposal,
    future_exceptions: FutureExceptionSummary,
    today: date,
    reserved_successor_uid: Optional[str] = None,
) -> tuple[Optional[RemoteSeriesSplitPlan], RemoteSeriesSplitValidation]:
    """Compute one deterministic two-master split plan or reject it.

    Pure: the caller supplies the source series, its remote event id, the
    immutable target slot key, the requested successor definition and the
    already-gathered future-exception summary.
    """
    issues: list[RemoteSeriesSplitIssue] = []
    schedule = source.schedule
    rule = source.rule

    target_date = _slot_date_from_key(target_occurrence_key)
    if target_date is None:
        issues.append(_issue(
            "invalid_target", "Не удалось определить целевой слот экземпляра."
        ))
        return None, RemoteSeriesSplitValidation(
            source.uid, target_occurrence_key, tuple(issues)
        )

    if not is_generated_slot(schedule, rule, target_occurrence_key):
        issues.append(_issue(
            "target_not_slot",
            "Целевой экземпляр не является слотом исходного расписания серии.",
        ))
    if target_date < today:
        issues.append(_issue(
            "target_in_past", "Целевой экземпляр находится в прошлом."
        ))
    first_key = first_generated_slot_key(schedule, rule)
    if first_key is not None and first_key == target_occurrence_key:
        issues.append(_issue(
            "target_is_first",
            "Целевой экземпляр — первый в серии: используйте правку всей "
            "серии вместо разделения на два мастера.",
        ))

    round_trip_ok = True
    try:
        round_trip = recurrence_round_trip_support(rule, schedule=schedule)
        round_trip_ok = (
            round_trip.supported and round_trip.planner_rule == rule
        )
    except (TypeError, ValueError):
        round_trip_ok = False
    if not round_trip_ok:
        issues.append(_issue(
            "unsupported_recurrence",
            "Правило серии нельзя без потерь представить в Google RRULE.",
        ))

    issues.extend(_future_exception_issues(future_exceptions))
    if issues:
        return None, RemoteSeriesSplitValidation(
            source.uid, target_occurrence_key, tuple(issues)
        )

    occurrences_before = count_occurrences_before(schedule, rule, target_date)
    if occurrences_before is None:
        issues.append(_issue(
            "count_overflow",
            "Слишком много экземпляров до целевого: сокращение исходной "
            f"серии не представимо как COUNT (максимум {MAX_OCCURRENCE_COUNT}).",
        ))
    elif occurrences_before < 1:
        issues.append(_issue(
            "target_is_first",
            "Перед целевым экземпляром нет ни одного слота: используйте "
            "правку всей серии.",
        ))
    if issues:
        return None, RemoteSeriesSplitValidation(
            source.uid, target_occurrence_key, tuple(issues)
        )

    # ---- trimmed source ----
    trimmed_rule = replace(
        rule,
        end_mode=RecurrenceEndMode.COUNT,
        occurrence_count=int(occurrences_before),
        until_date=None,
    )
    trimmed_source = replace_series_definition(
        source, rule=trimmed_rule, revision=source.revision + 1
    )

    # ---- successor ----
    successor_rule, end_issue = _successor_end_rule(
        rule,
        proposal.rule,
        int(occurrences_before),
        keep_rule_end=proposal.keep_rule_end,
    )
    if end_issue is not None:
        return None, RemoteSeriesSplitValidation(
            source.uid, target_occurrence_key, (end_issue,)
        )

    timezone_name = proposal.timezone_name or schedule.timezone_name
    if not schedule.all_day and not is_valid_timezone(timezone_name):
        return None, RemoteSeriesSplitValidation(
            source.uid,
            target_occurrence_key,
            (_issue(
                "invalid_timezone",
                "Таймзона преемника должна быть валидным IANA timezone.",
            ),),
        )

    successor_start = proposal.start_date or target_date
    successor_schedule = SeriesSchedule(
        start_date=successor_start,
        all_day=schedule.all_day,
        local_time=(
            None if schedule.all_day
            else (proposal.local_time or schedule.local_time)
        ),
        duration_minutes=(
            proposal.duration_minutes
            if proposal.duration_minutes is not None
            else schedule.duration_minutes
        ),
        timezone_name=timezone_name,
    )
    reserved_uid = reserved_successor_uid or str(uuid.uuid4())
    successor = TaskSeries(
        title=(proposal.title if proposal.title is not None else source.title).strip(),
        schedule=successor_schedule,
        rule=successor_rule,
        uid=reserved_uid,
        notes=(proposal.notes if proposal.notes is not None else source.notes).strip(),
        priority=(
            int(proposal.priority)
            if proposal.priority is not None else source.priority
        ),
        tags=tuple(source.tags),
        revision=1,
    )
    if not successor.title:
        issues.append(_issue(
            "empty_title", "Название серии-преемника не может быть пустым."
        ))
    successor_validation = validate_rule(successor.rule, successor.schedule)
    issues.extend(
        _issue("invalid_successor", message)
        for message in successor_validation.errors
    )
    if not issues:
        first_successor_key = first_generated_slot_key(
            successor_schedule, successor_rule
        )
        if first_successor_key is None:
            issues.append(_issue(
                "successor_empty",
                "Серия-преемник не породила бы ни одного экземпляра.",
            ))
        elif _slot_date_from_key(first_successor_key) != successor_start:
            issues.append(_issue(
                "successor_start_not_slot",
                "Дата начала преемника не является первым слотом его "
                "правила; Google и Planner разошлись бы в семантике DTSTART.",
            ))
        try:
            successor_round_trip = recurrence_round_trip_support(
                successor.rule, schedule=successor.schedule
            )
            if not successor_round_trip.supported or (
                successor_round_trip.planner_rule != successor.rule
            ):
                issues.append(_issue(
                    "unsupported_successor_recurrence",
                    "Правило преемника нельзя без потерь представить в "
                    "Google RRULE.",
                ))
        except (TypeError, ValueError) as exc:
            issues.append(_issue(
                "unsupported_successor_recurrence",
                f"Правило преемника не представимо в Google RRULE: {exc}",
            ))
    if issues:
        return None, RemoteSeriesSplitValidation(
            source.uid, target_occurrence_key, tuple(issues)
        )

    target_identity = local_occurrence_to_google_original_start(
        source, target_occurrence_key
    )
    successor_remote_id = deterministic_remote_event_id(reserved_uid)

    source_owned, source_hash = series_master_payload(source)
    source_before_payload = _with_private_properties(
        source_owned,
        planner_private_properties(source.uid, source.revision, source_hash),
    )
    trimmed_owned, trimmed_hash = series_master_payload(trimmed_source)
    trimmed_payload = _with_private_properties(
        trimmed_owned,
        planner_private_properties(
            source.uid, trimmed_source.revision, trimmed_hash
        ),
    )
    successor_owned, successor_hash = series_master_payload(successor)
    successor_private = planner_private_properties(
        reserved_uid, successor.revision, successor_hash
    )
    successor_private.update(split_provenance_properties(
        source.uid, target_occurrence_key, source_remote_event_id
    ))
    successor_payload = _with_private_properties(
        successor_owned, successor_private
    )

    plan = RemoteSeriesSplitPlan(
        source_series_uid=source.uid,
        target_occurrence_key=target_occurrence_key,
        target_original_start=target_identity,
        occurrences_before_target=int(occurrences_before),
        trimmed_source_series=trimmed_source,
        successor_series=successor,
        reserved_successor_series_uid=reserved_uid,
        successor_remote_event_id=successor_remote_id,
        source_before_payload=source_before_payload,
        source_before_hash=source_hash,
        trimmed_source_payload=trimmed_payload,
        trimmed_source_hash=trimmed_hash,
        successor_payload=successor_payload,
        successor_hash=successor_hash,
    )
    return plan, RemoteSeriesSplitValidation(
        source.uid, target_occurrence_key, ()
    )


def replace_series_definition(series: TaskSeries, **changes) -> TaskSeries:
    """dataclasses.replace-style copy of the mutable TaskSeries."""
    payload = {
        "title": series.title,
        "schedule": series.schedule,
        "rule": series.rule,
        "id": series.id,
        "uid": series.uid,
        "notes": series.notes,
        "priority": series.priority,
        "tags": tuple(series.tags),
        "revision": series.revision,
        "active": series.active,
        "created_at": series.created_at,
        "updated_at": series.updated_at,
        "deleted_at": series.deleted_at,
    }
    payload.update(changes)
    return TaskSeries(**payload)


def series_snapshot_data(series: TaskSeries) -> dict[str, Any]:
    """JSON-serialisable full local definition (for the durable plan)."""
    schedule, rule = series.schedule, series.rule
    return {
        "uid": series.uid,
        "title": series.title,
        "notes": series.notes,
        "priority": series.priority,
        "tags": list(series.tags),
        "revision": series.revision,
        "schedule": {
            "start_date": schedule.start_date.isoformat(),
            "all_day": bool(schedule.all_day),
            "local_time": (
                schedule.local_time.strftime("%H:%M")
                if schedule.local_time is not None else None
            ),
            "duration_minutes": schedule.duration_minutes,
            "timezone_name": schedule.timezone_name,
        },
        "rule": {
            "frequency": rule.frequency.value,
            "interval": int(rule.interval),
            "weekdays": list(rule.weekdays),
            "month_day": rule.month_day,
            "yearly_month": rule.yearly_month,
            "yearly_day": rule.yearly_day,
            "end_mode": rule.end_mode.value,
            "until_date": (
                rule.until_date.isoformat()
                if rule.until_date is not None else None
            ),
            "occurrence_count": rule.occurrence_count,
        },
    }


def series_from_snapshot_data(data: Mapping[str, Any]) -> TaskSeries:
    from datetime import time as time_type

    from planner_desktop.domain.recurrence import RecurrenceFrequency

    schedule_raw = dict(data.get("schedule") or {})
    rule_raw = dict(data.get("rule") or {})
    local_time = None
    if schedule_raw.get("local_time"):
        hours, minutes = str(schedule_raw["local_time"]).split(":", 1)
        local_time = time_type(int(hours), int(minutes))
    schedule = SeriesSchedule(
        start_date=date.fromisoformat(str(schedule_raw["start_date"])),
        all_day=bool(schedule_raw.get("all_day", True)),
        local_time=local_time,
        duration_minutes=schedule_raw.get("duration_minutes"),
        timezone_name=str(schedule_raw.get("timezone_name") or "UTC"),
    )
    rule = RecurrenceRule(
        frequency=RecurrenceFrequency(str(rule_raw["frequency"])),
        interval=int(rule_raw.get("interval") or 1),
        weekdays=tuple(int(item) for item in rule_raw.get("weekdays") or ()),
        month_day=rule_raw.get("month_day"),
        yearly_month=rule_raw.get("yearly_month"),
        yearly_day=rule_raw.get("yearly_day"),
        end_mode=RecurrenceEndMode(str(rule_raw.get("end_mode") or "never")),
        until_date=(
            date.fromisoformat(str(rule_raw["until_date"]))
            if rule_raw.get("until_date") else None
        ),
        occurrence_count=rule_raw.get("occurrence_count"),
    )
    return TaskSeries(
        title=str(data.get("title") or ""),
        schedule=schedule,
        rule=rule,
        uid=str(data.get("uid") or ""),
        notes=str(data.get("notes") or ""),
        priority=int(data.get("priority") or 0),
        tags=tuple(str(item) for item in data.get("tags") or ()),
        revision=int(data.get("revision") or 1),
    )


__all__ = [
    "ACTIVE_SPLIT_STATES",
    "FutureExceptionSummary",
    "PLANNER_SPLIT_PREDECESSOR_EVENT_ID_PROPERTY",
    "PLANNER_SPLIT_SCHEMA_VERSION",
    "PLANNER_SPLIT_SCHEMA_VERSION_PROPERTY",
    "PLANNER_SPLIT_SOURCE_SERIES_UID_PROPERTY",
    "PLANNER_SPLIT_TARGET_OCCURRENCE_KEY_PROPERTY",
    "PROCESSABLE_SPLIT_STATES",
    "RemoteSeriesSplitIssue",
    "RemoteSeriesSplitPlan",
    "RemoteSeriesSplitPlanRecord",
    "RemoteSeriesSplitProposal",
    "RemoteSeriesSplitRecoveryKind",
    "RemoteSeriesSplitResult",
    "RemoteSeriesSplitStatus",
    "RemoteSeriesSplitValidation",
    "canonical_json",
    "count_occurrences_before",
    "first_generated_slot_key",
    "is_generated_slot",
    "master_content_fingerprint",
    "plan_remote_series_split",
    "readable_split_status",
    "replace_series_definition",
    "series_from_snapshot_data",
    "series_master_payload",
    "series_snapshot_data",
    "split_provenance_properties",
]
