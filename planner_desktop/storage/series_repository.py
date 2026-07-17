"""SQLite-репозиторий локальных повторяющихся серий (Phase 3.2A).

Живёт в той же изолированной БД PlannerDesktop, что и задачи. Никаких
Google-полей, сетевых вызовов и Calendar-операций: серии строго локальны
в этой фазе. Схема — storage/schema.py (v6), миграция аддитивна.
"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime, time
from pathlib import Path
from typing import List, Optional, Sequence, Union

from planner_desktop.domain.recurrence import (
    RecurrenceEndMode,
    RecurrenceFrequency,
    RecurrenceRule,
    SeriesSchedule,
    TaskSeries,
)
from planner_desktop.domain.task import utc_now
from planner_desktop.domain.task import Task
from planner_desktop.storage.paths import ensure_desktop_data_dir, get_desktop_db_path
from planner_desktop.storage.schema import create_schema


def _dt_to_text(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value is not None else None


def _text_to_dt(value: Optional[str]) -> Optional[datetime]:
    return datetime.fromisoformat(value) if value else None


def _date_to_text(value: Optional[date]) -> Optional[str]:
    return value.isoformat() if value is not None else None


def _text_to_date(value: Optional[str]) -> Optional[date]:
    return date.fromisoformat(value) if value else None


def _time_to_text(value: Optional[time]) -> Optional[str]:
    return value.strftime("%H:%M") if value is not None else None


def _text_to_time(value: Optional[str]) -> Optional[time]:
    if not value:
        return None
    hours, minutes = value.split(":", 1)
    return time(int(hours), int(minutes))


def weekdays_to_csv(weekdays: Sequence[int]) -> str:
    return ",".join(str(int(d)) for d in weekdays)


def csv_to_weekdays(text: Optional[str]) -> tuple:
    if not text:
        return ()
    return tuple(int(part) for part in text.split(",") if part.strip() != "")


def _row_to_series(row: sqlite3.Row) -> TaskSeries:
    schedule = SeriesSchedule(
        start_date=_text_to_date(row["start_date"]),
        all_day=bool(row["all_day"]),
        local_time=_text_to_time(row["local_time"]),
        duration_minutes=row["duration_minutes"],
        timezone_name=row["timezone_name"],
    )
    rule = RecurrenceRule(
        frequency=RecurrenceFrequency(row["frequency"]),
        interval=int(row["interval"]),
        weekdays=csv_to_weekdays(row["weekdays_csv"]),
        month_day=row["month_day"],
        yearly_month=row["yearly_month"],
        yearly_day=row["yearly_day"],
        end_mode=RecurrenceEndMode(row["end_mode"]),
        until_date=_text_to_date(row["until_date"]),
        occurrence_count=row["occurrence_count"],
    )
    return TaskSeries(
        title=row["title"],
        schedule=schedule,
        rule=rule,
        id=row["id"],
        uid=row["uid"],
        notes=row["notes"],
        priority=row["priority"],
        revision=int(row["revision"]),
        active=bool(row["active"]),
        created_at=_text_to_dt(row["created_at"]) or utc_now(),
        updated_at=_text_to_dt(row["updated_at"]) or utc_now(),
        deleted_at=_text_to_dt(row["deleted_at"]),
    )


class SQLiteSeriesRepository:
    """Хранит TaskSeries + связи серия-тег в app_desktop.db."""

    def __init__(self, db_path: Union[Path, str, None] = None) -> None:
        if db_path is None:
            ensure_desktop_data_dir()
            db_path = get_desktop_db_path()
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(str(self.db_path))
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        create_schema(self._connection)

    def close(self) -> None:
        self._connection.close()

    # ---- CRUD ---------------------------------------------------------------

    def add(self, series: TaskSeries) -> TaskSeries:
        cursor = self._insert_series_no_commit(series)
        self._connection.commit()
        series.id = cursor.lastrowid
        return series

    def _insert_series_no_commit(self, series: TaskSeries) -> sqlite3.Cursor:
        return self._connection.execute(
            """
            INSERT INTO task_series (
                uid, title, notes, priority, start_date, all_day, local_time,
                duration_minutes, timezone_name, frequency, interval,
                weekdays_csv, month_day, yearly_month, yearly_day,
                end_mode, until_date, occurrence_count,
                revision, active, created_at, updated_at, deleted_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            self._series_params(series),
        )

    def update(self, series: TaskSeries) -> TaskSeries:
        series.touch()
        cursor = self._update_series_no_commit(series)
        if cursor.rowcount == 0:
            self._connection.rollback()
            raise KeyError("Серия не найдена")
        self._connection.commit()
        return series

    def _update_series_no_commit(self, series: TaskSeries) -> sqlite3.Cursor:
        return self._connection.execute(
            """
            UPDATE task_series SET
                title = ?, notes = ?, priority = ?, start_date = ?, all_day = ?,
                local_time = ?, duration_minutes = ?, timezone_name = ?,
                frequency = ?, interval = ?, weekdays_csv = ?, month_day = ?,
                yearly_month = ?, yearly_day = ?, end_mode = ?, until_date = ?,
                occurrence_count = ?, revision = ?, active = ?, created_at = ?,
                updated_at = ?, deleted_at = ?
            WHERE uid = ?
            """,
            self._series_params(series)[1:] + (series.uid,),
        )

    @staticmethod
    def _series_params(series: TaskSeries) -> tuple:
        schedule, rule = series.schedule, series.rule
        return (
            series.uid,
            series.title,
            series.notes,
            series.priority,
            _date_to_text(schedule.start_date),
            int(schedule.all_day),
            _time_to_text(schedule.local_time),
            schedule.duration_minutes,
            schedule.timezone_name,
            rule.frequency.value,
            int(rule.interval),
            weekdays_to_csv(rule.weekdays),
            rule.month_day,
            rule.yearly_month,
            rule.yearly_day,
            rule.end_mode.value,
            _date_to_text(rule.until_date),
            rule.occurrence_count,
            series.revision,
            int(series.active),
            _dt_to_text(series.created_at),
            _dt_to_text(series.updated_at),
            _dt_to_text(series.deleted_at),
        )

    def get_by_uid(self, uid: str) -> Optional[TaskSeries]:
        row = self._connection.execute(
            "SELECT * FROM task_series WHERE uid = ?", (uid,)
        ).fetchone()
        if row is None:
            return None
        series = _row_to_series(row)
        series.tags = tuple(self._tag_names_for(uid))
        return series

    def list_all(self, include_inactive: bool = False) -> List[TaskSeries]:
        query = "SELECT * FROM task_series WHERE deleted_at IS NULL"
        if not include_inactive:
            query += " AND active = 1"
        rows = self._connection.execute(query + " ORDER BY id").fetchall()
        result = []
        for row in rows:
            series = _row_to_series(row)
            series.tags = tuple(self._tag_names_for(series.uid))
            result.append(series)
        return result

    def delete(self, uid: str) -> bool:
        """Тумбстоун серии; исторические Task-строки не трогаются."""
        series = self.get_by_uid(uid)
        if series is None or series.is_deleted:
            return False
        series.mark_deleted()
        self._connection.execute(
            "UPDATE task_series SET deleted_at = ?, updated_at = ?, active = 0 "
            "WHERE uid = ?",
            (_dt_to_text(series.deleted_at), _dt_to_text(series.updated_at), uid),
        )
        self._connection.commit()
        return True

    # ---- теги серии ------------------------------------------------------------

    def set_series_tags(self, series_uid: str, tag_ids: Sequence[int]) -> None:
        unique = tuple(dict.fromkeys(int(item) for item in tag_ids))
        try:
            self._connection.execute(
                "DELETE FROM series_tags WHERE series_uid = ?", (series_uid,)
            )
            now = _dt_to_text(utc_now())
            for tag_id in unique:
                self._connection.execute(
                    "INSERT INTO series_tags (series_uid, tag_id, created_at) "
                    "VALUES (?, ?, ?)",
                    (series_uid, tag_id, now),
                )
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise

    def tag_ids_for_series(self, series_uid: str) -> List[int]:
        rows = self._connection.execute(
            "SELECT tag_id FROM series_tags WHERE series_uid = ? ORDER BY tag_id",
            (series_uid,),
        ).fetchall()
        return [int(row["tag_id"]) for row in rows]

    def _tag_names_for(self, series_uid: str) -> List[str]:
        rows = self._connection.execute(
            """
            SELECT tags.name
            FROM tags
            JOIN series_tags ON series_tags.tag_id = tags.id
            WHERE series_tags.series_uid = ?
            ORDER BY tags.normalized_name, tags.id
            """,
            (series_uid,),
        ).fetchall()
        return [row["name"] for row in rows]

    # ---- диагностика --------------------------------------------------------------

    def count_active(self) -> int:
        row = self._connection.execute(
            "SELECT COUNT(*) AS n FROM task_series "
            "WHERE deleted_at IS NULL AND active = 1"
        ).fetchone()
        return int(row["n"])

    # ---- atomic edit scope -------------------------------------------------

    def split_series_atomic(
        self,
        *,
        truncated: TaskSeries,
        new_series: TaskSeries,
        moved_task: Task,
        removed_task_uids: Sequence[str],
        series_tag_ids: Sequence[int],
        moved_task_tag_ids: Optional[Sequence[int]] = None,
    ) -> tuple[TaskSeries, Task]:
        """Commit a ``this_and_future`` split in one SQLite transaction.

        The task, series and tag repositories normally own separate
        connections to the same desktop database.  A split cannot therefore
        be made atomic by composing their public CRUD methods (each commits).
        This storage-level unit of work performs all related SQL through this
        single connection.  Any failure, including association writes, rolls
        the complete split back before it becomes visible.
        """
        truncated.touch()
        moved_task.touch()
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            updated = self._update_series_no_commit(truncated)
            if updated.rowcount == 0:
                raise KeyError("Серия не найдена")

            cursor = self._insert_series_no_commit(new_series)
            new_series.id = cursor.lastrowid

            now = _dt_to_text(utc_now())
            for tag_id in dict.fromkeys(int(item) for item in series_tag_ids):
                self._connection.execute(
                    "INSERT INTO series_tags (series_uid, tag_id, created_at) "
                    "VALUES (?, ?, ?)",
                    (new_series.uid, tag_id, now),
                )

            for task_uid in dict.fromkeys(str(item) for item in removed_task_uids):
                self._connection.execute(
                    "DELETE FROM tasks WHERE uid = ?", (task_uid,)
                )

            self._update_split_task_no_commit(moved_task)

            if moved_task_tag_ids is not None:
                self._connection.execute(
                    "DELETE FROM task_tags WHERE task_uid = ?",
                    (moved_task.uid,),
                )
                for tag_id in dict.fromkeys(
                    int(item) for item in moved_task_tag_ids
                ):
                    self._connection.execute(
                        "INSERT INTO task_tags (task_uid, tag_id, created_at) "
                        "VALUES (?, ?, ?)",
                        (moved_task.uid, tag_id, now),
                    )

            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise
        return new_series, moved_task

    def accept_remote_master_atomic(
        self,
        *,
        accepted: TaskSeries,
        removed_task_uids: Sequence[str],
        link_id: int,
        resolution_id: int,
        remote_etag: Optional[str],
        remote_updated_at_text: Optional[str],
        synced_payload_hash: Optional[str],
    ) -> TaskSeries:
        """Commit the "Use Google version" acceptance in one SQLite transaction.

        Phase 3.2B3A: the accepted remote definition replaces the local
        series row, future uncompleted non-exception occurrences are removed
        (the materializer recreates them), the link leaves ``conflict``, the
        pending series queue empties and the resolution audit completes — all
        atomically.  Completed history, exceptions, tombstones and tag
        associations are deliberately untouched.  Any failure rolls the whole
        acceptance back.
        """
        accepted.touch()
        now_text = _dt_to_text(utc_now())
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            updated = self._update_series_no_commit(accepted)
            if updated.rowcount == 0:
                raise KeyError("Серия не найдена")
            for task_uid in dict.fromkeys(str(item) for item in removed_task_uids):
                self._connection.execute(
                    "DELETE FROM tasks WHERE uid = ?", (task_uid,)
                )
            link_cursor = self._connection.execute(
                """
                UPDATE task_series_calendar_links SET
                    link_status = 'synced', last_error = NULL, remote_etag = ?,
                    remote_updated_at = ?, last_synced_series_revision = ?,
                    last_synced_payload_hash = ?, conflict_detected_at = NULL,
                    conflict_reason = NULL, conflict_remote_etag = NULL,
                    conflict_remote_payload_hash = NULL,
                    conflict_remote_snapshot_json = NULL, resolved_at = ?,
                    resolution_kind = 'use_google', updated_at = ?
                WHERE id = ?
                """,
                (
                    remote_etag,
                    remote_updated_at_text,
                    accepted.revision,
                    synced_payload_hash,
                    now_text,
                    now_text,
                    link_id,
                ),
            )
            if link_cursor.rowcount == 0:
                raise KeyError("Связь серии не найдена")
            self._connection.execute(
                "DELETE FROM pending_calendar_series_ops WHERE series_uid = ? "
                "AND status = 'pending'",
                (accepted.uid,),
            )
            audit_cursor = self._connection.execute(
                "UPDATE series_conflict_resolutions SET status = 'completed', "
                "local_revision_after = ?, remote_etag_after = ?, "
                "completed_at = ?, error = NULL WHERE id = ?",
                (accepted.revision, remote_etag, now_text, resolution_id),
            )
            if audit_cursor.rowcount == 0:
                raise KeyError("Запись аудита разрешения не найдена")
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise
        return accepted

    def _update_split_task_no_commit(self, task: Task) -> None:
        cursor = self._connection.execute(
            """
            UPDATE tasks SET
                title = ?, notes = ?, start = ?, "end" = ?,
                duration_minutes = ?, is_all_day = ?, priority = ?,
                completed = ?, completed_at = ?, series_uid = ?,
                occurrence_key = ?, series_revision = ?,
                is_series_exception = ?, updated_at = ?
            WHERE uid = ?
            """,
            (
                task.title,
                task.notes,
                _dt_to_text(task.start),
                _dt_to_text(task.end),
                task.duration_minutes,
                int(task.is_all_day),
                int(task.priority),
                int(task.completed),
                _dt_to_text(task.completed_at),
                task.series_uid,
                task.occurrence_key,
                task.series_revision,
                int(task.is_series_exception),
                _dt_to_text(task.updated_at),
                task.uid,
            ),
        )
        if cursor.rowcount == 0:
            raise KeyError("Экземпляр серии не найден")


__all__ = ["SQLiteSeriesRepository", "csv_to_weekdays", "weekdays_to_csv"]
