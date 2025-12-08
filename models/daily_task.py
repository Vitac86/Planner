# planner/models/daily_task.py
from __future__ import annotations

from datetime import datetime
import uuid

from sqlmodel import Field, SQLModel


def _now_iso() -> str:
    return datetime.utcnow().isoformat()


class DailyTask(SQLModel, table=True):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    title: str = Field(index=True)
    weekdays: int
    status_today: str = Field(default="inactive")
    last_done_at: str | None = None
    last_status_calc_at: str | None = None
    created_at: str = Field(default_factory=_now_iso)
    updated_at: str = Field(default_factory=_now_iso)
    timezone: str = Field(default="UTC")


__all__ = ["DailyTask"]
