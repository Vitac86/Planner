"""Manual push engine for one local TaskSeries <-> one Calendar master."""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from planner_desktop.domain.external_series import (
    EXTERNAL_START_ALL_DAY,
    EXTERNAL_START_TIMED,
    ExternalCalendarSeries,
)
from planner_desktop.domain.google_recurrence import parse_google_recurrence
from planner_desktop.domain.recurrence import SeriesSchedule
from planner_desktop.domain.series_calendar_link import (
    PLANNER_PAYLOAD_HASH_PROPERTY,
    PLANNER_SERIES_UID_PROPERTY,
    PendingSeriesSyncOp,
    SeriesLinkStatus,
    SeriesSyncItemResult,
    SeriesSyncOpKind,
    SeriesSyncResult,
)
from planner_desktop.domain.task import utc_now
from planner_desktop.sync.calendar_series_mapper import (
    master_event_to_owned_payload,
    master_payload_hash,
    remote_master_snapshot_json,
    series_to_master_event,
)
from planner_desktop.sync.sync_types import (
    CalendarEvent,
    RemoteMasterConflictError,
    RetryableGatewayError,
    TerminalGatewayError,
)
from planner_desktop.usecases.series_calendar_link_service import (
    finalize_local_series_delete,
)


class CalendarSeriesSyncEngine:
    def __init__(
        self,
        series_repository,
        task_repository,
        store,
        external_series_repository,
        gateway,
    ) -> None:
        self._series = series_repository
        self._tasks = task_repository
        self._store = store
        self._catalog = external_series_repository
        self._gateway = gateway
        self.last_result = SeriesSyncResult()

    def push_pending(self, limit: int = 50) -> SeriesSyncResult:
        result = SeriesSyncResult()
        for initial_op in self._store.list_due_ops(limit):
            op = initial_op
            try:
                op = self._refresh_latest_payload(op)
                item = self._push_op(op)
            except RetryableGatewayError as exc:
                remains_pending = self._store.requeue_op(op.id, str(exc))
                item = SeriesSyncItemResult(
                    op.series_uid,
                    op.op,
                    ok=False,
                    terminal=not remains_pending,
                    error=str(exc),
                )
                if not remains_pending:
                    result.terminal += 1
                    item.resolution_failed = self._fail_op_resolution(
                        op, str(exc)
                    )
            except TerminalGatewayError as exc:
                self._store.mark_terminal(op.id, str(exc))
                item = SeriesSyncItemResult(
                    op.series_uid,
                    op.op,
                    ok=False,
                    terminal=True,
                    error=str(exc),
                )
                result.terminal += 1
                item.resolution_failed = self._fail_op_resolution(op, str(exc))
            except RemoteMasterConflictError as exc:
                superseded = self._persist_conflict(op, exc.remote_event, str(exc))
                item = SeriesSyncItemResult(
                    op.series_uid,
                    op.op,
                    ok=False,
                    conflict=True,
                    error=str(exc),
                    resolution_superseded=superseded,
                )
                result.conflicts += 1
            result.items.append(item)
            if item.ok:
                if op.op is SeriesSyncOpKind.CREATE:
                    result.created += 1
                elif op.op is SeriesSyncOpKind.UPDATE:
                    result.updated += 1
                else:
                    result.deleted += 1
            result.resolved_keep_planner += int(item.resolved_keep_planner)
            result.resolution_superseded += int(item.resolution_superseded)
            result.resolution_failed += int(item.resolution_failed)
            result.remote_deleted_recreated += int(item.recreated)
        self.last_result = result
        return result

    def _fail_op_resolution(self, op: PendingSeriesSyncOp, error: str) -> bool:
        """A dead-lettered resolution op leaves a visible failed audit row."""
        if op.resolution_id is None:
            return False
        try:
            self._store.fail_resolution(op.resolution_id, error)
        except Exception:  # pragma: no cover - diagnostics must not mask push
            return False
        return True

    def _refresh_latest_payload(
        self, op: PendingSeriesSyncOp
    ) -> PendingSeriesSyncOp:
        if op.op is SeriesSyncOpKind.DELETE:
            return op
        series = self._series.get_by_uid(op.series_uid)
        if series is None or series.is_deleted:
            raise TerminalGatewayError(
                "Локальная серия отсутствует до завершения master write."
            )
        event = series_to_master_event(series)
        current_hash = master_payload_hash(event)
        if (
            current_hash != op.desired_payload_hash
            or series.revision != op.desired_revision
        ):
            self._store.enqueue_update(
                series.uid,
                desired_revision=series.revision,
                desired_payload_hash=current_hash,
                payload=master_event_to_owned_payload(event),
            )
            refreshed = self._store.get_pending_op(series.uid)
            if refreshed is not None:
                return refreshed
        return op

    def _push_op(self, op: PendingSeriesSyncOp) -> SeriesSyncItemResult:
        if op.op is SeriesSyncOpKind.DELETE:
            return self._push_delete(op)
        if op.op is SeriesSyncOpKind.UPDATE and op.resolution_id is not None:
            return self._push_keep_planner(op)
        series = self._series.get_by_uid(op.series_uid)
        if series is None or series.is_deleted:
            raise TerminalGatewayError("Локальная серия не найдена.")
        link = self._store.get_link(op.series_uid, include_detached=True)
        if link is None:
            raise TerminalGatewayError("Связь серии не найдена.")
        recreated = (
            op.op is SeriesSyncOpKind.CREATE and link.link_generation > 0
        )
        event = series_to_master_event(series)
        desired_hash = master_payload_hash(event)
        remote = self._gateway.get_recurring_master(link.remote_event_id)

        if remote is not None:
            self._verify_remote_owner(remote, series.uid, link.remote_event_id)
            remote_hash = remote.private_extended_properties.get(
                PLANNER_PAYLOAD_HASH_PROPERTY
            )
            if remote_hash == desired_hash:
                self._complete_write(op, link, remote, series.revision, desired_hash)
                return SeriesSyncItemResult(
                    op.series_uid, op.op, ok=True, reconciled=True,
                    recreated=recreated,
                )

        if op.op is SeriesSyncOpKind.CREATE:
            if remote is not None:
                raise RemoteMasterConflictError(
                    "Google-мастер этой серии уже существует, но отличается.",
                    remote,
                )
            written = self._gateway.insert_recurring_master(
                link.remote_event_id, event
            )
        else:
            if remote is None:
                raise RemoteMasterConflictError(
                    "Связанный мастер Google удалён.", None
                )
            if link.remote_etag and remote.etag != link.remote_etag:
                raise RemoteMasterConflictError(
                    "Google-мастер изменён вне Planner; перезапись остановлена.",
                    remote,
                )
            written = self._gateway.patch_recurring_master(
                link.remote_event_id,
                event,
                expected_etag=link.remote_etag,
            )

        self._complete_write(op, link, written, series.revision, desired_hash)
        return SeriesSyncItemResult(
            op.series_uid, op.op, ok=True, recreated=recreated
        )

    def _push_keep_planner(self, op: PendingSeriesSyncOp) -> SeriesSyncItemResult:
        """Explicit conflict overwrite with etag race protection.

        Sequence: load intent -> fetch current remote -> verify Planner
        ownership and series uid -> compare the *current* remote etag with the
        acknowledged conflict etag -> patch only when they still match.  A
        second remote edit never gets overwritten: the stored conflict base is
        refreshed and the stale decision becomes superseded.
        """
        link = self._store.get_link(op.series_uid, include_detached=True)
        if link is None or link.link_status is SeriesLinkStatus.DETACHED:
            raise TerminalGatewayError("Связь серии не найдена.")
        resolution = self._store.get_resolution(op.resolution_id)
        if resolution is None or not resolution.is_pending:
            # The audit row was superseded/failed elsewhere; the op is stale.
            self._store.remove_op(op.id)
            return SeriesSyncItemResult(
                op.series_uid, op.op, ok=False,
                error="Решение конфликта устарело; требуется новое решение.",
                resolution_superseded=True,
            )
        series = self._series.get_by_uid(op.series_uid)
        if series is None or series.is_deleted:
            raise TerminalGatewayError("Локальная серия не найдена.")

        remote = self._gateway.get_recurring_master(link.remote_event_id)
        if remote is None:
            message = "Связанный мастер Google удалён до перезаписи."
            self._store.mark_remote_deleted(op.series_uid, error=message)
            self._store.fail_resolution(op.resolution_id, message)
            return SeriesSyncItemResult(
                op.series_uid, op.op, ok=False, conflict=True, error=message,
                resolution_failed=True,
            )
        self._verify_remote_owner(remote, series.uid, link.remote_event_id)

        event = series_to_master_event(series)
        desired_hash = master_payload_hash(event)
        remote_hash = remote.private_extended_properties.get(
            PLANNER_PAYLOAD_HASH_PROPERTY
        )
        acknowledged = (
            op.acknowledged_remote_etag or resolution.acknowledged_remote_etag
        )
        if acknowledged and remote.etag == acknowledged:
            # The remote is still exactly the acknowledged conflict state:
            # this is the one situation where the explicit overwrite runs.
            written = self._gateway.patch_recurring_master(
                link.remote_event_id, event, expected_etag=acknowledged
            )
            self._finish_keep_planner(op, link, written, series, desired_hash)
            return SeriesSyncItemResult(
                op.series_uid, op.op, ok=True, resolved_keep_planner=True
            )

        if remote_hash == desired_hash and self._remote_matches_payload(
            remote, desired_hash
        ):
            # The etag moved past the acknowledged base, the Planner markers
            # carry the desired hash AND the actual remote content equals the
            # desired canonical payload: our own patch succeeded and only
            # local persistence crashed.  Finish persistence, never patch
            # twice.  The content check matters because a foreign edit does
            # not update private markers — stale markers alone must not be
            # mistaken for a completed overwrite.
            self._finish_keep_planner(op, link, remote, series, desired_hash)
            return SeriesSyncItemResult(
                op.series_uid, op.op, ok=True, reconciled=True,
                resolved_keep_planner=True,
            )

        # Race: Google changed again after the user decided (or the base was
        # never acknowledged).  Do not patch; refresh the conflict base and
        # require a new explicit decision.
        if self._catalog is not None:
            self._catalog.upsert(self._catalog_item(remote, link, remote_hash))
        message = (
            "Мастер Google изменён ещё раз после вашего решения; "
            "перезапись остановлена, выберите решение заново."
        )
        self._store.record_conflict(
            op.series_uid,
            reason=message,
            remote_etag=remote.etag,
            remote_payload_hash=remote_hash,
            remote_snapshot_json=remote_master_snapshot_json(remote),
            remote_updated_at=remote.updated_at,
        )
        return SeriesSyncItemResult(
            op.series_uid, op.op, ok=False, conflict=True, error=message,
            resolution_superseded=True,
        )

    @staticmethod
    def _remote_matches_payload(remote: CalendarEvent, desired_hash: str) -> bool:
        """True only when the remote's actual owned content is the desired
        canonical payload.  Any canonicalisation failure counts as a mismatch,
        which safely degrades to requiring a new user decision."""
        try:
            return master_payload_hash(remote) == desired_hash
        except (TypeError, ValueError):
            return False

    def _finish_keep_planner(
        self, op: PendingSeriesSyncOp, link, written: CalendarEvent,
        series, desired_hash: str,
    ) -> None:
        # Conflict clears only after both the remote update and the local
        # persistence succeed; the queue row is removed last so a crash here
        # replays into the no-second-patch reconciliation above.
        self._store.complete_conflict_resolution_link(
            op.series_uid,
            remote_etag=written.etag,
            remote_updated_at=written.updated_at,
            synced_revision=series.revision,
            synced_payload_hash=desired_hash,
            resolution_kind="keep_planner",
        )
        if self._catalog is not None:
            self._catalog.upsert(self._catalog_item(written, link, desired_hash))
        self._store.complete_resolution(
            op.resolution_id,
            local_revision_after=series.revision,
            remote_etag_after=written.etag,
        )
        self._store.remove_op(op.id)

    def _push_delete(self, op: PendingSeriesSyncOp) -> SeriesSyncItemResult:
        link = self._store.get_link(op.series_uid, include_detached=True)
        remote_event_id = op.remote_event_id or (
            link.remote_event_id if link is not None else None
        )
        if remote_event_id:
            remote = self._gateway.get_recurring_master(remote_event_id)
            if remote is not None:
                self._gateway.delete_recurring_master(remote_event_id)
            if self._catalog is not None:
                self._catalog.mark_deleted(
                    link.provider if link is not None else "google",
                    link.calendar_id if link is not None else "primary",
                    remote_event_id,
                    seen_at=utc_now(),
                )
        if op.payload.get("delete_local_after_remote"):
            finalize_local_series_delete(self._series, self._tasks, op.series_uid)
        if link is not None:
            self._store.set_link_status(
                op.series_uid, SeriesLinkStatus.DETACHED
            )
        self._store.remove_op(op.id)
        return SeriesSyncItemResult(op.series_uid, op.op, ok=True)

    @staticmethod
    def _verify_remote_owner(
        remote: CalendarEvent, series_uid: str, remote_event_id: str
    ) -> None:
        actual = remote.private_extended_properties.get(
            PLANNER_SERIES_UID_PROPERTY
        )
        if actual != series_uid:
            raise TerminalGatewayError(
                "Коллизия детерминированного Google event id "
                f"{remote_event_id}: маркер другой серии."
            )

    def _complete_write(
        self,
        op: PendingSeriesSyncOp,
        link,
        remote: CalendarEvent,
        revision: int,
        payload_hash: str,
    ) -> None:
        # Each step is intentionally independently retryable.  The queue row is
        # removed last, so a crash after the remote write can reconcile by id +
        # private markers without issuing a second mutation.
        self._store.set_link_status(
            op.series_uid,
            SeriesLinkStatus.SYNCED,
            remote_etag=remote.etag,
            remote_updated_at=remote.updated_at,
            synced_revision=revision,
            synced_payload_hash=payload_hash,
        )
        if self._catalog is not None:
            self._catalog.upsert(self._catalog_item(remote, link, payload_hash))
        if op.resolution_id is not None:
            # A generation-recreate CREATE carries its audit id; a successful
            # write completes the recovery audit before the queue row goes.
            self._store.complete_resolution(
                op.resolution_id,
                local_revision_after=revision,
                remote_etag_after=remote.etag,
            )
        self._store.remove_op(op.id)

    def _persist_conflict(
        self,
        op: PendingSeriesSyncOp,
        remote: Optional[CalendarEvent],
        message: str,
    ) -> bool:
        """Persist the durable conflict base; return True when a pending
        explicit resolution was superseded/failed by this event."""
        link = self._store.get_link(op.series_uid, include_detached=True)
        had_resolution = op.resolution_id is not None
        if link is None:
            self._store.mark_terminal(op.id, message)
            return False
        if remote is None:
            self._store.mark_remote_deleted(op.series_uid, error=message)
            if had_resolution:
                self._store.fail_resolution(op.resolution_id, message)
            self._store.remove_op(op.id)
            return had_resolution
        remote_hash = remote.private_extended_properties.get(
            PLANNER_PAYLOAD_HASH_PROPERTY
        )
        if self._catalog is not None:
            self._catalog.upsert(self._catalog_item(remote, link, remote_hash))
        # Conflict is a durable link state, not a retrying write: the queue
        # row disappears with the transaction and the complete remote snapshot
        # becomes the acknowledged base for an explicit user decision.
        self._store.record_conflict(
            op.series_uid,
            reason=message,
            remote_etag=remote.etag,
            remote_payload_hash=remote_hash,
            remote_snapshot_json=remote_master_snapshot_json(remote),
            remote_updated_at=remote.updated_at,
        )
        self._store.remove_op(op.id)
        return had_resolution

    @staticmethod
    def _schedule_for(remote: CalendarEvent) -> Optional[SeriesSchedule]:
        start = remote.recurrence_start or remote.start
        if start is None:
            return None
        if remote.is_all_day:
            day = (
                start
                if isinstance(start, date) and not isinstance(start, datetime)
                else start.date()
            )
            return SeriesSchedule(
                start_date=day,
                all_day=True,
                timezone_name=remote.start_timezone or "UTC",
            )
        if not isinstance(start, datetime):
            return None
        duration = None
        if isinstance(remote.end, datetime):
            duration = int((remote.end - remote.start).total_seconds() // 60)
        return SeriesSchedule(
            start_date=start.date(),
            all_day=False,
            local_time=start.time().replace(tzinfo=None),
            duration_minutes=duration,
            timezone_name=remote.start_timezone or "UTC",
        )

    def _catalog_item(
        self, remote: CalendarEvent, link, payload_hash: Optional[str]
    ) -> ExternalCalendarSeries:
        schedule = self._schedule_for(remote)
        parsed = parse_google_recurrence(
            remote.recurrence_lines, schedule=schedule
        )
        start = remote.recurrence_start or remote.start
        return ExternalCalendarSeries(
            provider=link.provider,
            calendar_id=link.calendar_id,
            remote_event_id=link.remote_event_id,
            etag=remote.etag,
            title=remote.summary,
            description=remote.description,
            start_kind=(
                EXTERNAL_START_ALL_DAY
                if remote.is_all_day
                else EXTERNAL_START_TIMED
            ),
            start_value=start.isoformat() if start is not None else "",
            end_value=remote.end.isoformat() if remote.end is not None else "",
            timezone_name=remote.start_timezone,
            recurrence_lines=remote.recurrence_lines,
            parsed_rule=parsed.planner_rule,
            support_status=parsed.support.value,
            unsupported_reason=parsed.readable_reason or None,
            remote_status=remote.status,
            remote_updated_at=remote.updated_at,
            first_seen_at=utc_now(),
            last_seen_at=utc_now(),
            planner_owned=True,
            linked_series_uid=link.series_uid,
            planner_payload_hash=payload_hash,
        )


__all__ = ["CalendarSeriesSyncEngine"]
