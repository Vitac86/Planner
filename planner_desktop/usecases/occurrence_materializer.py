"""Единый координатор материализации экземпляров серий (Phase 3.2A).

Страницы не генерируют экземпляры сами: Today/Calendar просят
материализатор обеспечить видимый диапазон. Правила:

- запрошенный диапазон расширяется документированным буфером
  (MATERIALIZATION_BUFFER_DAYS дней в обе стороны);
- одинаковые/вложенные запросы дедуплицируются кэшем покрытого диапазона,
  который сбрасывается при любом изменении серий;
- повторный вход (запрос во время запроса) возвращает нулевой результат;
- никакой сети/Google; никаких фоновых таймеров;
- «История» материализатор не вызывает вовсе;
- жёсткие пределы генерации — в domain/recurrence.py.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from threading import RLock
from typing import Optional

from planner_desktop.usecases.recurrence_service import (
    EnsureResult,
    RecurrenceService,
)

#: Буфер вокруг запрошенного видимого диапазона (дней в каждую сторону).
MATERIALIZATION_BUFFER_DAYS = 14
#: Максимальная ширина одного запроса (защита UI от гигантских диапазонов).
MAX_RANGE_DAYS = 400


@dataclass
class MaterializationResult:
    created: int = 0
    existing: int = 0
    skipped: int = 0
    #: Запрос отклонён (повторный вход или пустой диапазон).
    rejected: bool = False

    @classmethod
    def from_ensure(cls, ensure: EnsureResult) -> "MaterializationResult":
        return cls(
            created=ensure.created,
            existing=ensure.existing,
            skipped=ensure.skipped,
        )


class OccurrenceMaterializer:
    """Обеспечивает материализацию диапазона дат ровно один раз."""

    def __init__(
        self,
        recurrence_service: RecurrenceService,
        *,
        buffer_days: int = MATERIALIZATION_BUFFER_DAYS,
    ) -> None:
        self._service = recurrence_service
        self._buffer = timedelta(days=max(0, int(buffer_days)))
        self._covered_start: Optional[date] = None
        self._covered_end: Optional[date] = None
        self._running = False
        self._lock = RLock()
        recurrence_service.add_change_listener(self.invalidate)

    @property
    def recurrence_service(self) -> RecurrenceService:
        return self._service

    def invalidate(self) -> None:
        """Серии изменились: покрытый диапазон больше не гарантирован."""
        with self._lock:
            self._covered_start = None
            self._covered_end = None

    @property
    def covered_start(self) -> Optional[date]:
        with self._lock:
            return self._covered_start

    @property
    def covered_end(self) -> Optional[date]:
        with self._lock:
            return self._covered_end

    def ensure_range(
        self, range_start: date, range_end: date
    ) -> MaterializationResult:
        """Материализовать [range_start, range_end] + буфер. Идемпотентно."""
        with self._lock:
            if range_end < range_start:
                return MaterializationResult(rejected=True)
            if self._running:
                # Re-entrant refresh from a mutation signal is rejected. Other
                # threads wait for this lock and then reuse the covered range.
                return MaterializationResult(rejected=True)

            start = range_start - self._buffer
            end = range_end + self._buffer
            if (end - start).days > MAX_RANGE_DAYS:
                end = start + timedelta(days=MAX_RANGE_DAYS)
            if (
                self._covered_start is not None
                and self._covered_end is not None
                and start >= self._covered_start
                and end <= self._covered_end
            ):
                return MaterializationResult()  # диапазон уже обеспечен

            self._running = True
            try:
                ensure = self._service.ensure_occurrences(start, end)
            finally:
                self._running = False
            # ensure с created>0 уведомил слушателей и сбросил кэш; фиксируем
            # свежепокрытый диапазон ПОСЛЕ, чтобы кэш был валиден.
            if self._covered_start is None or self._covered_end is None:
                self._covered_start, self._covered_end = start, end
            else:
                self._covered_start = min(self._covered_start, start)
                self._covered_end = max(self._covered_end, end)
            return MaterializationResult.from_ensure(ensure)

    def ensure_day(self, day: date) -> MaterializationResult:
        return self.ensure_range(day, day)


__all__ = [
    "MATERIALIZATION_BUFFER_DAYS",
    "MAX_RANGE_DAYS",
    "MaterializationResult",
    "OccurrenceMaterializer",
]
