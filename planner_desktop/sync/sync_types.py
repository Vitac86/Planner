"""Общие типы Calendar-синхронизации нового десктопа.

Здесь нет ни Google-клиентов, ни сети: CalendarEvent — собственная
модель события, «мост» между фейковым шлюзом сейчас и реальным Google
Calendar позже. Реальный шлюз будет конвертировать её в тело
Calendar API v3 и обратно, но движок и маппер об этом не узнают.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import List, Optional, Tuple, Union

# Статусы события — как в Calendar API: cancelled означает удаление
# (в том числе сделанное на телефоне в приложении Google Calendar).
EVENT_STATUS_CONFIRMED = "confirmed"
EVENT_STATUS_CANCELLED = "cancelled"


class OpKind(str, Enum):
    """Вид отложенной операции в локальной очереди push-а."""

    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"


class OpStatus(str, Enum):
    """Статус операции: pending — ждёт push-а, terminal — dead-letter."""

    PENDING = "pending"
    TERMINAL = "terminal"


@dataclass
class CalendarEvent:
    """Событие календаря, независимое от Google-клиентов.

    Семантика start/end повторяет Calendar API:

    - событие со временем: start/end — ``datetime`` (dateTime/dateTime);
    - all-day событие: start/end — ``date`` (date/date), конец —
      ЭКСКЛЮЗИВНАЯ дата (событие на 5 июня: start=05.06, end=06.06);
    - формы не смешиваются: у одного события либо оба datetime,
      либо оба date.

    recurring_event_id + original_start заполняются у экземпляра
    повторяющегося события; такому экземпляру нельзя слепо патчить
    start/end (правило 3 контракта).
    """

    id: Optional[str] = None
    etag: Optional[str] = None
    summary: str = ""
    description: str = ""
    start: Optional[Union[date, datetime]] = None
    end: Optional[Union[date, datetime]] = None
    is_all_day: bool = False
    status: str = EVENT_STATUS_CONFIRMED
    updated_at: Optional[datetime] = None
    recurring_event_id: Optional[str] = None
    original_start: Optional[datetime] = None
    recurrence_lines: Tuple[str, ...] = field(default_factory=tuple)
    start_timezone: Optional[str] = None
    end_timezone: Optional[str] = None
    # Provider wall-clock DTSTART retained for lossless UTC UNTIL analysis.
    # ``start`` keeps the long-standing local-naive Task mapping semantics.
    recurrence_start: Optional[Union[date, datetime]] = None

    @property
    def is_cancelled(self) -> bool:
        return self.status == EVENT_STATUS_CANCELLED

    @property
    def is_recurring_instance(self) -> bool:
        return self.recurring_event_id is not None

    @property
    def is_recurring_master(self) -> bool:
        # An instance wins classification even if a provider returns extra
        # recurrence metadata on it.
        return bool(self.recurrence_lines) and self.recurring_event_id is None

    @property
    def is_ordinary_event(self) -> bool:
        return not self.is_recurring_master and not self.is_recurring_instance


@dataclass
class CalendarPullStats:
    """Additive classification counts for one explicit pull."""

    total_events: int = 0
    ordinary_events: int = 0
    recurring_masters: int = 0
    recurring_instances: int = 0
    unsupported_masters: int = 0
    cancelled_masters: int = 0


@dataclass
class RemoteChangeBatch:
    """Результат pull-а: изменившиеся события + курсор для следующего pull-а.

    Аналог nextSyncToken из Calendar API; курсор хранится в
    desktop_sync_state и передаётся в следующий list_changes().
    """

    events: List[CalendarEvent] = field(default_factory=list)
    next_cursor: str = ""


@dataclass
class PendingOp:
    """Строка очереди desktop_pending_calendar_ops."""

    id: int
    op: str  # значение OpKind
    task_uid: str
    payload_json: Optional[str] = None
    attempts: int = 0
    last_error: Optional[str] = None
    status: str = OpStatus.PENDING.value
    created_at: Optional[datetime] = None
    next_try_at: Optional[datetime] = None


class CalendarGatewayError(Exception):
    """Базовая ошибка шлюза календаря."""


class RetryableGatewayError(CalendarGatewayError):
    """Временная ошибка (аналог сети/5xx/429): операцию можно повторить.

    Очередь перепланирует операцию с бэкоффом; после MAX_ATTEMPTS
    попыток операция уходит в terminal — бесконечных ретраев нет.
    """


class TerminalGatewayError(CalendarGatewayError):
    """Постоянная ошибка (аналог 400/410): повтор бессмысленен.

    Операция сразу помечается terminal (dead-letter) и больше
    не выбирается в push.
    """
