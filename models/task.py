# planner/models/task.py
from typing import Optional
from datetime import datetime
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
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
