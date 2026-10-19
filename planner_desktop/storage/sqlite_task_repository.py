"""SQLite-репозиторий задач нового десктопа (экспериментальный).

Тот же интерфейс, что у FakeTaskRepository, плюс полный CRUD:
list_all/list_today/list_undated/add/update/get/delete/complete.
Удаление — всегда тумбстоун (``deleted_at``), запись физически остаётся,
чтобы будущая синхронизация могла допушить delete в Google Calendar.

Изоляция от старого приложения:
- по умолчанию БД лежит в ``<user data dir>/PlannerDesktop/app_desktop.db``
  (см. paths.py), старый ``Planner/app.db`` не открывается никогда;
- ничего из ``models/``, ``storage/``, ``services/``, ``core/`` старого
  кода не импортируется;
- никакой Google-синхронизации и сетевых вызовов здесь нет.

Для тестов путь к БД передаётся явно (tmp_path).
"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import List, Optional, Union

from planner_desktop.domain.task import Task, utc_now
from planner_desktop.domain.tags import normalized_tag_name
from planner_desktop.storage.paths import ensure_desktop_data_dir, get_desktop_db_path
from planner_desktop.storage.schema import create_schema


def _dt_to_text(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value is not None else None


def _text_to_dt(value: Optional[str]) -> Optional[datetime]:
    return datetime.fromisoformat(value) if value else None


def _row_to_task(row: sqlite3.Row, tags=()) -> Task:
    return Task(
        title=row["title"],
        id=row["id"],
        uid=row["uid"],
        notes=row["notes"],
        tags=tuple(tags),
        start=_text_to_dt(row["start"]),
        end=_text_to_dt(row["end"]),
        duration_minutes=row["duration_minutes"],
        is_all_day=bool(row["is_all_day"]),
        priority=row["priority"],
        completed=bool(row["completed"]),
        completed_at=_text_to_dt(row["completed_at"]),
        google_calendar_event_id=row["google_calendar_event_id"],
        google_calendar_etag=row["google_calendar_etag"],
        google_calendar_recurring_event_id=row["google_calendar_recurring_event_id"],
        google_calendar_original_start=_text_to_dt(row["google_calendar_original_start"]),
        series_uid=row["series_uid"],
        occurrence_key=row["occurrence_key"],
        series_revision=row["series_revision"],
        is_series_exception=bool(row["is_series_exception"]),
        updated_at=_text_to_dt(row["updated_at"]) or utc_now(),
        deleted_at=_text_to_dt(row["deleted_at"]),
    )


class SQLiteTaskRepository:
    """Хранит задачи в собственной SQLite-БД нового десктопа."""

    def __init__(self, db_path: Union[Path, str, None] = None) -> None:
        if db_path is None:
            # Явный запрос на создание каталога — только здесь, при старте
            # приложения с путём по умолчанию.
            ensure_desktop_data_dir()
            db_path = get_desktop_db_path()
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(str(self.db_path))
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        create_schema(self._connection)
        # Тот же фиксированный чек-лист, что и у FakeTaskRepository:
        # состояние галочек живёт в ViewModel, в БД не пишется.
        self.daily_titles: List[str] = [
            "Зарядка",
            "Разбор почты",
            "Итоги дня",
        ]

    def close(self) -> None:
        self._connection.close()

    # ---- CRUD ---------------------------------------------------------------

    def add(self, task: Task) -> Task:
        try:
            cursor = self._connection.execute(
                """
            INSERT INTO tasks (
                id, uid, title, notes, start, "end", duration_minutes,
                is_all_day, priority, completed, completed_at,
                google_calendar_event_id, google_calendar_etag,
                google_calendar_recurring_event_id, google_calendar_original_start,
                series_uid, occurrence_key, series_revision, is_series_exception,
                updated_at, deleted_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                task.id,
                task.uid,
                task.title,
                task.notes,
                _dt_to_text(task.start),
                _dt_to_text(task.end),
                task.duration_minutes,
                int(task.is_all_day),
                task.priority,
                int(task.completed),
                _dt_to_text(task.completed_at),
                task.google_calendar_event_id,
                task.google_calendar_etag,
                task.google_calendar_recurring_event_id,
                _dt_to_text(task.google_calendar_original_start),
                task.series_uid,
                task.occurrence_key,
                task.series_revision,
                int(task.is_series_exception),
                _dt_to_text(task.updated_at),
                _dt_to_text(task.deleted_at),
                ),
            )
            for tag_name in task.tags:
                row = self._connection.execute(
                    "SELECT id FROM tags WHERE normalized_name = ?",
                    (normalized_tag_name(tag_name),),
                ).fetchone()
                if row is None:
                    raise ValueError(f"Неизвестный тег: {tag_name}")
                self._connection.execute(
                    "INSERT OR IGNORE INTO task_tags "
                    "(task_uid, tag_id, created_at) VALUES (?, ?, ?)",
                    (task.uid, row["id"], _dt_to_text(task.updated_at)),
                )
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise
        if task.id is None:
            task.id = cursor.lastrowid
        return task

    def update(self, task: Task) -> Task:
        """Перезаписывает задачу целиком и обновляет updated_at."""
        if task.id is None:
            raise ValueError("Нельзя обновить задачу без id — сначала add()")
        task.touch()
        self._connection.execute(
            """
            UPDATE tasks SET
                uid = ?, title = ?, notes = ?, start = ?, "end" = ?,
                duration_minutes = ?, is_all_day = ?, priority = ?, completed = ?,
                completed_at = ?,
                google_calendar_event_id = ?, google_calendar_etag = ?,
                google_calendar_recurring_event_id = ?,
                google_calendar_original_start = ?,
                series_uid = ?, occurrence_key = ?, series_revision = ?,
                is_series_exception = ?,
                updated_at = ?, deleted_at = ?
            WHERE id = ?
            """,
            (
                task.uid,
                task.title,
                task.notes,
                _dt_to_text(task.start),
                _dt_to_text(task.end),
                task.duration_minutes,
                int(task.is_all_day),
                task.priority,
                int(task.completed),
                _dt_to_text(task.completed_at),
                task.google_calendar_event_id,
                task.google_calendar_etag,
                task.google_calendar_recurring_event_id,
                _dt_to_text(task.google_calendar_original_start),
                task.series_uid,
                task.occurrence_key,
                task.series_revision,
                int(task.is_series_exception),
                _dt_to_text(task.updated_at),
                _dt_to_text(task.deleted_at),
                task.id,
            ),
        )
        self._connection.commit()
        return task

    def update_local(self, task: Task) -> Task:
        """Save user edits while retaining a binding received during editing.

        Hold the write lock from reading metadata through the ordinary update;
        a background insert/patch reply cannot slip between these two writes.
        Unschedule explicitly clears the binding and therefore bypasses merge.
        """
        with self._connection:
            self._connection.execute("BEGIN IMMEDIATE")
            current = self.get_by_uid(task.uid)
            if (task.start is not None and current is not None
                    and current.google_calendar_event_id is not None):
                task.google_calendar_event_id = current.google_calendar_event_id
                task.google_calendar_etag = current.google_calendar_etag
                task.google_calendar_recurring_event_id = current.google_calendar_recurring_event_id
                task.google_calendar_original_start = current.google_calendar_original_start
            return self.update(task)

    def update_local_fields(
        self, uid: str, *, priority: Optional[int] = None,
        completed: Optional[bool] = None,
    ) -> Optional[Task]:
        """Change only Planner fields; preserve the Calendar payload/version.

        A remote pull may finish after an editor read its Task. Updating these
        columns directly keeps the pulled title, schedule and tombstone intact.
        Local completion/priority must not invalidate a pending pull's CAS or
        make an old Calendar payload win the next timestamp comparison.
        """
        assignments, values = [], []
        if priority is not None:
            assignments.append("priority = ?")
            values.append(priority)
        if completed is not None:
            assignments.extend([
                "completed_at = CASE WHEN ? = 0 THEN NULL "
                "WHEN completed = 0 THEN ? ELSE completed_at END",
                "completed = ?",
            ])
            values.extend([int(completed), _dt_to_text(utc_now()), int(completed)])
        with self._connection:
            if assignments:
                cursor = self._connection.execute(
                    "UPDATE tasks SET " + ", ".join(assignments)
                    + " WHERE uid = ? AND deleted_at IS NULL",
                    (*values, uid),
                )
                if cursor.rowcount == 0:
                    return None
            current = self.get_by_uid(uid)
            return current if current is not None and not current.is_deleted else None

    def detach_calendar_task(self, task: Task) -> tuple[Task, Optional[str]]:
        """Clear the binding and return the event actually detached under lock."""
        with self._connection:
            self._connection.execute("BEGIN IMMEDIATE")
            current = self.get_by_uid(task.uid)
            event_id = current.google_calendar_event_id if current is not None else None
            task.google_calendar_event_id = None
            task.google_calendar_etag = None
            task.google_calendar_recurring_event_id = None
            task.google_calendar_original_start = None
            return self.update(task), event_id

    def update_calendar_metadata(
        self, uid: str, event_id: str, etag: Optional[str],
        expected_event_id: Optional[str],
    ) -> bool:
        """Merge a push reply without rewriting user fields or their timestamp.

        A detached/rescheduled task must not regain its old Calendar binding.
        Tombstones retain the binding so the newly created event can be deleted.
        """
        with self._connection:
            cursor = self._connection.execute(
                "UPDATE tasks SET google_calendar_event_id = ?, google_calendar_etag = ? "
                "WHERE uid = ? AND google_calendar_event_id IS ? "
                "AND (start IS NOT NULL OR deleted_at IS NOT NULL)",
                (event_id, etag, uid, expected_event_id),
            )
        return cursor.rowcount > 0

    def apply_calendar_update(self, task: Task, expected: Task) -> bool:
        """Apply pull only if the observed local version is still unchanged.

        The condition and write are one SQLite statement, including the queue
        check: another connection cannot edit/delete the task between them.
        Local-only fields (completion, tags and priority) are never overwritten.
        """
        with self._connection:
            cursor = self._connection.execute(
                """
                UPDATE tasks SET title = ?, notes = ?, start = ?, "end" = ?,
                    duration_minutes = ?, is_all_day = ?,
                    google_calendar_event_id = ?, google_calendar_etag = ?,
                    google_calendar_recurring_event_id = ?,
                    google_calendar_original_start = ?, updated_at = ?, deleted_at = ?
                WHERE uid = ? AND updated_at = ?
                    AND google_calendar_event_id IS ? AND google_calendar_etag IS ?
                    AND NOT EXISTS (
                        SELECT 1 FROM desktop_pending_calendar_ops
                        WHERE task_uid = tasks.uid
                    )
                """,
                (
                    task.title, task.notes, _dt_to_text(task.start), _dt_to_text(task.end),
                    task.duration_minutes, int(task.is_all_day),
                    task.google_calendar_event_id, task.google_calendar_etag,
                    task.google_calendar_recurring_event_id,
                    _dt_to_text(task.google_calendar_original_start),
                    _dt_to_text(utc_now()), _dt_to_text(task.deleted_at),
                    expected.uid, _dt_to_text(expected.updated_at),
                    expected.google_calendar_event_id, expected.google_calendar_etag,
                ),
            )
        return cursor.rowcount > 0

    def get(self, task_id: int) -> Optional[Task]:
        """Возвращает задачу по id, включая тумбстоуны (для проверок удаления)."""
        row = self._connection.execute(
            "SELECT * FROM tasks WHERE id = ?", (task_id,)
        ).fetchone()
        return self._task_from_row(row) if row is not None else None

    def get_by_uid(self, uid: str) -> Optional[Task]:
        row = self._connection.execute(
            "SELECT * FROM tasks WHERE uid = ?", (uid,)
        ).fetchone()
        return self._task_from_row(row) if row is not None else None

    def get_by_google_event_id(self, event_id: str) -> Optional[Task]:
        """Задача, привязанная к событию календаря, включая тумбстоуны:
        pull не должен воскрешать локально удалённую задачу как новую."""
        row = self._connection.execute(
            "SELECT * FROM tasks WHERE google_calendar_event_id = ?", (event_id,)
        ).fetchone()
        return self._task_from_row(row) if row is not None else None

    def delete(self, task_id: int) -> bool:
        """Тумбстоун: помечает deleted_at, физически строку не удаляет."""
        task = self.get(task_id)
        if task is None or task.is_deleted:
            return False
        task.mark_deleted()
        self._connection.execute(
            "UPDATE tasks SET deleted_at = ?, updated_at = ? WHERE id = ?",
            (_dt_to_text(task.deleted_at), _dt_to_text(task.updated_at), task_id),
        )
        self._connection.commit()
        return True

    def complete(self, task_id: int, completed: bool = True) -> bool:
        task = self.get(task_id)
        if task is None or task.is_deleted:
            return False
        return self.update_local_fields(task.uid, completed=completed) is not None

    # ---- списки (тумбстоуны скрыты) ------------------------------------------

    def list_all(self) -> List[Task]:
        rows = self._connection.execute(
            "SELECT * FROM tasks WHERE deleted_at IS NULL ORDER BY id"
        ).fetchall()
        return [self._task_from_row(row) for row in rows]

    def _task_from_row(self, row: sqlite3.Row) -> Task:
        tag_rows = self._connection.execute(
            """
            SELECT tags.name
            FROM tags
            JOIN task_tags ON task_tags.tag_id = tags.id
            WHERE task_tags.task_uid = ?
            ORDER BY tags.normalized_name, tags.id
            """,
            (row["uid"],),
        ).fetchall()
        return _row_to_task(row, (item["name"] for item in tag_rows))

    def list_today(self, reference_date: Optional[date] = None) -> List[Task]:
        day = reference_date or datetime.now().date()
        return [
            t
            for t in self.list_all()
            if t.start is not None and t.start.date() == day
        ]

    def list_undated(self) -> List[Task]:
        return [t for t in self.list_all() if t.start is None]

    # ---- локальные серии (Phase 3.2A) -----------------------------------------

    def list_by_series(self, series_uid: str) -> List[Task]:
        """ВСЕ строки серии, включая тумбстоуны: тумбстоун — это защита
        слота от регенерации, поэтому материализация обязана его видеть."""
        rows = self._connection.execute(
            "SELECT * FROM tasks WHERE series_uid = ? ORDER BY id",
            (series_uid,),
        ).fetchall()
        return [self._task_from_row(row) for row in rows]

    def hard_delete_by_uid(self, uid: str) -> bool:
        """Физическое удаление строки (только для замены материализованных
        экземпляров серии при split/update; пользовательское удаление —
        всегда тумбстоун через delete())."""
        cursor = self._connection.execute(
            "DELETE FROM tasks WHERE uid = ?", (uid,)
        )
        self._connection.commit()
        return cursor.rowcount > 0

    # ---- диагностика (для панели «Настройки») --------------------------------

    def schema_version(self) -> int:
        """Фактическая версия схемы БД (PRAGMA user_version)."""
        row = self._connection.execute("PRAGMA user_version").fetchone()
        return int(row[0]) if row is not None else 0

    def count_active(self) -> int:
        """Число живых (не удалённых) задач — для диагностики."""
        row = self._connection.execute(
            "SELECT COUNT(*) AS n FROM tasks WHERE deleted_at IS NULL"
        ).fetchone()
        return int(row["n"])

    # ---- совместимость с интерфейсом FakeTaskRepository ----------------------

    def all(self) -> List[Task]:
        return self.list_all()

    def toggle_completed(self, uid: str) -> bool:
        with self._connection:
            cursor = self._connection.execute(
                "UPDATE tasks SET completed_at = CASE WHEN completed = 1 "
                "THEN NULL ELSE ? END, completed = 1 - completed "
                "WHERE uid = ? AND deleted_at IS NULL",
                (_dt_to_text(utc_now()), uid),
            )
        return cursor.rowcount > 0
