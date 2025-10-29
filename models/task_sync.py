"""SQLModel tables for Google Tasks synchronization metadata."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel


class TaskSyncMapping(SQLModel, table=True):
    """Mapping between local Planner tasks and Google Tasks identifiers."""

    local_id: int = Field(primary_key=True, foreign_key="task.id")
    google_task_id: Optional[str] = Field(default=None, index=True)
    tasklist_id: Optional[str] = None
    etag: Optional[str] = None
    updated_at_utc: datetime = Field(default_factory=datetime.utcnow)


class TaskSyncMeta(SQLModel, table=True):
    """Holds sync anchors and auxiliary metadata for Google Tasks sync."""

    id: int = Field(default=1, primary_key=True)
    tasklist_id: Optional[str] = None
    updated_min: Optional[str] = None
    last_pull_at: Optional[datetime] = None
    last_push_at: Optional[datetime] = None
    drive_snapshot_at: Optional[datetime] = None
    drive_file_id: Optional[str] = None


__all__ = ["TaskSyncMapping", "TaskSyncMeta"]
