"""Utilities for SQLite backups."""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from shutil import copy2
from typing import Optional


def _parse_backup_date(path: Path, prefix: str) -> datetime | None:
    stem = path.stem
    if not stem.startswith(prefix):
        return None
    date_part = stem[len(prefix) :]
    try:
        return datetime.strptime(date_part, "%Y-%m-%d")
    except ValueError:
        return None


def ensure_daily_backup(
    db_path: str | Path,
    backup_dir: str | Path,
    *,
    keep_days: int = 7,
) -> Path | None:
    """Create a dated SQLite backup and rotate old copies."""

    db_file = Path(db_path)
    if not db_file.exists():
        return None

    backups = Path(backup_dir)
    backups.mkdir(parents=True, exist_ok=True)

    today = datetime.now().date()
    prefix = f"{db_file.stem}_"
    backup_name = f"{prefix}{today.strftime('%Y-%m-%d')}{db_file.suffix}"
    destination = backups / backup_name

    created_path: Path | None = None
    if not destination.exists():
        copy2(db_file, destination)
        created_path = destination

    if keep_days > 0:
        cutoff = today - timedelta(days=keep_days - 1)
        for file in backups.glob(f"{db_file.stem}_*{db_file.suffix}"):
            backup_date = _parse_backup_date(file, prefix)
            if backup_date and backup_date.date() < cutoff:
                try:
                    file.unlink()
                except OSError:
                    pass

    return created_path


PRECUTOVER_TAG = "precutover"


def create_precutover_backup(
    db_path: str | Path | None = None,
    backup_dir: str | Path | None = None,
    *,
    now: Optional[datetime] = None,
) -> Path:
    """Create an explicit, on-demand backup of the database before migration.

    Unlike :func:`ensure_daily_backup` this is a hard gate for the undated
    sync cutover (docs/SYNC_ENGINE_DECISION.md §5 Phase 3): it fails loudly
    when the database is missing, never reuses or overwrites an existing
    backup (a colliding name gets a numeric suffix), and returns the path of
    the copy it created. The ``{stem}_precutover_...`` name does not parse as
    a daily-backup date, so daily rotation never deletes these snapshots.
    """

    if db_path is None or backup_dir is None:
        # Lazy import: defaults come from settings, but tests pass explicit
        # paths and must not touch the real user data directory.
        from core.settings import BACKUP, DB_PATH

        db_path = db_path or DB_PATH
        backup_dir = backup_dir or BACKUP.directory

    db_file = Path(db_path)
    if not db_file.exists():
        raise FileNotFoundError(f"database to back up does not exist: {db_file}")

    backups = Path(backup_dir)
    backups.mkdir(parents=True, exist_ok=True)

    stamp = (now or datetime.now()).strftime("%Y-%m-%d_%H%M%S")
    base = f"{db_file.stem}_{PRECUTOVER_TAG}_{stamp}"
    destination = backups / f"{base}{db_file.suffix}"
    counter = 1
    while destination.exists():
        counter += 1
        destination = backups / f"{base}_{counter}{db_file.suffix}"

    copy2(db_file, destination)
    return destination


__all__ = ["ensure_daily_backup", "create_precutover_backup"]
