"""Manual push engine for one occurrence of a Planner-owned linked series."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Optional

from planner_desktop.domain.google_occurrence import (
    OccurrenceOperationKind,
    PLANNER_OCCURRENCE_KEY_PROPERTY,
    PLANNER_OCCURRENCE_LINK_GENERATION_PROPERTY,
    PLANNER_OCCURRENCE_PAYLOAD_HASH_PROPERTY,
    PLANNER_OCCURRENCE_SERIES_UID_PROPERTY,
    canonical_occurrence_payload_data,
    canonical_occurrence_payload_fingerprint,
)
from planner_desktop.domain.series_calendar_link import (
    PLANNER_SERIES_UID_PROPERTY,
    RemoteOccurrenceChange,
)
from planner_desktop.domain.task import utc_now
from planner_desktop.storage.calendar_series_occurrence_sync_store import (
    CalendarSeriesOccurrenceSyncStore,
)
from planner_desktop.sync.calendar_series_occurrence_mapper import (
    merge_complete_instance_payload,
    original_start_from_payload,
    validate_remote_occurrence_payload,
)
from planner_desktop.sync.sync_types import (
    RemoteOccurrenceConflictError,
    RetryableGatewayError,
    TerminalGatewayError,
)


@dataclass
class OccurrenceSyncItemResult:
    series_uid: str
    occurrence_key: str
    operation: OccurrenceOperationKind
    ok: bool
    reconciled: bool = False
    conflict: bool = False
    terminal: bool = False
    error: str = ""


@dataclass
class OccurrenceSyncResult:
    updates_pushed: int = 0
    cancellations_pushed: int = 0
    conflicts_detected: int = 0
    conflicts_resolved_keep_planner: int = 0
    terminal: int = 0
    reconciled: int = 0
    items: list[OccurrenceSyncItemResult] = field(default_factory=list)

    @property
    def pushed(self) -> int:
        return self.updates_pushed + self.cancellations_pushed


def _parse_updated(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(
            timezone.utc
        )
    except ValueError:
        return None


class CalendarSeriesOccurrenceSyncEngine:
    """Pushes only the dedicated recurring-instance queue.

    It never calls recurring-master writes and never touches the ordinary Task
    queue.
    """

    def __init__(
        self,
        store: CalendarSeriesOccurrenceSyncStore,
        gateway: object,
    ) -> None:
        self._store = store
        self._gateway = gateway

    def push_pending(self, limit: int = 50) -> OccurrenceSyncResult:
        result = OccurrenceSyncResult()
        for op in self._store.list_due_ops(limit):
            try:
                item = self._push_one(op)
            except RetryableGatewayError as exc:
                self._store.requeue_op(op.id, str(exc))
                item = OccurrenceSyncItemResult(
                    op.series_uid,
                    op.occurrence_key,
                    op.op,
                    False,
                    error=str(exc),
                )
            except TerminalGatewayError as exc:
                self._store.mark_terminal(op.id, str(exc))
                item = OccurrenceSyncItemResult(
                    op.series_uid,
                    op.occurrence_key,
                    op.op,
                    False,
                    terminal=True,
                    error=str(exc),
                )
            except RemoteOccurrenceConflictError as exc:
                self._supersede_after_race(op, exc.remote_payload or {})
                item = OccurrenceSyncItemResult(
                    op.series_uid,
                    op.occurrence_key,
                    op.op,
                    False,
                    conflict=True,
                    error=str(exc),
                )
            result.items.append(item)
            if item.ok:
                if op.op is OccurrenceOperationKind.CANCEL:
                    result.cancellations_pushed += 1
                else:
                    result.updates_pushed += 1
                if item.reconciled:
                    result.reconciled += 1
                if op.acknowledged_remote_etag:
                    result.conflicts_resolved_keep_planner += 1
            elif item.conflict:
                result.conflicts_detected += 1
            elif item.terminal:
                result.terminal += 1
        return result

    def _verify_parent_owner(self, op) -> None:
        getter = getattr(self._gateway, "get_recurring_master", None)
        if not callable(getter):
            raise TerminalGatewayError(
                "gateway does not support recurring-master ownership checks"
            )
        master = getter(op.remote_master_event_id)
        if master is None:
            raise TerminalGatewayError("linked recurring master no longer exists")
        private = getattr(master, "private_extended_properties", {}) or {}
        if private.get(PLANNER_SERIES_UID_PROPERTY) != op.series_uid:
            raise TerminalGatewayError(
                "linked recurring master is not owned by this Planner series"
            )

    def _resolve_instance(self, op, link) -> dict[str, Any]:
        expected_identity = link.identity
        if op.remote_instance_event_id:
            instance = self._gateway.get_recurring_instance(
                op.remote_instance_event_id
            )
            if instance is None:
                raise TerminalGatewayError(
                    "known recurring instance no longer exists"
                )
            candidates = [instance]
        else:
            candidates = self._gateway.list_recurring_instances(
                op.remote_master_event_id,
                expected_identity.to_google(),
                show_deleted=True,
            )
        exact: list[dict[str, Any]] = []
        for candidate in candidates:
            validation = validate_remote_occurrence_payload(
                candidate,
                expected_master_event_id=op.remote_master_event_id,
                expected_original_start=expected_identity,
            )
            if validation.ok:
                exact.append(dict(candidate))
        if not exact:
            raise TerminalGatewayError(
                "no exact recurring instance matches originalStartTime"
            )
        if len(exact) != 1:
            raise TerminalGatewayError(
                "multiple recurring instances match the same originalStartTime"
            )
        instance_id = str(exact[0].get("id") or "")
        if not instance_id:
            raise TerminalGatewayError("recurring instance has no event id")
        # Always retrieve the complete current resource before writing.
        complete = self._gateway.get_recurring_instance(instance_id)
        if complete is None:
            raise TerminalGatewayError(
                "recurring instance disappeared during exact lookup"
            )
        validation = validate_remote_occurrence_payload(
            complete,
            expected_master_event_id=op.remote_master_event_id,
            expected_original_start=expected_identity,
        )
        if not validation.ok:
            raise TerminalGatewayError(validation.error)
        return dict(complete)

    @staticmethod
    def _actual_matches_update(
        actual: Mapping[str, Any], desired: Mapping[str, Any], op
    ) -> bool:
        if canonical_occurrence_payload_data(actual) != (
            canonical_occurrence_payload_data(desired)
        ):
            return False
        private = ((actual.get("extendedProperties") or {}).get("private") or {})
        desired_private = (
            (desired.get("extendedProperties") or {}).get("private") or {}
        )
        required = (
            PLANNER_OCCURRENCE_SERIES_UID_PROPERTY,
            PLANNER_OCCURRENCE_KEY_PROPERTY,
            PLANNER_OCCURRENCE_LINK_GENERATION_PROPERTY,
            PLANNER_OCCURRENCE_PAYLOAD_HASH_PROPERTY,
        )
        return all(
            str(private.get(name) or "") == str(desired_private.get(name) or "")
            for name in required
        )

    def _push_one(self, op) -> OccurrenceSyncItemResult:
        link = self._store.get_occurrence_link(
            op.series_uid, op.occurrence_key
        )
        if link is None or link.series_link_id != op.series_link_id:
            raise TerminalGatewayError(
                "occurrence operation targets a detached link generation"
            )
        self._verify_parent_owner(op)
        current = self._resolve_instance(op, link)
        instance_id = str(current["id"])
        current_etag = str(current.get("etag") or "") or None

        if (
            op.acknowledged_remote_etag
            and current_etag != op.acknowledged_remote_etag
        ):
            raise RemoteOccurrenceConflictError(
                "remote occurrence changed after the user's decision", current
            )

        desired = op.payload
        if op.op is OccurrenceOperationKind.CANCEL:
            already_applied = str(current.get("status") or "") == "cancelled"
        else:
            already_applied = self._actual_matches_update(current, desired, op)

        if already_applied:
            remote_hash = canonical_occurrence_payload_fingerprint(current)
            self._store.finalize_success(
                op,
                remote_instance_event_id=instance_id,
                remote_etag=current_etag,
                remote_updated_at=_parse_updated(current.get("updated")),
                local_hash=op.desired_payload_hash,
                remote_hash=remote_hash,
                cancelled=op.op is OccurrenceOperationKind.CANCEL,
            )
            return OccurrenceSyncItemResult(
                op.series_uid,
                op.occurrence_key,
                op.op,
                True,
                reconciled=True,
            )

        merged = merge_complete_instance_payload(current, desired)
        merged["id"] = instance_id
        merged["etag"] = current.get("etag")
        merged["recurringEventId"] = current["recurringEventId"]
        merged["originalStartTime"] = dict(current["originalStartTime"])
        expected_etag = op.acknowledged_remote_etag or current_etag
        if op.op is OccurrenceOperationKind.CANCEL:
            remote = self._gateway.cancel_recurring_instance(
                instance_id, merged, expected_etag
            )
        else:
            remote = self._gateway.update_recurring_instance(
                instance_id, merged, expected_etag
            )
        validation = validate_remote_occurrence_payload(
            remote,
            expected_master_event_id=op.remote_master_event_id,
            expected_original_start=link.identity,
        )
        if not validation.ok:
            raise TerminalGatewayError(
                "Google returned an invalid recurring instance: " + validation.error
            )
        cancelled = str(remote.get("status") or "") == "cancelled"
        if op.op is OccurrenceOperationKind.CANCEL and not cancelled:
            raise RetryableGatewayError(
                "Google did not persist the recurring-instance cancellation"
            )
        remote_hash = canonical_occurrence_payload_fingerprint(remote)
        self._store.finalize_success(
            op,
            remote_instance_event_id=str(remote.get("id") or instance_id),
            remote_etag=str(remote.get("etag") or "") or None,
            remote_updated_at=_parse_updated(remote.get("updated")),
            local_hash=op.desired_payload_hash,
            remote_hash=remote_hash,
            cancelled=cancelled,
        )
        return OccurrenceSyncItemResult(
            op.series_uid, op.occurrence_key, op.op, True
        )

    def _supersede_after_race(
        self, op, remote_payload: Mapping[str, Any]
    ) -> None:
        payload = dict(remote_payload)
        instance_id = str(payload.get("id") or op.remote_instance_event_id or "")
        cancelled = str(payload.get("status") or "") == "cancelled"
        stamp = utc_now()
        self._store.upsert_occurrence_change(
            RemoteOccurrenceChange(
                provider="google",
                calendar_id=getattr(self._gateway, "calendar_id", "primary"),
                remote_master_event_id=op.remote_master_event_id,
                remote_instance_event_id=instance_id,
                original_start_value=op.original_start_value,
                status=str(payload.get("status") or "confirmed"),
                payload_json=json.dumps(
                    payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                remote_etag=str(payload.get("etag") or "") or None,
                remote_updated_at=_parse_updated(payload.get("updated")),
                first_seen_at=stamp,
                last_seen_at=stamp,
                matched_series_uid=op.series_uid,
                matched_occurrence_key=op.occurrence_key,
                resolution_status="unresolved",
            )
        )
        self._store.remove_op(op.id)
        self._store.record_remote_conflict(
            op.series_uid,
            op.occurrence_key,
            reason=(
                "Удалённый экземпляр изменился после подтверждения. "
                "Выберите разрешение конфликта ещё раз."
            ),
            snapshot=payload,
            remote_instance_event_id=instance_id or None,
            remote_etag=str(payload.get("etag") or "") or None,
            remote_updated_at=_parse_updated(payload.get("updated")),
            cancelled=cancelled,
        )


# Short name for callers that already say "series occurrence" in context.
CalendarOccurrenceSyncEngine = CalendarSeriesOccurrenceSyncEngine


__all__ = [
    "CalendarOccurrenceSyncEngine",
    "CalendarSeriesOccurrenceSyncEngine",
    "OccurrenceSyncItemResult",
    "OccurrenceSyncResult",
]
