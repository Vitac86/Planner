# planner/models/task.py
from typing import Optional
from datetime import datetime

from utils.datetime_utils import utc_now
from sqlmodel import SQLModel, Field

class Task(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    title: str
    notes: Optional[str] = None
    start: Optional[datetime] = None
    due: Optional[datetime] = None
    duration_minutes: Optional[int] = None
    priority: int = 0
    status: str = "todo"          # todo / doing / done
    gcal_event_id: Optional[str] = None
    gcal_etag: Optional[str] = None
    gcal_updated: Optional[datetime] = None
    gtasks_id: Optional[str] = None
    gtasks_updated: Optional[datetime] = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
