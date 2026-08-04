"""Local-only explicit external-series adoption use cases (Phase 3.2B3C2)."""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any, Optional

from planner_desktop.domain.google_series_split import master_content_fingerprint
from planner_desktop.domain.series_adoption import (
    CalendarSeriesAdoption,
    SeriesAdoptionEligibility,
    SeriesAdoptionState,
    evaluate_series_adoption,
)
from planner_desktop.domain.series_calendar_link import (
    PLANNER_PAYLOAD_HASH_PROPERTY,
)


@dataclass(frozen=True)
class SeriesAdoptionActionResult:
    ok: bool
    record: Optional[CalendarSeriesAdoption] = None
    eligibility: Optional[SeriesAdoptionEligibility] = None
    changed: bool = False
    error: str = ""


class SeriesAdoptionService:
    """Creates durable intent only; no method performs network access."""

    def __init__(
        self,
        external_series_repository,
        adoption_store,
        *,
        series_store=None,
        split_store=None,
    ) -> None:
        self.external_series_repository = external_series_repository
        self.adoption_store = adoption_store
        self.series_store = series_store
        self.split_store = split_store

    def _catalog(self, remote_event_id: str):
        return self.external_series_repository.get(
            "google", "primary", str(remote_event_id)
        )

    def _has_link(self, remote_event_id: str) -> bool:
        if self.series_store is None:
            return False
        return (
            self.series_store.get_link_by_remote(
                "google", "primary", str(remote_event_id)
            )
            is not None
        )

    def _quarantine_count(self, remote_event_id: str) -> int:
        if self.series_store is None:
            return 0
        listing = getattr(self.series_store, "list_occurrence_changes", None)
        if not callable(listing):
            return 0
        return sum(
            1
            for item in listing(unresolved_only=True)
            if item.remote_master_event_id == remote_event_id
        )

    def _pending_operation_count(self, remote_event_id: str) -> int:
        count = 0
        if self.split_store is not None:
            getter = getattr(
                self.split_store, "get_active_plan_by_source_remote", None
            )
            if callable(getter) and getter(remote_event_id) is not None:
                count += 1
        if self.series_store is not None:
            link = self.series_store.get_link_by_remote(
                "google", "primary", remote_event_id
            )
            if link is not None:
                count += 1
        return count

    def validate_adoption(
        self, remote_event_id: str
    ) -> SeriesAdoptionEligibility:
        catalog = self._catalog(remote_event_id)
        if catalog is None:
            from planner_desktop.domain.series_adoption import (
                AdoptionBlockedReason,
            )

            return SeriesAdoptionEligibility(
                remote_event_id=str(remote_event_id),
                eligible=False,
                blocked_reasons=(
                    AdoptionBlockedReason(
                        "catalog_missing",
                        "Сначала выполните ручную синхронизацию: master "
                        "отсутствует в локальном каталоге.",
                    ),
                ),
            )
        active = self.adoption_store.get_active_adoption(
            catalog.provider, catalog.calendar_id, catalog.remote_event_id
        )
        legacy_ids = set(
            self.external_series_repository.possible_legacy_master_import_ids()
        )
        # The repository exposes exact Task UIDs, not a remote-id count.  A
        # catalog master has a legacy row when at least one returned UID maps
        # to this event; SQLite can answer this directly when available.
        legacy_count = 0
        connection = getattr(self.external_series_repository, "_connection", None)
        if connection is not None:
            row = connection.execute(
                "SELECT COUNT(*) AS n FROM tasks WHERE "
                "google_calendar_event_id = ? AND "
                "google_calendar_recurring_event_id IS NULL",
                (catalog.remote_event_id,),
            ).fetchone()
            legacy_count = int(row["n"])
        elif legacy_ids:
            legacy_count = 1
        return evaluate_series_adoption(
            catalog,
            has_active_link=self._has_link(catalog.remote_event_id),
            has_active_adoption=active is not None,
            legacy_master_task_count=legacy_count,
            imported_instance_task_count=(
                self.external_series_repository.count_imported_instances(
                    catalog.remote_event_id
                )
            ),
            unresolved_quarantine_count=self._quarantine_count(
                catalog.remote_event_id
            ),
            pending_operation_count=self._pending_operation_count(
                catalog.remote_event_id
            ),
        )

    @staticmethod
    def _snapshot_for(catalog) -> dict[str, Any]:
        raw = dict(catalog.raw_snapshot)
        if raw:
            return raw
        start: dict[str, Any]
        end: dict[str, Any]
        if catalog.is_all_day:
            start = {"date": catalog.start_value}
            end = {"date": catalog.end_value}
        else:
            start = {
                "dateTime": catalog.start_value,
                "timeZone": catalog.timezone_name,
            }
            end = {
                "dateTime": catalog.end_value,
                "timeZone": catalog.timezone_name,
            }
        return {
            "id": catalog.remote_event_id,
            "etag": catalog.etag,
            "status": catalog.remote_status,
            "summary": catalog.title,
            "description": catalog.description,
            "start": start,
            "end": end,
            "recurrence": list(catalog.recurrence_lines),
            "eventType": catalog.event_type,
            "locked": catalog.locked,
            "organizer": {"self": catalog.organizer_self},
            "extendedProperties": {
                "private": dict(catalog.private_properties)
            },
        }

    def create_adoption_plan(
        self, remote_event_id: str
    ) -> SeriesAdoptionActionResult:
        catalog = self._catalog(remote_event_id)
        if catalog is None:
            eligibility = self.validate_adoption(remote_event_id)
            return SeriesAdoptionActionResult(
                ok=False,
                eligibility=eligibility,
                error="; ".join(eligibility.errors),
            )
        existing = self.adoption_store.get_active_adoption(
            catalog.provider, catalog.calendar_id, catalog.remote_event_id
        )
        if existing is not None:
            return SeriesAdoptionActionResult(ok=True, record=existing)
        eligibility = self.validate_adoption(remote_event_id)
        if not eligibility.eligible:
            return SeriesAdoptionActionResult(
                ok=False,
                eligibility=eligibility,
                error="; ".join(eligibility.errors),
            )
        if not catalog.etag:
            return SeriesAdoptionActionResult(
                ok=False,
                eligibility=eligibility,
                error="В локальном каталоге отсутствует ETag master.",
            )
        snapshot = self._snapshot_for(catalog)
        try:
            fingerprint = master_content_fingerprint(snapshot)
        except (TypeError, ValueError) as exc:
            return SeriesAdoptionActionResult(
                ok=False,
                eligibility=eligibility,
                error=f"Не удалось канонизировать master без потерь: {exc}",
            )
        private = (
            (snapshot.get("extendedProperties") or {}).get("private") or {}
        )
        record = CalendarSeriesAdoption(
            provider=catalog.provider,
            calendar_id=catalog.calendar_id,
            remote_event_id=catalog.remote_event_id,
            external_catalog_id=catalog.id,
            reserved_series_uid=str(uuid.uuid4()),
            remote_etag_base=str(catalog.etag),
            remote_snapshot_json=json.dumps(
                snapshot,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
            canonical_remote_payload_hash=fingerprint,
            preexisting_private_properties_json=json.dumps(
                {str(key): str(value) for key, value in private.items()},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
        )
        stored = self.adoption_store.create_plan(record)
        return SeriesAdoptionActionResult(
            ok=True, record=stored, eligibility=eligibility, changed=True
        )

    def get_active_adoption(
        self, remote_event_id: str
    ) -> Optional[CalendarSeriesAdoption]:
        return self.adoption_store.get_active_adoption(
            "google", "primary", str(remote_event_id)
        )

    def list_adoption_history(self) -> list[CalendarSeriesAdoption]:
        return self.adoption_store.list_adoptions()

    def cancel_unstarted_adoption(
        self, plan_id: int
    ) -> SeriesAdoptionActionResult:
        changed = self.adoption_store.cancel_unstarted(int(plan_id))
        record = self.adoption_store.get_plan(int(plan_id))
        return SeriesAdoptionActionResult(
            ok=changed,
            record=record,
            changed=changed,
            error="" if changed else "Отменить можно только ожидающий план.",
        )

    def retry_adoption(self, plan_id: int) -> SeriesAdoptionActionResult:
        changed = self.adoption_store.retry(int(plan_id))
        return SeriesAdoptionActionResult(
            ok=changed,
            record=self.adoption_store.get_plan(int(plan_id)),
            changed=changed,
            error="" if changed else "План не находится в retry-состоянии.",
        )

    def request_adoption_rollback(
        self, plan_id: int
    ) -> SeriesAdoptionActionResult:
        changed = self.adoption_store.request_rollback(int(plan_id))
        return SeriesAdoptionActionResult(
            ok=changed,
            record=self.adoption_store.get_plan(int(plan_id)),
            changed=changed,
            error="" if changed else "Этот план нельзя откатить.",
        )

    def request_release_adopted_series(
        self, series_uid: str
    ) -> SeriesAdoptionActionResult:
        plan = self.adoption_store.get_adoption_by_series(series_uid)
        if plan is None:
            return SeriesAdoptionActionResult(
                ok=False, error="История импорта серии не найдена."
            )
        if self.series_store is None:
            return SeriesAdoptionActionResult(
                ok=False, record=plan, error="Хранилище связей недоступно."
            )
        link = self.series_store.get_link(series_uid)
        if link is None or link.link_origin != "adopted_external":
            return SeriesAdoptionActionResult(
                ok=False,
                record=plan,
                error="Обычная Planner-связь использует прежнее отключение.",
            )
        changed = self.adoption_store.request_release(series_uid)
        return SeriesAdoptionActionResult(
            ok=changed,
            record=self.adoption_store.get_plan(plan.id),
            changed=changed,
            error="" if changed else "Серия уже ожидает release или отключена.",
        )

    def diagnostics(self) -> dict[str, int]:
        counts = self.adoption_store.counts_by_state()
        return {
            "active": sum(
                counts.get(state.value, 0)
                for state in (
                    SeriesAdoptionState.PENDING,
                    SeriesAdoptionState.REMOTE_CLAIMED,
                    SeriesAdoptionState.LOCAL_FINALIZE_PENDING,
                    SeriesAdoptionState.ROLLBACK_PENDING,
                    SeriesAdoptionState.RELEASE_PENDING,
                )
            ),
            "incomplete_claims": (
                counts.get(SeriesAdoptionState.REMOTE_CLAIMED.value, 0)
                + counts.get(
                    SeriesAdoptionState.LOCAL_FINALIZE_PENDING.value, 0
                )
            ),
            "conflicts": (
                counts.get(SeriesAdoptionState.CONFLICT.value, 0)
                + counts.get(SeriesAdoptionState.RELEASE_CONFLICT.value, 0)
            ),
            "completed": counts.get(SeriesAdoptionState.COMPLETED.value, 0),
            "rolled_back": counts.get(
                SeriesAdoptionState.ROLLED_BACK.value, 0
            ),
            "released": counts.get(SeriesAdoptionState.RELEASED.value, 0),
            "terminal": counts.get(
                SeriesAdoptionState.TERMINAL_ERROR.value, 0
            ),
        }


__all__ = ["SeriesAdoptionActionResult", "SeriesAdoptionService"]
