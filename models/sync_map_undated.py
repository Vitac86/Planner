"""Mapping table for Google Tasks synchronization of undated items."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Field, SQLModel


class SyncMapUndated(SQLModel, table=True):
    """Keep mapping between local tasks and Google Tasks entities."""

    task_id: str = Field(primary_key=True, description="Local task identifier")
    gtask_id: Optional[str] = Field(
        default=None,
        index=True,
        description="Google Tasks task identifier",
    )
    tasklist_id: str = Field(description="Google Tasks tasklist identifier")
    updated_at_utc: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Last synchronization timestamp in UTC",
    )
    dirty_flag: int = Field(
        default=0,
        description="Dirty flag: 1 when local copy requires push",
    )


__all__ = ["SyncMapUndated"]
