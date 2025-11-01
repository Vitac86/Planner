"""Ad-hoc database migrations for Planner."""

from __future__ import annotations

from sqlalchemy import text


def _column_exists(conn, table: str, column: str) -> bool:
    result = conn.execute(text(f"PRAGMA table_info('{table}')"))
    return any(row[1] == column for row in result)


def ensure_task_columns(conn) -> None:
    columns = {
        "gcal_event_id": "TEXT",
        "gcal_etag": "TEXT",
        "gcal_updated": "TEXT",
        "gtasks_id": "TEXT",
        "gtasks_updated": "TEXT",
    }
    for name, ddl_type in columns.items():
        if not _column_exists(conn, "task", name):
            conn.execute(text(f"ALTER TABLE task ADD COLUMN {name} {ddl_type}"))

    if _column_exists(conn, "task", "gcal_updated_utc"):
        conn.execute(
            text(
                """
                UPDATE task
                SET gcal_updated = COALESCE(gcal_updated, gcal_updated_utc)
                WHERE gcal_updated_utc IS NOT NULL
                """
            )
        )


def ensure_pending_ops_table(conn) -> None:
    conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS pendingop (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                op TEXT NOT NULL,
                task_id INTEGER NOT NULL,
                payload TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                last_error TEXT,
                created_at TEXT NOT NULL,
                next_try_at TEXT NOT NULL
            )
            """
        )
    )
    conn.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS ix_pendingop_next_try_at
            ON pendingop (next_try_at)
            """
        )
    )


def run_all(engine) -> None:
    with engine.begin() as conn:
        ensure_task_columns(conn)
        # SQLModel creates the pendingop table, but ensure indexes exist in legacy DBs
        ensure_pending_ops_table(conn)


__all__ = ["run_all"]
