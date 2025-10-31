"""Console utility to migrate embedded metadata from task descriptions."""
from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import Optional

from core.settings import DATA_DIR
from services.appdata import AppDataClient
from services.google_auth import GoogleAuth
from services.tasks_bridge import GoogleTasksBridge


LOG_PATH = DATA_DIR / "migration.log"


def migrate_descriptions(
    *,
    auth: Optional[GoogleAuth] = None,
    bridge: Optional[GoogleTasksBridge] = None,
    appdata: Optional[AppDataClient] = None,
) -> int:
    """Run a migration that extracts metadata JSON from task descriptions."""

    auth = auth or GoogleAuth()
    if hasattr(auth, "ensure_credentials"):
        auth.ensure_credentials()
    appdata = appdata or AppDataClient(auth)
    bridge = bridge or GoogleTasksBridge(auth)

    appdata.ensure_files()
    index, etag = appdata.read_index()
    if not index:
        index = {"version": 1, "tasklist_id": None, "tasks": {}}

    tasklist_id = bridge.ensure_tasklist()
    index["tasklist_id"] = tasklist_id
    tasks_meta = index.setdefault("tasks", {})

    migrated = 0
    for item in bridge.fetch_all(tasklist_id):
        gtask_id = item.get("id")
        detected = item.get("detected_meta") or {}
        if not gtask_id or not detected:
            continue

        entry = dict(tasks_meta.get(gtask_id) or {})
        before = entry.copy()
        for key in ("task_id", "priority", "status", "updated_at", "device_id"):
            value = detected.get(key)
            if value in (None, ""):
                continue
            entry[key] = value
        if entry != before:
            tasks_meta[gtask_id] = entry
            migrated += 1
            logging.info("Migrated metadata for %s", gtask_id)

    if migrated:
        appdata.write_index(index, if_match=etag)

    logging.info("Migration completed; %d tasks updated", migrated)
    return migrated


def _setup_logging(log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=str(log_path),
        filemode="a",
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__ or "")
    parser.add_argument(
        "--log",
        type=Path,
        default=LOG_PATH,
        help="Path to a log file (default: %(default)s)",
    )
    args = parser.parse_args()

    _setup_logging(args.log)
    try:
        count = migrate_descriptions()
        print(f"Migration complete: {count} tasks processed.")
    except Exception as exc:  # pragma: no cover - defensive
        logging.exception("Migration failed: %s", exc)
        raise


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    main()

