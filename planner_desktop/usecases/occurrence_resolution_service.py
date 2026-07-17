"""Explicit resolution of quarantined linked recurring-instance changes."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field, fields
from datetime import timedelta
from typing import Optional

from planner_desktop.domain.google_occurrence import (
    OccurrenceResolutionKind,
    OccurrenceSyncStatus,
    canonical_occurrence_payload_fingerprint,
    google_original_start_to_occurrence_key,
    local_occurrence_to_google_original_start,
)
from planner_desktop.domain.task import Task
from planner_desktop.sync.calendar_series_occurrence_mapper import (
    build_desired_occurrence_payload,
    remote_payload_to_local_schedule,
)


CONFIRM_KEEP_PLANNER = (
    "Подтвердите перезапись одного удалённого экземпляра версией Planner."
)


@dataclass
class OccurrenceResolutionResult:
    ok: bool
    changed: bool = False
    task: Optional[Task] = None
    error: str = ""
    resolution_kind: Optional[OccurrenceResolutionKind] = None
    warnings: list[str] = field(default_factory=list)


class OccurrenceResolutionService:
    def __init__(
        self,
        series_repository,
        task_repository,
        series_link_service,
        occurrence_store,
    ) -> None:
        self.series_repository = series_repository
        self.task_repository = task_repository
        self.series_link_service = series_link_service
        self.store = occurrence_store

    def _restore_task_snapshot(self, snapshot: Task) -> None:
        current = self.task_repository.get_by_uid(snapshot.uid)
        if current is None:
            return
        for descriptor in fields(Task):
            setattr(
                current,
                descriptor.name,
                deepcopy(getattr(snapshot, descriptor.name)),
            )
        self.task_repository.update(current)

    def _context(self, change_id: int):
        change = self.store.get_occurrence_change(change_id)
        if change is None:
            raise ValueError("Изменение экземпляра не найдено.")
        if change.resolved_at is not None:
            raise ValueError("Изменение экземпляра уже разрешено.")
        if not change.matched_series_uid or not change.matched_occurrence_key:
            raise ValueError("Карантин не сопоставлен с локальным экземпляром.")
        series = self.series_repository.get_by_uid(change.matched_series_uid)
        if series is None or series.is_deleted:
            raise ValueError("Локальная серия не найдена.")
        link = self.series_link_service.get_link(series.uid)
        if link is None or link.remote_event_id != change.remote_master_event_id:
            raise ValueError("Активная связь серии Google не подтверждена.")
        occurrence_link = self.store.get_occurrence_link(
            series.uid,
            change.matched_occurrence_key,
            link_generation=link.link_generation,
        )
        if occurrence_link is None:
            identity = local_occurrence_to_google_original_start(
                series, change.matched_occurrence_key
            )
            occurrence_link = self.store.ensure_occurrence_link(
                series.uid, change.matched_occurrence_key, link, identity
            )
        identity = google_original_start_to_occurrence_key(
            series, occurrence_link.identity
        )
        if identity != change.matched_occurrence_key:
            raise ValueError("originalStartTime не соответствует локальному слоту.")
        task = next(
            (
                row
                for row in self.task_repository.list_by_series(series.uid)
                if row.occurrence_key == change.matched_occurrence_key
            ),
            None,
        )
        if task is None:
            raise ValueError("Материализованный локальный экземпляр не найден.")
        return change, series, link, occurrence_link, task

    def use_google(self, change_id: int) -> OccurrenceResolutionResult:
        """Apply a supported remote snapshot locally, without a Google call."""
        try:
            change, series, _, occurrence_link, task = self._context(change_id)
            payload = change.payload
            cancelled = change.status == "cancelled" or (
                payload.get("status") == "cancelled"
            )
            snapshot = deepcopy(task)
            link_snapshot = deepcopy(occurrence_link)
            if cancelled:
                if not task.is_deleted and task.id is not None:
                    if not self.task_repository.delete(task.id):
                        raise RuntimeError("Не удалось сохранить локальную отмену.")
                updated_task = self.task_repository.get_by_uid(task.uid) or task
            else:
                start, end, is_all_day = remote_payload_to_local_schedule(
                    payload, series
                )
                task.deleted_at = None
                task.title = str(payload.get("summary") or "")
                task.notes = str(payload.get("description") or "")
                task.start = start
                task.end = end
                task.is_all_day = is_all_day
                task.duration_minutes = (
                    None
                    if is_all_day
                    else max(1, int((end - start).total_seconds() // 60))
                )
                task.is_series_exception = True
                updated_task = self.task_repository.update(task)
            occurrence_link.remote_instance_event_id = (
                change.remote_instance_event_id
            )
            occurrence_link.remote_etag = change.remote_etag
            occurrence_link.remote_updated_at = change.remote_updated_at
            occurrence_link.is_cancelled_remote = cancelled
            occurrence_link.sync_status = (
                OccurrenceSyncStatus.CANCELLED
                if cancelled
                else OccurrenceSyncStatus.SYNCED_EXCEPTION
            )
            occurrence_link.last_synced_remote_hash = (
                canonical_occurrence_payload_fingerprint(payload)
                if payload else None
            )
            occurrence_link.last_synced_local_hash = (
                occurrence_link.last_synced_remote_hash
            )
            occurrence_link.conflict_reason = None
            occurrence_link.conflict_snapshot_json = None
            self.store.update_occurrence_link(occurrence_link)
            if not self.store.resolve_occurrence_change(
                change.id,
                OccurrenceResolutionKind.USE_GOOGLE.value,
            ):
                raise RuntimeError("Не удалось закрыть карантин.")
        except Exception as exc:
            # Best-effort compensation across repository adapters. SQLite
            # callers still observe all-or-nothing final state.
            if "snapshot" in locals():
                try:
                    self._restore_task_snapshot(snapshot)
                except Exception:
                    pass
            if "link_snapshot" in locals():
                try:
                    self.store.update_occurrence_link(link_snapshot)
                except Exception:
                    pass
            return OccurrenceResolutionResult(ok=False, error=str(exc))
        return OccurrenceResolutionResult(
            ok=True,
            changed=True,
            task=updated_task,
            resolution_kind=OccurrenceResolutionKind.USE_GOOGLE,
        )

    def keep_planner(
        self, change_id: int, *, confirmed: bool = False
    ) -> OccurrenceResolutionResult:
        """Queue one conditional UPDATE/CANCEL using the acknowledged ETag."""
        if not confirmed:
            return OccurrenceResolutionResult(
                ok=False, error=CONFIRM_KEEP_PLANNER
            )
        try:
            change, series, link, _, task = self._context(change_id)
            payload = build_desired_occurrence_payload(
                task, series, link.link_generation
            )
            if task.is_deleted:
                queued = self.store.enqueue_cancel(
                    series.uid,
                    str(task.occurrence_key),
                    payload,
                    acknowledged_remote_etag=change.remote_etag,
                )
            else:
                queued = self.store.enqueue_update(
                    series.uid,
                    str(task.occurrence_key),
                    payload,
                    desired_payload_hash=(
                        canonical_occurrence_payload_fingerprint(payload)
                    ),
                    acknowledged_remote_etag=change.remote_etag,
                )
            self.store.resolve_occurrence_change(
                change.id,
                OccurrenceResolutionKind.KEEP_PLANNER.value,
                pending=True,
            )
        except Exception as exc:
            return OccurrenceResolutionResult(ok=False, error=str(exc))
        return OccurrenceResolutionResult(
            ok=True,
            changed=queued,
            task=task,
            resolution_kind=OccurrenceResolutionKind.KEEP_PLANNER,
        )

    def keep_both_as_local_copy(
        self, change_id: int
    ) -> OccurrenceResolutionResult:
        """Duplicate the remote state as an independent local Task."""
        try:
            change, series, _, _, _ = self._context(change_id)
            payload = change.payload
            if change.status == "cancelled" or payload.get("status") == "cancelled":
                raise ValueError(
                    "Отменённый удалённый экземпляр нельзя дублировать как задачу."
                )
            start, end, is_all_day = remote_payload_to_local_schedule(
                payload, series
            )
            duplicate = Task(
                title=str(payload.get("summary") or ""),
                notes=str(payload.get("description") or ""),
                start=start,
                end=end,
                is_all_day=is_all_day,
                duration_minutes=(
                    None
                    if is_all_day
                    else max(1, int((end - start).total_seconds() // 60))
                ),
            )
            created = self.task_repository.add(duplicate)
            if not self.store.resolve_occurrence_change(
                change.id,
                OccurrenceResolutionKind.DUPLICATED_LOCAL_COPY.value,
            ):
                if created.id is not None:
                    self.task_repository.delete(created.id)
                raise RuntimeError("Не удалось закрыть карантин.")
        except Exception as exc:
            return OccurrenceResolutionResult(ok=False, error=str(exc))
        return OccurrenceResolutionResult(
            ok=True,
            changed=True,
            task=created,
            resolution_kind=OccurrenceResolutionKind.DUPLICATED_LOCAL_COPY,
        )

    def ignore_for_now(self, change_id: int) -> OccurrenceResolutionResult:
        change = self.store.get_occurrence_change(change_id)
        if change is None:
            return OccurrenceResolutionResult(
                ok=False, error="Изменение экземпляра не найдено."
            )
        return OccurrenceResolutionResult(
            ok=True,
            changed=False,
            resolution_kind=OccurrenceResolutionKind.IGNORE,
        )

    def use_google_support(self, change_id: int) -> tuple[bool, str]:
        try:
            change, series, _, _, _ = self._context(change_id)
            if change.status == "cancelled" or (
                change.payload.get("status") == "cancelled"
            ):
                return True, ""
            remote_payload_to_local_schedule(change.payload, series)
        except Exception as exc:
            return False, str(exc)
        return True, ""


__all__ = [
    "CONFIRM_KEEP_PLANNER",
    "OccurrenceResolutionResult",
    "OccurrenceResolutionService",
]
