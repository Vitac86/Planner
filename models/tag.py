# planner/models/tag.py
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel

from utils.datetime_utils import utc_now


class Tag(SQLModel, table=True):
    __tablename__ = "tags"
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(unique=True, index=True)
    color_hex: str
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class TaskTag(SQLModel, table=True):
    __tablename__ = "task_tags"

    task_id: int = Field(primary_key=True, foreign_key="task.id")
    tag_id: int = Field(primary_key=True, foreign_key="tags.id")


__all__ = ["Tag", "TaskTag"]
