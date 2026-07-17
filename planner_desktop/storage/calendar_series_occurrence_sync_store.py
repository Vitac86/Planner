"""SQLite state and queue for linked recurring-instance synchronization.

The queue in this module is intentionally independent from both
``desktop_pending_calendar_ops`` (ordinary Tasks) and
``pending_calendar_series_ops`` (recurring masters).
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Optional, Union

from planner_desktop.domain.google_occurrence import (
    GoogleOccurrenceIdentity,
    OccurrenceCalendarLink,
    OccurrenceOperationKind,
    OccurrenceOperationStatus,
    OccurrenceSyncStatus,
    PendingOccurrenceOperation,
    canonical_occurrence_payload_fingerprint,
)
from planner_desktop.domain.series_calendar_link import (
    RemoteOccurrenceChange,
    SeriesCalendarLink,
)
from planner_desktop.domain.task import utc_now
from planner_desktop.storage.calendar_sync_store import (
    MAX_ATTEMPTS,
    RETRY_BASE_DELAY_SECONDS,
    RETRY_MAX_DELAY_SECONDS,
)
from planner_desktop.storage.paths import (
    ensure_desktop_data_dir,
    get_desktop_db_path,
)
from planner_desktop.storage.schema import create_schema


def _dt_to_text(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value is not None else None


def _text_to_dt(value: Optional[str]) -> Optional[datetime]:
    return datetime.fromisoformat(value) if value else None


def _payload_json(payload: Optional[dict[str, Any]]) -> Optional[str]:
    if payload is None:
        return None
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def _row_to_link(row: sqlite3.Row) -> OccurrenceCalendarLink:
    return OccurrenceCalendarLink(
        id=int(row["id"]),
        series_uid=str(row["series_uid"]),
        occurrence_key=str(row["occurrence_key"]),
        series_link_id=int(row["series_link_id"]),
        link_generation=int(row["link_generation"]),
        remote_master_event_id=str(row["remote_master_event_id"]),
        remote_instance_event_id=row["remote_instance_event_id"],
        original_start_kind=str(row["original_start_kind"]),
        original_start_value=str(row["original_start_value"]),
        original_start_timezone=row["original_start_timezone"],
        remote_etag=row["remote_etag"],
        remote_updated_at=_text_to_dt(row["remote_updated_at"]),
        sync_status=OccurrenceSyncStatus(str(row["sync_status"])),
        last_synced_local_hash=row["last_synced_local_hash"],
        last_synced_remote_hash=row["last_synced_remote_hash"],
        is_cancelled_remote=bool(row["is_cancelled_remote"]),
        conflict_reason=row["conflict_reason"],
        conflict_snapshot_json=row["conflict_snapshot_json"],
        created_at=_text_to_dt(row["created_at"]) or utc_now(),
        updated_at=_text_to_dt(row["updated_at"]) or utc_now(),
        detached_at=_text_to_dt(row["detached_at"]),
    )


def _row_to_op(row: sqlite3.Row) -> PendingOccurrenceOperation:
    return PendingOccurrenceOperation(
        id=int(row["id"]),
        series_uid=str(row["series_uid"]),
        occurrence_key=str(row["occurrence_key"]),
        series_link_id=int(row["series_link_id"]),
        op=OccurrenceOperationKind(str(row["op"])),
        remote_master_event_id=str(row["remote_master_event_id"]),
        remote_instance_event_id=row["remote_instance_event_id"],
        original_start_value=str(row["original_start_value"]),
        acknowledged_remote_etag=row["acknowledged_remote_etag"],
        desired_payload_hash=row["desired_payload_hash"],
        payload_json=row["payload_json"],
        attempts=int(row["attempts"]),
        last_error=row["last_error"],
        status=OccurrenceOperationStatus(str(row["status"])),
        created_at=_text_to_dt(row["created_at"]),
        next_try_at=_text_to_dt(row["next_try_at"]),
    )


def _row_to_change(row: sqlite3.Row) -> RemoteOccurrenceChange:
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
        matched_series_uid=row["matched_series_uid"],
        matched_occurrence_key=row["matched_occurrence_key"],
        resolution_status=str(row["resolution_status"] or "unresolved"),
        resolution_kind=row["resolution_kind"],
        resolved_at=_text_to_dt(row["resolved_at"]),
        resolution_error=row["resolution_error"],
    )


class CalendarSeriesOccurrenceSyncStore:
    """Transaction boundary for occurrence links, queue and quarantine."""

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

    # ---- occurrence links -------------------------------------------------

    def get_occurrence_link(
        self,
        series_uid: str,
        occurrence_key: str,
        *,
        link_generation: Optional[int] = None,
        include_detached: bool = False,
    ) -> Optional[OccurrenceCalendarLink]:
        query = (
            "SELECT * FROM task_series_occurrence_calendar_links "
            "WHERE series_uid = ? AND occurrence_key = ?"
        )
        params: list[Any] = [series_uid, occurrence_key]
        if link_generation is not None:
            query += " AND link_generation = ?"
            params.append(int(link_generation))
        if not include_detached:
            query += " AND detached_at IS NULL"
        row = self._connection.execute(
            query + " ORDER BY link_generation DESC, id DESC LIMIT 1",
            tuple(params),
        ).fetchone()
        return _row_to_link(row) if row is not None else None

    # Short aliases make the repository pleasant in use-case and test code.
    get_link = get_occurrence_link

    def get_link_by_remote_instance(
        self, remote_master_event_id: str, remote_instance_event_id: str
    ) -> Optional[OccurrenceCalendarLink]:
        row = self._connection.execute(
            "SELECT * FROM task_series_occurrence_calendar_links "
            "WHERE remote_master_event_id = ? AND remote_instance_event_id = ? "
            "AND detached_at IS NULL ORDER BY id DESC LIMIT 1",
            (remote_master_event_id, remote_instance_event_id),
        ).fetchone()
        return _row_to_link(row) if row is not None else None

    def list_occurrence_links(
        self,
        *,
        series_uid: Optional[str] = None,
        include_detached: bool = False,
    ) -> list[OccurrenceCalendarLink]:
        query = "SELECT * FROM task_series_occurrence_calendar_links"
        where: list[str] = []
        params: list[Any] = []
        if series_uid is not None:
            where.append("series_uid = ?")
            params.append(series_uid)
        if not include_detached:
            where.append("detached_at IS NULL")
        if where:
            query += " WHERE " + " AND ".join(where)
        rows = self._connection.execute(
            query + " ORDER BY id", tuple(params)
        ).fetchall()
        return [_row_to_link(row) for row in rows]

    list_links = list_occurrence_links

    def ensure_occurrence_link(
        self,
        series_uid: str,
        occurrence_key: str,
        series_link: SeriesCalendarLink,
        identity: GoogleOccurrenceIdentity,
    ) -> OccurrenceCalendarLink:
        if series_link.id is None:
            raise ValueError("active series link must be persisted")
        current = self.get_occurrence_link(
            series_uid,
            occurrence_key,
            link_generation=series_link.link_generation,
        )
        if current is not None:
            if (
                current.remote_master_event_id != series_link.remote_event_id
                or current.identity != identity
            ):
                raise ValueError("occurrence identity does not match stored link")
            return current
        stamp = self._clock()
        try:
            cursor = self._connection.execute(
                """
                INSERT INTO task_series_occurrence_calendar_links (
                    series_uid, occurrence_key, series_link_id,
                    link_generation, remote_master_event_id,
                    original_start_kind, original_start_value,
                    original_start_timezone, sync_status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    series_uid,
                    occurrence_key,
                    int(series_link.id),
                    int(series_link.link_generation),
                    series_link.remote_event_id,
                    identity.kind,
                    identity.value,
                    identity.timezone_name,
                    OccurrenceSyncStatus.LOCAL_ONLY.value,
                    _dt_to_text(stamp),
                    _dt_to_text(stamp),
                ),
            )
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise
        row = self._connection.execute(
            "SELECT * FROM task_series_occurrence_calendar_links WHERE id = ?",
            (int(cursor.lastrowid),),
        ).fetchone()
        return _row_to_link(row)

    ensure_link = ensure_occurrence_link

    def update_occurrence_link(
        self, link: OccurrenceCalendarLink
    ) -> OccurrenceCalendarLink:
        if link.id is None:
            raise ValueError("occurrence link must be persisted")
        link.updated_at = self._clock()
        cursor = self._connection.execute(
            """
            UPDATE task_series_occurrence_calendar_links SET
                remote_instance_event_id = ?, remote_etag = ?,
                remote_updated_at = ?, sync_status = ?,
                last_synced_local_hash = ?, last_synced_remote_hash = ?,
                is_cancelled_remote = ?, conflict_reason = ?,
                conflict_snapshot_json = ?, updated_at = ?, detached_at = ?
            WHERE id = ?
            """,
            (
                link.remote_instance_event_id,
                link.remote_etag,
                _dt_to_text(link.remote_updated_at),
                link.sync_status.value,
                link.last_synced_local_hash,
                link.last_synced_remote_hash,
                int(link.is_cancelled_remote),
                link.conflict_reason,
                link.conflict_snapshot_json,
                _dt_to_text(link.updated_at),
                _dt_to_text(link.detached_at),
                link.id,
            ),
        )
        if cursor.rowcount != 1:
            self._connection.rollback()
            raise KeyError("occurrence link not found")
        self._connection.commit()
        return link

    update_link = update_occurrence_link

    # ---- independent queue ------------------------------------------------

    def get_pending_op(
        self, series_uid: str, occurrence_key: str
    ) -> Optional[PendingOccurrenceOperation]:
        row = self._connection.execute(
            "SELECT * FROM pending_calendar_series_instance_ops "
            "WHERE series_uid = ? AND occurrence_key = ? AND status = ? "
            "ORDER BY id DESC LIMIT 1",
            (
                series_uid,
                occurrence_key,
                OccurrenceOperationStatus.PENDING.value,
            ),
        ).fetchone()
        return _row_to_op(row) if row is not None else None

    def _require_active_link(
        self, series_uid: str, occurrence_key: str
    ) -> OccurrenceCalendarLink:
        link = self.get_occurrence_link(series_uid, occurrence_key)
        if link is None:
            raise KeyError("active occurrence link not found")
        return link

    def enqueue_update(
        self,
        series_uid: str,
        occurrence_key: str,
        payload: dict[str, Any],
        *,
        desired_payload_hash: Optional[str] = None,
        acknowledged_remote_etag: Optional[str] = None,
        allow_cancelled_restore: bool = False,
    ) -> bool:
        link = self._require_active_link(series_uid, occurrence_key)
        desired_hash = (
            desired_payload_hash
            or canonical_occurrence_payload_fingerprint(payload)
        )
        pending = self.get_pending_op(series_uid, occurrence_key)
        if pending is not None and pending.op is OccurrenceOperationKind.CANCEL:
            if not allow_cancelled_restore:
                raise ValueError(
                    "cancelled occurrence must be explicitly restored locally "
                    "before an update can be queued"
                )
        if (
            pending is not None
            and pending.op is OccurrenceOperationKind.UPDATE
            and pending.desired_payload_hash == desired_hash
        ):
            return False
        if (
            pending is None
            and link.sync_status is OccurrenceSyncStatus.SYNCED_EXCEPTION
            and link.last_synced_local_hash == desired_hash
        ):
            return False
        stamp = self._clock()
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            if pending is None:
                self._connection.execute(
                    """
                    INSERT INTO pending_calendar_series_instance_ops (
                        series_uid, occurrence_key, series_link_id, op,
                        remote_master_event_id, remote_instance_event_id,
                        original_start_value, acknowledged_remote_etag,
                        desired_payload_hash, payload_json, attempts, status,
                        created_at, next_try_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)
                    """,
                    (
                        series_uid,
                        occurrence_key,
                        link.series_link_id,
                        OccurrenceOperationKind.UPDATE.value,
                        link.remote_master_event_id,
                        link.remote_instance_event_id,
                        link.original_start_value,
                        acknowledged_remote_etag,
                        desired_hash,
                        _payload_json(payload),
                        OccurrenceOperationStatus.PENDING.value,
                        _dt_to_text(stamp),
                        _dt_to_text(stamp),
                    ),
                )
            else:
                self._connection.execute(
                    """
                    UPDATE pending_calendar_series_instance_ops SET
                        op = ?, remote_instance_event_id = ?,
                        acknowledged_remote_etag = ?,
                        desired_payload_hash = ?, payload_json = ?,
                        attempts = 0, last_error = NULL, next_try_at = ?
                    WHERE id = ?
                    """,
                    (
                        OccurrenceOperationKind.UPDATE.value,
                        link.remote_instance_event_id,
                        acknowledged_remote_etag,
                        desired_hash,
                        _payload_json(payload),
                        _dt_to_text(stamp),
                        pending.id,
                    ),
                )
            self._connection.execute(
                "UPDATE task_series_occurrence_calendar_links SET "
                "sync_status = ?, updated_at = ?, conflict_reason = NULL "
                "WHERE id = ?",
                (
                    OccurrenceSyncStatus.PENDING_UPDATE.value,
                    _dt_to_text(stamp),
                    link.id,
                ),
            )
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise
        return True

    def enqueue_cancel(
        self,
        series_uid: str,
        occurrence_key: str,
        payload: dict[str, Any],
        *,
        acknowledged_remote_etag: Optional[str] = None,
    ) -> bool:
        link = self._require_active_link(series_uid, occurrence_key)
        pending = self.get_pending_op(series_uid, occurrence_key)
        if pending is not None and pending.op is OccurrenceOperationKind.CANCEL:
            return False
        stamp = self._clock()
        payload_hash = canonical_occurrence_payload_fingerprint(payload)
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            if pending is None:
                self._connection.execute(
                    """
                    INSERT INTO pending_calendar_series_instance_ops (
                        series_uid, occurrence_key, series_link_id, op,
                        remote_master_event_id, remote_instance_event_id,
                        original_start_value, acknowledged_remote_etag,
                        desired_payload_hash, payload_json, attempts, status,
                        created_at, next_try_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)
                    """,
                    (
                        series_uid,
                        occurrence_key,
                        link.series_link_id,
                        OccurrenceOperationKind.CANCEL.value,
                        link.remote_master_event_id,
                        link.remote_instance_event_id,
                        link.original_start_value,
                        acknowledged_remote_etag,
                        payload_hash,
                        _payload_json(payload),
                        OccurrenceOperationStatus.PENDING.value,
                        _dt_to_text(stamp),
                        _dt_to_text(stamp),
                    ),
                )
            else:
                # UPDATE + CANCEL collapses to one CANCEL.
                self._connection.execute(
                    """
                    UPDATE pending_calendar_series_instance_ops SET
                        op = ?, remote_instance_event_id = ?,
                        acknowledged_remote_etag = ?,
                        desired_payload_hash = ?, payload_json = ?,
                        attempts = 0, last_error = NULL, next_try_at = ?
                    WHERE id = ?
                    """,
                    (
                        OccurrenceOperationKind.CANCEL.value,
                        link.remote_instance_event_id,
                        acknowledged_remote_etag,
                        payload_hash,
                        _payload_json(payload),
                        _dt_to_text(stamp),
                        pending.id,
                    ),
                )
            self._connection.execute(
                "UPDATE task_series_occurrence_calendar_links SET "
                "sync_status = ?, updated_at = ? WHERE id = ?",
                (
                    OccurrenceSyncStatus.PENDING_CANCEL.value,
                    _dt_to_text(stamp),
                    link.id,
                ),
            )
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise
        return True

    def list_due_ops(self, limit: int = 50) -> list[PendingOccurrenceOperation]:
        rows = self._connection.execute(
            "SELECT * FROM pending_calendar_series_instance_ops "
            "WHERE status = ? AND next_try_at <= ? ORDER BY id LIMIT ?",
            (
                OccurrenceOperationStatus.PENDING.value,
                _dt_to_text(self._clock()),
                int(limit),
            ),
        ).fetchall()
        return [_row_to_op(row) for row in rows]

    def list_terminal_ops(self) -> list[PendingOccurrenceOperation]:
        rows = self._connection.execute(
            "SELECT * FROM pending_calendar_series_instance_ops "
            "WHERE status = ? ORDER BY id",
            (OccurrenceOperationStatus.TERMINAL.value,),
        ).fetchall()
        return [_row_to_op(row) for row in rows]

    def count_pending_ops(self) -> int:
        row = self._connection.execute(
            "SELECT COUNT(*) AS n FROM pending_calendar_series_instance_ops "
            "WHERE status = ?",
            (OccurrenceOperationStatus.PENDING.value,),
        ).fetchone()
        return int(row["n"])

    def count_terminal_ops(self) -> int:
        row = self._connection.execute(
            "SELECT COUNT(*) AS n FROM pending_calendar_series_instance_ops "
            "WHERE status = ?",
            (OccurrenceOperationStatus.TERMINAL.value,),
        ).fetchone()
        return int(row["n"])

    def count_pending_by_op(self) -> dict[str, int]:
        result = {kind.value: 0 for kind in OccurrenceOperationKind}
        rows = self._connection.execute(
            "SELECT op, COUNT(*) AS n "
            "FROM pending_calendar_series_instance_ops WHERE status = ? "
            "GROUP BY op",
            (OccurrenceOperationStatus.PENDING.value,),
        ).fetchall()
        for row in rows:
            result[str(row["op"])] = int(row["n"])
        return result

    def remove_op(self, op_id: int) -> None:
        self._connection.execute(
            "DELETE FROM pending_calendar_series_instance_ops WHERE id = ?",
            (int(op_id),),
        )
        self._connection.commit()

    def requeue_op(self, op_id: int, error: str) -> bool:
        row = self._connection.execute(
            "SELECT attempts, series_uid, occurrence_key "
            "FROM pending_calendar_series_instance_ops WHERE id = ?",
            (int(op_id),),
        ).fetchone()
        if row is None:
            return False
        attempts = int(row["attempts"]) + 1
        if attempts >= MAX_ATTEMPTS:
            self.mark_terminal(op_id, error)
            return False
        delay = min(
            RETRY_MAX_DELAY_SECONDS,
            RETRY_BASE_DELAY_SECONDS * (2 ** max(0, attempts - 1)),
        )
        self._connection.execute(
            "UPDATE pending_calendar_series_instance_ops SET attempts = ?, "
            "last_error = ?, next_try_at = ? WHERE id = ?",
            (
                attempts,
                str(error),
                _dt_to_text(self._clock() + timedelta(seconds=delay)),
                int(op_id),
            ),
        )
        self._connection.commit()
        return True

    def mark_terminal(self, op_id: int, error: str) -> bool:
        row = self._connection.execute(
            "SELECT series_uid, occurrence_key "
            "FROM pending_calendar_series_instance_ops WHERE id = ?",
            (int(op_id),),
        ).fetchone()
        if row is None:
            return False
        stamp = self._clock()
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            self._connection.execute(
                "UPDATE pending_calendar_series_instance_ops SET "
                "status = ?, last_error = ? WHERE id = ?",
                (
                    OccurrenceOperationStatus.TERMINAL.value,
                    str(error),
                    int(op_id),
                ),
            )
            self._connection.execute(
                "UPDATE task_series_occurrence_calendar_links SET "
                "sync_status = ?, conflict_reason = ?, updated_at = ? "
                "WHERE series_uid = ? AND occurrence_key = ? "
                "AND detached_at IS NULL",
                (
                    OccurrenceSyncStatus.TERMINAL_ERROR.value,
                    str(error),
                    _dt_to_text(stamp),
                    str(row["series_uid"]),
                    str(row["occurrence_key"]),
                ),
            )
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise
        return True

    def retry_terminal_operation(self, op_id: int) -> bool:
        row = self._connection.execute(
            "SELECT series_uid, occurrence_key FROM "
            "pending_calendar_series_instance_ops "
            "WHERE id = ? AND status = ?",
            (int(op_id), OccurrenceOperationStatus.TERMINAL.value),
        ).fetchone()
        if row is None:
            return False
        if self.get_pending_op(str(row["series_uid"]), str(row["occurrence_key"])):
            return False
        stamp = self._clock()
        self._connection.execute(
            "UPDATE pending_calendar_series_instance_ops SET status = ?, "
            "attempts = 0, last_error = NULL, next_try_at = ? WHERE id = ?",
            (
                OccurrenceOperationStatus.PENDING.value,
                _dt_to_text(stamp),
                int(op_id),
            ),
        )
        self._connection.execute(
            "UPDATE task_series_occurrence_calendar_links SET "
            "sync_status = CASE WHEN ? = 'cancel' THEN ? ELSE ? END, "
            "conflict_reason = NULL, updated_at = ? "
            "WHERE series_uid = ? AND occurrence_key = ? "
            "AND detached_at IS NULL",
            (
                self._connection.execute(
                    "SELECT op FROM pending_calendar_series_instance_ops "
                    "WHERE id = ?",
                    (int(op_id),),
                ).fetchone()["op"],
                OccurrenceSyncStatus.PENDING_CANCEL.value,
                OccurrenceSyncStatus.PENDING_UPDATE.value,
                _dt_to_text(stamp),
                str(row["series_uid"]),
                str(row["occurrence_key"]),
            ),
        )
        self._connection.commit()
        return True

    # ---- success, conflict and reconciliation -----------------------------

    def finalize_success(
        self,
        op: PendingOccurrenceOperation,
        *,
        remote_instance_event_id: str,
        remote_etag: Optional[str],
        remote_updated_at: Optional[datetime],
        local_hash: Optional[str],
        remote_hash: Optional[str],
        cancelled: bool,
    ) -> OccurrenceCalendarLink:
        """Persist canonical remote state and remove the queue row last."""
        status = (
            OccurrenceSyncStatus.CANCELLED
            if cancelled
            else OccurrenceSyncStatus.SYNCED_EXCEPTION
        )
        stamp = self._clock()
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            self._connection.execute(
                """
                UPDATE task_series_occurrence_calendar_links SET
                    remote_instance_event_id = ?, remote_etag = ?,
                    remote_updated_at = ?, sync_status = ?,
                    last_synced_local_hash = ?, last_synced_remote_hash = ?,
                    is_cancelled_remote = ?, conflict_reason = NULL,
                    conflict_snapshot_json = NULL, updated_at = ?
                WHERE series_uid = ? AND occurrence_key = ?
                  AND series_link_id = ? AND detached_at IS NULL
                """,
                (
                    remote_instance_event_id,
                    remote_etag,
                    _dt_to_text(remote_updated_at),
                    status.value,
                    local_hash,
                    remote_hash,
                    int(cancelled),
                    _dt_to_text(stamp),
                    op.series_uid,
                    op.occurrence_key,
                    op.series_link_id,
                ),
            )
            self._connection.execute(
                """
                UPDATE external_series_occurrence_changes SET
                    resolution_status = 'resolved', resolved_at = ?,
                    resolution_kind = COALESCE(
                        resolution_kind, 'keep_planner'
                    ), resolution_error = NULL
                WHERE matched_series_uid = ? AND matched_occurrence_key = ?
                  AND resolved_at IS NULL
                """,
                (_dt_to_text(stamp), op.series_uid, op.occurrence_key),
            )
            # Queue deletion is intentionally the final mutation.
            self._connection.execute(
                "DELETE FROM pending_calendar_series_instance_ops WHERE id = ?",
                (op.id,),
            )
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise
        link = self.get_occurrence_link(op.series_uid, op.occurrence_key)
        if link is None:
            raise RuntimeError("occurrence link disappeared during finalization")
        return link

    def record_remote_conflict(
        self,
        series_uid: str,
        occurrence_key: str,
        *,
        reason: str,
        snapshot: dict[str, Any],
        remote_instance_event_id: Optional[str],
        remote_etag: Optional[str],
        remote_updated_at: Optional[datetime],
        cancelled: bool,
    ) -> OccurrenceCalendarLink:
        link = self._require_active_link(series_uid, occurrence_key)
        link.remote_instance_event_id = (
            remote_instance_event_id or link.remote_instance_event_id
        )
        link.remote_etag = remote_etag
        link.remote_updated_at = remote_updated_at
        link.is_cancelled_remote = bool(cancelled)
        link.sync_status = (
            OccurrenceSyncStatus.REMOTE_CANCELLED
            if cancelled
            else OccurrenceSyncStatus.REMOTE_CHANGED
        )
        link.conflict_reason = reason
        link.conflict_snapshot_json = _payload_json(snapshot)
        return self.update_occurrence_link(link)

    # ---- shared quarantine -------------------------------------------------

    def upsert_occurrence_change(
        self, change: RemoteOccurrenceChange
    ) -> RemoteOccurrenceChange:
        self._connection.execute(
            """
            INSERT INTO external_series_occurrence_changes (
                provider, calendar_id, remote_master_event_id,
                remote_instance_event_id, original_start_value, status,
                payload_json, remote_etag, remote_updated_at, first_seen_at,
                last_seen_at, resolved_at, matched_series_uid,
                matched_occurrence_key, resolution_status, resolution_kind,
                resolution_error
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (
                provider, calendar_id, remote_master_event_id,
                remote_instance_event_id, original_start_value
            ) DO UPDATE SET
                status = excluded.status,
                payload_json = excluded.payload_json,
                remote_etag = excluded.remote_etag,
                remote_updated_at = excluded.remote_updated_at,
                last_seen_at = excluded.last_seen_at,
                matched_series_uid = excluded.matched_series_uid,
                matched_occurrence_key = excluded.matched_occurrence_key,
                resolution_status = excluded.resolution_status,
                resolution_kind = excluded.resolution_kind,
                resolved_at = excluded.resolved_at,
                resolution_error = excluded.resolution_error
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
                change.matched_series_uid,
                change.matched_occurrence_key,
                change.resolution_status,
                change.resolution_kind,
                change.resolution_error,
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
        return _row_to_change(row)

    def get_occurrence_change(
        self, change_id: int
    ) -> Optional[RemoteOccurrenceChange]:
        row = self._connection.execute(
            "SELECT * FROM external_series_occurrence_changes WHERE id = ?",
            (int(change_id),),
        ).fetchone()
        return _row_to_change(row) if row is not None else None

    def list_occurrence_changes(
        self, *, unresolved_only: bool = True
    ) -> list[RemoteOccurrenceChange]:
        query = "SELECT * FROM external_series_occurrence_changes"
        if unresolved_only:
            query += " WHERE resolved_at IS NULL"
        rows = self._connection.execute(query + " ORDER BY id").fetchall()
        return [_row_to_change(row) for row in rows]

    def resolve_occurrence_change(
        self,
        change_id: int,
        resolution_kind: str,
        *,
        error: Optional[str] = None,
        pending: bool = False,
    ) -> bool:
        stamp = None if pending or error else self._clock()
        status = "pending" if pending else ("error" if error else "resolved")
        cursor = self._connection.execute(
            "UPDATE external_series_occurrence_changes SET "
            "resolution_status = ?, resolution_kind = ?, resolved_at = ?, "
            "resolution_error = ? WHERE id = ?",
            (
                status,
                str(resolution_kind),
                _dt_to_text(stamp),
                error,
                int(change_id),
            ),
        )
        self._connection.commit()
        return cursor.rowcount == 1

    def resolve_matching_quarantine(
        self,
        series_uid: str,
        occurrence_key: str,
        *,
        resolution_kind: str = "echo",
    ) -> int:
        stamp = self._clock()
        cursor = self._connection.execute(
            "UPDATE external_series_occurrence_changes SET "
            "resolution_status = 'resolved', resolution_kind = ?, "
            "resolved_at = ?, resolution_error = NULL "
            "WHERE matched_series_uid = ? AND matched_occurrence_key = ? "
            "AND resolved_at IS NULL",
            (
                resolution_kind,
                _dt_to_text(stamp),
                series_uid,
                occurrence_key,
            ),
        )
        self._connection.commit()
        return int(cursor.rowcount)

    def count_quarantined(self) -> int:
        row = self._connection.execute(
            "SELECT COUNT(*) AS n FROM external_series_occurrence_changes "
            "WHERE resolved_at IS NULL"
        ).fetchone()
        return int(row["n"])

    def count_resolutions_completed_after(
        self,
        after: Optional[datetime],
        kinds: tuple[str, ...],
    ) -> int:
        if not kinds:
            return 0
        placeholders = ",".join("?" for _ in kinds)
        query = (
            "SELECT COUNT(*) AS n FROM external_series_occurrence_changes "
            f"WHERE resolved_at IS NOT NULL AND resolution_kind IN ({placeholders})"
        )
        params: list[Any] = list(kinds)
        if after is not None:
            query += " AND resolved_at > ?"
            params.append(_dt_to_text(after))
        row = self._connection.execute(query, tuple(params)).fetchone()
        return int(row["n"])

    def diagnostics(self) -> dict[str, int]:
        result = {
            "occurrence_pending_update": 0,
            "occurrence_pending_cancel": 0,
            "occurrence_terminal": self.count_terminal_ops(),
            "occurrence_quarantined": self.count_quarantined(),
            "occurrence_remote_cancelled": 0,
            "occurrence_resolved_history": 0,
            "occurrence_exceptions": 0,
        }
        pending = self.count_pending_by_op()
        result["occurrence_pending_update"] = pending["update"]
        result["occurrence_pending_cancel"] = pending["cancel"]
        rows = self._connection.execute(
            "SELECT sync_status, COUNT(*) AS n "
            "FROM task_series_occurrence_calendar_links "
            "WHERE detached_at IS NULL GROUP BY sync_status"
        ).fetchall()
        for row in rows:
            if row["sync_status"] == OccurrenceSyncStatus.REMOTE_CANCELLED.value:
                result["occurrence_remote_cancelled"] = int(row["n"])
            if row["sync_status"] in (
                OccurrenceSyncStatus.SYNCED_EXCEPTION.value,
                OccurrenceSyncStatus.CANCELLED.value,
            ):
                result["occurrence_exceptions"] += int(row["n"])
        row = self._connection.execute(
            "SELECT COUNT(*) AS n FROM external_series_occurrence_changes "
            "WHERE resolved_at IS NOT NULL"
        ).fetchone()
        result["occurrence_resolved_history"] = int(row["n"])
        return result


# Compatibility aliases for tests/use cases that prefer repository wording.
OccurrenceSyncRepository = CalendarSeriesOccurrenceSyncStore
CalendarSeriesInstanceSyncStore = CalendarSeriesOccurrenceSyncStore


__all__ = [
    "CalendarSeriesInstanceSyncStore",
    "CalendarSeriesOccurrenceSyncStore",
    "OccurrenceSyncRepository",
]
