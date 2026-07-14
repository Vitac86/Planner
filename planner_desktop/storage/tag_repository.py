"""SQLite adapter for local Planner Desktop tags."""
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Union

from planner_desktop.domain.tags import Tag
from planner_desktop.storage.paths import ensure_desktop_data_dir, get_desktop_db_path
from planner_desktop.storage.schema import create_schema


def _to_text(value: datetime) -> str:
    return value.isoformat()


def _from_text(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _row_to_tag(row: sqlite3.Row) -> Tag:
    return Tag(
        id=row["id"],
        name=row["name"],
        normalized_name=row["normalized_name"],
        created_at=_from_text(row["created_at"]),
        updated_at=_from_text(row["updated_at"]),
    )


class SQLiteTagRepository:
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

    def add(self, tag: Tag) -> Tag:
        cursor = self._connection.execute(
            "INSERT INTO tags (name, normalized_name, created_at, updated_at) "
            "VALUES (?, ?, ?, ?)",
            (
                tag.name,
                tag.normalized_name,
                _to_text(tag.created_at),
                _to_text(tag.updated_at),
            ),
        )
        self._connection.commit()
        row = self._connection.execute(
            "SELECT * FROM tags WHERE id = ?", (cursor.lastrowid,)
        ).fetchone()
        assert row is not None
        return _row_to_tag(row)

    def update(self, tag: Tag) -> Tag:
        if tag.id is None:
            raise ValueError("Нельзя обновить тег без id.")
        cursor = self._connection.execute(
            "UPDATE tags SET name = ?, normalized_name = ?, updated_at = ? "
            "WHERE id = ?",
            (tag.name, tag.normalized_name, _to_text(tag.updated_at), tag.id),
        )
        if cursor.rowcount == 0:
            self._connection.rollback()
            raise KeyError("tag not found")
        self._connection.commit()
        return tag

    def get(self, tag_id: int) -> Optional[Tag]:
        row = self._connection.execute(
            "SELECT * FROM tags WHERE id = ?", (int(tag_id),)
        ).fetchone()
        return _row_to_tag(row) if row is not None else None

    def get_by_normalized_name(self, normalized_name: str) -> Optional[Tag]:
        row = self._connection.execute(
            "SELECT * FROM tags WHERE normalized_name = ?", (normalized_name,)
        ).fetchone()
        return _row_to_tag(row) if row is not None else None

    def list_all(self) -> List[Tag]:
        rows = self._connection.execute(
            "SELECT * FROM tags ORDER BY normalized_name, id"
        ).fetchall()
        return [_row_to_tag(row) for row in rows]

    def delete(self, tag_id: int) -> bool:
        cursor = self._connection.execute(
            "DELETE FROM tags WHERE id = ?", (int(tag_id),)
        )
        self._connection.commit()
        return cursor.rowcount > 0

    def list_for_task(self, task_uid: str) -> List[Tag]:
        rows = self._connection.execute(
            """
            SELECT tags.*
            FROM tags
            JOIN task_tags ON task_tags.tag_id = tags.id
            WHERE task_tags.task_uid = ?
            ORDER BY tags.normalized_name, tags.id
            """,
            (task_uid,),
        ).fetchall()
        return [_row_to_tag(row) for row in rows]

    def set_for_task(
        self, task_uid: str, tag_ids: Iterable[int], created_at: datetime
    ) -> None:
        unique = tuple(dict.fromkeys(int(item) for item in tag_ids))
        try:
            self._connection.execute("BEGIN")
            self._connection.execute(
                "DELETE FROM task_tags WHERE task_uid = ?", (task_uid,)
            )
            for tag_id in unique:
                self._connection.execute(
                    "INSERT INTO task_tags (task_uid, tag_id, created_at) "
                    "VALUES (?, ?, ?)",
                    (task_uid, tag_id, _to_text(created_at)),
                )
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise

    def task_counts(self) -> Dict[int, int]:
        rows = self._connection.execute(
            """
            SELECT tags.id, COUNT(tasks.uid) AS task_count
            FROM tags
            LEFT JOIN task_tags ON task_tags.tag_id = tags.id
            LEFT JOIN tasks ON tasks.uid = task_tags.task_uid
                           AND tasks.deleted_at IS NULL
            GROUP BY tags.id
            """
        ).fetchall()
        return {int(row["id"]): int(row["task_count"]) for row in rows}


__all__ = ["SQLiteTagRepository"]

