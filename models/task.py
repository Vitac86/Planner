# planner/models/task.py
from typing import Optional
from datetime import datetime
import uuid

from utils.datetime_utils import utc_now
from sqlmodel import SQLModel, Field

class Task(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    uid: str = Field(default_factory=lambda: str(uuid.uuid4()), index=True, unique=True)
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
    # True when the linked Google Calendar event is all-day (start/end use
    # "date", not "dateTime"); pushes must keep the same shape or Google
    # rejects the update (HTTP 400 "Invalid start time.").
    gcal_all_day: bool = Field(default=False)
    gtasks_id: Optional[str] = None
    gtasks_updated: Optional[datetime] = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
