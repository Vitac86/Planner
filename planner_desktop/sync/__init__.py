"""Calendar-синхронизация нового десктопа.

Ядро (движок, маппер, типы, фейковый шлюз) реализовано и покрыто
тестами; реального Google-шлюза (сеть/OAuth) в пакете по-прежнему нет.
"""

from .calendar_contract import (
    CalendarEventMapper,
    CalendarGateway,
    CalendarSyncGateway,
    RemoteEventChange,
)
from .calendar_sync_engine import CalendarSyncEngine
from .fake_calendar_gateway import FakeCalendarGateway
from .sync_types import (
    CalendarEvent,
    CalendarGatewayError,
    OpKind,
    OpStatus,
    PendingOp,
    RemoteChangeBatch,
    RetryableGatewayError,
    TerminalGatewayError,
)

__all__ = [
    "CalendarEvent",
    "CalendarEventMapper",
    "CalendarGateway",
    "CalendarGatewayError",
    "CalendarSyncEngine",
    "CalendarSyncGateway",
    "FakeCalendarGateway",
    "OpKind",
    "OpStatus",
    "PendingOp",
    "RemoteChangeBatch",
    "RemoteEventChange",
    "RetryableGatewayError",
    "TerminalGatewayError",
]
