"""Контракты будущей синхронизации нового десктопа. Реализаций пока нет."""

from .calendar_contract import (
    CalendarEventMapper,
    CalendarSyncGateway,
    RemoteEventChange,
)

__all__ = ["CalendarSyncGateway", "CalendarEventMapper", "RemoteEventChange"]
