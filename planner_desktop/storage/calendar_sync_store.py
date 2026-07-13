"""Локальная очередь Calendar-операций и состояние синка нового десктопа.

Живёт в той же изолированной БД, что и задачи
(``PlannerDesktop/app_desktop.db``); старый ``Planner/app.db`` не
открывается никогда. Никакой сети и Google API здесь нет — только SQLite.

Правила безопасности очереди:

- у terminal-операций (dead-letter) нет бесконечных ретраев: после
  ``MAX_ATTEMPTS`` временных ошибок или первой постоянной ошибки операция
  помечается terminal и в push больше не выбирается;
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
        """Update не ставится, если уже ждёт create/update той же задачи:
        push всегда читает АКТУАЛЬНОЕ состояние задачи из репозитория,
        поэтому одной отложенной операции достаточно."""
        if self._has_pending(task_uid, OpKind.CREATE):
            return
        self._enqueue(OpKind.UPDATE, task_uid, payload)

    def enqueue_delete(self, task_uid: str,
                       payload: Optional[Dict[str, Any]] = None) -> None:
        """Delete вытесняет отложенные create/update той же задачи:
        пушить правки события, которое сейчас будет удалено, бессмысленно."""
        self._connection.execute(
            "DELETE FROM desktop_pending_calendar_ops "
            "WHERE task_uid = ? AND status = ? AND op IN (?, ?)",
            (task_uid, OpStatus.PENDING.value,
             OpKind.CREATE.value, OpKind.UPDATE.value),
        )
        self._enqueue(OpKind.DELETE, task_uid, payload)

    def cancel_pending_ops(self, task_uid: str) -> None:
        """Снять все отложенные операции задачи (удалена до первого push-а)."""
        self._connection.execute(
            "DELETE FROM desktop_pending_calendar_ops "
            "WHERE task_uid = ? AND status = ?",
            (task_uid, OpStatus.PENDING.value),
        )
        self._connection.commit()

    def _enqueue(self, op: OpKind, task_uid: str,
                 payload: Optional[Dict[str, Any]]) -> None:
        if self._has_pending(task_uid, op):
            return  # дедупликация: одинаковая операция уже ждёт push-а
        now = self._clock()
        self._connection.execute(
            """
            INSERT INTO desktop_pending_calendar_ops
                (op, task_uid, payload_json, attempts, status,
                 created_at, next_try_at)
            VALUES (?, ?, ?, 0, ?, ?, ?)
            """,
            (
                op.value,
                task_uid,
                json.dumps(payload, ensure_ascii=False) if payload else None,
                OpStatus.PENDING.value,
                _dt_to_text(now),
                _dt_to_text(now),
            ),
        )
        self._connection.commit()

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

    def requeue_op(self, op_id: int, error: str) -> None:
        """Временная ошибка: attempts+1 и бэкофф; после MAX_ATTEMPTS — terminal."""
        row = self._connection.execute(
            "SELECT attempts FROM desktop_pending_calendar_ops WHERE id = ?",
            (op_id,),
        ).fetchone()
        if row is None:
            return
        attempts = row["attempts"] + 1
        if attempts >= MAX_ATTEMPTS:
            self.mark_terminal(op_id, error, attempts=attempts)
            return
        delay = min(
            RETRY_BASE_DELAY_SECONDS * 2 ** (attempts - 1),
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
