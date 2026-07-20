"""Preflight and lifecycle of remote "this and future" splits (B3C1).

Every operation here is local: creating, validating, cancelling and
requesting rollback of a split plan performs zero network calls.  The
remote steps execute exclusively inside the manual sync cycle
(``sync/calendar_series_remote_split_engine.py``).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Callable, List, Optional, Tuple

from planner_desktop.domain.google_occurrence import OccurrenceSyncStatus
from planner_desktop.domain.google_recurrence import parse_google_recurrence
from planner_desktop.domain.google_series_split import (
    FutureExceptionSummary,
    RemoteSeriesSplitIssue,
    RemoteSeriesSplitPlan,
    RemoteSeriesSplitPlanRecord,
    RemoteSeriesSplitProposal,
    RemoteSeriesSplitStatus,
    RemoteSeriesSplitValidation,
    canonical_json,
    plan_remote_series_split,
    series_master_payload,
    series_snapshot_data,
)
from planner_desktop.domain.series_calendar_link import SeriesLinkStatus

SPLIT_SERIES_NOT_FOUND = "Серия не найдена или уже удалена."
SPLIT_NOT_LINKED = "Серия не связана с Google Calendar."
SPLIT_LINK_NOT_SYNCED = (
    "Связь серии не в состоянии «синхронизирована»: завершите или "
    "разрешите текущие операции мастера перед разделением."
)
SPLIT_LOCAL_NOT_SYNCED = (
    "Локальная серия отличается от последнего синхронизированного "
    "состояния Google; выполните ручной синк перед разделением."
)
SPLIT_MISSING_ETAG = (
    "У связи нет подтверждённого ETag мастера Google; выполните ручной синк."
)
SPLIT_ACTIVE_SERIES_EDIT_ERROR = (
    "Для серии выполняется разделение «этот и будущие»: правка определения "
    "серии заблокирована до его завершения или отката."
)
SPLIT_ACTIVE_LINK_ERROR = (
    "Для серии выполняется разделение «этот и будущие»: операции со связью "
    "Google заблокированы до его завершения или отката."
)
SPLIT_ACTIVE_OCCURRENCE_ERROR = (
    "Экземпляр находится в зоне активного разделения «этот и будущие»: "
    "изменение расписания заблокировано до завершения разделения."
)
SPLIT_ALREADY_ACTIVE = (
    "Для серии уже существует активный план разделения."
)
SPLIT_CONFLICT_RESOLUTION_BLOCKED = (
    "Для серии выполняется разделение «этот и будущие»: разрешение "
    "конфликта мастера возможно только после конфликта самого разделения."
)


@dataclass
class RemoteSplitActionResult:
    ok: bool
    record: Optional[RemoteSeriesSplitPlanRecord] = None
    plan: Optional[RemoteSeriesSplitPlan] = None
    validation: Optional[RemoteSeriesSplitValidation] = None
    error: str = ""


def _slot_date(key: Optional[str]) -> Optional[date]:
    try:
        return date.fromisoformat(str(key)[:10])
    except (TypeError, ValueError):
        return None


class RemoteSeriesSplitService:
    """Local-only split plan lifecycle; the engine performs remote steps."""

    def __init__(
        self,
        series_repository,
        task_repository,
        link_store,
        occurrence_store,
        split_store,
        *,
        external_series_repository=None,
        today_provider: Callable[[], date] = date.today,
    ) -> None:
        self.series_repository = series_repository
        self.task_repository = task_repository
        self.link_store = link_store
        self.occurrence_store = occurrence_store
        self.split_store = split_store
        self.external_series_repository = external_series_repository
        self._today = today_provider

    # ---- lock helpers used by other services -------------------------------

    def has_active_split(self, series_uid: Optional[str]) -> bool:
        if not series_uid or self.split_store is None:
            return False
        return self.split_store.get_active_plan(series_uid) is not None

    def get_active_split(
        self, series_uid: str
    ) -> Optional[RemoteSeriesSplitPlanRecord]:
        return self.split_store.get_active_plan(series_uid)

    def is_occurrence_locked(
        self, series_uid: Optional[str], occurrence_key: Optional[str]
    ) -> bool:
        """True when a schedule operation on this occurrence must be blocked:
        an active plan exists and the slot is at or after its target."""
        if not series_uid or not occurrence_key:
            return False
        plan = self.split_store.get_active_plan(series_uid)
        if plan is None:
            return False
        slot = _slot_date(occurrence_key)
        target = _slot_date(plan.target_occurrence_key)
        if slot is None or target is None:
            return True  # unknown slot inside an active split: stay safe
        return slot >= target

    def allows_conflict_resolution(self, series_uid: str) -> bool:
        plan = self.split_store.get_active_plan(series_uid)
        if plan is None:
            return True
        return plan.state is RemoteSeriesSplitStatus.CONFLICT

    # ---- future exception summary (Part 9) ---------------------------------

    def _future_exception_summary(
        self, series, link, target_date: Optional[date]
    ) -> FutureExceptionSummary:
        local_exceptions: List[str] = []
        local_tombstones: List[str] = []
        pending_ops: List[str] = []
        terminal_ops: List[str] = []
        remote_exceptions: List[str] = []
        remote_cancelled: List[str] = []
        quarantine: List[str] = []
        exdate_rdate: List[str] = []

        def _after_target(key: Optional[str]) -> bool:
            slot = _slot_date(key)
            if slot is None or target_date is None:
                return True
            return slot >= target_date

        for task in self.task_repository.list_by_series(series.uid):
            if task.is_deleted:
                if _after_target(task.occurrence_key):
                    local_tombstones.append(str(task.occurrence_key))
                continue
            if task.is_series_exception and _after_target(task.occurrence_key):
                local_exceptions.append(str(task.occurrence_key))

        if self.occurrence_store is not None:
            for op in self.occurrence_store.list_terminal_ops():
                if op.series_uid == series.uid:
                    terminal_ops.append(str(op.occurrence_key))
            for occ_link in self.occurrence_store.list_occurrence_links(
                series_uid=series.uid
            ):
                if occ_link.detached_at is not None:
                    continue
                if occ_link.sync_status is OccurrenceSyncStatus.LOCAL_ONLY:
                    continue
                if occ_link.sync_status in (
                    OccurrenceSyncStatus.PENDING_UPDATE,
                    OccurrenceSyncStatus.PENDING_CANCEL,
                ):
                    # Any in-flight occurrence write blocks regardless of the
                    # slot date: the queue must fully drain before a split.
                    pending_ops.append(str(occ_link.occurrence_key))
                    continue
                if not _after_target(occ_link.occurrence_key):
                    continue
                if occ_link.sync_status in (
                    OccurrenceSyncStatus.CANCELLED,
                    OccurrenceSyncStatus.REMOTE_CANCELLED,
                ):
                    remote_cancelled.append(str(occ_link.occurrence_key))
                else:
                    remote_exceptions.append(str(occ_link.occurrence_key))
            for change in self.occurrence_store.list_occurrence_changes(
                unresolved_only=True
            ):
                if change.remote_master_event_id == link.remote_event_id or (
                    change.matched_series_uid == series.uid
                ):
                    quarantine.append(
                        str(
                            change.matched_occurrence_key
                            or change.original_start_value
                        )
                    )

        if self.external_series_repository is not None:
            catalog_row = self.external_series_repository.get(
                link.provider, link.calendar_id, link.remote_event_id
            )
            if catalog_row is not None:
                parsed = parse_google_recurrence(
                    tuple(catalog_row.recurrence_lines)
                )
                from datetime import datetime as datetime_type

                for entry in (*parsed.exdates, *parsed.rdates):
                    for value in entry.values:
                        value_date = (
                            value.date()
                            if isinstance(value, datetime_type)
                            else value
                        )
                        if target_date is None or value_date >= target_date:
                            exdate_rdate.append(entry.raw_line)
                            break
                if parsed.recurrence_set.other_lines:
                    exdate_rdate.extend(parsed.recurrence_set.other_lines)

        return FutureExceptionSummary(
            local_exception_dates=tuple(dict.fromkeys(local_exceptions)),
            local_tombstone_dates=tuple(dict.fromkeys(local_tombstones)),
            pending_occurrence_op_dates=tuple(dict.fromkeys(pending_ops)),
            terminal_occurrence_op_dates=tuple(dict.fromkeys(terminal_ops)),
            remote_exception_dates=tuple(dict.fromkeys(remote_exceptions)),
            remote_cancelled_dates=tuple(dict.fromkeys(remote_cancelled)),
            unresolved_quarantine_dates=tuple(dict.fromkeys(quarantine)),
            exdate_rdate_lines=tuple(dict.fromkeys(exdate_rdate)),
        )

    # ---- validation (Part 5 + Part 9) ---------------------------------------

    def validate_split(
        self,
        series_uid: str,
        target_occurrence_key: str,
        proposal: Optional[RemoteSeriesSplitProposal] = None,
    ) -> Tuple[Optional[RemoteSeriesSplitPlan], RemoteSeriesSplitValidation]:
        issues: List[RemoteSeriesSplitIssue] = []

        def _reject(code: str, message: str):
            issues.append(RemoteSeriesSplitIssue(code, message))
            return None, RemoteSeriesSplitValidation(
                series_uid, target_occurrence_key, tuple(issues)
            )

        series = self.series_repository.get_by_uid(series_uid)
        if series is None or series.is_deleted or not series.active:
            return _reject("series_inactive", SPLIT_SERIES_NOT_FOUND)

        existing = self.split_store.get_active_plan(series_uid)
        if existing is not None:
            return _reject("split_active", SPLIT_ALREADY_ACTIVE)

        link = self.link_store.get_link(series_uid)
        if link is None:
            return _reject("not_linked", SPLIT_NOT_LINKED)
        if link.link_status is not SeriesLinkStatus.SYNCED:
            return _reject("link_not_synced", SPLIT_LINK_NOT_SYNCED)
        if not link.remote_etag:
            return _reject("missing_etag", SPLIT_MISSING_ETAG)
        if self.link_store.get_pending_op(series_uid) is not None:
            return _reject(
                "pending_master_op",
                "У серии есть незавершённая операция мастера Google.",
            )
        terminal_master_ops = [
            op for op in self._terminal_master_ops()
            if op.series_uid == series_uid
        ]
        if terminal_master_ops:
            return _reject(
                "terminal_master_op",
                "У серии есть dead-letter операция мастера Google; "
                "сначала разберите её.",
            )

        _, current_hash = series_master_payload(series)
        if link.last_synced_payload_hash != current_hash:
            return _reject("local_not_synced", SPLIT_LOCAL_NOT_SYNCED)

        target_date = _slot_date(target_occurrence_key)
        summary = self._future_exception_summary(series, link, target_date)

        plan, validation = plan_remote_series_split(
            series,
            source_remote_event_id=link.remote_event_id,
            target_occurrence_key=target_occurrence_key,
            proposal=proposal or RemoteSeriesSplitProposal(),
            future_exceptions=summary,
            today=self._today(),
        )
        if issues:
            merged = tuple(issues) + validation.issues
            return None, RemoteSeriesSplitValidation(
                series_uid, target_occurrence_key, merged
            )
        return plan, validation

    def _terminal_master_ops(self):
        from planner_desktop.domain.series_calendar_link import (
            SeriesSyncOpStatus,
        )

        lister = getattr(self.link_store, "list_ops", None)
        if not callable(lister):
            return []
        return lister(status=SeriesSyncOpStatus.TERMINAL)

    # ---- plan lifecycle ------------------------------------------------------

    def create_split_plan(
        self,
        series_uid: str,
        target_occurrence_key: str,
        proposal: Optional[RemoteSeriesSplitProposal] = None,
    ) -> RemoteSplitActionResult:
        """Validate and persist exactly one durable plan; zero network calls.

        A duplicate rapid request returns the already-active plan.  Neither
        local TaskSeries is mutated here: the successor definition lives only
        inside the durable plan until local finalization.
        """
        existing = self.split_store.get_active_plan(series_uid)
        if existing is not None:
            return RemoteSplitActionResult(ok=True, record=existing)

        plan, validation = self.validate_split(
            series_uid, target_occurrence_key, proposal
        )
        if plan is None or not validation.ok:
            return RemoteSplitActionResult(
                ok=False,
                validation=validation,
                error="\n".join(validation.errors),
            )
        series = self.series_repository.get_by_uid(series_uid)
        link = self.link_store.get_link(series_uid)
        if series is None or link is None or link.id is None:
            return RemoteSplitActionResult(ok=False, error=SPLIT_NOT_LINKED)
        identity = plan.target_original_start
        record = RemoteSeriesSplitPlanRecord(
            source_series_uid=series_uid,
            source_link_id=int(link.id),
            source_link_generation=int(link.link_generation),
            source_remote_event_id=link.remote_event_id,
            target_occurrence_key=target_occurrence_key,
            target_original_start_kind=identity.kind,
            target_original_start_value=identity.value,
            target_original_start_timezone=identity.timezone_name,
            source_local_revision=int(series.revision),
            source_remote_etag_base=str(link.remote_etag),
            source_original_snapshot_json=canonical_json(
                dict(plan.source_before_payload)
            ),
            source_original_payload_hash=plan.source_before_hash,
            source_trimmed_payload_json=canonical_json(
                dict(plan.trimmed_source_payload)
            ),
            source_trimmed_payload_hash=plan.trimmed_source_hash,
            reserved_successor_series_uid=plan.reserved_successor_series_uid,
            successor_remote_event_id=plan.successor_remote_event_id,
            successor_series_snapshot_json=canonical_json(
                series_snapshot_data(plan.successor_series)
            ),
            successor_payload_json=canonical_json(
                dict(plan.successor_payload)
            ),
            successor_payload_hash=plan.successor_hash,
        )
        try:
            stored = self.split_store.create_plan(record)
        except Exception as exc:
            return RemoteSplitActionResult(
                ok=False, error=f"Не удалось сохранить план разделения: {exc}"
            )
        return RemoteSplitActionResult(
            ok=True, record=stored, plan=plan, validation=validation
        )

    def list_split_history(
        self, series_uid: Optional[str] = None
    ) -> List[RemoteSeriesSplitPlanRecord]:
        return self.split_store.list_plans(series_uid=series_uid)

    def retry_split(self, plan_id: int) -> RemoteSplitActionResult:
        """Re-arm a processable plan after transient errors; local only."""
        record = self.split_store.get_plan(plan_id)
        if record is None:
            return RemoteSplitActionResult(ok=False, error="План не найден.")
        if record.state not in (
            RemoteSeriesSplitStatus.PENDING,
            RemoteSeriesSplitStatus.SOURCE_TRIMMED,
            RemoteSeriesSplitStatus.SUCCESSOR_CREATED,
            RemoteSeriesSplitStatus.LOCAL_FINALIZE_PENDING,
            RemoteSeriesSplitStatus.ROLLBACK_PENDING,
            RemoteSeriesSplitStatus.SUCCESSOR_REMOVED_FOR_ROLLBACK,
        ):
            return RemoteSplitActionResult(
                ok=False,
                record=record,
                error="План нельзя повторить из текущего состояния.",
            )
        refreshed = self.split_store._transition(  # noqa: SLF001 - same package
            plan_id, record.state, error=None
        )
        return RemoteSplitActionResult(ok=True, record=refreshed)

    def request_split_rollback(self, plan_id: int) -> RemoteSplitActionResult:
        """Durable explicit rollback for partial remote completion."""
        record = self.split_store.get_plan(plan_id)
        if record is None:
            return RemoteSplitActionResult(ok=False, error="План не найден.")
        if record.state is RemoteSeriesSplitStatus.PENDING:
            cancelled = self.split_store.cancel_unstarted_plan(plan_id)
            return RemoteSplitActionResult(
                ok=cancelled,
                record=self.split_store.get_plan(plan_id),
                error="" if cancelled else "План уже начал удалённые шаги.",
            )
        if record.state in (
            RemoteSeriesSplitStatus.SOURCE_TRIMMED,
            RemoteSeriesSplitStatus.SUCCESSOR_CREATED,
            RemoteSeriesSplitStatus.LOCAL_FINALIZE_PENDING,
            RemoteSeriesSplitStatus.CONFLICT,
        ):
            refreshed = self.split_store.mark_rollback_pending(
                plan_id, reason="Пользователь запросил откат разделения."
            )
            return RemoteSplitActionResult(ok=True, record=refreshed)
        if record.state in (
            RemoteSeriesSplitStatus.ROLLBACK_PENDING,
            RemoteSeriesSplitStatus.SUCCESSOR_REMOVED_FOR_ROLLBACK,
        ):
            return RemoteSplitActionResult(ok=True, record=record)
        return RemoteSplitActionResult(
            ok=False,
            record=record,
            error="Откат из текущего состояния не поддерживается.",
        )

    def cancel_unstarted_split(self, plan_id: int) -> RemoteSplitActionResult:
        """Cancel a plan before any remote work; zero Google calls."""
        cancelled = self.split_store.cancel_unstarted_plan(plan_id)
        record = self.split_store.get_plan(plan_id)
        return RemoteSplitActionResult(
            ok=cancelled,
            record=record,
            error="" if cancelled else (
                "План уже выполнил удалённые шаги: используйте явный откат."
            ),
        )

    # ---- diagnostics ---------------------------------------------------------

    def diagnostics(self) -> dict:
        return self.split_store.counts_by_state()


__all__ = [
    "RemoteSeriesSplitService",
    "RemoteSplitActionResult",
    "SPLIT_ACTIVE_LINK_ERROR",
    "SPLIT_ACTIVE_OCCURRENCE_ERROR",
    "SPLIT_ACTIVE_SERIES_EDIT_ERROR",
    "SPLIT_ALREADY_ACTIVE",
    "SPLIT_CONFLICT_RESOLUTION_BLOCKED",
    "SPLIT_LINK_NOT_SYNCED",
    "SPLIT_LOCAL_NOT_SYNCED",
    "SPLIT_MISSING_ETAG",
    "SPLIT_NOT_LINKED",
    "SPLIT_SERIES_NOT_FOUND",
]
