"""SQLite-репозиторий локальных шаблонов задач (Phase 3.2A).

Живёт в изолированной БД PlannerDesktop. Google-метаданные в шаблоны
не пишутся; сетевых вызовов нет. Схема — storage/schema.py (v6).
"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import List, Optional, Sequence, Union

from planner_desktop.domain.recurrence import (
    RecurrenceEndMode,
    RecurrenceFrequency,
    RecurrenceRule,
)
from planner_desktop.domain.task import utc_now
from planner_desktop.domain.templates import (
    TaskTemplate,
    normalized_template_name,
)
from planner_desktop.storage.paths import ensure_desktop_data_dir, get_desktop_db_path
from planner_desktop.storage.schema import create_schema
from planner_desktop.storage.series_repository import (
    csv_to_weekdays,
    weekdays_to_csv,
)


def _dt_to_text(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value is not None else None


def _text_to_dt(value: Optional[str]) -> Optional[datetime]:
    return datetime.fromisoformat(value) if value else None


def _row_to_template(row: sqlite3.Row) -> TaskTemplate:
    rule = None
    if row["rule_frequency"]:
        rule = RecurrenceRule(
            frequency=RecurrenceFrequency(row["rule_frequency"]),
            interval=int(row["rule_interval"] or 1),
            weekdays=csv_to_weekdays(row["rule_weekdays_csv"]),
            month_day=row["rule_month_day"],
            yearly_month=row["rule_yearly_month"],
            yearly_day=row["rule_yearly_day"],
            end_mode=RecurrenceEndMode(row["rule_end_mode"] or "never"),
            until_date=(
                date.fromisoformat(row["rule_until_date"])
                if row["rule_until_date"] else None
            ),
            occurrence_count=row["rule_occurrence_count"],
        )
    return TaskTemplate(
        name=row["name"],
        kind=row["template_kind"],
        id=row["id"],
        uid=row["uid"],
        title=row["title"],
        notes=row["notes"],
        priority=row["priority"],
        schedule_mode=row["schedule_mode"],
        time_text=row["time_text"],
        duration_minutes=row["duration_minutes"],
        rule=rule,
        created_at=_text_to_dt(row["created_at"]) or utc_now(),
        updated_at=_text_to_dt(row["updated_at"]) or utc_now(),
        deleted_at=_text_to_dt(row["deleted_at"]),
    )


class SQLiteTemplateRepository:
    """Хранит шаблоны + связи шаблон-тег в app_desktop.db."""

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

    @staticmethod
    def _template_params(template: TaskTemplate) -> tuple:
        rule = template.rule
        return (
            template.uid,
            template.name,
            normalized_template_name(template.name),
            template.kind,
            template.title,
            template.notes,
            template.priority,
            template.schedule_mode,
            template.time_text,
            template.duration_minutes,
            rule.frequency.value if rule is not None else None,
            rule.interval if rule is not None else None,
            weekdays_to_csv(rule.weekdays) if rule is not None else None,
            rule.month_day if rule is not None else None,
            rule.yearly_month if rule is not None else None,
            rule.yearly_day if rule is not None else None,
            rule.end_mode.value if rule is not None else None,
            (
                rule.until_date.isoformat()
                if rule is not None and rule.until_date is not None
                else None
            ),
            rule.occurrence_count if rule is not None else None,
            _dt_to_text(template.created_at),
            _dt_to_text(template.updated_at),
            _dt_to_text(template.deleted_at),
        )

    def add(self, template: TaskTemplate) -> TaskTemplate:
        cursor = self._connection.execute(
            """
            INSERT INTO task_templates (
                uid, name, normalized_name, template_kind, title, notes,
                priority, schedule_mode, time_text, duration_minutes,
                rule_frequency, rule_interval, rule_weekdays_csv,
                rule_month_day, rule_yearly_month, rule_yearly_day,
                rule_end_mode, rule_until_date, rule_occurrence_count,
                created_at, updated_at, deleted_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            self._template_params(template),
        )
        self._connection.commit()
        template.id = cursor.lastrowid
        return template

    def update(self, template: TaskTemplate) -> TaskTemplate:
        template.touch()
        params = self._template_params(template)
        cursor = self._connection.execute(
            """
            UPDATE task_templates SET
                name = ?, normalized_name = ?, template_kind = ?, title = ?,
                notes = ?, priority = ?, schedule_mode = ?, time_text = ?,
                duration_minutes = ?, rule_frequency = ?, rule_interval = ?,
                rule_weekdays_csv = ?, rule_month_day = ?, rule_yearly_month = ?,
                rule_yearly_day = ?, rule_end_mode = ?, rule_until_date = ?,
                rule_occurrence_count = ?, created_at = ?, updated_at = ?,
                deleted_at = ?
            WHERE uid = ?
            """,
            params[1:] + (template.uid,),
        )
        if cursor.rowcount == 0:
            self._connection.rollback()
            raise KeyError("Шаблон не найден")
        self._connection.commit()
        return template

    def get_by_uid(self, uid: str) -> Optional[TaskTemplate]:
        row = self._connection.execute(
            "SELECT * FROM task_templates WHERE uid = ?", (uid,)
        ).fetchone()
        if row is None:
            return None
        template = _row_to_template(row)
        template.tags = tuple(self._tag_names_for(uid))
        return template

    def get_by_normalized_name(self, name: str) -> Optional[TaskTemplate]:
        row = self._connection.execute(
            "SELECT * FROM task_templates "
            "WHERE normalized_name = ? AND deleted_at IS NULL",
            (name,),
        ).fetchone()
        return _row_to_template(row) if row is not None else None

    def list_all(self) -> List[TaskTemplate]:
        rows = self._connection.execute(
            "SELECT * FROM task_templates WHERE deleted_at IS NULL "
            "ORDER BY normalized_name, id"
        ).fetchall()
        result = []
        for row in rows:
            template = _row_to_template(row)
            template.tags = tuple(self._tag_names_for(template.uid))
            result.append(template)
        return result

    def delete(self, uid: str) -> bool:
        """Тумбстоун шаблона; созданные из него задачи/серии не трогаются."""
        template = self.get_by_uid(uid)
        if template is None or template.is_deleted:
            return False
        template.mark_deleted()
        self._connection.execute(
            "UPDATE task_templates SET deleted_at = ?, updated_at = ? "
            "WHERE uid = ?",
            (
                _dt_to_text(template.deleted_at),
                _dt_to_text(template.updated_at),
                uid,
            ),
        )
        self._connection.commit()
        return True

    # ---- теги шаблона ------------------------------------------------------------

    def set_template_tags(self, template_uid: str, tag_ids: Sequence[int]) -> None:
        unique = tuple(dict.fromkeys(int(item) for item in tag_ids))
        try:
            self._connection.execute(
                "DELETE FROM template_tags WHERE template_uid = ?",
                (template_uid,),
            )
            now = _dt_to_text(utc_now())
            for tag_id in unique:
                self._connection.execute(
                    "INSERT INTO template_tags (template_uid, tag_id, created_at) "
                    "VALUES (?, ?, ?)",
                    (template_uid, tag_id, now),
                )
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise

    def tag_ids_for_template(self, template_uid: str) -> List[int]:
        rows = self._connection.execute(
            "SELECT tag_id FROM template_tags WHERE template_uid = ? "
            "ORDER BY tag_id",
            (template_uid,),
        ).fetchall()
        return [int(row["tag_id"]) for row in rows]

    def _tag_names_for(self, template_uid: str) -> List[str]:
        rows = self._connection.execute(
            """
            SELECT tags.name
            FROM tags
            JOIN template_tags ON template_tags.tag_id = tags.id
            WHERE template_tags.template_uid = ?
            ORDER BY tags.normalized_name, tags.id
            """,
            (template_uid,),
        ).fetchall()
        return [row["name"] for row in rows]

    def count_active(self) -> int:
        row = self._connection.execute(
            "SELECT COUNT(*) AS n FROM task_templates WHERE deleted_at IS NULL"
        ).fetchone()
        return int(row["n"])


__all__ = ["SQLiteTemplateRepository"]
