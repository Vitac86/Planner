"""Контракт будущей двусторонней синхронизации с Google Calendar.

Только дизайн: ни одного реального вызова Google API в этом модуле нет
и в скелете не будет. «Мобильная версия» Planner — это родное приложение
Google Calendar на телефоне пользователя, поэтому контракт описывает
обмен именно с Calendar.

Правила маппинга (закрепляем заранее, чтобы не повторить историю
с HTTP 400 в старом приложении):

1. Задача со временем -> событие с ``start.dateTime`` / ``end.dateTime``
   (+ ``timeZone``). Никогда не смешивать с ``date``.
2. All-day задача -> событие с ``start.date`` / ``end.date``, где
   ``end.date`` — ЭКСКЛЮЗИВНАЯ дата (однодневное событие на 5 июня:
   start.date=2026-06-05, end.date=2026-06-06).
3. Экземпляр повторяющегося all-day события нельзя слепо патчить по
   start/end: PATCH со сдвинутыми датами Google трактует как перенос
   экземпляра и может ответить 400/409. Обновление экземпляра идёт по
   ``recurringEventId`` + ``originalStartTime``; менять start/end можно
   только когда это осознанный перенос.
4. Правки, сделанные на телефоне (в приложении Google Calendar), приходят
   к нам как удалённые изменения через pull_changed_events().
5. Правки в десктопе — локальные изменения; они уходят через push_task_*.
6. Разрешение конфликтов (локальная и удалённая правка одной задачи) —
   ответственность слоя НАД шлюзом (движка синхронизации), не самого
   шлюза: шлюз лишь честно переносит данные и отдаёт etag.
7. Задачи без даты в Calendar не отправляются вовсе (фаза 1); позже их
   можно явно замапить на Google Tasks или all-day события.
8. CalendarEvent классифицируется взаимоисключающе: ordinary (нет recurrence
   и recurring_event_id), recurring instance (есть recurring_event_id),
   recurring master (есть recurrence, но нет recurring_event_id). Instance
   никогда не становится master из-за лишней provider metadata.
9. Phase 3.2B1 читает master только в external_calendar_series: master не
   превращается в Task/TaskSeries и не создаёт Calendar queue operation.
   Production insert/patch/delete recurrence не пишут; adoption и remote
   master writes отложены до Phase 3.2B2.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Mapping, Optional, Protocol, runtime_checkable

from planner_desktop.domain.task import Task
from planner_desktop.sync.sync_types import CalendarEvent, RemoteChangeBatch


@dataclass
class RemoteEventChange:
    """Одно изменение, пришедшее со стороны Google Calendar.

    status="cancelled" означает удаление события (в т.ч. с телефона);
    payload — сырое событие Calendar API v3, как его вернул list/get.
    """

    event_id: str
    status: str = "confirmed"  # confirmed / tentative / cancelled
    etag: Optional[str] = None
    updated: Optional[datetime] = None
    payload: Dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class CalendarSyncGateway(Protocol):
    """Шлюз к Google Calendar. Единственное место будущих сетевых вызовов.

    Реализация появится в отдельной фазе и будет использовать уже
    существующие в проекте OAuth-токены; сам контракт про сеть ничего
    не знает и тестируется фейками.
    """

    def pull_changed_events(self) -> List[RemoteEventChange]:
        """Забрать изменения с сервера (через syncToken/updatedMin).

        Сюда попадают в том числе правки, сделанные на телефоне в
        приложении Google Calendar, — для нас это удалённые изменения.
        """
        ...

    def push_task_create(self, task: Task) -> RemoteEventChange:
        """Создать событие для локально созданной задачи.

        Возвращает данные созданного события (id, etag), чтобы движок
        записал их в поля task.google_calendar_*.
        """
        ...

    def push_task_update(self, task: Task) -> RemoteEventChange:
        """Отправить локальную правку задачи в связанное событие.

        Обязана соблюдать правила маппинга 1–3 (см. докстринг модуля):
        форма start/end (date vs dateTime) должна соответствовать
        реальному событию, а экземпляры повторяющихся событий не
        патчатся по start/end вслепую.
        """
        ...

    def push_task_delete(self, task: Task) -> None:
        """Удалить/отменить связанное событие для локально удалённой задачи
        (task несёт тумбстоун deleted_at)."""
        ...


@runtime_checkable
class CalendarGateway(Protocol):
    """Событийный шлюз календаря для движка синхронизации.

    Работает в терминах собственной модели CalendarEvent (sync_types.py),
    а не сырых payload-ов Google — поэтому один и тот же движок гоняется
    и на FakeCalendarGateway (сейчас), и на будущем GoogleCalendarGateway.
    Сеть/OAuth появятся только внутри реальной реализации; движок и тесты
    про них не знают.
    """

    def insert_event(self, event: CalendarEvent) -> CalendarEvent:
        """Создать событие; возвращает событие с назначенными id/etag."""
        ...

    def patch_event(self, event_id: str, patch: Mapping[str, Any]) -> CalendarEvent:
        """Частично обновить событие (имена полей CalendarEvent).

        Экземпляр повторяющегося события нельзя слепо патчить по
        start/end — шлюз (как и Google) вправе ответить ошибкой;
        безопасный патч для экземпляра строит маппер (без start/end).
        """
        ...

    def delete_event(self, event_id: str) -> None:
        """Удалить/отменить событие; уже отсутствующее — не ошибка."""
        ...

    def list_changes(self, cursor: Optional[str]) -> RemoteChangeBatch:
        """Изменения после курсора (аналог syncToken) + новый курсор.

        Сюда попадают в том числе правки с телефона (приложение
        Google Calendar): создание, перенос, удаление событий.
        """
        ...


@runtime_checkable
class CalendarEventMapper(Protocol):
    """Чистое преобразование Task <-> событие Calendar. Без сети."""

    def task_to_google_event_payload(self, task: Task) -> Dict[str, Any]:
        """Собрать тело события Calendar API из задачи.

        Задача со временем -> {"start": {"dateTime", "timeZone"},
        "end": {"dateTime", "timeZone"}}.
        All-day задача -> {"start": {"date"}, "end": {"date"}}, где
        end.date — эксклюзивная дата конца.
        Формы не смешиваются никогда.
        """
        ...

    def google_event_to_task(self, payload: Dict[str, Any]) -> Task:
        """Собрать/обновить доменную задачу из события Calendar.

        Событие с start.date считается all-day (is_all_day=True, end
        эксклюзивный); событие с start.dateTime — задачей со временем.
        recurringEventId и originalStartTime сохраняются в
        google_calendar_recurring_event_id / _original_start, чтобы
        последующие push-и не переносили экземпляр вслепую.
        """
        ...
