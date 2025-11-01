# planner/storage/db.py
from sqlmodel import SQLModel, create_engine, Session

from core.settings import DB_PATH, BACKUP
from storage.backup import ensure_daily_backup

# Ensure SQLModel metadata is populated
import models.task  # noqa: F401
import models.task_sync  # noqa: F401
import models.pending_op  # noqa: F401
from storage import migrations


_engine = create_engine(f"sqlite:///{DB_PATH.as_posix()}", echo=False)


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    SQLModel.metadata.create_all(_engine)
    migrations.run_all(_engine)
    if BACKUP.enabled:
        ensure_daily_backup(DB_PATH, BACKUP.directory, keep_days=BACKUP.keep_days)


def get_engine():
    return _engine


def get_session() -> Session:
    return Session(_engine)
