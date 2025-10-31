"""Console utility to migrate embedded metadata from task descriptions."""
from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import Optional

from core.settings import DATA_DIR
from services.google_auth import GoogleAuth
from services.tasks_bridge import GoogleTasksBridge
from storage.store import MetadataStore, init_store


LOG_PATH = DATA_DIR / "migration.log"


def migrate_descriptions(
    *,
    auth: Optional[GoogleAuth] = None,
    bridge: Optional[GoogleTasksBridge] = None,
    metadata_store: Optional[MetadataStore] = None,
) -> int:
    """Run a migration that extracts metadata JSON from task descriptions."""

    init_store()
    store = metadata_store or MetadataStore()
    auth = auth or GoogleAuth()
    if hasattr(auth, "ensure_credentials"):
        auth.ensure_credentials()
    bridge = bridge or GoogleTasksBridge(auth, metadata_store=store)

    tasklist_id = bridge.ensure_tasklist()
    migrated = 0
    for item in bridge.fetch_all(tasklist_id):
        raw_notes = (item.get("raw") or {}).get("notes", "") or ""
        cleaned = item.get("notes") or ""
        logging.info(
            "Task %s migrated: notes length %d -> %d", item.get("id"), len(raw_notes), len(cleaned)
        )
        migrated += 1
    logging.info("Migration completed; %d tasks processed", migrated)
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

