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
            except RemoteMasterConflictError as exc:
                self._persist_conflict(op, exc.remote_event, str(exc))
                item = SeriesSyncItemResult(
                    op.series_uid,
                    op.op,
                    ok=False,
                    conflict=True,
                    error=str(exc),
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
        self.last_result = result
        return result

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
        series = self._series.get_by_uid(op.series_uid)
        if series is None or series.is_deleted:
            raise TerminalGatewayError("Локальная серия не найдена.")
        link = self._store.get_link(op.series_uid, include_detached=True)
        if link is None:
            raise TerminalGatewayError("Связь серии не найдена.")
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
                    op.series_uid, op.op, ok=True, reconciled=True
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
                self._store.set_link_status(
                    op.series_uid,
                    SeriesLinkStatus.REMOTE_DELETED,
                    error="Связанный мастер Google удалён.",
                )
                self._store.remove_op(op.id)
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
        return SeriesSyncItemResult(op.series_uid, op.op, ok=True)

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
        self._store.remove_op(op.id)

    def _persist_conflict(
        self,
        op: PendingSeriesSyncOp,
        remote: Optional[CalendarEvent],
        message: str,
    ) -> None:
        link = self._store.get_link(op.series_uid, include_detached=True)
        if link is None:
            self._store.mark_terminal(op.id, message)
            return
        if remote is None:
            status = SeriesLinkStatus.REMOTE_DELETED
        else:
            status = SeriesLinkStatus.CONFLICT
            if self._catalog is not None:
                remote_hash = remote.private_extended_properties.get(
                    PLANNER_PAYLOAD_HASH_PROPERTY
                )
                self._catalog.upsert(self._catalog_item(remote, link, remote_hash))
        self._store.set_link_status(
            op.series_uid,
            status,
            error=message,
            remote_etag=remote.etag if remote is not None else None,
            remote_updated_at=remote.updated_at if remote is not None else None,
        )
        # Conflict is a durable link state, not a retrying write.  B3 will own
        # user-directed resolution; leaving a pending overwrite would be unsafe.
        self._store.remove_op(op.id)

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
