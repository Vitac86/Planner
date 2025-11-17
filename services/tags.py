# planner/services/tags.py
from __future__ import annotations

import re
from typing import List, Optional

from sqlmodel import select

from models.tag import Tag, TaskTag
from storage.db import get_session
from utils.datetime_utils import utc_now


NAME_RE = re.compile(r"^.{1,40}$")
COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")


class TagService:
    def list(self) -> List[Tag]:
        with get_session() as session:
            stmt = select(Tag).order_by(Tag.name.asc())
            return list(session.exec(stmt))

    def create(self, name: str, color_hex: str) -> Tag:
        name = name.strip()
        self._validate_inputs(name, color_hex)
        normalized_color = color_hex.upper()
        with get_session() as session:
            tag = Tag(name=name, color_hex=normalized_color)
            session.add(tag)
            session.commit()
            session.refresh(tag)
            return tag

    def rename(self, tag_id: int, new_name: str) -> Optional[Tag]:
        new_name = new_name.strip()
        self._validate_name(new_name)
        with get_session() as session:
            tag = session.get(Tag, tag_id)
            if not tag:
                return None
            tag.name = new_name
            tag.updated_at = utc_now()
            session.add(tag)
            session.commit()
            session.refresh(tag)
            return tag

    def recolor(self, tag_id: int, color_hex: str) -> Optional[Tag]:
        self._validate_color(color_hex)
        normalized_color = color_hex.upper()
        with get_session() as session:
            tag = session.get(Tag, tag_id)
            if not tag:
                return None
            tag.color_hex = normalized_color
            tag.updated_at = utc_now()
            session.add(tag)
            session.commit()
            session.refresh(tag)
            return tag

    def delete(self, tag_id: int) -> None:
        with get_session() as session:
            tag = session.get(Tag, tag_id)
            if not tag:
                return
            # Remove associations first due to composite PK
            stmt = select(TaskTag).where(TaskTag.tag_id == tag_id)
            for link in session.exec(stmt):
                session.delete(link)
            session.delete(tag)
            session.commit()

    def set_for_task(self, task_id: int, tag_ids: List[int]) -> None:
        unique_ids = set(tag_ids)
        with get_session() as session:
            # Remove old associations
            stmt = select(TaskTag).where(TaskTag.task_id == task_id)
            existing = list(session.exec(stmt))
            for link in existing:
                if link.tag_id not in unique_ids:
                    session.delete(link)
            # Add new links
            existing_ids = {link.tag_id for link in existing}
            for tag_id in unique_ids - existing_ids:
                session.add(TaskTag(task_id=task_id, tag_id=tag_id))
            session.commit()

    def add_to_task(self, task_id: int, tag_id: int) -> None:
        with get_session() as session:
            link = session.get(TaskTag, (task_id, tag_id))
            if link:
                return
            session.add(TaskTag(task_id=task_id, tag_id=tag_id))
            session.commit()

    def remove_from_task(self, task_id: int, tag_id: int) -> None:
        with get_session() as session:
            link = session.get(TaskTag, (task_id, tag_id))
            if link:
                session.delete(link)
                session.commit()

    def get_for_task(self, task_id: int) -> List[Tag]:
        with get_session() as session:
            stmt = (
                select(Tag)
                .join(TaskTag, Tag.id == TaskTag.tag_id)
                .where(TaskTag.task_id == task_id)
                .order_by(Tag.name.asc())
            )
            return list(session.exec(stmt))

    # ------------------------------------------------------------------
    def _validate_inputs(self, name: str, color_hex: str) -> None:
        self._validate_name(name)
        self._validate_color(color_hex)

    def _validate_name(self, name: str) -> None:
        if not NAME_RE.match(name):
            raise ValueError("Tag name must be between 1 and 40 characters")

    def _validate_color(self, color_hex: str) -> None:
        if not COLOR_RE.match(color_hex):
            raise ValueError("Color must be in #RRGGBB format")


__all__ = ["TagService"]
