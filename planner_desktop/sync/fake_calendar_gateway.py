"""Фейковый in-memory шлюз календаря для тестов и разработки.

Реализует контракт CalendarGateway без сети, без OAuth и без импорта
Google-клиентов. Симулирует ровно то поведение Calendar API, на которое
опирается движок синхронизации:

- create/update/delete с etag-ами и updated_at;
- журнал изменений с курсором (аналог syncToken): list_changes(cursor)
  отдаёт события, изменившиеся после курсора, включая правки,
  «сделанные на телефоне» (в тестах — прямые вызовы insert/patch/delete);
- delete = status "cancelled" (событие остаётся в журнале, как у Google);
- all-day события (start/end — date) и события со временем (datetime);
- метаданные экземпляра повторяющегося события (recurring_event_id,
  original_start) и фирменный отказ Google: слепой патч start/end
  такого экземпляра поднимает TerminalGatewayError (аналог 400);
- инъекция ошибок для тестов ретраев/dead-letter: fail_next(error).

Часы детерминированные: base_time + секунда за каждое изменение,
поэтому updated_at событий строго монотонен и сравним между тестами.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any, Dict, List, Mapping, Optional

from planner_desktop.domain.task import utc_now
from planner_desktop.sync.sync_types import (
    EVENT_STATUS_CANCELLED,
    CalendarEvent,
    RemoteChangeBatch,
    TerminalGatewayError,
)

_TIME_FIELDS = ("start", "end", "is_all_day")
_PATCHABLE_FIELDS = ("summary", "description", "start", "end", "is_all_day")


class FakeCalendarGateway:
    """Календарь в памяти процесса. Никакой сети, никаких Google-импортов."""

    def __init__(self, base_time: Optional[datetime] = None,
                 calendar_id: str = "primary") -> None:
        self._events: Dict[str, CalendarEvent] = {}
        self._change_log: List[str] = []  # id событий в порядке изменений
        self._next_id = 1
        self._base_time = base_time or utc_now()
        self._ticks = 0
        self._pending_errors: List[Exception] = []
        self._calendar_id = calendar_id
        self.list_call_count = 0
        self.write_call_count = 0

    @property
    def calendar_id(self) -> str:
        return self._calendar_id

    def reset_call_counts(self) -> None:
        self.list_call_count = 0
        self.write_call_count = 0

    # ---- управление фейком из тестов ------------------------------------------

    def fail_next(self, error: Exception) -> None:
        """Следующий мутирующий вызов (insert/patch/delete) поднимет error."""
        self._pending_errors.append(error)

    def get_event(self, event_id: str) -> Optional[CalendarEvent]:
        """Прямой доступ к «удалённому» состоянию для ассертов в тестах."""
        event = self._events.get(event_id)
        return replace(event) if event is not None else None

    @property
    def events(self) -> List[CalendarEvent]:
        return [replace(e) for e in self._events.values()]

    # ---- внутреннее -------------------------------------------------------------

    def _tick(self) -> datetime:
        self._ticks += 1
        return self._base_time + timedelta(seconds=self._ticks)

    def _maybe_fail(self) -> None:
        if self._pending_errors:
            raise self._pending_errors.pop(0)

    def _bump_etag(self, event: CalendarEvent) -> None:
        revision = int((event.etag or '"0"').strip('"')) + 1
        event.etag = f'"{revision}"'

    def _record_change(self, event: CalendarEvent) -> None:
        self._change_log.append(event.id)

    # ---- контракт CalendarGateway ------------------------------------------------

    def insert_event(self, event: CalendarEvent) -> CalendarEvent:
        self.write_call_count += 1
        self._maybe_fail()
        stored = replace(event)
        stored.id = f"evt-{self._next_id}"
        self._next_id += 1
        stored.etag = '"1"'
        stored.updated_at = self._tick()
        self._events[stored.id] = stored
        self._record_change(stored)
        return replace(stored)

    def patch_event(self, event_id: str, patch: Mapping[str, Any]) -> CalendarEvent:
        self.write_call_count += 1
        self._maybe_fail()
        event = self._events.get(event_id)
        if event is None:
            raise TerminalGatewayError(f"Событие {event_id} не существует (404).")
        if event.is_cancelled:
            raise TerminalGatewayError(f"Событие {event_id} отменено (410).")
        unknown = set(patch) - set(_PATCHABLE_FIELDS)
        if unknown:
            raise TerminalGatewayError(f"Непатчабельные поля: {sorted(unknown)} (400).")
        if event.is_recurring_instance and any(f in patch for f in _TIME_FIELDS):
            # Так отвечает Google на слепой перенос экземпляра серии.
            raise TerminalGatewayError(
                "Слепой патч start/end экземпляра повторяющегося события "
                "запрещён (400): используйте recurringEventId + originalStartTime."
            )
        for name, value in patch.items():
            setattr(event, name, value)
        self._bump_etag(event)
        event.updated_at = self._tick()
        self._record_change(event)
        return replace(event)

    def delete_event(self, event_id: str) -> None:
        self.write_call_count += 1
        self._maybe_fail()
        event = self._events.get(event_id)
        if event is None or event.is_cancelled:
            return  # идемпотентность: уже отсутствует/отменено — не ошибка
        event.status = EVENT_STATUS_CANCELLED
        self._bump_etag(event)
        event.updated_at = self._tick()
        self._record_change(event)

    def list_changes(self, cursor: Optional[str]) -> RemoteChangeBatch:
        self.list_call_count += 1
        start_index = int(cursor) if cursor else 0
        changed_ids: List[str] = []
        for event_id in self._change_log[start_index:]:
            if event_id in changed_ids:
                changed_ids.remove(event_id)
            changed_ids.append(event_id)  # каждое событие один раз, свежим
        return RemoteChangeBatch(
            events=[replace(self._events[eid]) for eid in changed_ids],
            next_cursor=str(len(self._change_log)),
        )
