"""Persistence helpers for Google Tasks synchronization state."""

from __future__ import annotations

from datetime import datetime
from typing import Iterable, List, Optional

from sqlalchemy import func, select

from models.task_sync import TaskSyncMapping, TaskSyncMeta
from storage.db import get_session


class TaskSyncStore:
    """Wrapper around SQLModel session for sync mappings and metadata."""

    def get_mapping(self, local_id: int) -> Optional[TaskSyncMapping]:
        with get_session() as session:
            return session.get(TaskSyncMapping, local_id)

    def get_mapping_by_google(self, google_task_id: str) -> Optional[TaskSyncMapping]:
        if not google_task_id:
            return None
        with get_session() as session:
            stmt = select(TaskSyncMapping).where(TaskSyncMapping.google_task_id == google_task_id)
            return session.exec(stmt).first()

    def list_mappings(self) -> List[TaskSyncMapping]:
        with get_session() as session:
            stmt = select(TaskSyncMapping)
            return list(session.exec(stmt))

    def upsert_mapping(
        self,
        local_id: int,
        *,
        google_task_id: Optional[str],
        tasklist_id: Optional[str],
        etag: Optional[str],
        updated_at_utc: Optional[datetime] = None,
    ) -> TaskSyncMapping:
        updated_at_utc = updated_at_utc or datetime.utcnow()
        with get_session() as session:
            mapping = session.get(TaskSyncMapping, local_id)
            if mapping is None:
                mapping = TaskSyncMapping(
                    local_id=local_id,
                    google_task_id=google_task_id,
                    tasklist_id=tasklist_id,
                    etag=etag,
                    updated_at_utc=updated_at_utc,
                )
            else:
                mapping.google_task_id = google_task_id
                mapping.tasklist_id = tasklist_id
                mapping.etag = etag
                mapping.updated_at_utc = updated_at_utc
            session.add(mapping)
            session.commit()
            session.refresh(mapping)
            return mapping

    def delete_mapping(self, local_id: int) -> None:
        with get_session() as session:
            mapping = session.get(TaskSyncMapping, local_id)
            if mapping:
                session.delete(mapping)
                session.commit()

    def replace_mappings(self, entries: Iterable[TaskSyncMapping]) -> None:
        with get_session() as session:
            existing = session.exec(select(TaskSyncMapping)).all()
            for obj in existing:
                session.delete(obj)
            for entry in entries:
                session.add(entry)
            session.commit()

    # ----- metadata -----
    def get_meta(self) -> TaskSyncMeta:
        with get_session() as session:
            meta = session.get(TaskSyncMeta, 1)
            if meta is None:
                meta = TaskSyncMeta(id=1)
                session.add(meta)
                session.commit()
                session.refresh(meta)
            return meta

    def update_meta(self, **fields) -> TaskSyncMeta:
        with get_session() as session:
            meta = session.get(TaskSyncMeta, 1)
            if meta is None:
                meta = TaskSyncMeta(id=1)
            for key, value in fields.items():
                setattr(meta, key, value)
            session.add(meta)
            session.commit()
            session.refresh(meta)
            return meta

    def max_mapping_updated_at(self) -> Optional[datetime]:
        with get_session() as session:
            stmt = select(func.max(TaskSyncMapping.updated_at_utc))
            return session.exec(stmt).one()


__all__ = ["TaskSyncStore"]
