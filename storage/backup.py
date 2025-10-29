"""Utilities for SQLite backups."""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from shutil import copy2


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
    backup_name = f"{prefix}{today.isoformat()}{db_file.suffix}"
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


__all__ = ["ensure_daily_backup"]
