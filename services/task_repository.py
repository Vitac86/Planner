from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlmodel import select

from models.task import Task
from storage.db import get_session


def _utcnow() -> datetime:
    return datetime.utcnow()


class TaskRepository:
    def get(self, task_id: int) -> Optional[Task]:
        with get_session() as session:
            return session.get(Task, task_id)

    def get_by_event_id(self, event_id: str) -> Optional[Task]:
        if not event_id:
            return None
        with get_session() as session:
            stmt = select(Task).where(Task.gcal_event_id == event_id)
            return session.exec(stmt).first()

    def add(self, **fields) -> Task:
        with get_session() as session:
            task = Task(**fields)
            session.add(task)
            session.commit()
            session.refresh(task)
            return task

    def update(self, task: Task, **fields) -> Task:
        with get_session() as session:
            obj = session.get(Task, task.id)
            if not obj:
                raise ValueError("Task not found")
            for key, value in fields.items():
                setattr(obj, key, value)
            obj.updated_at = _utcnow()
            session.add(obj)
            session.commit()
            session.refresh(obj)
            return obj

    def delete(self, task_id: int) -> None:
        with get_session() as session:
            obj = session.get(Task, task_id)
            if obj:
                session.delete(obj)
                session.commit()

    def mark_unscheduled(self, task_id: int) -> Optional[Task]:
        with get_session() as session:
            obj = session.get(Task, task_id)
            if not obj:
                return None
            obj.start = None
            obj.duration_minutes = None
            obj.gcal_event_id = None
            obj.gcal_etag = None
            obj.gcal_updated_utc = None
            obj.updated_at = _utcnow()
            session.add(obj)
            session.commit()
            session.refresh(obj)
            return obj


__all__ = ["TaskRepository"]
