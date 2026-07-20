"""Explicit, local-only conflict/remote-deleted resolution use cases.

Phase 3.2B3A.  No method here builds a gateway or performs network access:
"Keep Planner" only queues one explicit conflict-resolution UPDATE and
"Recreate in Google" queues one generation-specific CREATE — both execute
during the next *manual* sync.  "Use Google", disconnect and remote-deleted
keep-local/delete-local finish entirely in local SQLite state.
"""
from __future__ import annotations

import logging
from copy import deepcopy
from dataclasses import dataclass
from datetime import date
from typing import Any, Callable, List, Optional

from planner_desktop.domain.recurrence import describe_rule, replace_series
from planner_desktop.domain.series_calendar_link import (
    SeriesCalendarLink,
    SeriesLinkStatus,
    readable_series_link_status,
)
from planner_desktop.domain.series_conflict_resolution import (
    AcceptedRemoteSeriesState,
    ConflictResolutionKind,
    ConflictResolutionProposal,
    ConflictResolutionStatus,
    ConflictResolutionValidation,
    RemoteDeletedRecoveryKind,
    SeriesConflictResolution,
    evaluate_use_google,
    next_link_generation_proposal,
    readable_resolution_kind,
    readable_resolution_status,
    snapshot_is_all_day,
    snapshot_recurrence_lines,
    snapshot_schedule,
    snapshot_series_uid_marker,
    validate_disconnect,
    validate_keep_planner,
    validate_remote_deleted_recovery,
)
from planner_desktop.domain.google_recurrence import (
    parse_google_recurrence,
    readable_google_recurrence_summary,
)
from planner_desktop.sync.calendar_series_mapper import (
    master_event_to_owned_payload,
    master_payload_hash,
    series_to_master_event,
)
from planner_desktop.usecases.recurrence_service import slot_date_from_key
from planner_desktop.usecases.series_calendar_link_service import (
    finalize_local_series_delete,
)

logger = logging.getLogger(__name__)

CONFIRMATION_REQUIRED_ERROR = (
    "Действие требует явного подтверждения пользователя."
)


@dataclass
class SeriesConflictActionResult:
    ok: bool
    changed: bool = False
    error: str = ""
    validation: Optional[ConflictResolutionValidation] = None
    resolution: Optional[SeriesConflictResolution] = None
    link: Optional[SeriesCalendarLink] = None


class SeriesConflictService:
    """User-directed resolution; every mutation ends at local schema-v9 rows."""

    def __init__(
        self,
        series_repository,
        task_repository,
        store,
        *,
        today_provider: Callable[[], date] = date.today,
    ) -> None:
        self.series_repository = series_repository
        self.task_repository = task_repository
        self.store = store
        self._today = today_provider
        # Phase 3.2B3C1: while a remote split plan is active for the same
        # master, explicit conflict resolution is blocked unless the split
        # plan itself entered its conflict state first.
        self.remote_split_service = None

    def _split_block_error(self, series_uid: str) -> str:
        service = self.remote_split_service
        if service is None:
            return ""
        try:
            allowed = service.allows_conflict_resolution(series_uid)
        except Exception:
            return ""
        if allowed:
            return ""
        from planner_desktop.usecases.remote_series_split_service import (
            SPLIT_CONFLICT_RESOLUTION_BLOCKED,
        )
        return SPLIT_CONFLICT_RESOLUTION_BLOCKED

    # ---- read-only conflict data for the UI ---------------------------------

    def get_conflict(self, series_uid: str) -> dict[str, Any]:
        link = self.store.get_link(series_uid)
        series = self.series_repository.get_by_uid(series_uid)
        base: dict[str, Any] = {
            "seriesUid": series_uid,
            "available": False,
            "status": link.link_status.value if link is not None else "",
            "statusText": readable_series_link_status(
                link.link_status if link is not None else None
            ),
        }
        if link is None or series is None:
            return base
        base["available"] = True
        base["linkGeneration"] = link.link_generation
        base["conflictReason"] = link.conflict_reason or link.last_error or ""
        base["local"] = self._local_side(series)
        snapshot = link.conflict_remote_snapshot
        base["remote"] = self._remote_side(snapshot, link)
        marker = (
            snapshot_series_uid_marker(snapshot) if snapshot is not None else None
        )
        ownership_ok = marker == series.uid
        base["ownershipOk"] = ownership_ok
        base["ownershipText"] = (
            "Мастер принадлежит этой серии Planner (маркеры совпадают)."
            if ownership_ok
            else "Владелец мастера не подтверждён; перезапись запрещена."
        )
        base["acknowledgedRemoteEtag"] = link.conflict_remote_etag or ""

        keep = validate_keep_planner(
            series=series,
            link=link,
            snapshot=snapshot,
            acknowledged_remote_etag=link.conflict_remote_etag,
            pending_op=self.store.get_pending_op(series_uid),
        )
        use, _accepted = evaluate_use_google(
            series=series, link=link, snapshot=snapshot
        )
        disconnect = validate_disconnect(link=link)
        base["canKeepPlanner"] = keep.ok
        base["keepPlannerErrors"] = list(keep.errors)
        base["canUseGoogle"] = use.ok
        base["useGoogleErrors"] = list(use.errors)
        base["canDisconnect"] = disconnect.ok
        pending = self.store.get_pending_resolution(series_uid)
        base["pendingResolutionKind"] = (
            pending.resolution_kind if pending is not None else ""
        )
        base["pendingResolutionText"] = (
            readable_resolution_kind(pending.resolution_kind)
            if pending is not None
            else ""
        )
        return base

    def get_remote_deleted(self, series_uid: str) -> dict[str, Any]:
        link = self.store.get_link(series_uid)
        series = self.series_repository.get_by_uid(series_uid)
        data: dict[str, Any] = {
            "seriesUid": series_uid,
            "available": (
                link is not None
                and link.link_status is SeriesLinkStatus.REMOTE_DELETED
            ),
            "statusText": readable_series_link_status(
                link.link_status if link is not None else None
            ),
            "linkGeneration": link.link_generation if link is not None else 0,
            "nextGeneration": self.store.max_link_generation(series_uid) + 1,
            "reappeared": (
                link is not None and link.conflict_reason == "remote_reappeared"
            ),
            "title": series.title if series is not None else "",
            "canRecreate": False,
            "canDeleteLocal": False,
        }
        if link is None:
            return data
        recreate = validate_remote_deleted_recovery(
            kind=RemoteDeletedRecoveryKind.RECREATE, series=series, link=link
        )
        delete_local = validate_remote_deleted_recovery(
            kind=RemoteDeletedRecoveryKind.DELETE_LOCAL, series=series, link=link
        )
        data["canRecreate"] = recreate.ok
        data["recreateErrors"] = list(recreate.errors)
        data["canDeleteLocal"] = delete_local.ok
        return data

    # ---- Keep Planner --------------------------------------------------------

    def propose_keep_planner(self, series_uid: str) -> ConflictResolutionProposal:
        series = self.series_repository.get_by_uid(series_uid)
        link = self.store.get_link(series_uid)
        snapshot = link.conflict_remote_snapshot if link is not None else None
        validation = validate_keep_planner(
            series=series,
            link=link,
            snapshot=snapshot,
            acknowledged_remote_etag=(
                link.conflict_remote_etag if link is not None else None
            ),
            pending_op=self.store.get_pending_op(series_uid),
        )
        desired_revision = None
        desired_hash = None
        if validation.ok and series is not None:
            event = series_to_master_event(series)
            desired_revision = series.revision
            desired_hash = master_payload_hash(event)
        return ConflictResolutionProposal(
            series_uid=series_uid,
            kind=ConflictResolutionKind.KEEP_PLANNER,
            validation=validation,
            acknowledged_remote_etag=(
                link.conflict_remote_etag if link is not None else None
            ),
            desired_revision=desired_revision,
            desired_payload_hash=desired_hash,
        )

    def resolve_keep_planner(
        self, series_uid: str, *, confirmed: bool = False
    ) -> SeriesConflictActionResult:
        block = self._split_block_error(series_uid)
        if block:
            return SeriesConflictActionResult(ok=False, error=block)
        proposal = self.propose_keep_planner(series_uid)
        if not proposal.ok:
            return SeriesConflictActionResult(
                ok=False,
                validation=proposal.validation,
                error="\n".join(proposal.validation.errors),
                link=self.store.get_link(series_uid),
            )
        if not confirmed:
            return SeriesConflictActionResult(
                ok=False, error=CONFIRMATION_REQUIRED_ERROR
            )
        link = self.store.get_link(series_uid)
        existing = self.store.get_pending_resolution(
            series_uid, kind=ConflictResolutionKind.KEEP_PLANNER.value
        )
        pending_op = self.store.get_pending_op(series_uid)
        if (
            existing is not None
            and pending_op is not None
            and pending_op.resolution_id == existing.id
        ):
            # Duplicate button press: keep exactly one queue row and one audit.
            return SeriesConflictActionResult(
                ok=True, changed=False, resolution=existing, link=link
            )
        series = self.series_repository.get_by_uid(series_uid)
        event = series_to_master_event(series)
        payload = master_event_to_owned_payload(event)
        resolution = self.store.add_resolution(SeriesConflictResolution(
            series_uid=series_uid,
            link_id=int(link.id),
            resolution_kind=ConflictResolutionKind.KEEP_PLANNER.value,
            local_revision_before=series.revision,
            remote_etag_before=link.conflict_remote_etag,
            remote_payload_hash=link.conflict_remote_payload_hash,
            acknowledged_remote_etag=link.conflict_remote_etag,
        ))
        queued = self.store.enqueue_conflict_resolution_update(
            series_uid,
            desired_revision=series.revision,
            desired_payload_hash=master_payload_hash(event),
            payload=payload,
            acknowledged_remote_etag=str(link.conflict_remote_etag),
            resolution_id=int(resolution.id),
        )
        if not queued:
            self.store.fail_resolution(
                int(resolution.id),
                "Не удалось поставить операцию разрешения в очередь.",
            )
            return SeriesConflictActionResult(
                ok=False,
                error="Не удалось поставить операцию разрешения в очередь.",
                link=self.store.get_link(series_uid),
            )
        return SeriesConflictActionResult(
            ok=True,
            changed=True,
            resolution=self.store.get_resolution(int(resolution.id)),
            link=self.store.get_link(series_uid),
        )

    # ---- Use Google ----------------------------------------------------------

    def resolve_use_google(
        self, series_uid: str, *, confirmed: bool = False
    ) -> SeriesConflictActionResult:
        block = self._split_block_error(series_uid)
        if block:
            return SeriesConflictActionResult(ok=False, error=block)
        series = self.series_repository.get_by_uid(series_uid)
        link = self.store.get_link(series_uid)
        snapshot = link.conflict_remote_snapshot if link is not None else None
        validation, accepted_state = evaluate_use_google(
            series=series, link=link, snapshot=snapshot
        )
        if not validation.ok or accepted_state is None:
            return SeriesConflictActionResult(
                ok=False,
                validation=validation,
                error="\n".join(validation.errors),
                link=link,
            )
        if not confirmed:
            return SeriesConflictActionResult(
                ok=False, error=CONFIRMATION_REQUIRED_ERROR
            )

        accepted = replace_series(
            series,
            title=accepted_state.title,
            notes=accepted_state.notes,
            schedule=accepted_state.schedule,
            rule=accepted_state.rule,
            revision=series.revision + 1,
        )
        replaceable = self._replaceable_future_occurrences(series_uid)
        resolution = self.store.add_resolution(SeriesConflictResolution(
            series_uid=series_uid,
            link_id=int(link.id),
            resolution_kind=ConflictResolutionKind.USE_GOOGLE.value,
            local_revision_before=series.revision,
            remote_etag_before=link.conflict_remote_etag,
            remote_payload_hash=link.conflict_remote_payload_hash,
        ))
        try:
            self._apply_use_google(link, resolution, accepted, accepted_state,
                                   replaceable)
        except Exception as exc:
            logger.exception("Use-Google acceptance failed")
            try:
                self.store.fail_resolution(int(resolution.id), str(exc))
            except Exception:
                pass
            return SeriesConflictActionResult(
                ok=False,
                error=f"Не удалось применить версию Google: {exc}",
                link=self.store.get_link(series_uid),
                resolution=self.store.get_resolution(int(resolution.id)),
            )
        return SeriesConflictActionResult(
            ok=True,
            changed=True,
            resolution=self.store.get_resolution(int(resolution.id)),
            link=self.store.get_link(series_uid),
        )

    def _apply_use_google(
        self,
        link: SeriesCalendarLink,
        resolution: SeriesConflictResolution,
        accepted,
        accepted_state: AcceptedRemoteSeriesState,
        replaceable,
    ) -> None:
        remote_updated_text = (
            accepted_state.remote_updated_at.isoformat()
            if accepted_state.remote_updated_at is not None
            else None
        )
        atomic = getattr(
            self.series_repository, "accept_remote_master_atomic", None
        )
        if callable(atomic):
            atomic(
                accepted=accepted,
                removed_task_uids=[task.uid for task in replaceable],
                link_id=int(link.id),
                resolution_id=int(resolution.id),
                remote_etag=accepted_state.remote_etag,
                remote_updated_at_text=remote_updated_text,
                synced_payload_hash=accepted_state.remote_payload_hash,
            )
            return
        # Compensation semantics for in-memory repositories: every mutated
        # row is snapshotted first and restored on any failure.
        original_series = replace_series(
            self.series_repository.get_by_uid(accepted.uid)
        )
        removed: list = []
        try:
            self.series_repository.update(accepted)
            for task in replaceable:
                snapshot_row = deepcopy(task)
                if self.task_repository.hard_delete_by_uid(task.uid):
                    removed.append(snapshot_row)
            self.store.complete_use_google_locally(
                accepted.uid,
                int(resolution.id),
                remote_etag=accepted_state.remote_etag,
                remote_updated_at=accepted_state.remote_updated_at,
                synced_revision=accepted.revision,
                synced_payload_hash=accepted_state.remote_payload_hash,
                local_revision_after=accepted.revision,
            )
        except Exception:
            try:
                self.series_repository.update(original_series)
            except Exception:
                pass
            for row in removed:
                try:
                    if self.task_repository.get_by_uid(row.uid) is None:
                        self.task_repository.add(row)
                except Exception:
                    continue
            raise

    # ---- disconnect and remote-deleted recovery ------------------------------

    def resolve_disconnect(self, series_uid: str) -> SeriesConflictActionResult:
        block = self._split_block_error(series_uid)
        if block:
            return SeriesConflictActionResult(ok=False, error=block)
        link = self.store.get_link(series_uid)
        validation = validate_disconnect(link=link)
        if not validation.ok:
            return SeriesConflictActionResult(
                ok=False,
                validation=validation,
                error="\n".join(validation.errors),
                link=link,
            )
        series = self.series_repository.get_by_uid(series_uid)
        kind = (
            ConflictResolutionKind.DISCONNECT.value
            if link.link_status is SeriesLinkStatus.CONFLICT
            else ConflictResolutionKind.KEEP_LOCAL.value
        )
        return self._detach_with_audit(series_uid, link, series, kind)

    def recover_remote_deleted_keep_local(
        self, series_uid: str
    ) -> SeriesConflictActionResult:
        link = self.store.get_link(series_uid)
        series = self.series_repository.get_by_uid(series_uid)
        validation = validate_remote_deleted_recovery(
            kind=RemoteDeletedRecoveryKind.KEEP_LOCAL, series=series, link=link
        )
        if not validation.ok:
            return SeriesConflictActionResult(
                ok=False,
                validation=validation,
                error="\n".join(validation.errors),
                link=link,
            )
        return self._detach_with_audit(
            series_uid, link, series, ConflictResolutionKind.KEEP_LOCAL.value
        )

    def _detach_with_audit(
        self, series_uid: str, link, series, kind: str
    ) -> SeriesConflictActionResult:
        changed = self.store.detach_link_resolved(
            series_uid, resolution_kind=kind
        )
        if not changed:
            return SeriesConflictActionResult(
                ok=False, error="Активная связь серии не найдена."
            )
        resolution = self.store.add_resolution(SeriesConflictResolution(
            series_uid=series_uid,
            link_id=int(link.id),
            resolution_kind=kind,
            status=ConflictResolutionStatus.COMPLETED.value,
            local_revision_before=(series.revision if series is not None else 0),
            local_revision_after=(series.revision if series is not None else None),
            remote_etag_before=link.conflict_remote_etag or link.remote_etag,
            completed_at=self.store._clock(),
        ))
        return SeriesConflictActionResult(
            ok=True,
            changed=True,
            resolution=resolution,
            link=self.store.get_link(series_uid, include_detached=True),
        )

    def recover_remote_deleted_recreate(
        self, series_uid: str, *, confirmed: bool = False
    ) -> SeriesConflictActionResult:
        link = self.store.get_link(series_uid)
        series = self.series_repository.get_by_uid(series_uid)
        # Idempotency across rapid duplicate presses: a recreation already in
        # flight (new generation pending CREATE) is returned, never repeated.
        if (
            link is not None
            and link.link_status is SeriesLinkStatus.PENDING_CREATE
            and link.link_generation > 0
        ):
            return SeriesConflictActionResult(
                ok=True,
                changed=False,
                link=link,
                resolution=self.store.get_pending_resolution(
                    series_uid, kind=ConflictResolutionKind.RECREATE.value
                ),
            )
        validation = validate_remote_deleted_recovery(
            kind=RemoteDeletedRecoveryKind.RECREATE, series=series, link=link
        )
        if not validation.ok:
            return SeriesConflictActionResult(
                ok=False,
                validation=validation,
                error="\n".join(validation.errors),
                link=link,
            )
        if not confirmed:
            return SeriesConflictActionResult(
                ok=False, error=CONFIRMATION_REQUIRED_ERROR
            )
        proposal = next_link_generation_proposal(
            series_uid,
            [self.store.max_link_generation(series_uid)],
        )
        event = series_to_master_event(series)
        try:
            new_link, resolution = self.store.recreate_link_generation(
                series_uid,
                generation=proposal.generation,
                remote_event_id=proposal.remote_event_id,
                desired_revision=series.revision,
                desired_payload_hash=master_payload_hash(event),
                payload=master_event_to_owned_payload(event),
                local_revision_before=series.revision,
            )
        except Exception as exc:
            return SeriesConflictActionResult(
                ok=False,
                error=f"Не удалось создать новое поколение связи: {exc}",
                link=self.store.get_link(series_uid),
            )
        return SeriesConflictActionResult(
            ok=True, changed=True, link=new_link, resolution=resolution
        )

    def delete_remote_deleted_local_series(
        self, series_uid: str, *, confirmed: bool = False
    ) -> SeriesConflictActionResult:
        link = self.store.get_link(series_uid)
        series = self.series_repository.get_by_uid(series_uid)
        validation = validate_remote_deleted_recovery(
            kind=RemoteDeletedRecoveryKind.DELETE_LOCAL, series=series, link=link
        )
        if not validation.ok:
            return SeriesConflictActionResult(
                ok=False,
                validation=validation,
                error="\n".join(validation.errors),
                link=link,
            )
        if not confirmed:
            return SeriesConflictActionResult(
                ok=False, error=CONFIRMATION_REQUIRED_ERROR
            )
        try:
            # The master is already absent: existing safe local deletion only,
            # no Google operation is queued.
            finalize_local_series_delete(
                self.series_repository, self.task_repository, series_uid
            )
        except Exception as exc:
            return SeriesConflictActionResult(
                ok=False,
                error=f"Не удалось удалить локальную серию: {exc}",
                link=link,
            )
        return self._detach_with_audit(
            series_uid, link, series, ConflictResolutionKind.DELETE_LOCAL.value
        )

    # ---- history and diagnostics ---------------------------------------------

    def list_resolution_history(
        self, series_uid: Optional[str] = None
    ) -> List[SeriesConflictResolution]:
        return self.store.list_resolutions(series_uid)

    # ---- internal --------------------------------------------------------------

    def _replaceable_future_occurrences(self, series_uid: str):
        """Future live, uncompleted, non-exception rows; history/exceptions/
        tombstones are never replaced."""
        today = self._today()
        rows = []
        for row in self.task_repository.list_by_series(series_uid):
            if row.is_deleted or row.completed or row.is_series_exception:
                continue
            slot = slot_date_from_key(row.occurrence_key)
            if slot is None or slot < today:
                continue
            rows.append(row)
        return rows

    def _local_side(self, series) -> dict[str, Any]:
        schedule = series.schedule
        return {
            "title": series.title,
            "notesPresent": bool((series.notes or "").strip()),
            "allDay": schedule.all_day,
            "formText": "Весь день" if schedule.all_day else "Со временем",
            "startDate": schedule.start_date.isoformat(),
            "startTime": (
                schedule.local_time.strftime("%H:%M")
                if schedule.local_time is not None
                else ""
            ),
            "durationMinutes": schedule.duration_minutes or 0,
            "timezone": schedule.timezone_name,
            "ruleSummary": describe_rule(series.rule, schedule),
            "revision": series.revision,
        }

    def _remote_side(self, snapshot, link) -> dict[str, Any]:
        if snapshot is None:
            return {
                "available": False,
                "title": "",
                "summaryText": "Снимок мастера Google недоступен; выполните "
                               "ручную синхронизацию.",
            }
        schedule_result = snapshot_schedule(snapshot)
        schedule = schedule_result.schedule
        lines = snapshot_recurrence_lines(snapshot)
        parsed = parse_google_recurrence(lines, schedule=schedule)
        all_day = snapshot_is_all_day(snapshot)
        start = snapshot.get("start") or {}
        return {
            "available": True,
            "title": str(snapshot.get("summary") or ""),
            "notesPresent": bool(str(snapshot.get("description") or "").strip()),
            "allDay": bool(all_day),
            "formText": (
                "Весь день" if all_day
                else ("Со временем" if all_day is False else "Неизвестно")
            ),
            "startDate": (
                schedule.start_date.isoformat() if schedule is not None
                else str(start.get("date") or start.get("dateTime") or "")
            ),
            "startTime": (
                schedule.local_time.strftime("%H:%M")
                if schedule is not None and schedule.local_time is not None
                else ""
            ),
            "durationMinutes": (
                schedule.duration_minutes or 0 if schedule is not None else 0
            ),
            "timezone": (
                schedule.timezone_name if schedule is not None
                else str(start.get("timeZone") or "")
            ),
            "ruleSummary": readable_google_recurrence_summary(
                parsed, schedule=schedule
            ),
            "rawRecurrence": list(lines),
            "supported": parsed.supported,
            "unsupportedReason": parsed.readable_reason,
            "etag": str(snapshot.get("etag") or ""),
            "updatedAt": str(snapshot.get("updated_at") or ""),
        }


__all__ = [
    "CONFIRMATION_REQUIRED_ERROR",
    "SeriesConflictActionResult",
    "SeriesConflictService",
]
