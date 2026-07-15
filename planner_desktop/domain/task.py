"""Доменная задача нового десктопа.

Обычный dataclass: не зависит ни от Qt/QML, ни от Flet, ни от SQLModel
старого приложения. Поля google_calendar_* заложены заранее под будущую
двустороннюю синхронизацию с Google Calendar (см. sync/calendar_contract.py),
но в этом скелете никогда не заполняются реальными данными.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Tuple


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Task:
    title: str
    id: Optional[int] = None
    uid: str = field(default_factory=lambda: str(uuid.uuid4()))
    notes: str = ""
    # Локальные Planner-теги. Tuple не даёт случайно разделить изменяемый
    # список между экземплярами и сохраняет совместимость конструкторов.
    tags: Tuple[str, ...] = field(default_factory=tuple)

    # Расписание. Для задачи со временем start — момент начала,
    # end либо задан явно, либо выводится из duration_minutes.
    # Для all-day задачи start несёт только дату (время игнорируется),
    # end трактуется как эксклюзивная дата конца (семантика Google Calendar).
    start: Optional[datetime] = None
    end: Optional[datetime] = None
    duration_minutes: Optional[int] = None
    is_all_day: bool = False

    priority: int = 0
    completed: bool = False
    # Момент выполнения (локально, UTC). Заполняется при переходе
    # «не выполнено -> выполнено» и очищается при снятии галочки; нужен
    # странице «История», чтобы группировать выполненное по датам.
    completed_at: Optional[datetime] = None

    # Поля привязки к Google Calendar (пока всегда пустые).
    # recurring_event_id + original_start нужны, чтобы отличать экземпляр
    # повторяющегося события от одиночного: экземпляру нельзя слепо
    # патчить start/end — это превращается в перенос экземпляра.
    google_calendar_event_id: Optional[str] = None
    google_calendar_etag: Optional[str] = None
    google_calendar_recurring_event_id: Optional[str] = None
    google_calendar_original_start: Optional[datetime] = None

    # Привязка к локальной повторяющейся серии (Phase 3.2A).
    # series_uid + occurrence_key — неизменяемая идентичность слота серии
    # (см. domain/recurrence.py); series_revision — ревизия правила,
    # породившего экземпляр; is_series_exception — экземпляр правился
    # в области «только этот» и регенерацией не перезаписывается.
    # Локальные серии НЕ синхронизируются с Google в этой фазе.
    series_uid: Optional[str] = None
    occurrence_key: Optional[str] = None
    series_revision: Optional[int] = None
    is_series_exception: bool = False

    updated_at: datetime = field(default_factory=utc_now)
    # Тумбстоун: удаление помечается, а не стирает запись, чтобы будущая
    # синхронизация могла отправить delete в Calendar.
    deleted_at: Optional[datetime] = None

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None

    @property
    def is_scheduled(self) -> bool:
        return self.start is not None

    @property
    def is_series_occurrence(self) -> bool:
        """Экземпляр локальной повторяющейся серии (Phase 3.2A)."""
        return self.series_uid is not None

    def mark_deleted(self, when: Optional[datetime] = None) -> None:
        self.deleted_at = when or utc_now()
        self.updated_at = self.deleted_at

    def set_completed(self, value: bool, when: Optional[datetime] = None) -> None:
        """Меняет галочку «выполнено», сопровождая её меткой времени.

        completed_at ставится ТОЛЬКО при переходе «не выполнено -> выполнено»,
        поэтому повторное сохранение уже выполненной задачи не сдвигает её в
        «Истории»; снятие галочки метку очищает.
        """
        value = bool(value)
        if value and not self.completed:
            self.completed_at = when or utc_now()
        elif not value:
            self.completed_at = None
        self.completed = value

    def touch(self) -> None:
        self.updated_at = utc_now()
