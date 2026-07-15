"""SQLite storage for the read-only external recurring-series catalog."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Union

from planner_desktop.domain.external_series import (
    ExternalCalendarSeries,
    recurrence_rule_from_data,
    recurrence_rule_to_data,
)
from planner_desktop.domain.task import utc_now
from planner_desktop.storage.paths import ensure_desktop_data_dir, get_desktop_db_path
from planner_desktop.storage.schema import create_schema


def _dt_to_text(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value is not None else None


def _text_to_dt(value: Optional[str]) -> Optional[datetime]:
    return datetime.fromisoformat(value) if value else None


def _row_to_series(row: sqlite3.Row) -> ExternalCalendarSeries:
    parsed_data = json.loads(row["parsed_rule_json"]) if row["parsed_rule_json"] else None
    return ExternalCalendarSeries(
        id=row["id"],
        provider=row["provider"],
        calendar_id=row["calendar_id"],
        remote_event_id=row["remote_event_id"],
        etag=row["etag"],
        title=row["title"],
        description=row["description"],
        start_kind=row["start_kind"],
        start_value=row["start_value"] or "",
        end_value=row["end_value"] or "",
        timezone_name=row["timezone_name"],
        recurrence_lines=tuple(json.loads(row["recurrence_lines_json"] or "[]")),
        parsed_rule=recurrence_rule_from_data(parsed_data),
        support_status=row["support_status"],
        unsupported_reason=row["unsupported_reason"],
        remote_status=row["remote_status"],
        remote_updated_at=_text_to_dt(row["remote_updated_at"]),
        first_seen_at=_text_to_dt(row["first_seen_at"]) or utc_now(),
        last_seen_at=_text_to_dt(row["last_seen_at"]) or utc_now(),
        deleted_at=_text_to_dt(row["deleted_at"]),
    )


class SQLiteExternalSeriesRepository:
    def __init__(self, db_path: Union[Path, str, None] = None) -> None:
        if db_path is None:
            ensure_desktop_data_dir()
            db_path = get_desktop_db_path()
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(str(self.db_path))
        self._connection.row_factory = sqlite3.Row
        create_schema(self._connection)

    def close(self) -> None:
        self._connection.close()

    @staticmethod
    def _params(series: ExternalCalendarSeries) -> tuple:
        rule_data = recurrence_rule_to_data(series.parsed_rule)
        return (
            series.provider, series.calendar_id, series.remote_event_id,
            series.etag, series.title, series.description, series.start_kind,
            series.start_value or None, series.end_value or None,
            series.timezone_name,
            json.dumps(list(series.recurrence_lines), ensure_ascii=False,
                       separators=(",", ":")),
            (json.dumps(rule_data, ensure_ascii=False, sort_keys=True,
                        separators=(",", ":")) if rule_data else None),
            series.support_status, series.unsupported_reason,
            series.remote_status, _dt_to_text(series.remote_updated_at),
            _dt_to_text(series.first_seen_at), _dt_to_text(series.last_seen_at),
            _dt_to_text(series.deleted_at),
        )

    def upsert(self, series: ExternalCalendarSeries) -> ExternalCalendarSeries:
        self._connection.execute(
            """
            INSERT INTO external_calendar_series (
                provider, calendar_id, remote_event_id, etag, title, description,
                start_kind, start_value, end_value, timezone_name,
                recurrence_lines_json, parsed_rule_json, support_status,
                unsupported_reason, remote_status, remote_updated_at,
                first_seen_at, last_seen_at, deleted_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(provider, calendar_id, remote_event_id) DO UPDATE SET
                etag = excluded.etag,
                title = excluded.title,
                description = excluded.description,
                start_kind = excluded.start_kind,
                start_value = excluded.start_value,
                end_value = excluded.end_value,
                timezone_name = excluded.timezone_name,
                recurrence_lines_json = excluded.recurrence_lines_json,
                parsed_rule_json = excluded.parsed_rule_json,
                support_status = excluded.support_status,
                unsupported_reason = excluded.unsupported_reason,
                remote_status = excluded.remote_status,
                remote_updated_at = excluded.remote_updated_at,
                last_seen_at = excluded.last_seen_at,
                deleted_at = excluded.deleted_at
            """,
            self._params(series),
        )
        self._connection.commit()
        stored = self.get(series.provider, series.calendar_id, series.remote_event_id)
        if stored is None:  # pragma: no cover - defensive SQLite invariant
            raise RuntimeError("External series upsert did not persist a row.")
        series.id = stored.id
        series.first_seen_at = stored.first_seen_at
        return series

    def get(self, provider: str, calendar_id: str, remote_event_id: str):
        row = self._connection.execute(
            "SELECT * FROM external_calendar_series "
            "WHERE provider = ? AND calendar_id = ? AND remote_event_id = ?",
            (provider, calendar_id, remote_event_id),
        ).fetchone()
        return _row_to_series(row) if row is not None else None

    def list_all(self, include_deleted: bool = True) -> List[ExternalCalendarSeries]:
        query = "SELECT * FROM external_calendar_series"
        if not include_deleted:
            query += " WHERE deleted_at IS NULL AND remote_status <> 'cancelled'"
        rows = self._connection.execute(query + " ORDER BY id").fetchall()
        return [_row_to_series(row) for row in rows]

    def mark_deleted(
        self, provider: str, calendar_id: str, remote_event_id: str, *,
        etag: Optional[str] = None, remote_updated_at: Optional[datetime] = None,
        seen_at: Optional[datetime] = None,
    ) -> Optional[ExternalCalendarSeries]:
        existing = self.get(provider, calendar_id, remote_event_id)
        if existing is None:
            return None
        stamp = seen_at or utc_now()
        self._connection.execute(
            """
            UPDATE external_calendar_series SET
                etag = COALESCE(?, etag), remote_status = 'cancelled',
                remote_updated_at = COALESCE(?, remote_updated_at),
                last_seen_at = ?, deleted_at = COALESCE(deleted_at, ?)
            WHERE provider = ? AND calendar_id = ? AND remote_event_id = ?
            """,
            (etag, _dt_to_text(remote_updated_at), _dt_to_text(stamp),
             _dt_to_text(stamp), provider, calendar_id, remote_event_id),
        )
        self._connection.commit()
        return self.get(provider, calendar_id, remote_event_id)

    def count_imported_instances(self, remote_event_id: str) -> int:
        row = self._connection.execute(
            "SELECT COUNT(*) AS n FROM tasks "
            "WHERE google_calendar_recurring_event_id = ?",
            (remote_event_id,),
        ).fetchone()
        return int(row["n"])

    def possible_legacy_master_import_ids(self) -> List[str]:
        rows = self._connection.execute(
            """
            SELECT tasks.uid
            FROM tasks
            JOIN external_calendar_series AS series
              ON series.remote_event_id = tasks.google_calendar_event_id
            WHERE tasks.google_calendar_recurring_event_id IS NULL
            ORDER BY tasks.uid
            """
        ).fetchall()
        return [str(row["uid"]) for row in rows]

    def latest_refresh_at(self) -> Optional[datetime]:
        row = self._connection.execute(
            "SELECT MAX(last_seen_at) AS stamp FROM external_calendar_series"
        ).fetchone()
        return _text_to_dt(row["stamp"]) if row and row["stamp"] else None


__all__ = ["SQLiteExternalSeriesRepository"]
