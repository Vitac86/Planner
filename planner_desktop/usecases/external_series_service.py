"""Read-only query service for the locally cached Google series catalog."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from planner_desktop.repositories.external_series_repository import (
    ExternalSeriesRepository,
)


CATALOG_NOTE_RU = (
    "Серии обнаружены при ручной синхронизации. Мастера, созданные Planner, "
    "помечены отдельно; принятие чужих серий остаётся в Phase 3.2B3."
)


def _format_stamp(value: Optional[datetime]) -> str:
    if value is None:
        return "—"
    local = value.astimezone() if value.tzinfo is not None else value
    return local.strftime("%Y-%m-%d %H:%M")


class ExternalSeriesService:
    def __init__(self, repository: ExternalSeriesRepository) -> None:
        self.repository = repository

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
        }

    def rows(self) -> list[dict]:
        rows = []
        for item in self.repository.list_all(include_deleted=True):
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
                "ownershipText": (
                    "Создана Planner"
                    if item.planner_owned
                    else "Внешняя серия (только чтение)"
                ),
            })
        return rows


__all__ = ["CATALOG_NOTE_RU", "ExternalSeriesService"]
