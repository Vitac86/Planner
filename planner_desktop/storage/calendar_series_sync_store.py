"""SQLite links, recurring-master queue, and instance quarantine (schema v8)."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Optional, Sequence, Union

from planner_desktop.domain.series_calendar_link import (
    PendingSeriesSyncOp,
    RemoteOccurrenceChange,
    SeriesCalendarLink,
    SeriesLinkStatus,
    SeriesSyncOpKind,
    SeriesSyncOpStatus,
)
from planner_desktop.domain.task import utc_now
from planner_desktop.storage.calendar_sync_store import (
    MAX_ATTEMPTS,
    RETRY_BASE_DELAY_SECONDS,
    RETRY_MAX_DELAY_SECONDS,
)
from planner_desktop.storage.paths import ensure_desktop_data_dir, get_desktop_db_path
from planner_desktop.storage.schema import create_schema


def _dt_to_text(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value is not None else None


def _text_to_dt(value: Optional[str]) -> Optional[datetime]:
    return datetime.fromisoformat(value) if value else None


def _row_to_link(row: sqlite3.Row) -> SeriesCalendarLink:
    return SeriesCalendarLink(
        id=int(row["id"]),
        series_uid=str(row["series_uid"]),
        provider=str(row["provider"]),
        calendar_id=str(row["calendar_id"]),
        remote_event_id=str(row["remote_event_id"]),
        remote_etag=row["remote_etag"],
        remote_updated_at=_text_to_dt(row["remote_updated_at"]),
        link_status=SeriesLinkStatus(str(row["link_status"])),
        last_synced_series_revision=row["last_synced_series_revision"],
        last_synced_payload_hash=row["last_synced_payload_hash"],
        linked_at=_text_to_dt(row["linked_at"]) or utc_now(),
        updated_at=_text_to_dt(row["updated_at"]) or utc_now(),
        detached_at=_text_to_dt(row["detached_at"]),
        last_error=row["last_error"],
    )


def _row_to_op(row: sqlite3.Row) -> PendingSeriesSyncOp:
    return PendingSeriesSyncOp(
        id=int(row["id"]),
        series_uid=str(row["series_uid"]),
        op=SeriesSyncOpKind(str(row["op"])),
        remote_event_id=row["remote_event_id"],
        desired_revision=row["desired_revision"],
        desired_payload_hash=row["desired_payload_hash"],
        payload_json=row["payload_json"],
        attempts=int(row["attempts"]),
        last_error=row["last_error"],
        status=SeriesSyncOpStatus(str(row["status"])),
        created_at=_text_to_dt(row["created_at"]),
        next_try_at=_text_to_dt(row["next_try_at"]),
    )


def _row_to_occurrence_change(row: sqlite3.Row) -> RemoteOccurrenceChange:
    return RemoteOccurrenceChange(
        id=int(row["id"]),
        provider=str(row["provider"]),
        calendar_id=str(row["calendar_id"]),
        remote_master_event_id=str(row["remote_master_event_id"]),
        remote_instance_event_id=str(row["remote_instance_event_id"]),
        original_start_value=str(row["original_start_value"]),
        status=str(row["status"]),
        payload_json=row["payload_json"],
        remote_etag=row["remote_etag"],
        remote_updated_at=_text_to_dt(row["remote_updated_at"]),
        first_seen_at=_text_to_dt(row["first_seen_at"]) or utc_now(),
        last_seen_at=_text_to_dt(row["last_seen_at"]) or utc_now(),
        resolved_at=_text_to_dt(row["resolved_at"]),
    )


class CalendarSeriesSyncStore:
    """One local transaction boundary for links and the independent queue."""

    def __init__(
        self,
        db_path: Union[Path, str, None] = None,
        *,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        if db_path is None:
            ensure_desktop_data_dir()
            db_path = get_desktop_db_path()
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._clock = clock
        self._connection = sqlite3.connect(str(self.db_path))
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        create_schema(self._connection)

    def close(self) -> None:
        self._connection.close()

    # ---- links -------------------------------------------------------------

    def get_link(
        self, series_uid: str, *, include_detached: bool = False
    ) -> Optional[SeriesCalendarLink]:
        query = "SELECT * FROM task_series_calendar_links WHERE series_uid = ?"
        params: list[Any] = [series_uid]
        if not include_detached:
            query += " AND link_status <> ?"
            params.append(SeriesLinkStatus.DETACHED.value)
        row = self._connection.execute(
            query + " ORDER BY id DESC LIMIT 1", tuple(params)
        ).fetchone()
        return _row_to_link(row) if row is not None else None

    def get_link_by_remote(
        self,
        provider: str,
        calendar_id: str,
        remote_event_id: str,
        *,
        include_detached: bool = False,
    ) -> Optional[SeriesCalendarLink]:
        query = (
            "SELECT * FROM task_series_calendar_links WHERE provider = ? "
            "AND calendar_id = ? AND remote_event_id = ?"
        )
        params: list[Any] = [provider, calendar_id, remote_event_id]
        if not include_detached:
            query += " AND link_status <> ?"
            params.append(SeriesLinkStatus.DETACHED.value)
        row = self._connection.execute(
            query + " ORDER BY id DESC LIMIT 1", tuple(params)
        ).fetchone()
        return _row_to_link(row) if row is not None else None

    def list_links(self, *, include_detached: bool = True) -> list[SeriesCalendarLink]:
        query = "SELECT * FROM task_series_calendar_links"
        params: tuple[Any, ...] = ()
        if not include_detached:
            query += " WHERE link_status <> ?"
            params = (SeriesLinkStatus.DETACHED.value,)
        rows = self._connection.execute(query + " ORDER BY id", params).fetchall()
        return [_row_to_link(row) for row in rows]

    def create_pending_link(
        self,
        link: SeriesCalendarLink,
        *,
        desired_revision: int,
        desired_payload_hash: str,
        payload: dict[str, Any],
    ) -> SeriesCalendarLink:
        """Atomically insert one active link and one CREATE operation."""
        existing = self.get_link(link.series_uid)
        if existing is not None:
            return existing
        stamp = self._clock()
        payload_json = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            cursor = self._connection.execute(
                """
                INSERT INTO task_series_calendar_links (
                    series_uid, provider, calendar_id, remote_event_id,
                    link_status, linked_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    link.series_uid,
                    link.provider,
                    link.calendar_id,
                    link.remote_event_id,
                    SeriesLinkStatus.PENDING_CREATE.value,
                    _dt_to_text(stamp),
                    _dt_to_text(stamp),
                ),
            )
            self._connection.execute(
                """
                INSERT INTO pending_calendar_series_ops (
                    series_uid, op, remote_event_id, desired_revision,
                    desired_payload_hash, payload_json, attempts, status,
                    created_at, next_try_at
                ) VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, ?)
                """,
                (
                    link.series_uid,
                    SeriesSyncOpKind.CREATE.value,
                    link.remote_event_id,
                    desired_revision,
                    desired_payload_hash,
                    payload_json,
                    SeriesSyncOpStatus.PENDING.value,
                    _dt_to_text(stamp),
                    _dt_to_text(stamp),
                ),
            )
            self._connection.commit()
            link.id = int(cursor.lastrowid)
        except Exception:
            self._connection.rollback()
            raise
        stored = self.get_link(link.series_uid)
        if stored is None:  # pragma: no cover - SQLite invariant
            raise RuntimeError("Series link was not persisted.")
        return stored

    def update_link(self, link: SeriesCalendarLink) -> SeriesCalendarLink:
        link.updated_at = self._clock()
        cursor = self._connection.execute(
            """
            UPDATE task_series_calendar_links SET
                remote_event_id = ?, remote_etag = ?, remote_updated_at = ?,
                link_status = ?, last_synced_series_revision = ?,
                last_synced_payload_hash = ?, updated_at = ?, detached_at = ?,
                last_error = ?
            WHERE id = ?
            """,
            (
                link.remote_event_id,
                link.remote_etag,
                _dt_to_text(link.remote_updated_at),
                link.link_status.value,
                link.last_synced_series_revision,
                link.last_synced_payload_hash,
                _dt_to_text(link.updated_at),
                _dt_to_text(link.detached_at),
                link.last_error,
                link.id,
            ),
        )
        if cursor.rowcount == 0:
            self._connection.rollback()
            raise KeyError("Связь серии не найдена")
        self._connection.commit()
        return link

    def set_link_status(
        self,
        series_uid: str,
        status: SeriesLinkStatus,
        *,
        error: Optional[str] = None,
        remote_etag: Optional[str] = None,
        remote_updated_at: Optional[datetime] = None,
        synced_revision: Optional[int] = None,
        synced_payload_hash: Optional[str] = None,
    ) -> Optional[SeriesCalendarLink]:
        link = self.get_link(series_uid, include_detached=True)
        if link is None:
            return None
        link.link_status = status
        link.last_error = error
        if remote_etag is not None:
            link.remote_etag = remote_etag
        if remote_updated_at is not None:
            link.remote_updated_at = remote_updated_at
        if synced_revision is not None:
            link.last_synced_series_revision = synced_revision
        if synced_payload_hash is not None:
            link.last_synced_payload_hash = synced_payload_hash
        link.detached_at = self._clock() if status is SeriesLinkStatus.DETACHED else None
        return self.update_link(link)

    # ---- coalescing queue --------------------------------------------------

    @staticmethod
    def _payload_json(payload: Optional[dict[str, Any]]) -> Optional[str]:
        if payload is None:
            return None
        return json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )

    def get_pending_op(self, series_uid: str) -> Optional[PendingSeriesSyncOp]:
        row = self._connection.execute(
            "SELECT * FROM pending_calendar_series_ops WHERE series_uid = ? "
            "AND status = ? ORDER BY id DESC LIMIT 1",
            (series_uid, SeriesSyncOpStatus.PENDING.value),
        ).fetchone()
        return _row_to_op(row) if row is not None else None

    def enqueue_update(
        self,
        series_uid: str,
        *,
        desired_revision: int,
        desired_payload_hash: str,
        payload: dict[str, Any],
    ) -> bool:
        link = self.get_link(series_uid)
        if link is None:
            return False
        if link.link_status in (
            SeriesLinkStatus.PENDING_DELETE,
            SeriesLinkStatus.CONFLICT,
            SeriesLinkStatus.REMOTE_DELETED,
            SeriesLinkStatus.TERMINAL_ERROR,
        ):
            return False
        pending = self.get_pending_op(series_uid)
        if pending is None and link.last_synced_payload_hash == desired_payload_hash:
            return False
        if pending is not None and pending.op is SeriesSyncOpKind.DELETE:
            return False
        now = self._clock()
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            if pending is None:
                self._connection.execute(
                    """
                    INSERT INTO pending_calendar_series_ops (
                        series_uid, op, remote_event_id, desired_revision,
                        desired_payload_hash, payload_json, attempts, status,
                        created_at, next_try_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, ?)
                    """,
                    (
                        series_uid,
                        SeriesSyncOpKind.UPDATE.value,
                        link.remote_event_id,
                        desired_revision,
                        desired_payload_hash,
                        self._payload_json(payload),
                        SeriesSyncOpStatus.PENDING.value,
                        _dt_to_text(now),
                        _dt_to_text(now),
                    ),
                )
                next_status = SeriesLinkStatus.PENDING_UPDATE
            else:
                # CREATE + UPDATE stays CREATE; UPDATE + UPDATE stays UPDATE.
                self._connection.execute(
                    """
                    UPDATE pending_calendar_series_ops SET
                        desired_revision = ?, desired_payload_hash = ?,
                        payload_json = ?, attempts = 0, last_error = NULL,
                        next_try_at = ? WHERE id = ?
                    """,
                    (
                        desired_revision,
                        desired_payload_hash,
                        self._payload_json(payload),
                        _dt_to_text(now),
                        pending.id,
                    ),
                )
                next_status = (
                    SeriesLinkStatus.PENDING_CREATE
                    if pending.op is SeriesSyncOpKind.CREATE
                    else SeriesLinkStatus.PENDING_UPDATE
                )
            self._connection.execute(
                "UPDATE task_series_calendar_links SET link_status = ?, "
                "updated_at = ?, last_error = NULL WHERE id = ?",
                (next_status.value, _dt_to_text(now), link.id),
            )
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise
        return True

    def enqueue_delete(
        self,
        series_uid: str,
        *,
        delete_local_after_remote: bool = False,
    ) -> str:
        """Coalesce DELETE; return queued/already/cancelled_create/missing."""
        link = self.get_link(series_uid)
        if link is None:
            return "missing"
        pending = self.get_pending_op(series_uid)
        now = self._clock()
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            if pending is not None and pending.op is SeriesSyncOpKind.CREATE:
                # No remote master exists yet: CREATE + DELETE cancels both.
                self._connection.execute(
                    "DELETE FROM pending_calendar_series_ops WHERE id = ?",
                    (pending.id,),
                )
                self._connection.execute(
                    "UPDATE task_series_calendar_links SET link_status = ?, "
                    "updated_at = ?, detached_at = ?, last_error = NULL WHERE id = ?",
                    (
                        SeriesLinkStatus.DETACHED.value,
                        _dt_to_text(now),
                        _dt_to_text(now),
                        link.id,
                    ),
                )
                self._connection.commit()
                return "cancelled_create"

            payload = {"delete_local_after_remote": bool(delete_local_after_remote)}
            if pending is None:
                self._connection.execute(
                    """
                    INSERT INTO pending_calendar_series_ops (
                        series_uid, op, remote_event_id, payload_json,
                        attempts, status, created_at, next_try_at
                    ) VALUES (?, ?, ?, ?, 0, ?, ?, ?)
                    """,
                    (
                        series_uid,
                        SeriesSyncOpKind.DELETE.value,
                        link.remote_event_id,
                        self._payload_json(payload),
                        SeriesSyncOpStatus.PENDING.value,
                        _dt_to_text(now),
                        _dt_to_text(now),
                    ),
                )
            elif pending.op is SeriesSyncOpKind.DELETE:
                current = pending.payload
                payload["delete_local_after_remote"] = bool(
                    current.get("delete_local_after_remote")
                    or delete_local_after_remote
                )
                self._connection.execute(
                    "UPDATE pending_calendar_series_ops SET payload_json = ? "
                    "WHERE id = ?",
                    (self._payload_json(payload), pending.id),
                )
            else:
                # UPDATE + DELETE becomes one DELETE.
                self._connection.execute(
                    """
                    UPDATE pending_calendar_series_ops SET
                        op = ?, remote_event_id = ?, desired_revision = NULL,
                        desired_payload_hash = NULL, payload_json = ?, attempts = 0,
                        last_error = NULL, next_try_at = ? WHERE id = ?
                    """,
                    (
                        SeriesSyncOpKind.DELETE.value,
                        link.remote_event_id,
                        self._payload_json(payload),
                        _dt_to_text(now),
                        pending.id,
                    ),
                )
            self._connection.execute(
                "UPDATE task_series_calendar_links SET link_status = ?, "
                "updated_at = ?, last_error = NULL WHERE id = ?",
                (SeriesLinkStatus.PENDING_DELETE.value, _dt_to_text(now), link.id),
            )
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise
        return "already" if pending and pending.op is SeriesSyncOpKind.DELETE else "queued"

    def disconnect_keep_remote(self, series_uid: str) -> bool:
        link = self.get_link(series_uid)
        if link is None:
            return False
        now = self._clock()
        with self._connection:
            self._connection.execute(
                "DELETE FROM pending_calendar_series_ops WHERE series_uid = ? "
                "AND status = ?",
                (series_uid, SeriesSyncOpStatus.PENDING.value),
            )
            self._connection.execute(
                "UPDATE task_series_calendar_links SET link_status = ?, "
                "updated_at = ?, detached_at = ?, last_error = NULL WHERE id = ?",
                (
                    SeriesLinkStatus.DETACHED.value,
                    _dt_to_text(now),
                    _dt_to_text(now),
                    link.id,
                ),
            )
        return True

    def cancel_pending_ops(self, series_uid: str) -> None:
        self._connection.execute(
            "DELETE FROM pending_calendar_series_ops WHERE series_uid = ? "
            "AND status = ?",
            (series_uid, SeriesSyncOpStatus.PENDING.value),
        )
        self._connection.commit()

    def cancel_unpushed_delete(self, series_uid: str) -> bool:
        link = self.get_link(series_uid)
        op = self.get_pending_op(series_uid)
        if (
            link is None
            or op is None
            or op.op is not SeriesSyncOpKind.DELETE
            or op.attempts != 0
        ):
            return False
        now = self._clock()
        with self._connection:
            self._connection.execute(
                "DELETE FROM pending_calendar_series_ops WHERE id = ?", (op.id,)
            )
            self._connection.execute(
                "UPDATE task_series_calendar_links SET link_status = ?, "
                "updated_at = ?, last_error = NULL WHERE id = ?",
                (SeriesLinkStatus.SYNCED.value, _dt_to_text(now), link.id),
            )
        return True

    def list_due_ops(self, limit: int = 50) -> list[PendingSeriesSyncOp]:
        rows = self._connection.execute(
            "SELECT * FROM pending_calendar_series_ops WHERE status = ? "
            "AND next_try_at <= ? ORDER BY id LIMIT ?",
            (
                SeriesSyncOpStatus.PENDING.value,
                _dt_to_text(self._clock()),
                int(limit),
            ),
        ).fetchall()
        return [_row_to_op(row) for row in rows]

    def list_ops(
        self, *, status: Optional[SeriesSyncOpStatus] = None
    ) -> list[PendingSeriesSyncOp]:
        query = "SELECT * FROM pending_calendar_series_ops"
        params: tuple[Any, ...] = ()
        if status is not None:
            query += " WHERE status = ?"
            params = (status.value,)
        rows = self._connection.execute(query + " ORDER BY id", params).fetchall()
        return [_row_to_op(row) for row in rows]

    def remove_op(self, op_id: int) -> None:
        self._connection.execute(
            "DELETE FROM pending_calendar_series_ops WHERE id = ?", (op_id,)
        )
        self._connection.commit()

    def requeue_op(self, op_id: int, error: str) -> bool:
        row = self._connection.execute(
            "SELECT attempts, series_uid FROM pending_calendar_series_ops "
            "WHERE id = ?",
            (op_id,),
        ).fetchone()
        if row is None:
            return False
        attempts = int(row["attempts"]) + 1
        if attempts >= MAX_ATTEMPTS:
            self.mark_terminal(op_id, error, attempts=attempts)
            return False
        delay = min(
            RETRY_BASE_DELAY_SECONDS * 2 ** (attempts - 1),
            RETRY_MAX_DELAY_SECONDS,
        )
        self._connection.execute(
            "UPDATE pending_calendar_series_ops SET attempts = ?, "
            "last_error = ?, next_try_at = ? WHERE id = ?",
            (
                attempts,
                error,
                _dt_to_text(self._clock() + timedelta(seconds=delay)),
                op_id,
            ),
        )
        self._connection.commit()
        return True

    def mark_terminal(
        self, op_id: int, error: str, *, attempts: Optional[int] = None
    ) -> None:
        op_row = self._connection.execute(
            "SELECT series_uid FROM pending_calendar_series_ops WHERE id = ?",
            (op_id,),
        ).fetchone()
        if op_row is None:
            return
        values: list[Any] = [SeriesSyncOpStatus.TERMINAL.value, error]
        sql = "UPDATE pending_calendar_series_ops SET status = ?, last_error = ?"
        if attempts is not None:
            sql += ", attempts = ?"
            values.append(attempts)
        sql += " WHERE id = ?"
        values.append(op_id)
        now = self._clock()
        with self._connection:
            self._connection.execute(sql, tuple(values))
            self._connection.execute(
                "UPDATE task_series_calendar_links SET link_status = ?, "
                "last_error = ?, updated_at = ? WHERE series_uid = ? "
                "AND link_status <> ?",
                (
                    SeriesLinkStatus.TERMINAL_ERROR.value,
                    error,
                    _dt_to_text(now),
                    str(op_row["series_uid"]),
                    SeriesLinkStatus.DETACHED.value,
                ),
            )

    def retry_terminal_operation(self, op_id: int) -> bool:
        row = self._connection.execute(
            "SELECT * FROM pending_calendar_series_ops WHERE id = ? "
            "AND status = ?",
            (op_id, SeriesSyncOpStatus.TERMINAL.value),
        ).fetchone()
        if row is None:
            return False
        op = _row_to_op(row)
        if self.get_pending_op(op.series_uid) is not None:
            return False
        link = self.get_link(op.series_uid)
        if link is None or link.link_status is not SeriesLinkStatus.TERMINAL_ERROR:
            return False
        status = {
            SeriesSyncOpKind.CREATE: SeriesLinkStatus.PENDING_CREATE,
            SeriesSyncOpKind.UPDATE: SeriesLinkStatus.PENDING_UPDATE,
            SeriesSyncOpKind.DELETE: SeriesLinkStatus.PENDING_DELETE,
        }[op.op]
        now = self._clock()
        with self._connection:
            self._connection.execute(
                "UPDATE pending_calendar_series_ops SET status = ?, attempts = 0, "
                "last_error = NULL, next_try_at = ? WHERE id = ?",
                (SeriesSyncOpStatus.PENDING.value, _dt_to_text(now), op_id),
            )
            self._connection.execute(
                "UPDATE task_series_calendar_links SET link_status = ?, "
                "last_error = NULL, updated_at = ? WHERE id = ?",
                (status.value, _dt_to_text(now), link.id),
            )
        return True

    def count_pending_ops(self) -> int:
        row = self._connection.execute(
            "SELECT COUNT(*) AS n FROM pending_calendar_series_ops WHERE status = ?",
            (SeriesSyncOpStatus.PENDING.value,),
        ).fetchone()
        return int(row["n"])

    def count_terminal_ops(self) -> int:
        row = self._connection.execute(
            "SELECT COUNT(*) AS n FROM pending_calendar_series_ops WHERE status = ?",
            (SeriesSyncOpStatus.TERMINAL.value,),
        ).fetchone()
        return int(row["n"])

    def count_pending_by_op(self) -> dict[str, int]:
        counts = {kind.value: 0 for kind in SeriesSyncOpKind}
        rows = self._connection.execute(
            "SELECT op, COUNT(*) AS n FROM pending_calendar_series_ops "
            "WHERE status = ? GROUP BY op",
            (SeriesSyncOpStatus.PENDING.value,),
        ).fetchall()
        for row in rows:
            counts[str(row["op"])] = int(row["n"])
        return counts

    # ---- changed linked instances -----------------------------------------

    def upsert_occurrence_change(
        self, change: RemoteOccurrenceChange
    ) -> RemoteOccurrenceChange:
        self._connection.execute(
            """
            INSERT INTO external_series_occurrence_changes (
                provider, calendar_id, remote_master_event_id,
                remote_instance_event_id, original_start_value, status,
                payload_json, remote_etag, remote_updated_at, first_seen_at,
                last_seen_at, resolved_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (
                provider, calendar_id, remote_master_event_id,
                remote_instance_event_id, original_start_value
            ) DO UPDATE SET
                status = excluded.status,
                payload_json = excluded.payload_json,
                remote_etag = excluded.remote_etag,
                remote_updated_at = excluded.remote_updated_at,
                last_seen_at = excluded.last_seen_at,
                resolved_at = excluded.resolved_at
            """,
            (
                change.provider,
                change.calendar_id,
                change.remote_master_event_id,
                change.remote_instance_event_id,
                change.original_start_value,
                change.status,
                change.payload_json,
                change.remote_etag,
                _dt_to_text(change.remote_updated_at),
                _dt_to_text(change.first_seen_at),
                _dt_to_text(change.last_seen_at),
                _dt_to_text(change.resolved_at),
            ),
        )
        self._connection.commit()
        row = self._connection.execute(
            "SELECT * FROM external_series_occurrence_changes WHERE "
            "provider = ? AND calendar_id = ? AND remote_master_event_id = ? "
            "AND remote_instance_event_id = ? AND original_start_value = ?",
            (
                change.provider,
                change.calendar_id,
                change.remote_master_event_id,
                change.remote_instance_event_id,
                change.original_start_value,
            ),
        ).fetchone()
        return _row_to_occurrence_change(row)

    def list_occurrence_changes(
        self, *, unresolved_only: bool = True
    ) -> list[RemoteOccurrenceChange]:
        query = "SELECT * FROM external_series_occurrence_changes"
        if unresolved_only:
            query += " WHERE resolved_at IS NULL"
        rows = self._connection.execute(query + " ORDER BY id").fetchall()
        return [_row_to_occurrence_change(row) for row in rows]

    def count_quarantined(self) -> int:
        row = self._connection.execute(
            "SELECT COUNT(*) AS n FROM external_series_occurrence_changes "
            "WHERE resolved_at IS NULL"
        ).fetchone()
        return int(row["n"])

    def diagnostics(self) -> dict[str, int]:
        counts = {status.value: 0 for status in SeriesLinkStatus}
        rows = self._connection.execute(
            "SELECT link_status, COUNT(*) AS n FROM task_series_calendar_links "
            "GROUP BY link_status"
        ).fetchall()
        for row in rows:
            counts[str(row["link_status"])] = int(row["n"])
        counts["quarantined"] = self.count_quarantined()
        counts["series_ops_terminal"] = self.count_terminal_ops()
        return counts


__all__ = ["CalendarSeriesSyncStore"]
