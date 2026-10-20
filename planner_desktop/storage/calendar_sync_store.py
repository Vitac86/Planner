"""Локальная очередь Calendar-операций и состояние синка нового десктопа.

Живёт в той же изолированной БД, что и задачи
(``PlannerDesktop/app_desktop.db``); старый ``Planner/app.db`` не
открывается никогда. Никакой сети и Google API здесь нет — только SQLite.

Правила безопасности очереди:

- у terminal-операций (dead-letter) нет бесконечных ретраев: после
  ``MAX_ATTEMPTS`` временных ошибок или первой постоянной ошибки операция
  помечается terminal и в push больше не выбирается; отсутствие сети
  откладывает попытку, не расходуя этот лимит;
- строки операций удаляются только после успешного push-а или явной
  отмены (задача удалена до первого push-а); локальные задачи очередь
  не трогает вовсе — тумбстоунами занимается репозиторий.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Union

from planner_desktop.domain.task import utc_now
from planner_desktop.storage.paths import ensure_desktop_data_dir, get_desktop_db_path
from planner_desktop.storage.schema import create_schema
from planner_desktop.sync.sync_types import OpKind, OpStatus, PendingOp

MAX_ATTEMPTS = 5
RETRY_BASE_DELAY_SECONDS = 30
RETRY_MAX_DELAY_SECONDS = 3600

SYNC_CURSOR_KEY = "calendar_sync_cursor"


def _dt_to_text(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value is not None else None


def _text_to_dt(value: Optional[str]) -> Optional[datetime]:
    return datetime.fromisoformat(value) if value else None


def _row_to_op(row: sqlite3.Row) -> PendingOp:
    return PendingOp(
        id=row["id"],
        op=row["op"],
        task_uid=row["task_uid"],
        payload_json=row["payload_json"],
        attempts=row["attempts"],
        last_error=row["last_error"],
        status=row["status"],
        created_at=_text_to_dt(row["created_at"]),
        next_try_at=_text_to_dt(row["next_try_at"]),
    )


class CalendarSyncStore:
    """Очередь push-операций + ключ-значение состояния синка.

    ``clock`` подменяется в тестах, чтобы детерминированно проверять
    бэкофф и просроченность next_try_at.
    """

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
        create_schema(self._connection)

    def close(self) -> None:
        self._connection.close()

    # ---- постановка операций в очередь ---------------------------------------

    def enqueue_create(self, task_uid: str,
                       payload: Optional[Dict[str, Any]] = None) -> None:
        self._enqueue(OpKind.CREATE, task_uid, payload)

    def enqueue_update(self, task_uid: str,
                       payload: Optional[Dict[str, Any]] = None) -> None:
        """Объединить правки, сохранив новое поколение ожидающей операции."""
        self._enqueue(OpKind.UPDATE, task_uid, payload)

    def enqueue_delete(self, task_uid: str,
                       payload: Optional[Dict[str, Any]] = None) -> None:
        """Delete вытесняет отложенные create/update той же задачи:
        пушить правки события, которое сейчас будет удалено, бессмысленно."""
        self._enqueue(OpKind.DELETE, task_uid, payload, supersede_updates=True)

    def enqueue_event_cleanup(self, task_uid: str, event_id: str) -> None:
        """Удалить результат запоздалого insert, сохранив новые правки задачи."""
        self._enqueue(OpKind.DELETE, task_uid, {"event_id": event_id, "cleanup": True})

    def cancel_pending_ops(self, task_uid: str) -> None:
        """Cancel local writes; retain cleanup of previously detached events."""
        self._connection.execute(
            "DELETE FROM desktop_pending_calendar_ops "
            "WHERE task_uid = ? AND op IN (?, ?)",
            (task_uid, OpKind.CREATE.value, OpKind.UPDATE.value),
        )
        self._connection.commit()

    def snapshot_task_ops(self, task_uid: str) -> List[Dict[str, Any]]:
        """Capture this task's queue rows for a short compensating transaction.

        Calendar interactions update the task repository and this queue through
        separate SQLite connections.  The snapshot lets the service restore the
        logical queue state if the second write fails; it never performs network IO.
        """
        rows = self._connection.execute(
            "SELECT * FROM desktop_pending_calendar_ops WHERE task_uid = ? "
            "ORDER BY id",
            (task_uid,),
        ).fetchall()
        return [dict(row) for row in rows]

    def restore_task_ops(
        self, task_uid: str, snapshot: List[Dict[str, Any]]
    ) -> None:
        """Restore logical operations as fresh generations, retaining cleanup.

        A worker may still hold any snapshotted id. Reusing it would allow a
        late acknowledgement to remove the compensating write we need now.
        """
        with self._connection:
            self._connection.execute("BEGIN IMMEDIATE")
            cleanup = [
                row for row in self.snapshot_task_ops(task_uid)
                if row["op"] == OpKind.DELETE.value and row["payload_json"]
                and json.loads(row["payload_json"]).get("cleanup")
                and not any(saved["payload_json"] == row["payload_json"] for saved in snapshot)
            ]
            self._connection.execute(
                "DELETE FROM desktop_pending_calendar_ops WHERE task_uid = ?",
                (task_uid,),
            )
            for row in [*snapshot, *cleanup]:
                self._connection.execute(
                    """
                    INSERT INTO desktop_pending_calendar_ops (
                        op, task_uid, payload_json, attempts, last_error,
                        status, created_at, next_try_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row["op"], row["task_uid"],
                        row["payload_json"], row["attempts"], row["last_error"],
                        row["status"], row["created_at"], row["next_try_at"],
                    ),
                )

    def _enqueue(self, op: OpKind, task_uid: str,
                 payload: Optional[Dict[str, Any]], *,
                 supersede_updates: bool = False) -> None:
        # A fresh id is the operation's generation. An in-flight worker may
        # acknowledge/retry only its old id, never a newer local edit. Acquire
        # the write lock before deciding whether an update belongs to a create.
        with self._connection:
            self._connection.execute("BEGIN IMMEDIATE")
            if op == OpKind.UPDATE and self._has_pending(task_uid, OpKind.CREATE):
                op = OpKind.CREATE
            if supersede_updates:
                self._connection.execute(
                    "DELETE FROM desktop_pending_calendar_ops "
                    "WHERE task_uid = ? AND op IN (?, ?)",
                    (task_uid, OpKind.CREATE.value, OpKind.UPDATE.value),
                )
            payload_json = json.dumps(payload, ensure_ascii=False) if payload else None
            # Different delete targets must survive unschedule -> reschedule.
            self._connection.execute(
                "DELETE FROM desktop_pending_calendar_ops "
                "WHERE task_uid = ? AND op = ? "
                "AND (? != 'delete' OR payload_json IS ?)",
                (task_uid, op.value, op.value, payload_json),
            )
            now = _dt_to_text(self._clock())
            self._connection.execute(
                """
                INSERT INTO desktop_pending_calendar_ops
                    (op, task_uid, payload_json, attempts, status,
                     created_at, next_try_at)
                VALUES (?, ?, ?, 0, ?, ?, ?)
                """,
                (op.value, task_uid, payload_json, OpStatus.PENDING.value, now, now),
            )

    def is_pending_op(self, op_id: int) -> bool:
        """Whether a fetched operation is still the current pending generation."""
        return self._connection.execute(
            "SELECT 1 FROM desktop_pending_calendar_ops WHERE id = ? AND status = ?",
            (op_id, OpStatus.PENDING.value),
        ).fetchone() is not None

    def has_event_delete(self, event_id: str) -> bool:
        """Do not import an event whose detached local copy awaits deletion."""
        rows = self._connection.execute(
            "SELECT payload_json FROM desktop_pending_calendar_ops WHERE op = ?",
            (OpKind.DELETE.value,),
        ).fetchall()
        return any(
            row["payload_json"] and json.loads(row["payload_json"]).get("event_id") == event_id
            for row in rows
        )

    def _has_pending(self, task_uid: str, op: OpKind) -> bool:
        row = self._connection.execute(
            "SELECT 1 FROM desktop_pending_calendar_ops "
            "WHERE task_uid = ? AND op = ? AND status = ? LIMIT 1",
            (task_uid, op.value, OpStatus.PENDING.value),
        ).fetchone()
        return row is not None

    # ---- выборка и завершение операций ----------------------------------------

    def list_due_ops(self, limit: int = 50) -> List[PendingOp]:
        """Pending-операции, чей next_try_at уже наступил, в порядке создания."""
        rows = self._connection.execute(
            "SELECT * FROM desktop_pending_calendar_ops "
            "WHERE status = ? AND next_try_at <= ? "
            "ORDER BY id LIMIT ?",
            (OpStatus.PENDING.value, _dt_to_text(self._clock()), limit),
        ).fetchall()
        return [_row_to_op(row) for row in rows]

    def has_pending_op(self, task_uid: str) -> bool:
        """Есть ли у задачи хоть одна pending-операция (задача «грязная»)."""
        row = self._connection.execute(
            "SELECT 1 FROM desktop_pending_calendar_ops "
            "WHERE task_uid = ? AND status = ? LIMIT 1",
            (task_uid, OpStatus.PENDING.value),
        ).fetchone()
        return row is not None

    def has_unsynced_op(self, task_uid: str) -> bool:
        """Failed local writes also protect user data from a subsequent pull."""
        return self._connection.execute(
            "SELECT 1 FROM desktop_pending_calendar_ops WHERE task_uid = ? LIMIT 1",
            (task_uid,),
        ).fetchone() is not None

    def count_pending_ops(self) -> int:
        """Сколько операций ждёт push-а (для статистики в UI/настройках)."""
        row = self._connection.execute(
            "SELECT COUNT(*) AS n FROM desktop_pending_calendar_ops WHERE status = ?",
            (OpStatus.PENDING.value,),
        ).fetchone()
        return int(row["n"])

    def count_terminal_ops(self) -> int:
        """Сколько операций в dead-letter (для статистики в UI/настройках)."""
        row = self._connection.execute(
            "SELECT COUNT(*) AS n FROM desktop_pending_calendar_ops WHERE status = ?",
            (OpStatus.TERMINAL.value,),
        ).fetchone()
        return int(row["n"])

    def count_pending_by_op(self) -> Dict[str, int]:
        """Разбивка ожидающих операций по типу: {'create': n, 'update': n,
        'delete': n} — для наглядного статуса синка в «Настройках»."""
        counts = {kind.value: 0 for kind in OpKind}
        rows = self._connection.execute(
            "SELECT op, COUNT(*) AS n FROM desktop_pending_calendar_ops "
            "WHERE status = ? GROUP BY op",
            (OpStatus.PENDING.value,),
        ).fetchall()
        for row in rows:
            counts[row["op"]] = int(row["n"])
        return counts

    def latest_pending_created_at(self) -> Optional[datetime]:
        """Время самой свежей ожидающей операции — «последнее локальное
        изменение, ждущее синка». None, если очередь пуста."""
        row = self._connection.execute(
            "SELECT MAX(created_at) AS ts FROM desktop_pending_calendar_ops "
            "WHERE status = ?",
            (OpStatus.PENDING.value,),
        ).fetchone()
        return _text_to_dt(row["ts"]) if row is not None else None

    def list_pending_uids(self) -> set:
        """uid-ы задач с pending-операциями — для бейджей «Синк…» в списках."""
        rows = self._connection.execute(
            "SELECT DISTINCT task_uid FROM desktop_pending_calendar_ops "
            "WHERE status = ?",
            (OpStatus.PENDING.value,),
        ).fetchall()
        return {row["task_uid"] for row in rows}

    def remove_op(self, op_id: int) -> None:
        """Успешный push: операция выполнена и больше не нужна."""
        self._connection.execute(
            "DELETE FROM desktop_pending_calendar_ops WHERE id = ?", (op_id,)
        )
        self._connection.commit()

    def requeue_op(self, op_id: int, error: str, *,
                   consume_attempt: bool = True) -> None:
        """Bounded retries; transport outages defer without consuming attempts."""
        row = self._connection.execute(
            "SELECT attempts FROM desktop_pending_calendar_ops WHERE id = ?",
            (op_id,),
        ).fetchone()
        if row is None:
            return
        attempts = row["attempts"] + int(consume_attempt)
        if consume_attempt and attempts >= MAX_ATTEMPTS:
            self.mark_terminal(op_id, error, attempts=attempts)
            return
        delay = min(
            RETRY_BASE_DELAY_SECONDS * 2 ** max(0, attempts - 1),
            RETRY_MAX_DELAY_SECONDS,
        )
        next_try = self._clock() + timedelta(seconds=delay)
        self._connection.execute(
            "UPDATE desktop_pending_calendar_ops "
            "SET attempts = ?, last_error = ?, next_try_at = ? WHERE id = ?",
            (attempts, error, _dt_to_text(next_try), op_id),
        )
        self._connection.commit()

    def mark_terminal(self, op_id: int, error: str,
                      attempts: Optional[int] = None) -> None:
        """Постоянная ошибка: операция уходит в dead-letter, ретраев больше нет."""
        if attempts is None:
            self._connection.execute(
                "UPDATE desktop_pending_calendar_ops "
                "SET status = ?, last_error = ? WHERE id = ?",
                (OpStatus.TERMINAL.value, error, op_id),
            )
        else:
            self._connection.execute(
                "UPDATE desktop_pending_calendar_ops "
                "SET status = ?, last_error = ?, attempts = ? WHERE id = ?",
                (OpStatus.TERMINAL.value, error, attempts, op_id),
            )
        self._connection.commit()

    def list_terminal_ops(self) -> List[PendingOp]:
        """Dead-letter: постоянные ошибки для разбора человеком."""
        rows = self._connection.execute(
            "SELECT * FROM desktop_pending_calendar_ops "
            "WHERE status = ? ORDER BY id",
            (OpStatus.TERMINAL.value,),
        ).fetchall()
        return [_row_to_op(row) for row in rows]

    def list_failed_ops(self) -> List[PendingOp]:
        """Current failures, including pending operations awaiting auto retry."""
        rows = self._connection.execute(
            "SELECT * FROM desktop_pending_calendar_ops "
            "WHERE status = ? OR COALESCE(last_error, '') != '' ORDER BY id",
            (OpStatus.TERMINAL.value,),
        ).fetchall()
        return [_row_to_op(row) for row in rows]

    def terminal_retry_block_reason(self, op_id: int) -> str:
        """Explain why a stopped operation cannot safely be retried locally.

        This is only a UI preview. retry_terminal_op repeats every check while
        holding the SQLite write lock, including the exact operation generation.
        """
        row = self._connection.execute(
            "SELECT * FROM desktop_pending_calendar_ops WHERE id = ?", (op_id,),
        ).fetchone()
        return self._terminal_retry_block_reason(row)

    def _terminal_retry_block_reason(self, row: Optional[sqlite3.Row]) -> str:
        if row is None or row["status"] != OpStatus.TERMINAL.value:
            return "Операция уже изменилась. Список обновлён."
        task = self._connection.execute(
            "SELECT * FROM tasks WHERE uid = ?", (row["task_uid"],),
        ).fetchone()
        if row["op"] == OpKind.DELETE.value:
            try:
                payload = json.loads(row["payload_json"] or "{}")
                event_id = payload.get("event_id") if isinstance(payload, dict) else None
            except (TypeError, ValueError):
                event_id = None
            if not isinstance(event_id, str) or not event_id.strip():
                return ("Не удалось однозначно определить удаляемое событие. "
                        "Повтор заблокирован, чтобы не удалить другое событие.")
            owner = self._connection.execute(
                "SELECT 1 FROM tasks WHERE google_calendar_event_id = ? "
                "AND deleted_at IS NULL AND start IS NOT NULL LIMIT 1",
                (event_id,),
            ).fetchone()
            if owner is not None:
                return ("Это событие снова связано с запланированной задачей. "
                        "Повтор удаления заблокирован.")
            # Distinct detached targets are independent and must be retained.
            newer = self._connection.execute(
                "SELECT payload_json FROM desktop_pending_calendar_ops "
                "WHERE task_uid = ? AND op = ? AND id > ?",
                (row["task_uid"], OpKind.DELETE.value, row["id"]),
            ).fetchall()
            for candidate in newer:
                try:
                    other = json.loads(candidate["payload_json"] or "{}")
                except (TypeError, ValueError):
                    continue
                if isinstance(other, dict) and other.get("event_id") == event_id:
                    return "Удаление уже заменено более новой операцией."
            return ""
        if task is None:
            return "Локальная задача не найдена. Повтор отправки недоступен."
        if task["deleted_at"] is not None:
            return "Задача удалена. Устаревшее создание или обновление не отправляется."
        if task["start"] is None:
            return "У задачи больше нет даты; она остаётся на компьютере."
        if task["series_uid"] is not None:
            return "Этот экземпляр управляется через настройки повторяющейся серии."
        newer = self._connection.execute(
            "SELECT 1 FROM desktop_pending_calendar_ops "
            "WHERE task_uid = ? AND id > ? LIMIT 1",
            (row["task_uid"], row["id"]),
        ).fetchone()
        if newer is not None:
            return "У задачи есть более новые изменения. Старая отправка не повторяется."
        has_event = bool(task["google_calendar_event_id"])
        if (row["op"] == OpKind.CREATE.value and has_event
                or row["op"] == OpKind.UPDATE.value and not has_event):
            return "Связь задачи с Google изменилась. Старая отправка не повторяется."
        return ""

    def retry_terminal_op(
        self, op_id: int, *, expected_last_error: Optional[str] = None,
    ) -> int:
        """Retry one valid generation as a fresh id; never discard other work.

        Task state and queue are checked in one transaction. Explicit delete
        targets survive rescheduling and missing local tasks, but cannot delete
        an event currently owned by a live scheduled task.
        """
        with self._connection:
            self._connection.execute("BEGIN IMMEDIATE")
            row = self._connection.execute(
                "SELECT * FROM desktop_pending_calendar_ops WHERE id = ?", (op_id,),
            ).fetchone()
            reason = self._terminal_retry_block_reason(row)
            if reason:
                raise ValueError(reason)
            if (expected_last_error is not None
                    and (row["last_error"] or "") != expected_last_error):
                raise ValueError("Причина ошибки изменилась. Проверьте обновлённый список.")
            now = _dt_to_text(self._clock())
            cursor = self._connection.execute(
                "INSERT INTO desktop_pending_calendar_ops "
                "(op, task_uid, payload_json, attempts, last_error, status, "
                "created_at, next_try_at) VALUES (?, ?, ?, 0, NULL, ?, ?, ?)",
                (row["op"], row["task_uid"], row["payload_json"],
                 OpStatus.PENDING.value, now, now),
            )
            self._connection.execute(
                "DELETE FROM desktop_pending_calendar_ops WHERE id = ?", (op_id,),
            )
            return int(cursor.lastrowid)

    # ---- состояние синка (курсор pull-а и т.п.) --------------------------------

    def get_state(self, key: str) -> Optional[str]:
        row = self._connection.execute(
            "SELECT value FROM desktop_sync_state WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row is not None else None

    def set_state(self, key: str, value: Optional[str]) -> None:
        self._connection.execute(
            """
            INSERT INTO desktop_sync_state (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = excluded.updated_at
            """,
            (key, value, _dt_to_text(self._clock())),
        )
        self._connection.commit()

    def get_sync_cursor(self) -> Optional[str]:
        return self.get_state(SYNC_CURSOR_KEY)

    def set_sync_cursor(self, cursor: str) -> None:
        self.set_state(SYNC_CURSOR_KEY, cursor)
