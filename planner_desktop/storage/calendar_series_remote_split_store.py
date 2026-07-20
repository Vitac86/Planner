"""SQLite store for durable remote split plans (Phase 3.2B3C1, schema v11).

One connection owns the ``calendar_series_remote_splits`` table plus the
single cross-table transaction that finalizes a completed remote split
locally.  No cascading deletes ever touch Task or TaskSeries history and a
plan row is never removed: completed and rolled-back plans stay queryable.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional, Sequence, Union

from planner_desktop.domain.google_series_split import (
    ACTIVE_SPLIT_STATES,
    PROCESSABLE_SPLIT_STATES,
    RemoteSeriesSplitPlanRecord,
    RemoteSeriesSplitStatus,
)
from planner_desktop.domain.recurrence import TaskSeries
from planner_desktop.domain.series_calendar_link import SeriesLinkStatus
from planner_desktop.domain.task import utc_now
from planner_desktop.storage.paths import ensure_desktop_data_dir, get_desktop_db_path
from planner_desktop.storage.schema import create_schema


def _dt_to_text(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value is not None else None


def _text_to_dt(value: Optional[str]) -> Optional[datetime]:
    return datetime.fromisoformat(value) if value else None


def _row_to_record(row: sqlite3.Row) -> RemoteSeriesSplitPlanRecord:
    return RemoteSeriesSplitPlanRecord(
        id=int(row["id"]),
        source_series_uid=str(row["source_series_uid"]),
        source_link_id=int(row["source_link_id"]),
        source_link_generation=int(row["source_link_generation"] or 0),
        source_remote_event_id=str(row["source_remote_event_id"]),
        target_occurrence_key=str(row["target_occurrence_key"]),
        target_original_start_kind=str(row["target_original_start_kind"]),
        target_original_start_value=str(row["target_original_start_value"]),
        target_original_start_timezone=row["target_original_start_timezone"],
        source_local_revision=int(row["source_local_revision"]),
        source_remote_etag_base=str(row["source_remote_etag_base"]),
        source_original_snapshot_json=str(row["source_original_snapshot_json"]),
        source_original_payload_hash=str(row["source_original_payload_hash"]),
        source_trimmed_payload_json=str(row["source_trimmed_payload_json"]),
        source_trimmed_payload_hash=str(row["source_trimmed_payload_hash"]),
        reserved_successor_series_uid=str(row["reserved_successor_series_uid"]),
        successor_remote_event_id=str(row["successor_remote_event_id"]),
        successor_series_snapshot_json=str(row["successor_series_snapshot_json"]),
        successor_payload_json=str(row["successor_payload_json"]),
        successor_payload_hash=str(row["successor_payload_hash"]),
        state=RemoteSeriesSplitStatus(str(row["state"])),
        source_trimmed_remote_etag=row["source_trimmed_remote_etag"],
        successor_remote_etag=row["successor_remote_etag"],
        attempts=int(row["attempts"] or 0),
        last_error=row["last_error"],
        created_at=_text_to_dt(row["created_at"]),
        updated_at=_text_to_dt(row["updated_at"]),
        completed_at=_text_to_dt(row["completed_at"]),
    )


_ACTIVE_STATE_VALUES = tuple(state.value for state in ACTIVE_SPLIT_STATES)
_PROCESSABLE_STATE_VALUES = tuple(
    state.value for state in PROCESSABLE_SPLIT_STATES
)


class CalendarSeriesRemoteSplitStore:
    """Durable plan rows and the local split finalization transaction."""

    def __init__(
        self,
        db_path: Union[Path, str, None] = None,
        *,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        if db_path is None:
            ensure_desktop_data_dir()
            db_path = get_desktop_db_path()
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._clock = clock
        self._connection = sqlite3.connect(str(self.db_path))
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        create_schema(self._connection)

    def close(self) -> None:
        self._connection.close()

    # ---- creation ----------------------------------------------------------

    def create_plan(
        self, record: RemoteSeriesSplitPlanRecord
    ) -> RemoteSeriesSplitPlanRecord:
        """Insert exactly one active plan; duplicates return the existing one."""
        existing = self.get_active_plan(record.source_series_uid)
        if existing is not None:
            return existing
        stamp = self._clock()
        cursor = self._connection.execute(
            """
            INSERT INTO calendar_series_remote_splits (
                source_series_uid, source_link_id, source_link_generation,
                source_remote_event_id, target_occurrence_key,
                target_original_start_kind, target_original_start_value,
                target_original_start_timezone, source_local_revision,
                source_remote_etag_base, source_original_snapshot_json,
                source_original_payload_hash, source_trimmed_payload_json,
                source_trimmed_payload_hash, reserved_successor_series_uid,
                successor_remote_event_id, successor_series_snapshot_json,
                successor_payload_json, successor_payload_hash, state,
                attempts, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
            """,
            (
                record.source_series_uid,
                record.source_link_id,
                record.source_link_generation,
                record.source_remote_event_id,
                record.target_occurrence_key,
                record.target_original_start_kind,
                record.target_original_start_value,
                record.target_original_start_timezone,
                record.source_local_revision,
                record.source_remote_etag_base,
                record.source_original_snapshot_json,
                record.source_original_payload_hash,
                record.source_trimmed_payload_json,
                record.source_trimmed_payload_hash,
                record.reserved_successor_series_uid,
                record.successor_remote_event_id,
                record.successor_series_snapshot_json,
                record.successor_payload_json,
                record.successor_payload_hash,
                RemoteSeriesSplitStatus.PENDING.value,
                _dt_to_text(stamp),
                _dt_to_text(stamp),
            ),
        )
        self._connection.commit()
        stored = self.get_plan(int(cursor.lastrowid))
        if stored is None:  # pragma: no cover - SQLite invariant
            raise RuntimeError("Split plan was not persisted.")
        return stored

    # ---- queries -----------------------------------------------------------

    def get_plan(self, plan_id: int) -> Optional[RemoteSeriesSplitPlanRecord]:
        row = self._connection.execute(
            "SELECT * FROM calendar_series_remote_splits WHERE id = ?",
            (plan_id,),
        ).fetchone()
        return _row_to_record(row) if row is not None else None

    def get_active_plan(
        self, series_uid: str
    ) -> Optional[RemoteSeriesSplitPlanRecord]:
        placeholders = ",".join("?" for _ in _ACTIVE_STATE_VALUES)
        row = self._connection.execute(
            "SELECT * FROM calendar_series_remote_splits "
            f"WHERE source_series_uid = ? AND state IN ({placeholders}) "
            "ORDER BY id DESC LIMIT 1",
            (series_uid, *_ACTIVE_STATE_VALUES),
        ).fetchone()
        return _row_to_record(row) if row is not None else None

    def get_active_plan_by_source_remote(
        self, remote_event_id: str
    ) -> Optional[RemoteSeriesSplitPlanRecord]:
        placeholders = ",".join("?" for _ in _ACTIVE_STATE_VALUES)
        row = self._connection.execute(
            "SELECT * FROM calendar_series_remote_splits "
            f"WHERE source_remote_event_id = ? AND state IN ({placeholders}) "
            "ORDER BY id DESC LIMIT 1",
            (remote_event_id, *_ACTIVE_STATE_VALUES),
        ).fetchone()
        return _row_to_record(row) if row is not None else None

    def get_plan_by_successor_uid(
        self, series_uid: str
    ) -> Optional[RemoteSeriesSplitPlanRecord]:
        row = self._connection.execute(
            "SELECT * FROM calendar_series_remote_splits "
            "WHERE reserved_successor_series_uid = ? ORDER BY id DESC LIMIT 1",
            (series_uid,),
        ).fetchone()
        return _row_to_record(row) if row is not None else None

    def get_plan_by_successor_remote(
        self, remote_event_id: str
    ) -> Optional[RemoteSeriesSplitPlanRecord]:
        row = self._connection.execute(
            "SELECT * FROM calendar_series_remote_splits "
            "WHERE successor_remote_event_id = ? ORDER BY id DESC LIMIT 1",
            (remote_event_id,),
        ).fetchone()
        return _row_to_record(row) if row is not None else None

    def list_plans(
        self,
        *,
        states: Optional[Sequence[RemoteSeriesSplitStatus]] = None,
        series_uid: Optional[str] = None,
    ) -> list[RemoteSeriesSplitPlanRecord]:
        query = "SELECT * FROM calendar_series_remote_splits"
        clauses: list[str] = []
        params: list[Any] = []
        if states:
            values = tuple(state.value for state in states)
            clauses.append(
                "state IN (" + ",".join("?" for _ in values) + ")"
            )
            params.extend(values)
        if series_uid is not None:
            clauses.append("source_series_uid = ?")
            params.append(series_uid)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        rows = self._connection.execute(
            query + " ORDER BY id DESC", tuple(params)
        ).fetchall()
        return [_row_to_record(row) for row in rows]

    def list_processable_plans(self) -> list[RemoteSeriesSplitPlanRecord]:
        placeholders = ",".join("?" for _ in _PROCESSABLE_STATE_VALUES)
        rows = self._connection.execute(
            "SELECT * FROM calendar_series_remote_splits "
            f"WHERE state IN ({placeholders}) ORDER BY id",
            _PROCESSABLE_STATE_VALUES,
        ).fetchall()
        return [_row_to_record(row) for row in rows]

    def counts_by_state(self) -> dict[str, int]:
        counts = {state.value: 0 for state in RemoteSeriesSplitStatus}
        rows = self._connection.execute(
            "SELECT state, COUNT(*) AS n FROM calendar_series_remote_splits "
            "GROUP BY state"
        ).fetchall()
        for row in rows:
            counts[str(row["state"])] = int(row["n"])
        return counts

    # ---- state transitions -------------------------------------------------

    def _transition(
        self,
        plan_id: int,
        state: RemoteSeriesSplitStatus,
        *,
        error: Optional[str] = None,
        source_trimmed_remote_etag: Optional[str] = None,
        successor_remote_etag: Optional[str] = None,
        completed: bool = False,
        bump_attempts: bool = False,
    ) -> Optional[RemoteSeriesSplitPlanRecord]:
        now = self._clock()
        sql = (
            "UPDATE calendar_series_remote_splits SET state = ?, "
            "last_error = ?, updated_at = ?"
        )
        params: list[Any] = [state.value, error, _dt_to_text(now)]
        if source_trimmed_remote_etag is not None:
            sql += ", source_trimmed_remote_etag = ?"
            params.append(source_trimmed_remote_etag)
        if successor_remote_etag is not None:
            sql += ", successor_remote_etag = ?"
            params.append(successor_remote_etag)
        if completed:
            sql += ", completed_at = ?"
            params.append(_dt_to_text(now))
        if bump_attempts:
            sql += ", attempts = attempts + 1"
        sql += " WHERE id = ?"
        params.append(plan_id)
        cursor = self._connection.execute(sql, tuple(params))
        if cursor.rowcount == 0:
            self._connection.rollback()
            return None
        self._connection.commit()
        return self.get_plan(plan_id)

    def mark_source_trimmed(
        self, plan_id: int, *, remote_etag: str
    ) -> Optional[RemoteSeriesSplitPlanRecord]:
        return self._transition(
            plan_id,
            RemoteSeriesSplitStatus.SOURCE_TRIMMED,
            source_trimmed_remote_etag=remote_etag,
        )

    def mark_successor_created(
        self, plan_id: int, *, remote_etag: str
    ) -> Optional[RemoteSeriesSplitPlanRecord]:
        return self._transition(
            plan_id,
            RemoteSeriesSplitStatus.SUCCESSOR_CREATED,
            successor_remote_etag=remote_etag,
        )

    def mark_conflict(
        self, plan_id: int, error: str
    ) -> Optional[RemoteSeriesSplitPlanRecord]:
        return self._transition(
            plan_id, RemoteSeriesSplitStatus.CONFLICT, error=error
        )

    def mark_terminal(
        self, plan_id: int, error: str
    ) -> Optional[RemoteSeriesSplitPlanRecord]:
        return self._transition(
            plan_id, RemoteSeriesSplitStatus.TERMINAL_ERROR, error=error,
            completed=True,
        )

    def mark_rollback_pending(
        self, plan_id: int, *, reason: str
    ) -> Optional[RemoteSeriesSplitPlanRecord]:
        return self._transition(
            plan_id, RemoteSeriesSplitStatus.ROLLBACK_PENDING, error=reason
        )

    def mark_successor_removed_for_rollback(
        self, plan_id: int
    ) -> Optional[RemoteSeriesSplitPlanRecord]:
        return self._transition(
            plan_id, RemoteSeriesSplitStatus.SUCCESSOR_REMOVED_FOR_ROLLBACK
        )

    def mark_rolled_back(
        self, plan_id: int, *, note: Optional[str] = None
    ) -> Optional[RemoteSeriesSplitPlanRecord]:
        return self._transition(
            plan_id, RemoteSeriesSplitStatus.ROLLED_BACK, error=note,
            completed=True,
        )

    def record_attempt_error(self, plan_id: int, error: str) -> None:
        now = self._clock()
        self._connection.execute(
            "UPDATE calendar_series_remote_splits SET attempts = attempts + 1, "
            "last_error = ?, updated_at = ? WHERE id = ?",
            (error, _dt_to_text(now), plan_id),
        )
        self._connection.commit()

    def update_remote_etags(
        self,
        plan_id: int,
        *,
        source_trimmed_remote_etag: Optional[str] = None,
        successor_remote_etag: Optional[str] = None,
    ) -> None:
        """Refresh acknowledged ETags after an expected split echo on pull."""
        assignments: list[str] = []
        params: list[Any] = []
        if source_trimmed_remote_etag is not None:
            assignments.append("source_trimmed_remote_etag = ?")
            params.append(source_trimmed_remote_etag)
        if successor_remote_etag is not None:
            assignments.append("successor_remote_etag = ?")
            params.append(successor_remote_etag)
        if not assignments:
            return
        assignments.append("updated_at = ?")
        params.append(_dt_to_text(self._clock()))
        params.append(plan_id)
        self._connection.execute(
            "UPDATE calendar_series_remote_splits SET "
            + ", ".join(assignments)
            + " WHERE id = ?",
            tuple(params),
        )
        self._connection.commit()

    def cancel_unstarted_plan(self, plan_id: int) -> bool:
        """Cancel a plan that performed zero remote steps; no Google calls."""
        now = self._clock()
        cursor = self._connection.execute(
            "UPDATE calendar_series_remote_splits SET state = ?, "
            "last_error = ?, updated_at = ?, completed_at = ? "
            "WHERE id = ? AND state = ?",
            (
                RemoteSeriesSplitStatus.ROLLED_BACK.value,
                "Отменено до каких-либо удалённых изменений.",
                _dt_to_text(now),
                _dt_to_text(now),
                plan_id,
                RemoteSeriesSplitStatus.PENDING.value,
            ),
        )
        self._connection.commit()
        return cursor.rowcount > 0

    # ---- local atomic finalization (Part 8) --------------------------------

    def finalize_linked_remote_split_atomic(
        self,
        record: RemoteSeriesSplitPlanRecord,
        *,
        trimmed_source: TaskSeries,
        successor: TaskSeries,
        replaced_task_uids: Sequence[str],
        successor_tag_ids: Sequence[int],
    ) -> None:
        """Apply the completed remote split locally in one SQLite transaction.

        Verifies the source revision, trims the local source series, creates
        the successor series under the reserved UID, removes only replaceable
        (live, uncompleted, non-exception) target-and-future rows so the
        materializer regenerates them under the successor, copies series tags,
        creates a synced successor link, refreshes the source link and marks
        the plan completed.  Completed history, past exceptions and tombstones
        are deliberately untouched; no ordinary Task Calendar operation is
        generated.
        """
        now = self._clock()
        now_text = _dt_to_text(now)
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            row = self._connection.execute(
                "SELECT revision, deleted_at FROM task_series WHERE uid = ?",
                (record.source_series_uid,),
            ).fetchone()
            if row is None or row["deleted_at"] is not None:
                raise KeyError("Исходная серия не найдена или удалена.")
            if int(row["revision"]) != int(record.source_local_revision):
                raise ValueError(
                    "Ревизия исходной серии изменилась после планирования "
                    "разделения; финализация остановлена."
                )

            trimmed = self._series_row_params(trimmed_source)
            source_cursor = self._connection.execute(
                """
                UPDATE task_series SET
                    title = ?, notes = ?, priority = ?, start_date = ?,
                    all_day = ?, local_time = ?, duration_minutes = ?,
                    timezone_name = ?, frequency = ?, interval = ?,
                    weekdays_csv = ?, month_day = ?, yearly_month = ?,
                    yearly_day = ?, end_mode = ?, until_date = ?,
                    occurrence_count = ?, revision = ?, updated_at = ?
                WHERE uid = ?
                """,
                (*trimmed[1:18], trimmed_source.revision, now_text,
                 record.source_series_uid),
            )
            if source_cursor.rowcount == 0:
                raise KeyError("Исходная серия не найдена.")

            successor_params = self._series_row_params(successor)
            self._connection.execute(
                """
                INSERT INTO task_series (
                    uid, title, notes, priority, start_date, all_day,
                    local_time, duration_minutes, timezone_name, frequency,
                    interval, weekdays_csv, month_day, yearly_month,
                    yearly_day, end_mode, until_date, occurrence_count,
                    revision, active, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (*successor_params[:18], successor.revision, now_text, now_text),
            )

            for tag_id in dict.fromkeys(int(item) for item in successor_tag_ids):
                self._connection.execute(
                    "INSERT OR IGNORE INTO series_tags "
                    "(series_uid, tag_id, created_at) VALUES (?, ?, ?)",
                    (successor.uid, tag_id, now_text),
                )

            for task_uid in dict.fromkeys(str(item) for item in replaced_task_uids):
                # Only replaceable rows selected by the caller policy; the
                # materializer recreates the slots under the successor series.
                self._connection.execute(
                    "DELETE FROM tasks WHERE uid = ? AND completed = 0 "
                    "AND is_series_exception = 0 AND deleted_at IS NULL",
                    (task_uid,),
                )

            link_cursor = self._connection.execute(
                """
                UPDATE task_series_calendar_links SET
                    remote_etag = ?, last_synced_series_revision = ?,
                    last_synced_payload_hash = ?, link_status = ?,
                    last_error = NULL, updated_at = ?
                WHERE id = ?
                """,
                (
                    record.source_trimmed_remote_etag,
                    trimmed_source.revision,
                    record.source_trimmed_payload_hash,
                    SeriesLinkStatus.SYNCED.value,
                    now_text,
                    record.source_link_id,
                ),
            )
            if link_cursor.rowcount == 0:
                raise KeyError("Связь исходной серии не найдена.")

            self._connection.execute(
                """
                INSERT INTO task_series_calendar_links (
                    series_uid, provider, calendar_id, remote_event_id,
                    remote_etag, link_status, last_synced_series_revision,
                    last_synced_payload_hash, linked_at, updated_at,
                    link_generation
                )
                SELECT ?, provider, calendar_id, ?, ?, ?, ?, ?, ?, ?, 0
                FROM task_series_calendar_links WHERE id = ?
                """,
                (
                    successor.uid,
                    record.successor_remote_event_id,
                    record.successor_remote_etag,
                    SeriesLinkStatus.SYNCED.value,
                    successor.revision,
                    record.successor_payload_hash,
                    now_text,
                    now_text,
                    record.source_link_id,
                ),
            )

            plan_cursor = self._connection.execute(
                "UPDATE calendar_series_remote_splits SET state = ?, "
                "last_error = NULL, updated_at = ?, completed_at = ? "
                "WHERE id = ? AND state IN (?, ?)",
                (
                    RemoteSeriesSplitStatus.COMPLETED.value,
                    now_text,
                    now_text,
                    record.id,
                    RemoteSeriesSplitStatus.SUCCESSOR_CREATED.value,
                    RemoteSeriesSplitStatus.LOCAL_FINALIZE_PENDING.value,
                ),
            )
            if plan_cursor.rowcount == 0:
                raise KeyError(
                    "План разделения не находится в состоянии финализации."
                )
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise

    @staticmethod
    def _series_row_params(series: TaskSeries) -> tuple:
        schedule, rule = series.schedule, series.rule
        return (
            series.uid,
            series.title,
            series.notes,
            series.priority,
            schedule.start_date.isoformat(),
            int(schedule.all_day),
            (
                schedule.local_time.strftime("%H:%M")
                if schedule.local_time is not None else None
            ),
            schedule.duration_minutes,
            schedule.timezone_name,
            rule.frequency.value,
            int(rule.interval),
            ",".join(str(int(day)) for day in rule.weekdays),
            rule.month_day,
            rule.yearly_month,
            rule.yearly_day,
            rule.end_mode.value,
            (
                rule.until_date.isoformat()
                if rule.until_date is not None else None
            ),
            rule.occurrence_count,
        )


__all__ = ["CalendarSeriesRemoteSplitStore"]
