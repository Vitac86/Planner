# planner/services/tasks.py
from typing import Iterable, Optional
from datetime import datetime, date, timedelta
from sqlmodel import select
from sqlalchemy import and_
from storage.db import get_session
from models.task import Task

class TaskService:
    def add(self, title: str, notes: Optional[str]=None,
            start: Optional[datetime]=None, duration_minutes: Optional[int]=None) -> Task:
        with get_session() as s:
            t = Task(title=title.strip(), notes=notes or None,
                     start=start, duration_minutes=duration_minutes or None)
            s.add(t)
            s.commit()
            s.refresh(t)
            return t

    def get(self, task_id: int) -> Optional[Task]:
        with get_session() as s:
            return s.get(Task, task_id)

    def update(self, task_id: int, *,
               title: Optional[str]=None,
               notes: Optional[str]=None,
               start: Optional[datetime]=None,
               duration_minutes: Optional[int]=None) -> Optional[Task]:
        with get_session() as s:
            t = s.get(Task, task_id)
            if not t:
                return None
            if title is not None:
                t.title = title.strip()
            if notes is not None:
                t.notes = (notes or None)
            if start is not None or start is None:
                t.start = start
            if duration_minutes is not None or duration_minutes is None:
                t.duration_minutes = duration_minutes
            t.updated_at = datetime.utcnow()
            s.add(t)
            s.commit()
            s.refresh(t)
            return t

    def set_event_id(self, task_id: int, event_id: Optional[str]):
        with get_session() as s:
            t = s.get(Task, task_id)
            if t:
                t.gcal_event_id = event_id
                t.updated_at = datetime.utcnow()
                s.add(t)
                s.commit()

    def set_status(self, task_id: int, status: str):
        with get_session() as s:
            t = s.get(Task, task_id)
            if t:
                t.status = status
                t.updated_at = datetime.utcnow()
                s.add(t)
                s.commit()

    def delete(self, task_id: int):
        with get_session() as s:
            t = s.get(Task, task_id)
            if t:
                s.delete(t)
                s.commit()

    def list_for_day(self, d: date) -> Iterable[Task]:
        start = datetime(d.year, d.month, d.day, 0, 0, 0)
        end = start + timedelta(days=1)
        with get_session() as s:
            stmt = select(Task).where(
                and_(Task.status != "done", Task.start >= start, Task.start < end)
            ).order_by(Task.start.asc(), Task.created_at.desc())
            return list(s.exec(stmt))

    def list_unscheduled(self) -> Iterable[Task]:
        with get_session() as s:
            stmt = select(Task).where(
                and_(Task.status != "done", Task.start == None)  # noqa: E711
            ).order_by(Task.created_at.desc())
            return list(s.exec(stmt))
    def get_by_event_id(self, gcal_event_id: str | None):
        if not gcal_event_id:
            return None
        with get_session() as s:
            stmt = select(Task).where(Task.gcal_event_id == gcal_event_id)
            return s.exec(stmt).first()

    def unschedule(self, task_id: int):
        """Снять расписание и отвязать от Google-события (но задачу не удалять)."""
        with get_session() as s:
            t = s.get(Task, task_id)
            if not t:
                return None
            t.start = None
            t.duration_minutes = None
            t.gcal_event_id = None
            s.add(t)
            s.commit()
            s.refresh(t)
            return t
