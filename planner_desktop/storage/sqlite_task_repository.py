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
from planner_desktop.storage.paths import ensure_desktop_data_dir, get_desktop_db_path
from planner_desktop.storage.schema import create_schema


def _dt_to_text(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value is not None else None


def _text_to_dt(value: Optional[str]) -> Optional[datetime]:
    return datetime.fromisoformat(value) if value else None


def _row_to_task(row: sqlite3.Row) -> Task:
    return Task(
        title=row["title"],
        id=row["id"],
        uid=row["uid"],
        notes=row["notes"],
        start=_text_to_dt(row["start"]),
        end=_text_to_dt(row["end"]),
        duration_minutes=row["duration_minutes"],
        is_all_day=bool(row["is_all_day"]),
        priority=row["priority"],
        completed=bool(row["completed"]),
        google_calendar_event_id=row["google_calendar_event_id"],
        google_calendar_etag=row["google_calendar_etag"],
        google_calendar_recurring_event_id=row["google_calendar_recurring_event_id"],
        google_calendar_original_start=_text_to_dt(row["google_calendar_original_start"]),
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
        cursor = self._connection.execute(
            """
            INSERT INTO tasks (
                id, uid, title, notes, start, "end", duration_minutes,
                is_all_day, priority, completed,
                google_calendar_event_id, google_calendar_etag,
                google_calendar_recurring_event_id, google_calendar_original_start,
                updated_at, deleted_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                task.google_calendar_event_id,
                task.google_calendar_etag,
                task.google_calendar_recurring_event_id,
                _dt_to_text(task.google_calendar_original_start),
                _dt_to_text(task.updated_at),
                _dt_to_text(task.deleted_at),
            ),
        )
        self._connection.commit()
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
                google_calendar_event_id = ?, google_calendar_etag = ?,
                google_calendar_recurring_event_id = ?,
                google_calendar_original_start = ?,
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
                task.google_calendar_event_id,
                task.google_calendar_etag,
                task.google_calendar_recurring_event_id,
                _dt_to_text(task.google_calendar_original_start),
                _dt_to_text(task.updated_at),
                _dt_to_text(task.deleted_at),
                task.id,
            ),
        )
        self._connection.commit()
        return task

    def get(self, task_id: int) -> Optional[Task]:
        """Возвращает задачу по id, включая тумбстоуны (для проверок удаления)."""
        row = self._connection.execute(
            "SELECT * FROM tasks WHERE id = ?", (task_id,)
        ).fetchone()
        return _row_to_task(row) if row is not None else None

    def get_by_uid(self, uid: str) -> Optional[Task]:
        row = self._connection.execute(
            "SELECT * FROM tasks WHERE uid = ?", (uid,)
        ).fetchone()
        return _row_to_task(row) if row is not None else None

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
        task.completed = completed
        task.touch()
        self._connection.execute(
            "UPDATE tasks SET completed = ?, updated_at = ? WHERE id = ?",
            (int(completed), _dt_to_text(task.updated_at), task_id),
        )
        self._connection.commit()
        return True

    # ---- списки (тумбстоуны скрыты) ------------------------------------------

    def list_all(self) -> List[Task]:
        rows = self._connection.execute(
            "SELECT * FROM tasks WHERE deleted_at IS NULL ORDER BY id"
        ).fetchall()
        return [_row_to_task(row) for row in rows]

    def list_today(self, reference_date: Optional[date] = None) -> List[Task]:
        day = reference_date or datetime.now().date()
        return [
            t
            for t in self.list_all()
            if t.start is not None and t.start.date() == day
        ]

    def list_undated(self) -> List[Task]:
        return [t for t in self.list_all() if t.start is None]

    # ---- совместимость с интерфейсом FakeTaskRepository ----------------------

    def all(self) -> List[Task]:
        return self.list_all()

    def toggle_completed(self, uid: str) -> bool:
        task = self.get_by_uid(uid)
        if task is None or task.is_deleted:
            return False
        return self.complete(task.id, not task.completed)
