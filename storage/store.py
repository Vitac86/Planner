"""Local metadata store for task synchronization."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, Optional

from sqlmodel import Field, SQLModel, Session, create_engine, select

from core.settings import STORE_DB_PATH


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ListRecord(SQLModel, table=True):
    """Representation of a remote list stored locally."""

    list_id: str = Field(primary_key=True)
    name: Optional[str] = None
    backend: Optional[str] = None
    last_sync: Optional[datetime] = None


class TaskMetadata(SQLModel, table=True):
    """Persisted service metadata for a remote task."""

    remote_task_id: str = Field(primary_key=True)
    list_id: str = Field(primary_key=True)
    meta_json: Optional[str] = None
    updated_at: datetime = Field(default_factory=_utcnow)


class SyncState(SQLModel, table=True):
    """Version information for synchronised entities."""

    entity_type: str = Field(primary_key=True)
    entity_id: str = Field(primary_key=True)
    version: Optional[str] = None
    last_pulled: Optional[datetime] = None
    last_pushed: Optional[datetime] = None


STORE_TABLES = [ListRecord.__table__, TaskMetadata.__table__, SyncState.__table__]

_store_engine = None


def get_store_engine():
    """Return (and lazily create) the SQLAlchemy engine for the metadata store."""

    global _store_engine
    if _store_engine is None:
        STORE_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _store_engine = create_engine(f"sqlite:///{STORE_DB_PATH.as_posix()}", echo=False)
    return _store_engine


def init_store(engine=None) -> None:
    """Initialise the metadata store schema."""

    actual_engine = engine or get_store_engine()
    if engine is not None:
        actual_engine = engine
    SQLModel.metadata.create_all(actual_engine, tables=STORE_TABLES)


def get_store_session() -> Session:
    """Return a SQLModel session bound to the metadata store."""

    engine = get_store_engine()
    return Session(engine)


def _serialise_meta(meta: Dict[str, Any]) -> str:
    return json.dumps(meta, ensure_ascii=False, sort_keys=True)


def _deserialise_meta(payload: Optional[str]) -> Dict[str, Any]:
    if not payload:
        return {}
    try:
        data = json.loads(payload)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    return {}


class MetadataStore:
    """High level helper around ``store.db`` tables."""

    def __init__(self, session_factory: Callable[[], Session] = get_store_session):
        self._session_factory = session_factory

    # ----- task metadata -----
    def load_task_meta(self, remote_task_id: str, list_id: str) -> Dict[str, Any]:
        with self._session_factory() as session:
            row = session.get(TaskMetadata, (remote_task_id, list_id))
            return _deserialise_meta(row.meta_json if row else None)

    def save_task_meta(
        self,
        remote_task_id: str,
        list_id: str,
        meta: Optional[Dict[str, Any]],
        updated_at: Optional[datetime] = None,
    ) -> None:
        with self._session_factory() as session:
            row = session.get(TaskMetadata, (remote_task_id, list_id))
            if not meta:
                if row is not None:
                    session.delete(row)
                    session.commit()
                return

            payload = _serialise_meta(meta)
            timestamp = updated_at or _utcnow()
            if row is None:
                row = TaskMetadata(
                    remote_task_id=remote_task_id,
                    list_id=list_id,
                    meta_json=payload,
                    updated_at=timestamp,
                )
            else:
                row.meta_json = payload
                row.updated_at = timestamp
            session.add(row)
            session.commit()

    def delete_task_meta(self, remote_task_id: str, list_id: str) -> None:
        self.save_task_meta(remote_task_id, list_id, None)

    # ----- lists -----
    def register_list(
        self,
        list_id: str,
        *,
        name: Optional[str] = None,
        backend: Optional[str] = None,
        last_sync: Optional[datetime] = None,
    ) -> None:
        with self._session_factory() as session:
            row = session.get(ListRecord, list_id)
            if row is None:
                row = ListRecord(list_id=list_id)
            if name is not None:
                row.name = name
            if backend is not None:
                row.backend = backend
            if last_sync is not None:
                row.last_sync = last_sync
            session.add(row)
            session.commit()

    def update_list_sync(self, list_id: str, timestamp: Optional[datetime]) -> None:
        with self._session_factory() as session:
            row = session.get(ListRecord, list_id)
            if row is None:
                row = ListRecord(list_id=list_id)
            row.last_sync = timestamp or _utcnow()
            session.add(row)
            session.commit()

    def iter_lists(self) -> Iterable[ListRecord]:
        with self._session_factory() as session:
            stmt = session.exec(select(ListRecord))
            yield from stmt


__all__ = [
    "ListRecord",
    "MetadataStore",
    "SyncState",
    "TaskMetadata",
    "get_store_session",
    "get_store_engine",
    "init_store",
    "STORE_TABLES",
]

