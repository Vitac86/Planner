"""SQLModel table for pending synchronization operations."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel

from datetime_utils import utc_now


class PendingOp(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    op: str = Field(index=True)
    task_id: int = Field(index=True)
    payload: str
    attempts: int = Field(default=0)
    last_error: Optional[str] = None
    created_at: datetime = Field(default_factory=utc_now)
    next_try_at: datetime = Field(default_factory=utc_now, index=True)


__all__ = ["PendingOp"]
