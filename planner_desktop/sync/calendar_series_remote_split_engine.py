"""Durable remote split execution inside the manual sync cycle (B3C1).

The engine advances active ``calendar_series_remote_splits`` plans:
``pending -> source_trimmed -> successor_created -> completed`` plus the
explicit rollback path.  Every transition persists before the next remote
step; every verification requires Planner ownership markers PLUS the
actual canonical content — markers alone never prove anything.
"""
from __future__ import annotations

import logging
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Mapping, Optional

from planner_desktop.domain.google_recurrence import parse_google_recurrence
from planner_desktop.domain.google_series_split import (
    RemoteSeriesSplitPlanRecord,
    RemoteSeriesSplitRecoveryKind,
    RemoteSeriesSplitResult,
    RemoteSeriesSplitStatus,
    replace_series_definition,
    series_from_snapshot_data,
)
from planner_desktop.domain.series_calendar_link import (
    PLANNER_SERIES_UID_PROPERTY,
)
from planner_desktop.storage.calendar_sync_store import MAX_ATTEMPTS
from planner_desktop.sync.google_calendar_gateway import (
    split_resource_content_matches,
)
from planner_desktop.sync.sync_types import (
    RemoteMasterConflictError,
    RetryableGatewayError,
    TerminalGatewayError,
)

logger = logging.getLogger(__name__)


@dataclass
class RemoteSplitSyncResult:
    """Additive counters for the manual sync summary (Part 13)."""

    splits_started: int = 0
    sources_trimmed: int = 0
    successors_created: int = 0
    splits_finalized: int = 0
    conflicts: int = 0
    rollbacks_completed: int = 0
    terminal: int = 0
    reconciliation_completions: int = 0
    items: list[RemoteSeriesSplitResult] = field(default_factory=list)


def _resource_private(resource: Mapping[str, Any]) -> dict[str, str]:
    private = (resource.get("extendedProperties") or {}).get("private") or {}
    return {str(key): str(value) for key, value in private.items()}


def merge_split_master_resource(
    current_resource: Mapping[str, Any],
    desired_payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Merge Planner-owned master fields into the complete remote resource.

    Unrelated provider fields (attendees, reminders, location, conferencing,
    shared extended properties, ...) are preserved verbatim.
    """
    merged = deepcopy(dict(current_resource))
    for name in ("summary", "description", "start", "end", "recurrence"):
        if name in desired_payload:
            merged[name] = deepcopy(desired_payload[name])
    extended = deepcopy(merged.get("extendedProperties") or {})
    private = dict(extended.get("private") or {})
    desired_private = (
        (desired_payload.get("extendedProperties") or {}).get("private") or {}
    )
    private.update({str(k): str(v) for k, v in desired_private.items()})
    extended["private"] = private
    merged["extendedProperties"] = extended
    return merged


def _slot_date(key: Optional[str]) -> Optional[date]:
    try:
        return date.fromisoformat(str(key)[:10])
    except (TypeError, ValueError):
        return None


class CalendarSeriesRemoteSplitEngine:
    """Processes active remote split plans; runs FIRST in manual sync."""

    def __init__(
        self,
        split_store,
        series_repository,
        task_repository,
        gateway,
    ) -> None:
        self._store = split_store
        self._series = series_repository
        self._tasks = task_repository
        self._gateway = gateway

    # ---- public entry -------------------------------------------------------

    def process_pending(self) -> RemoteSplitSyncResult:
        result = RemoteSplitSyncResult()
        for plan in self._store.list_processable_plans():
            item = RemoteSeriesSplitResult(
                plan_id=int(plan.id or 0),
                series_uid=plan.source_series_uid,
                status=plan.state,
            )
            try:
                self._advance(plan, item, result)
            except RetryableGatewayError as exc:
                self._register_transient_failure(plan, item, result, str(exc))
            except TerminalGatewayError as exc:
                self._store.mark_terminal(plan.id, str(exc))
                item.terminal = True
                item.error = str(exc)
                item.status = RemoteSeriesSplitStatus.TERMINAL_ERROR
                result.terminal += 1
            except RemoteMasterConflictError as exc:
                self._store.mark_conflict(plan.id, str(exc))
                item.conflict = True
                item.error = str(exc)
                item.status = RemoteSeriesSplitStatus.CONFLICT
                result.conflicts += 1
            result.items.append(item)
        return result

    def _register_transient_failure(
        self,
        plan: RemoteSeriesSplitPlanRecord,
        item: RemoteSeriesSplitResult,
        result: RemoteSplitSyncResult,
        error: str,
    ) -> None:
        refreshed = self._store.get_plan(plan.id)
        attempts = (refreshed.attempts if refreshed is not None else 0) + 1
        if attempts >= MAX_ATTEMPTS:
            self._store.mark_terminal(
                plan.id, f"Превышено число попыток: {error}"
            )
            item.terminal = True
            item.status = RemoteSeriesSplitStatus.TERMINAL_ERROR
            result.terminal += 1
        else:
            self._store.record_attempt_error(plan.id, error)
        item.error = error

    # ---- state machine ------------------------------------------------------

    def _advance(
        self,
        plan: RemoteSeriesSplitPlanRecord,
        item: RemoteSeriesSplitResult,
        result: RemoteSplitSyncResult,
    ) -> None:
        current = plan
        # One manual sync may carry a plan through every remaining stage,
        # persisting each transition before the next remote step.
        for _ in range(6):
            if current is None:
                return
            state = current.state
            if state is RemoteSeriesSplitStatus.PENDING:
                result.splits_started += 1
                item.started = True
                current = self._step_trim_source(current, item, result)
            elif state is RemoteSeriesSplitStatus.SOURCE_TRIMMED:
                current = self._step_create_successor(current, item, result)
            elif state in (
                RemoteSeriesSplitStatus.SUCCESSOR_CREATED,
                RemoteSeriesSplitStatus.LOCAL_FINALIZE_PENDING,
            ):
                current = self._step_finalize_local(current, item, result)
            elif state is RemoteSeriesSplitStatus.ROLLBACK_PENDING:
                current = self._step_rollback_remove_successor(
                    current, item, result
                )
            elif state is RemoteSeriesSplitStatus.SUCCESSOR_REMOVED_FOR_ROLLBACK:
                current = self._step_rollback_restore_source(
                    current, item, result
                )
            else:
                item.status = state
                return
        if current is not None:
            item.status = current.state

    # ---- helpers ------------------------------------------------------------

    def _fetch_source(self, plan: RemoteSeriesSplitPlanRecord):
        return self._gateway.get_recurring_master_resource(
            plan.source_remote_event_id
        )

    def _fetch_successor(self, plan: RemoteSeriesSplitPlanRecord):
        return self._gateway.get_recurring_master_resource(
            plan.successor_remote_event_id
        )

    @staticmethod
    def _verify_owner(
        resource: Mapping[str, Any], expected_uid: str, remote_event_id: str
    ) -> None:
        actual = _resource_private(resource).get(PLANNER_SERIES_UID_PROPERTY)
        if actual != expected_uid:
            raise TerminalGatewayError(
                f"Мастер {remote_event_id} принадлежит другой серии; "
                "разделение остановлено."
            )

    def _conflict(
        self,
        plan: RemoteSeriesSplitPlanRecord,
        item: RemoteSeriesSplitResult,
        result: RemoteSplitSyncResult,
        message: str,
    ) -> None:
        self._store.mark_conflict(plan.id, message)
        item.conflict = True
        item.error = message
        item.status = RemoteSeriesSplitStatus.CONFLICT
        result.conflicts += 1

    # ---- forward steps ------------------------------------------------------

    def _step_trim_source(
        self,
        plan: RemoteSeriesSplitPlanRecord,
        item: RemoteSeriesSplitResult,
        result: RemoteSplitSyncResult,
    ) -> Optional[RemoteSeriesSplitPlanRecord]:
        resource = self._fetch_source(plan)
        if resource is None:
            self._conflict(
                plan, item, result,
                "Исходный мастер Google отменён/удалён до сокращения; "
                "автоматическое пересоздание отключено.",
            )
            return None
        self._verify_owner(
            resource, plan.source_series_uid, plan.source_remote_event_id
        )
        if split_resource_content_matches(
            resource, plan.trimmed_source_payload
        ):
            # Crash recovery: our trim already succeeded remotely.
            refreshed = self._store.mark_source_trimmed(
                plan.id, remote_etag=str(resource.get("etag") or "")
            )
            item.source_trimmed = True
            item.recovery = RemoteSeriesSplitRecoveryKind.SOURCE_TRIM_RECONCILED
            result.sources_trimmed += 1
            result.reconciliation_completions += 1
            return refreshed
        etag = str(resource.get("etag") or "")
        if etag != plan.source_remote_etag_base:
            self._conflict(
                plan, item, result,
                "ETag исходного мастера изменился после планирования; "
                "разделение остановлено без записи.",
            )
            return None
        if not split_resource_content_matches(
            resource, plan.source_original_snapshot
        ):
            self._conflict(
                plan, item, result,
                "Содержимое исходного мастера отличается от запланированного; "
                "разделение остановлено без записи.",
            )
            return None
        merged = merge_split_master_resource(
            resource, plan.trimmed_source_payload
        )
        written = self._gateway.update_recurring_master_full(
            plan.source_remote_event_id,
            merged,
            expected_etag=plan.source_remote_etag_base,
        )
        refreshed = self._store.mark_source_trimmed(
            plan.id, remote_etag=str(written.get("etag") or "")
        )
        item.source_trimmed = True
        result.sources_trimmed += 1
        return refreshed

    def _step_create_successor(
        self,
        plan: RemoteSeriesSplitPlanRecord,
        item: RemoteSeriesSplitResult,
        result: RemoteSplitSyncResult,
    ) -> Optional[RemoteSeriesSplitPlanRecord]:
        source = self._fetch_source(plan)
        if source is None:
            self._conflict(
                plan, item, result,
                "Исходный мастер исчез после сокращения; преемник не создан.",
            )
            return None
        self._verify_owner(
            source, plan.source_series_uid, plan.source_remote_event_id
        )
        if not split_resource_content_matches(
            source, plan.trimmed_source_payload
        ):
            self._conflict(
                plan, item, result,
                "Исходный мастер изменился после сокращения; преемник не "
                "создан.",
            )
            return None
        source_etag = str(source.get("etag") or "")
        if source_etag and source_etag != plan.source_trimmed_remote_etag:
            self._store.update_remote_etags(
                plan.id, source_trimmed_remote_etag=source_etag
            )

        existing = self._fetch_successor(plan)
        if existing is not None:
            self._verify_owner(
                existing,
                plan.reserved_successor_series_uid,
                plan.successor_remote_event_id,
            )
            if split_resource_content_matches(
                existing, plan.successor_payload
            ):
                refreshed = self._store.mark_successor_created(
                    plan.id, remote_etag=str(existing.get("etag") or "")
                )
                item.successor_created = True
                item.recovery = (
                    RemoteSeriesSplitRecoveryKind.SUCCESSOR_INSERT_RECONCILED
                )
                result.successors_created += 1
                result.reconciliation_completions += 1
                return refreshed
            self._conflict(
                plan, item, result,
                "Событие с детерминированным id преемника существует, но его "
                "содержимое не совпадает; вставка остановлена.",
            )
            return None
        written = self._gateway.insert_split_successor_master(
            plan.successor_remote_event_id, plan.successor_payload
        )
        refreshed = self._store.mark_successor_created(
            plan.id, remote_etag=str(written.get("etag") or "")
        )
        item.successor_created = True
        result.successors_created += 1
        return refreshed

    def _step_finalize_local(
        self,
        plan: RemoteSeriesSplitPlanRecord,
        item: RemoteSeriesSplitResult,
        result: RemoteSplitSyncResult,
    ) -> Optional[RemoteSeriesSplitPlanRecord]:
        source = self._fetch_source(plan)
        successor = self._fetch_successor(plan)
        if source is None or successor is None:
            self._conflict(
                plan, item, result,
                "Один из двух мастеров исчез до локальной финализации; "
                "автоматическое пересоздание отключено.",
            )
            return None
        self._verify_owner(
            source, plan.source_series_uid, plan.source_remote_event_id
        )
        self._verify_owner(
            successor,
            plan.reserved_successor_series_uid,
            plan.successor_remote_event_id,
        )
        if not split_resource_content_matches(
            source, plan.trimmed_source_payload
        ) or not split_resource_content_matches(
            successor, plan.successor_payload
        ):
            self._conflict(
                plan, item, result,
                "Содержимое одного из мастеров изменилось до локальной "
                "финализации; локальное состояние не изменено.",
            )
            return None
        self._store.update_remote_etags(
            plan.id,
            source_trimmed_remote_etag=str(source.get("etag") or "") or None,
            successor_remote_etag=str(successor.get("etag") or "") or None,
        )
        refreshed = self._store.get_plan(plan.id)
        if refreshed is None:
            return None

        series = self._series.get_by_uid(plan.source_series_uid)
        if series is None or series.is_deleted:
            raise TerminalGatewayError(
                "Локальная исходная серия отсутствует; финализация невозможна."
            )
        parsed = parse_google_recurrence(
            tuple(
                str(line)
                for line in refreshed.trimmed_source_payload.get("recurrence")
                or ()
            ),
            schedule=series.schedule,
        )
        if parsed.planner_rule is None:
            raise TerminalGatewayError(
                "Сокращённое правило плана не парсится; финализация "
                "остановлена."
            )
        trimmed_source = replace_series_definition(
            series,
            rule=parsed.planner_rule,
            revision=refreshed.source_local_revision + 1,
        )
        successor_series = series_from_snapshot_data(
            refreshed.successor_series_snapshot
        )
        target = _slot_date(refreshed.target_occurrence_key)
        replaced: list[str] = []
        for task in self._tasks.list_by_series(plan.source_series_uid):
            if task.is_deleted or task.completed or task.is_series_exception:
                continue
            slot = _slot_date(task.occurrence_key)
            if slot is None or target is None or slot < target:
                continue
            replaced.append(task.uid)
        tag_ids = list(
            self._series.tag_ids_for_series(plan.source_series_uid)
        )
        was_retry = refreshed.attempts > 0
        try:
            self._store.finalize_linked_remote_split_atomic(
                refreshed,
                trimmed_source=trimmed_source,
                successor=successor_series,
                replaced_task_uids=replaced,
                successor_tag_ids=tag_ids,
            )
        except Exception as exc:
            # Remote work is complete; only the local transaction retries on
            # the next manual sync.  No further remote update or insert runs.
            logger.exception("Локальная финализация разделения не удалась")
            self._store.record_attempt_error(
                plan.id, f"Локальная финализация не удалась: {exc}"
            )
            item.error = str(exc)
            item.status = RemoteSeriesSplitStatus.SUCCESSOR_CREATED
            return None
        item.finalized = True
        item.status = RemoteSeriesSplitStatus.COMPLETED
        if was_retry:
            item.recovery = RemoteSeriesSplitRecoveryKind.LOCAL_FINALIZE_RETRIED
            result.reconciliation_completions += 1
        result.splits_finalized += 1
        return None

    # ---- rollback steps (Part 11) -------------------------------------------

    def _step_rollback_remove_successor(
        self,
        plan: RemoteSeriesSplitPlanRecord,
        item: RemoteSeriesSplitResult,
        result: RemoteSplitSyncResult,
    ) -> Optional[RemoteSeriesSplitPlanRecord]:
        successor = self._fetch_successor(plan)
        if successor is None:
            # Never created or already deleted: the step is reconciled.
            refreshed = self._store.mark_successor_removed_for_rollback(plan.id)
            item.recovery = RemoteSeriesSplitRecoveryKind.ROLLBACK_DELETE_RECONCILED
            return refreshed
        actual_uid = _resource_private(successor).get(
            PLANNER_SERIES_UID_PROPERTY
        )
        if actual_uid != plan.reserved_successor_series_uid or (
            not split_resource_content_matches(
                successor, plan.successor_payload
            )
        ):
            self._conflict(
                plan, item, result,
                "Мастер-преемник изменился или принадлежит другой серии; "
                "удаление при откате остановлено — требуется ручной разбор.",
            )
            return None
        self._gateway.delete_recurring_master(plan.successor_remote_event_id)
        return self._store.mark_successor_removed_for_rollback(plan.id)

    def _step_rollback_restore_source(
        self,
        plan: RemoteSeriesSplitPlanRecord,
        item: RemoteSeriesSplitResult,
        result: RemoteSplitSyncResult,
    ) -> Optional[RemoteSeriesSplitPlanRecord]:
        source = self._fetch_source(plan)
        if source is None:
            self._conflict(
                plan, item, result,
                "Исходный мастер исчез; автоматическое восстановление при "
                "откате остановлено.",
            )
            return None
        self._verify_owner(
            source, plan.source_series_uid, plan.source_remote_event_id
        )
        if split_resource_content_matches(
            source, plan.source_original_snapshot
        ):
            # Already restored (retry after a local persistence failure).
            self._store.mark_rolled_back(
                plan.id, note="Исходный мастер уже восстановлен."
            )
            item.rollback_completed = True
            item.recovery = (
                RemoteSeriesSplitRecoveryKind.ROLLBACK_RESTORE_RECONCILED
            )
            item.status = RemoteSeriesSplitStatus.ROLLED_BACK
            result.rollbacks_completed += 1
            return None
        if not split_resource_content_matches(
            source, plan.trimmed_source_payload
        ):
            self._conflict(
                plan, item, result,
                "Исходный мастер не находится ни в исходном, ни в "
                "сокращённом состоянии; восстановление остановлено.",
            )
            return None
        merged = merge_split_master_resource(
            source, plan.source_original_snapshot
        )
        self._gateway.update_recurring_master_full(
            plan.source_remote_event_id,
            merged,
            expected_etag=str(source.get("etag") or "") or None,
        )
        self._store.mark_rolled_back(plan.id)
        item.rollback_completed = True
        item.status = RemoteSeriesSplitStatus.ROLLED_BACK
        result.rollbacks_completed += 1
        return None


__all__ = [
    "CalendarSeriesRemoteSplitEngine",
    "RemoteSplitSyncResult",
    "merge_split_master_resource",
]
