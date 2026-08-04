"""SQLite store and atomic local finalization for series adoption (schema v12)."""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable, Optional, Sequence, Union

from planner_desktop.domain.recurrence import TaskSeries, generate_occurrences
from planner_desktop.domain.series_adoption import (
    ACTIVE_ADOPTION_STATES,
    PROCESSABLE_ADOPTION_STATES,
    CalendarSeriesAdoption,
    SeriesAdoptionState,
)
from planner_desktop.domain.task import utc_now
from planner_desktop.storage.paths import ensure_desktop_data_dir, get_desktop_db_path
from planner_desktop.storage.schema import create_schema


def _dt_to_text(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value is not None else None


def _text_to_dt(value: Optional[str]) -> Optional[datetime]:
    return datetime.fromisoformat(value) if value else None


def _row_to_record(row: sqlite3.Row) -> CalendarSeriesAdoption:
    return CalendarSeriesAdoption(
        id=int(row["id"]),
        provider=str(row["provider"]),
        calendar_id=str(row["calendar_id"]),
        remote_event_id=str(row["remote_event_id"]),
        external_catalog_id=row["external_catalog_id"],
        reserved_series_uid=str(row["reserved_series_uid"]),
        remote_etag_base=str(row["remote_etag_base"]),
        remote_snapshot_json=str(row["remote_snapshot_json"]),
        canonical_remote_payload_hash=str(row["canonical_remote_payload_hash"]),
        preexisting_private_properties_json=str(
            row["preexisting_private_properties_json"] or "{}"
        ),
        claimed_private_properties_json=row["claimed_private_properties_json"],
        claimed_remote_etag=row["claimed_remote_etag"],
        local_link_id=row["local_link_id"],
        state=SeriesAdoptionState(str(row["state"])),
        attempts=int(row["attempts"] or 0),
        last_error=row["last_error"],
        conflict_snapshot_json=row["conflict_snapshot_json"],
        created_at=_text_to_dt(row["created_at"]) or utc_now(),
        updated_at=_text_to_dt(row["updated_at"]) or utc_now(),
        completed_at=_text_to_dt(row["completed_at"]),
        rolled_back_at=_text_to_dt(row["rolled_back_at"]),
        release_requested_at=_text_to_dt(row["release_requested_at"]),
        released_at=_text_to_dt(row["released_at"]),
    )


_ACTIVE_VALUES = tuple(state.value for state in ACTIVE_ADOPTION_STATES)
_PROCESSABLE_VALUES = tuple(state.value for state in PROCESSABLE_ADOPTION_STATES)


class CalendarSeriesAdoptionStore:
    """Durable adoption plans plus cross-table finalization transactions."""

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

    # ---- plans -------------------------------------------------------------

    def create_plan(
        self, record: CalendarSeriesAdoption
    ) -> CalendarSeriesAdoption:
        existing = self.get_active_adoption(
            record.provider, record.calendar_id, record.remote_event_id
        )
        if existing is not None:
            return existing
        stamp = self._clock()
        try:
            cursor = self._connection.execute(
                """
                INSERT INTO calendar_series_adoptions (
                    provider, calendar_id, remote_event_id, external_catalog_id,
                    reserved_series_uid, remote_etag_base, remote_snapshot_json,
                    canonical_remote_payload_hash,
                    preexisting_private_properties_json, state, attempts,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
                """,
                (
                    record.provider,
                    record.calendar_id,
                    record.remote_event_id,
                    record.external_catalog_id,
                    record.reserved_series_uid,
                    record.remote_etag_base,
                    record.remote_snapshot_json,
                    record.canonical_remote_payload_hash,
                    record.preexisting_private_properties_json,
                    SeriesAdoptionState.PENDING.value,
                    _dt_to_text(stamp),
                    _dt_to_text(stamp),
                ),
            )
            self._connection.execute(
                "UPDATE external_calendar_series SET adoption_state = ? "
                "WHERE id = ?",
                (
                    SeriesAdoptionState.PENDING.value,
                    record.external_catalog_id,
                ),
            )
            self._connection.commit()
        except sqlite3.IntegrityError:
            self._connection.rollback()
            existing = self.get_active_adoption(
                record.provider, record.calendar_id, record.remote_event_id
            )
            if existing is not None:
                return existing
            raise
        stored = self.get_plan(int(cursor.lastrowid))
        if stored is None:  # pragma: no cover
            raise RuntimeError("Adoption plan was not persisted.")
        return stored

    def get_plan(self, plan_id: int) -> Optional[CalendarSeriesAdoption]:
        row = self._connection.execute(
            "SELECT * FROM calendar_series_adoptions WHERE id = ?",
            (int(plan_id),),
        ).fetchone()
        return _row_to_record(row) if row is not None else None

    def get_active_adoption(
        self, provider: str, calendar_id: str, remote_event_id: str
    ) -> Optional[CalendarSeriesAdoption]:
        placeholders = ",".join("?" for _ in _ACTIVE_VALUES)
        row = self._connection.execute(
            "SELECT * FROM calendar_series_adoptions "
            "WHERE provider = ? AND calendar_id = ? AND remote_event_id = ? "
            f"AND state IN ({placeholders}) ORDER BY id DESC LIMIT 1",
            (provider, calendar_id, remote_event_id, *_ACTIVE_VALUES),
        ).fetchone()
        return _row_to_record(row) if row is not None else None

    def get_adoption_by_remote(
        self, provider: str, calendar_id: str, remote_event_id: str
    ) -> Optional[CalendarSeriesAdoption]:
        row = self._connection.execute(
            "SELECT * FROM calendar_series_adoptions "
            "WHERE provider = ? AND calendar_id = ? AND remote_event_id = ? "
            "ORDER BY id DESC LIMIT 1",
            (provider, calendar_id, remote_event_id),
        ).fetchone()
        return _row_to_record(row) if row is not None else None

    def get_adoption_by_series(
        self, series_uid: str
    ) -> Optional[CalendarSeriesAdoption]:
        row = self._connection.execute(
            "SELECT * FROM calendar_series_adoptions "
            "WHERE reserved_series_uid = ? ORDER BY id DESC LIMIT 1",
            (str(series_uid),),
        ).fetchone()
        return _row_to_record(row) if row is not None else None

    def list_adoptions(
        self, *, states: Optional[Sequence[SeriesAdoptionState]] = None
    ) -> list[CalendarSeriesAdoption]:
        query = "SELECT * FROM calendar_series_adoptions"
        params: tuple[object, ...] = ()
        if states:
            values = tuple(state.value for state in states)
            query += " WHERE state IN (" + ",".join("?" for _ in values) + ")"
            params = values
        rows = self._connection.execute(query + " ORDER BY id", params).fetchall()
        return [_row_to_record(row) for row in rows]

    def list_processable(self) -> list[CalendarSeriesAdoption]:
        placeholders = ",".join("?" for _ in _PROCESSABLE_VALUES)
        rows = self._connection.execute(
            "SELECT * FROM calendar_series_adoptions "
            f"WHERE state IN ({placeholders}) ORDER BY id",
            _PROCESSABLE_VALUES,
        ).fetchall()
        return [_row_to_record(row) for row in rows]

    def counts_by_state(self) -> dict[str, int]:
        rows = self._connection.execute(
            "SELECT state, COUNT(*) AS n FROM calendar_series_adoptions "
            "GROUP BY state"
        ).fetchall()
        return {str(row["state"]): int(row["n"]) for row in rows}

    def _transition(
        self,
        plan_id: int,
        state: SeriesAdoptionState,
        *,
        error: Optional[str] = None,
        conflict_snapshot_json: Optional[str] = None,
        increment_attempts: bool = False,
        completed: bool = False,
        rolled_back: bool = False,
        released: bool = False,
    ) -> None:
        now = self._clock()
        assignments = [
            "state = ?",
            "last_error = ?",
            "updated_at = ?",
        ]
        values: list[object] = [state.value, error, _dt_to_text(now)]
        if conflict_snapshot_json is not None:
            assignments.append("conflict_snapshot_json = ?")
            values.append(conflict_snapshot_json)
        if increment_attempts:
            assignments.append("attempts = attempts + 1")
        if completed:
            assignments.append("completed_at = ?")
            values.append(_dt_to_text(now))
        if rolled_back:
            assignments.append("rolled_back_at = ?")
            values.append(_dt_to_text(now))
        if released:
            assignments.append("released_at = ?")
            values.append(_dt_to_text(now))
        values.append(int(plan_id))
        with self._connection:
            self._connection.execute(
                "UPDATE calendar_series_adoptions SET "
                + ", ".join(assignments)
                + " WHERE id = ?",
                tuple(values),
            )
            self._connection.execute(
                "UPDATE external_calendar_series SET adoption_state = ? "
                "WHERE id = (SELECT external_catalog_id FROM "
                "calendar_series_adoptions WHERE id = ?)",
                (state.value, int(plan_id)),
            )

    def mark_remote_claimed(
        self,
        plan_id: int,
        *,
        claimed_private_properties: dict[str, str],
        claimed_remote_etag: str,
    ) -> None:
        now = self._clock()
        payload = json.dumps(
            claimed_private_properties,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        with self._connection:
            self._connection.execute(
                "UPDATE calendar_series_adoptions SET state = ?, "
                "claimed_private_properties_json = ?, claimed_remote_etag = ?, "
                "last_error = NULL, attempts = attempts + 1, updated_at = ? "
                "WHERE id = ? AND state = ?",
                (
                    SeriesAdoptionState.REMOTE_CLAIMED.value,
                    payload,
                    claimed_remote_etag,
                    _dt_to_text(now),
                    int(plan_id),
                    SeriesAdoptionState.PENDING.value,
                ),
            )
            self._connection.execute(
                "UPDATE external_calendar_series SET adoption_state = ? "
                "WHERE id = (SELECT external_catalog_id FROM "
                "calendar_series_adoptions WHERE id = ?)",
                (SeriesAdoptionState.REMOTE_CLAIMED.value, int(plan_id)),
            )

    def update_claimed_etag(self, plan_id: int, etag: str) -> None:
        with self._connection:
            self._connection.execute(
                "UPDATE calendar_series_adoptions SET claimed_remote_etag = ?, "
                "updated_at = ? WHERE id = ?",
                (etag, _dt_to_text(self._clock()), int(plan_id)),
            )

    def mark_local_finalize_pending(self, plan_id: int, error: str) -> None:
        self._transition(
            plan_id,
            SeriesAdoptionState.LOCAL_FINALIZE_PENDING,
            error=error,
            increment_attempts=True,
        )

    def mark_conflict(
        self, plan_id: int, error: str, snapshot_json: Optional[str] = None
    ) -> None:
        plan = self.get_plan(plan_id)
        state = (
            SeriesAdoptionState.RELEASE_CONFLICT
            if plan is not None
            and plan.state
            in (SeriesAdoptionState.RELEASE_PENDING, SeriesAdoptionState.RELEASE_CONFLICT)
            else SeriesAdoptionState.CONFLICT
        )
        self._transition(
            plan_id,
            state,
            error=error,
            conflict_snapshot_json=snapshot_json,
            increment_attempts=True,
        )

    def mark_remote_deleted(self, plan_id: int) -> None:
        self._transition(
            plan_id,
            SeriesAdoptionState.REMOTE_DELETED,
            error="Удалённый recurring master больше не существует.",
        )

    def mark_terminal(self, plan_id: int, error: str) -> None:
        self._transition(
            plan_id,
            SeriesAdoptionState.TERMINAL_ERROR,
            error=error,
            increment_attempts=True,
        )

    def cancel_unstarted(self, plan_id: int) -> bool:
        now = self._clock()
        with self._connection:
            cursor = self._connection.execute(
                "UPDATE calendar_series_adoptions SET state = ?, last_error = ?, "
                "updated_at = ?, rolled_back_at = ? WHERE id = ? AND state = ?",
                (
                    SeriesAdoptionState.ROLLED_BACK.value,
                    "Отменено до удалённого claim.",
                    _dt_to_text(now),
                    _dt_to_text(now),
                    int(plan_id),
                    SeriesAdoptionState.PENDING.value,
                ),
            )
            if cursor.rowcount:
                self._connection.execute(
                    "UPDATE external_calendar_series SET adoption_state = NULL "
                    "WHERE id = (SELECT external_catalog_id FROM "
                    "calendar_series_adoptions WHERE id = ?)",
                    (int(plan_id),),
                )
        return bool(cursor.rowcount)

    def request_rollback(self, plan_id: int) -> bool:
        now = self._clock()
        allowed = (
            SeriesAdoptionState.REMOTE_CLAIMED.value,
            SeriesAdoptionState.LOCAL_FINALIZE_PENDING.value,
            SeriesAdoptionState.CONFLICT.value,
        )
        with self._connection:
            cursor = self._connection.execute(
                "UPDATE calendar_series_adoptions SET state = ?, last_error = NULL, "
                "updated_at = ? WHERE id = ? AND state IN (?,?,?)",
                (
                    SeriesAdoptionState.ROLLBACK_PENDING.value,
                    _dt_to_text(now),
                    int(plan_id),
                    *allowed,
                ),
            )
        return bool(cursor.rowcount)

    def mark_rolled_back(self, plan_id: int) -> None:
        self._transition(
            plan_id,
            SeriesAdoptionState.ROLLED_BACK,
            rolled_back=True,
        )
        with self._connection:
            self._connection.execute(
                "UPDATE external_calendar_series SET adoption_state = NULL, "
                "adopted_series_uid = NULL WHERE id = (SELECT external_catalog_id "
                "FROM calendar_series_adoptions WHERE id = ?)",
                (int(plan_id),),
            )

    def request_release(self, series_uid: str) -> bool:
        plan = self.get_adoption_by_series(series_uid)
        if plan is None or plan.state is not SeriesAdoptionState.COMPLETED:
            return False
        now = self._clock()
        with self._connection:
            cursor = self._connection.execute(
                "UPDATE calendar_series_adoptions SET state = ?, "
                "release_requested_at = ?, updated_at = ?, last_error = NULL "
                "WHERE id = ? AND state = ?",
                (
                    SeriesAdoptionState.RELEASE_PENDING.value,
                    _dt_to_text(now),
                    _dt_to_text(now),
                    plan.id,
                    SeriesAdoptionState.COMPLETED.value,
                ),
            )
            self._connection.execute(
                "UPDATE external_calendar_series SET adoption_state = ? "
                "WHERE id = ?",
                (
                    SeriesAdoptionState.RELEASE_PENDING.value,
                    plan.external_catalog_id,
                ),
            )
        return bool(cursor.rowcount)

    def retry(self, plan_id: int) -> bool:
        plan = self.get_plan(plan_id)
        if plan is None:
            return False
        target: Optional[SeriesAdoptionState] = None
        if plan.state is SeriesAdoptionState.CONFLICT:
            target = (
                SeriesAdoptionState.LOCAL_FINALIZE_PENDING
                if plan.claimed_remote_etag
                else SeriesAdoptionState.PENDING
            )
        elif plan.state is SeriesAdoptionState.RELEASE_CONFLICT:
            target = SeriesAdoptionState.RELEASE_PENDING
        elif plan.state is SeriesAdoptionState.TERMINAL_ERROR:
            target = (
                SeriesAdoptionState.LOCAL_FINALIZE_PENDING
                if plan.claimed_remote_etag
                else SeriesAdoptionState.PENDING
            )
        if target is None:
            return False
        self._transition(plan_id, target)
        return True

    # ---- atomic local work -------------------------------------------------

    @staticmethod
    def _series_params(series: TaskSeries) -> tuple[object, ...]:
        schedule, rule = series.schedule, series.rule
        return (
            series.uid,
            series.title,
            series.notes,
            int(series.priority),
            schedule.start_date.isoformat(),
            int(schedule.all_day),
            schedule.local_time.strftime("%H:%M") if schedule.local_time else None,
            schedule.duration_minutes,
            schedule.timezone_name,
            rule.frequency.value,
            int(rule.interval),
            ",".join(str(item) for item in rule.weekdays),
            rule.month_day,
            rule.yearly_month,
            rule.yearly_day,
            rule.end_mode.value,
            rule.until_date.isoformat() if rule.until_date else None,
            rule.occurrence_count,
            int(series.revision),
        )

    def finalize_external_series_adoption_atomic(
        self,
        plan: CalendarSeriesAdoption,
        *,
        series: TaskSeries,
        remote_etag: str,
        remote_updated_at: Optional[datetime],
        synced_payload_hash: str,
        materialize_start: Optional[date] = None,
        materialize_end: Optional[date] = None,
    ) -> int:
        """Create series, adopted link, bounded occurrences and completion atomically."""
        now = self._clock()
        now_text = _dt_to_text(now)
        start_day = materialize_start or now.date()
        end_day = materialize_end or (start_day + timedelta(days=44))
        specs = generate_occurrences(
            series.schedule, series.rule, start_day, end_day
        )
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            if self._connection.execute(
                "SELECT 1 FROM task_series WHERE uid = ?",
                (plan.reserved_series_uid,),
            ).fetchone():
                raise ValueError("Зарезервированный UID уже используется TaskSeries.")
            if self._connection.execute(
                "SELECT 1 FROM task_series_calendar_links WHERE provider = ? "
                "AND calendar_id = ? AND remote_event_id = ? "
                "AND link_status <> 'detached'",
                (plan.provider, plan.calendar_id, plan.remote_event_id),
            ).fetchone():
                raise ValueError("Удалённый master уже связан с TaskSeries.")
            if series.uid != plan.reserved_series_uid:
                raise ValueError("TaskSeries не использует зарезервированный UID.")

            params = self._series_params(series)
            self._connection.execute(
                """
                INSERT INTO task_series (
                    uid, title, notes, priority, start_date, all_day,
                    local_time, duration_minutes, timezone_name, frequency,
                    interval, weekdays_csv, month_day, yearly_month, yearly_day,
                    end_mode, until_date, occurrence_count, revision, active,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                          ?, 1, ?, ?)
                """,
                (*params, now_text, now_text),
            )
            link_cursor = self._connection.execute(
                """
                INSERT INTO task_series_calendar_links (
                    series_uid, provider, calendar_id, remote_event_id,
                    remote_etag, remote_updated_at, link_status,
                    last_synced_series_revision, last_synced_payload_hash,
                    linked_at, updated_at, link_generation, link_origin,
                    remote_id_strategy, adoption_id, ownership_claimed_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'synced', ?, ?, ?, ?, 0,
                          'adopted_external', 'external_existing', ?, ?)
                """,
                (
                    series.uid,
                    plan.provider,
                    plan.calendar_id,
                    plan.remote_event_id,
                    remote_etag,
                    _dt_to_text(remote_updated_at),
                    series.revision,
                    synced_payload_hash,
                    now_text,
                    now_text,
                    plan.id,
                    now_text,
                ),
            )
            link_id = int(link_cursor.lastrowid)

            for spec in specs:
                task_uid = str(uuid.uuid4())
                self._connection.execute(
                    """
                    INSERT INTO tasks (
                        uid, title, notes, start, "end", duration_minutes,
                        is_all_day, priority, completed, completed_at,
                        google_calendar_event_id, google_calendar_etag,
                        google_calendar_recurring_event_id,
                        google_calendar_original_start, series_uid,
                        occurrence_key, series_revision, is_series_exception,
                        updated_at, deleted_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, NULL, NULL, NULL, NULL,
                              NULL, ?, ?, ?, 0, ?, NULL)
                    """,
                    (
                        task_uid,
                        series.title,
                        series.notes,
                        spec.start.isoformat(),
                        spec.end.isoformat(),
                        (
                            None
                            if spec.all_day
                            else series.schedule.duration_minutes
                        ),
                        int(spec.all_day),
                        int(series.priority),
                        series.uid,
                        spec.occurrence_key,
                        series.revision,
                        now_text,
                    ),
                )

            self._connection.execute(
                "UPDATE external_calendar_series SET planner_owned = 1, "
                "linked_series_uid = ?, adopted_series_uid = ?, "
                "adoption_state = ?, etag = ?, planner_payload_hash = ? "
                "WHERE provider = ? AND calendar_id = ? AND remote_event_id = ?",
                (
                    series.uid,
                    series.uid,
                    SeriesAdoptionState.COMPLETED.value,
                    remote_etag,
                    synced_payload_hash,
                    plan.provider,
                    plan.calendar_id,
                    plan.remote_event_id,
                ),
            )
            cursor = self._connection.execute(
                "UPDATE calendar_series_adoptions SET state = ?, "
                "claimed_remote_etag = ?, local_link_id = ?, last_error = NULL, "
                "updated_at = ?, completed_at = ? WHERE id = ? "
                "AND state IN (?, ?)",
                (
                    SeriesAdoptionState.COMPLETED.value,
                    remote_etag,
                    link_id,
                    now_text,
                    now_text,
                    plan.id,
                    SeriesAdoptionState.REMOTE_CLAIMED.value,
                    SeriesAdoptionState.LOCAL_FINALIZE_PENDING.value,
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError("План не находится в состоянии локальной финализации.")
            self._connection.commit()
            return link_id
        except Exception:
            self._connection.rollback()
            raise

    def finalize_release_atomic(
        self,
        plan: CalendarSeriesAdoption,
        *,
        released_remote_etag: Optional[str],
    ) -> None:
        """Detach only the adopted link; keep the local TaskSeries and history."""
        now = self._clock()
        now_text = _dt_to_text(now)
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            cursor = self._connection.execute(
                "UPDATE task_series_calendar_links SET link_status = 'detached', "
                "remote_etag = ?, updated_at = ?, detached_at = ?, "
                "last_error = NULL, resolution_kind = 'release' "
                "WHERE id = ? AND link_origin = 'adopted_external' "
                "AND link_status <> 'detached'",
                (released_remote_etag, now_text, now_text, plan.local_link_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("Активная adopted_external связь не найдена.")
            self._connection.execute(
                "DELETE FROM pending_calendar_series_ops WHERE series_uid = ? "
                "AND status = 'pending'",
                (plan.reserved_series_uid,),
            )
            self._connection.execute(
                "DELETE FROM pending_calendar_series_instance_ops "
                "WHERE series_uid = ? AND status = 'pending'",
                (plan.reserved_series_uid,),
            )
            self._connection.execute(
                "UPDATE external_calendar_series SET planner_owned = 0, "
                "linked_series_uid = NULL, adopted_series_uid = NULL, "
                "adoption_state = NULL, planner_payload_hash = NULL, etag = ? "
                "WHERE provider = ? AND calendar_id = ? AND remote_event_id = ?",
                (
                    released_remote_etag,
                    plan.provider,
                    plan.calendar_id,
                    plan.remote_event_id,
                ),
            )
            plan_cursor = self._connection.execute(
                "UPDATE calendar_series_adoptions SET state = ?, "
                "released_at = ?, updated_at = ?, last_error = NULL "
                "WHERE id = ? AND state = ?",
                (
                    SeriesAdoptionState.RELEASED.value,
                    now_text,
                    now_text,
                    plan.id,
                    SeriesAdoptionState.RELEASE_PENDING.value,
                ),
            )
            if plan_cursor.rowcount != 1:
                raise ValueError("План не находится в состоянии release_pending.")
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise


__all__ = ["CalendarSeriesAdoptionStore"]
