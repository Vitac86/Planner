"""SQLite-репозиторий ежедневных задач нового десктопа.

Тот же интерфейс, что у InMemoryDailyTaskRepository, поверх той же
изолированной БД (``PlannerDesktop/app_desktop.db``). Как и
:class:`CalendarSyncStore`, открывает собственное соединение к тому же
файлу и вызывает идемпотентный ``create_schema`` — старый ``Planner/app.db``
не открывается никогда, сети и Google API здесь нет.

Удаление ежедневной задачи — тумбстоун (``deleted_at``); отметки
выполнения по датам живут в отдельной таблице ``desktop_daily_completions``.
"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import List, Optional, Set, Union

from planner_desktop.domain.daily_task import DailyTask
from planner_desktop.domain.task import utc_now
from planner_desktop.storage.paths import ensure_desktop_data_dir, get_desktop_db_path
from planner_desktop.storage.schema import create_schema


def _dt_to_text(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value is not None else None


def _text_to_dt(value: Optional[str]) -> Optional[datetime]:
    return datetime.fromisoformat(value) if value else None


def _row_to_daily(row: sqlite3.Row) -> DailyTask:
    return DailyTask(
        title=row["title"],
        id=row["id"],
        uid=row["uid"],
        notes=row["notes"],
        enabled=bool(row["enabled"]),
        weekdays_mask=row["weekdays_mask"],
        preferred_time=row["preferred_time"],
        created_at=_text_to_dt(row["created_at"]) or utc_now(),
        updated_at=_text_to_dt(row["updated_at"]) or utc_now(),
        deleted_at=_text_to_dt(row["deleted_at"]),
    )


class SQLiteDailyTaskRepository:
    """Ежедневные задачи и отметки выполнения в собственной SQLite-БД."""

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

    # ---- CRUD задач ----------------------------------------------------------

    def add(self, task: DailyTask) -> DailyTask:
        cursor = self._connection.execute(
            """
            INSERT INTO desktop_daily_tasks (
                id, uid, title, notes, enabled, weekdays_mask,
                preferred_time, created_at, updated_at, deleted_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task.id,
                task.uid,
                task.title,
                task.notes,
                int(task.enabled),
                int(task.weekdays_mask),
                task.preferred_time,
                _dt_to_text(task.created_at),
                _dt_to_text(task.updated_at),
                _dt_to_text(task.deleted_at),
            ),
        )
        self._connection.commit()
        if task.id is None:
            task.id = cursor.lastrowid
        return task

    def update(self, task: DailyTask) -> DailyTask:
        if task.id is None:
            raise ValueError("Нельзя обновить ежедневную задачу без id — сначала add()")
        task.touch()
        self._connection.execute(
            """
            UPDATE desktop_daily_tasks SET
                uid = ?, title = ?, notes = ?, enabled = ?, weekdays_mask = ?,
                preferred_time = ?, updated_at = ?, deleted_at = ?
            WHERE id = ?
            """,
            (
                task.uid,
                task.title,
                task.notes,
                int(task.enabled),
                int(task.weekdays_mask),
                task.preferred_time,
                _dt_to_text(task.updated_at),
                _dt_to_text(task.deleted_at),
                task.id,
            ),
        )
        self._connection.commit()
        return task

    def get_by_uid(self, uid: str) -> Optional[DailyTask]:
        row = self._connection.execute(
            "SELECT * FROM desktop_daily_tasks WHERE uid = ?", (uid,)
        ).fetchone()
        return _row_to_daily(row) if row is not None else None

    def list_all(self) -> List[DailyTask]:
        rows = self._connection.execute(
            "SELECT * FROM desktop_daily_tasks WHERE deleted_at IS NULL ORDER BY id"
        ).fetchall()
        return [_row_to_daily(row) for row in rows]

    def delete(self, uid: str) -> bool:
        task = self.get_by_uid(uid)
        if task is None or task.is_deleted:
            return False
        task.mark_deleted()
        self._connection.execute(
            "UPDATE desktop_daily_tasks SET deleted_at = ?, updated_at = ? WHERE id = ?",
            (_dt_to_text(task.deleted_at), _dt_to_text(task.updated_at), task.id),
        )
        self._connection.commit()
        return True

    # ---- отметки выполнения по датам -----------------------------------------

    def set_completed(self, uid: str, day: date, completed: bool) -> None:
        stamp = day.isoformat()
        if completed:
            self._connection.execute(
                """
                INSERT INTO desktop_daily_completions (daily_uid, done_date, completed_at)
                VALUES (?, ?, ?)
                ON CONFLICT(daily_uid, done_date) DO UPDATE SET
                    completed_at = excluded.completed_at
                """,
                (uid, stamp, _dt_to_text(utc_now())),
            )
        else:
            self._connection.execute(
                "DELETE FROM desktop_daily_completions WHERE daily_uid = ? AND done_date = ?",
                (uid, stamp),
            )
        self._connection.commit()

    def is_completed(self, uid: str, day: date) -> bool:
        row = self._connection.execute(
            "SELECT 1 FROM desktop_daily_completions "
            "WHERE daily_uid = ? AND done_date = ? LIMIT 1",
            (uid, day.isoformat()),
        ).fetchone()
        return row is not None

    def completed_uids_for(self, day: date) -> Set[str]:
        rows = self._connection.execute(
            "SELECT daily_uid FROM desktop_daily_completions WHERE done_date = ?",
            (day.isoformat(),),
        ).fetchall()
        return {row["daily_uid"] for row in rows}
