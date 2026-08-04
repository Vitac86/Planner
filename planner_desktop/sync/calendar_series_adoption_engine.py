"""Durable external recurring-master adoption inside explicit manual sync."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from planner_desktop.domain.external_series import (
    EXTERNAL_START_ALL_DAY,
    EXTERNAL_START_TIMED,
    ExternalCalendarSeries,
)
from planner_desktop.domain.google_recurrence import parse_google_recurrence
from planner_desktop.domain.google_series_split import master_content_fingerprint
from planner_desktop.domain.recurrence import SeriesSchedule, TaskSeries
from planner_desktop.domain.series_adoption import (
    ADOPTED_EXTERNAL_ORIGIN,
    PLANNER_ADOPTION_ID_PROPERTY,
    PLANNER_LINK_ORIGIN_PROPERTY,
    PLANNER_SERIES_UID_PROPERTY,
    CalendarSeriesAdoption,
    SeriesAdoptionState,
    evaluate_series_adoption,
    planner_adoption_private_properties,
)
from planner_desktop.sync.calendar_series_mapper import (
    master_event_to_owned_payload,
    master_payload_hash,
    series_to_master_event,
)
from planner_desktop.sync.google_calendar_gateway import payload_to_event
from planner_desktop.sync.sync_types import (
    CalendarEvent,
    RemoteMasterConflictError,
    RetryableGatewayError,
    TerminalGatewayError,
)


@dataclass
class SeriesAdoptionSyncResult:
    adoptions_started: int = 0
    ownership_claims_written: int = 0
    adoptions_finalized: int = 0
    conflicts: int = 0
    rollbacks: int = 0
    releases: int = 0
    terminal: int = 0
    reconciliations: int = 0


def _canonical_snapshot(event: CalendarEvent) -> str:
    return json.dumps(
        event.raw_payload or master_event_to_owned_payload(event),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _event_duration_minutes(event: CalendarEvent) -> Optional[int]:
    if event.start is None or event.end is None:
        return None
    try:
        if event.is_all_day:
            return int((event.end - event.start).days * 24 * 60)
        return int((event.end - event.start).total_seconds() // 60)
    except (AttributeError, TypeError):
        return None


def _catalog_from_event(
    event: CalendarEvent,
    *,
    calendar_id: str,
    access_role: str,
    exception_dates: Sequence[str] = (),
) -> ExternalCalendarSeries:
    schedule = CalendarSeriesAdoptionEngine.event_schedule(event)
    parsed = parse_google_recurrence(
        event.recurrence_lines, schedule=schedule
    )
    start = event.recurrence_start or event.start
    raw = dict(event.raw_payload or {})
    return ExternalCalendarSeries(
        provider="google",
        calendar_id=calendar_id,
        remote_event_id=str(event.id or ""),
        etag=event.etag,
        title=event.summary,
        description=event.description,
        start_kind=(
            EXTERNAL_START_ALL_DAY if event.is_all_day else EXTERNAL_START_TIMED
        ),
        start_value=start.isoformat() if start is not None else "",
        end_value=event.end.isoformat() if event.end is not None else "",
        timezone_name=event.start_timezone,
        recurrence_lines=event.recurrence_lines,
        parsed_rule=parsed.planner_rule,
        support_status=parsed.support.value,
        unsupported_reason=parsed.readable_reason or None,
        remote_status=event.status,
        planner_owned=bool(
            event.private_extended_properties.get(PLANNER_SERIES_UID_PROPERTY)
        ),
        raw_snapshot_json=json.dumps(
            raw,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
        private_properties_json=json.dumps(
            event.private_extended_properties,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
        calendar_access_role=access_role,
        organizer_self=bool((raw.get("organizer") or {}).get("self")),
        event_type=str(raw.get("eventType") or "default"),
        locked=bool(raw.get("locked")),
        exception_count=len(exception_dates),
        exception_dates_json=json.dumps(
            list(exception_dates), ensure_ascii=False, separators=(",", ":")
        ),
        duration_minutes=_event_duration_minutes(event),
    )


class CalendarSeriesAdoptionEngine:
    """Processes active adoption/release plans before every other sync queue."""

    def __init__(
        self,
        adoption_store,
        series_repository,
        task_repository,
        series_store,
        external_series_repository,
        gateway,
    ) -> None:
        self.store = adoption_store
        self.series_repository = series_repository
        self.task_repository = task_repository
        self.series_store = series_store
        self.catalog = external_series_repository
        self.gateway = gateway

    @staticmethod
    def event_schedule(event: CalendarEvent) -> Optional[SeriesSchedule]:
        start = event.recurrence_start or event.start
        if start is None:
            return None
        if event.is_all_day:
            start_day = (
                start
                if isinstance(start, date) and not isinstance(start, datetime)
                else start.date()
            )
            return SeriesSchedule(
                start_date=start_day,
                all_day=True,
                duration_minutes=24 * 60,
                timezone_name=event.start_timezone or "UTC",
            )
        if not isinstance(start, datetime) or not event.start_timezone:
            return None
        return SeriesSchedule(
            start_date=start.date(),
            all_day=False,
            local_time=start.time().replace(tzinfo=None),
            duration_minutes=_event_duration_minutes(event),
            timezone_name=event.start_timezone,
        )

    @staticmethod
    def series_from_event(
        event: CalendarEvent, reserved_uid: str
    ) -> TaskSeries:
        schedule = CalendarSeriesAdoptionEngine.event_schedule(event)
        parsed = parse_google_recurrence(
            event.recurrence_lines, schedule=schedule
        )
        if schedule is None or not parsed.supported or parsed.planner_rule is None:
            raise ValueError("Remote master no longer maps losslessly to TaskSeries.")
        return TaskSeries(
            uid=reserved_uid,
            title=event.summary,
            notes=event.description,
            priority=0,
            tags=(),
            schedule=schedule,
            rule=parsed.planner_rule,
            revision=1,
            active=True,
        )

    @staticmethod
    def _bound(raw: Mapping[str, Any]) -> Optional[Any]:
        if raw.get("date"):
            return date.fromisoformat(str(raw["date"]))
        if raw.get("dateTime"):
            return datetime.fromisoformat(
                str(raw["dateTime"]).replace("Z", "+00:00")
            )
        return None

    @classmethod
    def exception_dates(
        cls,
        master: CalendarEvent,
        instances: Sequence[Mapping[str, Any]],
    ) -> tuple[str, ...]:
        """Return only changed/cancelled instances, not generated normal slots."""
        master_raw = master.raw_payload or {}
        master_duration = _event_duration_minutes(master)
        changed: list[str] = []
        for item in instances:
            original_raw = item.get("originalStartTime") or {}
            original = cls._bound(original_raw)
            if original is None:
                changed.append(str(item.get("id") or "unknown"))
                continue
            key = (
                original.date().isoformat()
                if isinstance(original, datetime)
                else original.isoformat()
            )
            is_changed = str(item.get("status") or "confirmed") == "cancelled"
            actual_start = cls._bound(item.get("start") or {})
            actual_end = cls._bound(item.get("end") or {})
            if actual_start is None or actual_end is None:
                is_changed = True
            else:
                try:
                    if actual_start != original:
                        is_changed = True
                    duration = (
                        int((actual_end - actual_start).total_seconds() // 60)
                        if isinstance(actual_start, datetime)
                        else int((actual_end - actual_start).days * 1440)
                    )
                    if master_duration is not None and duration != master_duration:
                        is_changed = True
                except (AttributeError, TypeError):
                    is_changed = True
            for field in (
                "summary",
                "description",
                "location",
                "visibility",
                "transparency",
                "colorId",
            ):
                if field in item and item.get(field) != master_raw.get(field):
                    is_changed = True
            if item.get("attendees") != master_raw.get("attendees") and item.get(
                "attendees"
            ):
                is_changed = True
            if item.get("conferenceData") or item.get("hangoutLink"):
                is_changed = True
            if is_changed and key not in changed:
                changed.append(key)
        return tuple(changed)

    def process_active(self) -> SeriesAdoptionSyncResult:
        result = SeriesAdoptionSyncResult()
        for initial in self.store.list_processable():
            plan = self.store.get_plan(initial.id)
            if plan is None:
                continue
            try:
                if plan.state is SeriesAdoptionState.PENDING:
                    result.adoptions_started += 1
                    if not self._claim(plan, result):
                        continue
                    plan = self.store.get_plan(plan.id)
                    if plan is None:
                        continue
                if plan.state in (
                    SeriesAdoptionState.REMOTE_CLAIMED,
                    SeriesAdoptionState.LOCAL_FINALIZE_PENDING,
                ):
                    self._finalize(plan, result)
                elif plan.state is SeriesAdoptionState.ROLLBACK_PENDING:
                    self._rollback(plan, result)
                elif plan.state is SeriesAdoptionState.RELEASE_PENDING:
                    self._release(plan, result)
            except RetryableGatewayError as exc:
                # Durable state remains processable; the next manual sync tries
                # again.  A remote claim that already succeeded is never redone.
                if plan.state in (
                    SeriesAdoptionState.REMOTE_CLAIMED,
                    SeriesAdoptionState.LOCAL_FINALIZE_PENDING,
                ):
                    self.store.mark_local_finalize_pending(plan.id, str(exc))
                else:
                    self.store.mark_terminal(plan.id, str(exc))
                    result.terminal += 1
            except RemoteMasterConflictError as exc:
                snapshot = (
                    _canonical_snapshot(exc.remote_event)
                    if exc.remote_event is not None
                    else None
                )
                self.store.mark_conflict(plan.id, str(exc), snapshot)
                result.conflicts += 1
            except TerminalGatewayError as exc:
                self.store.mark_terminal(plan.id, str(exc))
                result.terminal += 1
            except Exception as exc:
                # If markers are already remote, only local finalization is
                # retried.  This is the core remote-success/local-failure path.
                refreshed = self.store.get_plan(plan.id)
                if refreshed is not None and refreshed.claimed_remote_etag:
                    self.store.mark_local_finalize_pending(plan.id, str(exc))
                else:
                    self.store.mark_terminal(plan.id, str(exc))
                    result.terminal += 1
        return result

    def _local_counts(self, remote_event_id: str) -> dict[str, int]:
        imported = self.catalog.count_imported_instances(remote_event_id)
        legacy = 0
        connection = getattr(self.catalog, "_connection", None)
        if connection is not None:
            row = connection.execute(
                "SELECT COUNT(*) AS n FROM tasks WHERE "
                "google_calendar_event_id = ? AND "
                "google_calendar_recurring_event_id IS NULL",
                (remote_event_id,),
            ).fetchone()
            legacy = int(row["n"])
        quarantine = 0
        if self.series_store is not None:
            quarantine = sum(
                1
                for item in self.series_store.list_occurrence_changes(
                    unresolved_only=True
                )
                if item.remote_master_event_id == remote_event_id
            )
        return {
            "imported": imported,
            "legacy": legacy,
            "quarantine": quarantine,
        }

    def _claim(
        self,
        plan: CalendarSeriesAdoption,
        result: SeriesAdoptionSyncResult,
    ) -> bool:
        access = self.gateway.get_calendar_access(plan.calendar_id)
        remote = self.gateway.get_recurring_master(plan.remote_event_id)
        if remote is None:
            self.store.mark_remote_deleted(plan.id)
            return False
        instances = self.gateway.list_recurring_instances(plan.remote_event_id)
        exception_dates = self.exception_dates(remote, instances)
        local = self._local_counts(plan.remote_event_id)
        catalog = _catalog_from_event(
            remote,
            calendar_id=plan.calendar_id,
            access_role=str((access or {}).get("access_role") or ""),
            exception_dates=exception_dates,
        )
        eligibility = evaluate_series_adoption(
            catalog,
            has_active_link=(
                self.series_store.get_link_by_remote(
                    plan.provider, plan.calendar_id, plan.remote_event_id
                )
                is not None
            ),
            has_active_adoption=False,
            legacy_master_task_count=local["legacy"],
            imported_instance_task_count=local["imported"],
            unresolved_quarantine_count=local["quarantine"],
            exception_dates=exception_dates,
            live_calendar_access_role=str(
                (access or {}).get("access_role") or ""
            ),
        )
        if not eligibility.eligible:
            self.store.mark_conflict(
                plan.id,
                "; ".join(eligibility.errors),
                _canonical_snapshot(remote),
            )
            result.conflicts += 1
            return False
        actual_hash = master_content_fingerprint(
            master_event_to_owned_payload(remote)
        )
        if (
            remote.etag != plan.remote_etag_base
            or actual_hash != plan.canonical_remote_payload_hash
            or remote.private_extended_properties
            != plan.preexisting_private_properties
        ):
            self.store.mark_conflict(
                plan.id,
                "Master изменён после подтверждения; marker claim не выполнен.",
                _canonical_snapshot(remote),
            )
            result.conflicts += 1
            return False
        series = self.series_from_event(remote, plan.reserved_series_uid)
        local_hash = master_payload_hash(series_to_master_event(series))
        markers = planner_adoption_private_properties(
            adoption_id=plan.id,
            series_uid=series.uid,
            revision=series.revision,
            payload_hash=local_hash,
        )
        merged = dict(plan.preexisting_private_properties)
        merged.update(markers)
        claimed = self.gateway.claim_external_recurring_master(
            plan.remote_event_id,
            merged,
            plan.remote_etag_base,
        )
        if not claimed.etag:
            raise TerminalGatewayError("Google claim response does not contain ETag.")
        self.store.mark_remote_claimed(
            plan.id,
            claimed_private_properties=merged,
            claimed_remote_etag=claimed.etag,
        )
        result.ownership_claims_written += 1
        return True

    def _finalize(
        self,
        plan: CalendarSeriesAdoption,
        result: SeriesAdoptionSyncResult,
    ) -> None:
        remote = self.gateway.get_recurring_master(plan.remote_event_id)
        if remote is None:
            self.store.mark_remote_deleted(plan.id)
            return
        private = remote.private_extended_properties
        if (
            private.get(PLANNER_SERIES_UID_PROPERTY)
            != plan.reserved_series_uid
            or private.get(PLANNER_ADOPTION_ID_PROPERTY) != str(plan.id)
            or private.get(PLANNER_LINK_ORIGIN_PROPERTY)
            != ADOPTED_EXTERNAL_ORIGIN
        ):
            raise RemoteMasterConflictError(
                "Remote ownership markers do not match the adoption plan.",
                remote,
            )
        actual_hash = master_content_fingerprint(
            master_event_to_owned_payload(remote)
        )
        if actual_hash != plan.canonical_remote_payload_hash:
            raise RemoteMasterConflictError(
                "Visible remote master content changed after claim.", remote
            )
        series = self.series_from_event(remote, plan.reserved_series_uid)
        local_hash = master_payload_hash(series_to_master_event(series))
        if private.get("planner_payload_hash") != local_hash:
            raise RemoteMasterConflictError(
                "Planner payload marker does not match the planned TaskSeries.",
                remote,
            )
        was_retry = plan.state is SeriesAdoptionState.LOCAL_FINALIZE_PENDING
        self.store.finalize_external_series_adoption_atomic(
            plan,
            series=series,
            remote_etag=str(remote.etag or plan.claimed_remote_etag or ""),
            remote_updated_at=remote.updated_at,
            synced_payload_hash=local_hash,
        )
        result.adoptions_finalized += 1
        if was_retry:
            result.reconciliations += 1

    def _rollback(
        self,
        plan: CalendarSeriesAdoption,
        result: SeriesAdoptionSyncResult,
    ) -> None:
        remote = self.gateway.get_recurring_master(plan.remote_event_id)
        if remote is None:
            self.store.mark_rolled_back(plan.id)
            result.rollbacks += 1
            return
        actual_hash = master_content_fingerprint(
            master_event_to_owned_payload(remote)
        )
        if actual_hash != plan.canonical_remote_payload_hash:
            raise RemoteMasterConflictError(
                "Visible master changed; rollback would overwrite it.", remote
            )
        private = remote.private_extended_properties
        if (
            private.get(PLANNER_ADOPTION_ID_PROPERTY) != str(plan.id)
            or private.get(PLANNER_SERIES_UID_PROPERTY)
            != plan.reserved_series_uid
        ):
            raise RemoteMasterConflictError(
                "Adoption marker identity changed; rollback refused.", remote
            )
        released = self.gateway.release_adopted_recurring_master(
            plan.remote_event_id,
            plan.preexisting_private_properties,
            plan.claimed_remote_etag,
        )
        self.store.mark_rolled_back(plan.id)
        if released.etag:
            catalog = self.catalog.get(
                plan.provider, plan.calendar_id, plan.remote_event_id
            )
            if catalog is not None:
                catalog.etag = released.etag
                catalog.private_properties_json = json.dumps(
                    released.private_extended_properties,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                catalog.planner_owned = False
                catalog.adoption_state = None
                self.catalog.upsert(catalog)
        result.rollbacks += 1

    def _release(
        self,
        plan: CalendarSeriesAdoption,
        result: SeriesAdoptionSyncResult,
    ) -> None:
        link = self.series_store.get_link(plan.reserved_series_uid)
        if (
            link is None
            or link.link_origin != ADOPTED_EXTERNAL_ORIGIN
            or link.adoption_id != plan.id
        ):
            raise TerminalGatewayError("Active adopted link identity is missing.")
        remote = self.gateway.get_recurring_master(plan.remote_event_id)
        if remote is None:
            raise RemoteMasterConflictError(
                "Adopted master was deleted before release."
            )
        private = remote.private_extended_properties
        if (
            private.get(PLANNER_ADOPTION_ID_PROPERTY) != str(plan.id)
            or private.get(PLANNER_SERIES_UID_PROPERTY)
            != plan.reserved_series_uid
        ):
            raise RemoteMasterConflictError(
                "Adoption markers changed before release.", remote
            )
        released = self.gateway.release_adopted_recurring_master(
            plan.remote_event_id,
            plan.preexisting_private_properties,
            link.remote_etag,
        )
        self.store.finalize_release_atomic(
            plan, released_remote_etag=released.etag
        )
        result.releases += 1


__all__ = ["CalendarSeriesAdoptionEngine", "SeriesAdoptionSyncResult"]
