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
        task.set_completed(completed)
        task.touch()
        self._connection.execute(
            "UPDATE tasks SET completed = ?, completed_at = ?, updated_at = ? "
            "WHERE id = ?",
            (
                int(task.completed),
                _dt_to_text(task.completed_at),
                _dt_to_text(task.updated_at),
                task_id,
            ),
        )
        self._connection.commit()
        return True

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
        task = self.get_by_uid(uid)
        if task is None or task.is_deleted:
            return False
        return self.complete(task.id, not task.completed)
