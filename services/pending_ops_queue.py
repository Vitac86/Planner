from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import timedelta
from typing import Iterable, List, Optional

from sqlmodel import select
from sqlalchemy import func

from datetime_utils import utc_now
from models.pending_op import PendingOp
from storage.db import get_session


VALID_OPS = {
    "gcal_create",
    "gcal_update",
    "gcal_delete",
    "gtasks_create",
    "gtasks_update",
    "gtasks_delete",
}


def _next_try(attempts: int) -> datetime:
    delay = min(30, 2 ** max(attempts, 0))
    return utc_now() + timedelta(seconds=delay)


@dataclass
class PendingOperation:
    id: int
    op: str
    task_id: int
    payload: dict
    attempts: int
    last_error: Optional[str]
    next_try_at: datetime


class PendingOpsQueue:
    def enqueue(self, op: str, task_id: int, payload: dict) -> None:
        if op not in VALID_OPS:
            raise ValueError(f"Unsupported op: {op}")
        record = PendingOp(
            op=op,
            task_id=task_id,
            payload=json.dumps(payload, ensure_ascii=False),
            created_at=utc_now(),
            next_try_at=utc_now(),
        )
        with get_session() as session:
            session.add(record)
            session.commit()

    def requeue(self, op_id: int, error: str) -> None:
        with get_session() as session:
            record = session.get(PendingOp, op_id)
            if not record:
                return
            record.attempts += 1
            record.last_error = error[:1000]
            record.next_try_at = _next_try(record.attempts)
            session.add(record)
            session.commit()

    def remove(self, op_id: int) -> None:
        with get_session() as session:
            record = session.get(PendingOp, op_id)
            if record:
                session.delete(record)
                session.commit()

    def due(self, limit: int = 10) -> List[PendingOperation]:
        now = utc_now()
        with get_session() as session:
            stmt = (
                select(PendingOp)
                .where(PendingOp.next_try_at <= now)
                .order_by(PendingOp.next_try_at.asc())
                .limit(limit)
            )
            rows = list(session.exec(stmt))

        result: List[PendingOperation] = []
        for row in rows:
            try:
                payload = json.loads(row.payload)
            except json.JSONDecodeError:
                payload = {}
            result.append(
                PendingOperation(
                    id=row.id,
                    op=row.op,
                    task_id=row.task_id,
                    payload=payload,
                    attempts=row.attempts,
                    last_error=row.last_error,
                    next_try_at=row.next_try_at,
                )
            )
        return result

    def count(self) -> int:
        with get_session() as session:
            return int(session.exec(select(func.count()).select_from(PendingOp)).one())


__all__ = ["PendingOpsQueue", "PendingOperation"]
