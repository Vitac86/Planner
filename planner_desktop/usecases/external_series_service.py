"""Read-only query service for the locally cached Google series catalog."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from planner_desktop.repositories.external_series_repository import (
    ExternalSeriesRepository,
)


CATALOG_NOTE_RU = (
    "Серии обнаружены при синхронизации. Мастера, созданные Planner, "
    "помечены отдельно; чистую внешнюю серию можно импортировать только явно."
)


def _format_stamp(value: Optional[datetime]) -> str:
    if value is None:
        return "—"
    local = value.astimezone() if value.tzinfo is not None else value
    return local.strftime("%Y-%m-%d %H:%M")


class ExternalSeriesService:
    def __init__(
        self,
        repository: ExternalSeriesRepository,
        *,
        adoption_service=None,
    ) -> None:
        self.repository = repository
        self.adoption_service = adoption_service

    def diagnostics(self) -> dict:
        items = self.repository.list_all(include_deleted=True)
        legacy_ids = self.repository.possible_legacy_master_import_ids()
        return {
            "active_master_count": sum(not item.is_cancelled for item in items),
            "unsupported_master_count": sum(
                not item.is_supported for item in items if not item.is_cancelled
            ),
            "cancelled_master_count": sum(item.is_cancelled for item in items),
            "possible_legacy_master_import_count": len(legacy_ids),
            "possible_legacy_master_import_ids": tuple(legacy_ids),
            "last_catalog_refresh_at": self.repository.latest_refresh_at(),
            "orphan_planner_marker_count": sum(
                item.planner_owned
                and not item.linked_series_uid
                and not item.adoption_state
                for item in items
            ),
        }

    def rows(self) -> list[dict]:
        rows = []
        for item in self.repository.list_all(include_deleted=True):
            orphan_marker = bool(
                item.planner_owned
                and not item.linked_series_uid
                and not item.adoption_state
            )
            eligibility = (
                self.adoption_service.validate_adoption(item.remote_event_id)
                if self.adoption_service is not None
                else None
            )
            active_adoption = (
                self.adoption_service.get_active_adoption(item.remote_event_id)
                if self.adoption_service is not None
                else None
            )
            rows.append({
                "remoteEventId": item.remote_event_id,
                "title": item.title or "(без названия)",
                "recurrenceSummary": item.recurrence_summary(),
                "isAllDay": item.is_all_day,
                "timingText": "Весь день" if item.is_all_day else "Со временем",
                "timezoneName": item.timezone_name or "—",
                "supportStatus": item.support_status,
                "supportText": "Поддерживается" if item.is_supported else "Не поддерживается",
                "unsupportedReason": item.unsupported_reason or "",
                "rawRecurrence": "\n".join(item.recurrence_lines),
                "importedInstanceCount": self.repository.count_imported_instances(
                    item.remote_event_id
                ),
                "lastRemoteUpdate": _format_stamp(item.remote_updated_at),
                "cancelled": item.is_cancelled,
                "stateText": "Отменена" if item.is_cancelled else "Активна",
                "plannerOwned": item.planner_owned,
                "linkedSeriesUid": item.linked_series_uid or "",
                "adoptedSeriesUid": item.adopted_series_uid or "",
                "adoptionState": (
                    active_adoption.state.value
                    if active_adoption is not None
                    else (item.adoption_state or "")
                ),
                "adoptionPlanId": (
                    active_adoption.id if active_adoption is not None else 0
                ),
                "adoptionEligible": bool(
                    eligibility is not None and eligibility.eligible
                ),
                "adoptionBlockedReasons": (
                    list(eligibility.errors) if eligibility is not None else []
                ),
                "adoptionBlockedText": (
                    "\n".join(eligibility.errors)
                    if eligibility is not None else ""
                ),
                "organizerSelf": (
                    eligibility.organizer_self
                    if eligibility is not None
                    else bool(item.organizer_self)
                ),
                "calendarAccessRole": (
                    eligibility.calendar_access_role
                    if eligibility is not None
                    else item.calendar_access_role
                ),
                "remoteOnlyFields": (
                    list(eligibility.remote_only_fields)
                    if eligibility is not None else []
                ),
                "exceptionCount": int(item.exception_count),
                "orphanOwnershipMarker": orphan_marker,
                "ownershipText": (
                    "Возможная незавершённая связь Planner"
                    if orphan_marker
                    else (
                        "Импортирована из Google"
                        if item.adopted_series_uid
                        else (
                            "Создана Planner"
                            if item.planner_owned
                            else "Внешняя серия"
                        )
                    )
                ),
            })
        return rows


__all__ = ["CATALOG_NOTE_RU", "ExternalSeriesService"]
