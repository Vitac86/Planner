"""Двусторонний движок Calendar-синхронизации нового десктопа.

Работает поверх трёх зависимостей и ничего не знает про их реализацию:

- репозиторий задач (SQLiteTaskRepository / FakeTaskRepository);
- CalendarSyncStore — локальная очередь push-операций + курсор pull-а;
- CalendarGateway — сейчас FakeCalendarGateway, позже реальный Google;
  движок не импортирует ни Google-клиенты, ни OAuth и не делает сети.

Схема одного цикла sync_once():

1. push_pending() — отложенные локальные операции уходят в календарь:
   - задача с датой без google_calendar_event_id -> insert_event,
     полученные id/etag записываются в задачу;
   - задача с event_id -> patch_event (патч строит маппер; для экземпляра
     повторяющегося события start/end сознательно опускаются);
   - тумбстоун с event_id -> delete_event;
   - временная ошибка шлюза -> requeue с бэкоффом, постоянная -> terminal
     (dead-letter), бесконечных ретраев нет.
2. pull_remote_changes() — изменения календаря (в т.ч. с телефона):
   - незнакомое активное событие -> новая локальная задача;
   - знакомое событие -> обновление локальной задачи по конфликтной
     политике (ниже);
   - cancelled -> тумбстоун локальной задачи.

Конфликтная политика (детерминированная, фаза 1):

1. Если у задачи есть pending-операция в очереди — remote её НЕ трогает:
   локальная правка ещё не допушена, перезапись потеряла бы её.
   Задача «догонит» календарь после push-а следующего цикла.
2. Иначе, если etag события совпадает с сохранённым в задаче — это эхо
   нашего же push-а, пропускаем.
3. Иначе побеждает бОльший updated_at: remote новее -> накатываем на
   задачу; локальная новее -> ставим push update в очередь.
4. Равенство (или неизвестный remote updated_at) — оставляем локальную
   версию без изменений (лог/отладка), ничего не пушим.

Прочие правила фазы 1:

- локальный тумбстоун «липкий»: если delete уже допушен, поздние
  remote-правки того же события задачу не воскрешают;
- unschedule (запланирована -> без даты) выполняет DesktopTaskService
  (_detach_schedule): для непушенной задачи снимается pending create,
  для привязанной одиночной ставится delete события (event_id — в payload)
  и задача отвязывается; экземпляры повторяющихся серий не отвязываются
  (сервис возвращает ошибку). record_local_update по-прежнему игнорирует
  задачи без start — правка без даты сама по себе операций не ставит;
- задачи без даты в календарь не отправляются вовсе.
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime
from typing import TYPE_CHECKING, Optional

from planner_desktop.domain.external_series import (
    EXTERNAL_PROVIDER_GOOGLE,
    EXTERNAL_START_ALL_DAY,
    EXTERNAL_START_TIMED,
    ExternalCalendarSeries,
)
from planner_desktop.domain.google_recurrence import parse_google_recurrence
from planner_desktop.domain.recurrence import SeriesSchedule
from planner_desktop.domain.series_calendar_link import (
    PLANNER_PAYLOAD_HASH_PROPERTY,
    PLANNER_SERIES_UID_PROPERTY,
    RemoteOccurrenceChange,
    SeriesLinkStatus,
)
from planner_desktop.domain.task import Task
from planner_desktop.domain.task import utc_now
from planner_desktop.sync import calendar_mapper
from planner_desktop.sync.sync_types import (
    CalendarEvent,
    CalendarPullStats,
    OpKind,
    PendingOp,
    RetryableGatewayError,
    TerminalGatewayError,
)

if TYPE_CHECKING:  # только подсказки типов: движок зависит от поведения, не модулей
    from planner_desktop.repositories import TaskRepository
    from planner_desktop.storage.calendar_sync_store import CalendarSyncStore
    from planner_desktop.sync.calendar_contract import CalendarGateway
    from planner_desktop.repositories.external_series_repository import (
        ExternalSeriesRepository,
    )

logger = logging.getLogger(__name__)


# ---- запись локальных изменений в очередь -------------------------------------
#
# Эти функции — единственное место, где решается, какая операция ставится
# в очередь; ими пользуются и движок (handle_local_task_*), и
# DesktopTaskService (usecases/task_service.py), чтобы правила не расходились.

def record_local_create(store: "CalendarSyncStore", task: Task) -> None:
    """Локально создана задача: событие нужно только задачам с датой."""
    if not calendar_mapper.is_syncable(task):
        return
    store.enqueue_create(task.uid)


def record_local_update(store: "CalendarSyncStore", task: Task) -> None:
    """Локально изменена задача: create/update/delete по её состоянию."""
    if task.is_deleted:
        record_local_delete(store, task)
        return
    if task.start is None:
        # Unschedule (дата снята у уже синхронизированной задачи) в фазе 1
        # не реализован: события не трогаем, новых операций не ставим.
        return
    if task.google_calendar_event_id is None:
        store.enqueue_create(task.uid)
    else:
        store.enqueue_update(task.uid)


def record_local_delete(store: "CalendarSyncStore", task: Task) -> None:
    """Локально удалена задача (тумбстоун): delete только если событие было."""
    if task.google_calendar_event_id is not None:
        store.enqueue_delete(
            task.uid, payload={"event_id": task.google_calendar_event_id}
        )
    else:
        # Событие не создавалось — снимаем и отложенный create, если был.
        store.cancel_pending_ops(task.uid)


class CalendarSyncEngine:
    """Оркестратор push/pull. Создаётся на репозитории, очереди и шлюзе."""

    def __init__(
        self,
        repository: "TaskRepository",
        store: "CalendarSyncStore",
        gateway: "CalendarGateway",
        external_series_repository: Optional["ExternalSeriesRepository"] = None,
        *,
        external_series_provider: str = EXTERNAL_PROVIDER_GOOGLE,
        external_series_calendar_id: Optional[str] = None,
        series_link_store=None,
    ) -> None:
        self._repository = repository
        self._store = store
        self._gateway = gateway
        self._external_series_repository = external_series_repository
        self._external_series_provider = external_series_provider
        self._external_series_calendar_id = (
            external_series_calendar_id
            or getattr(gateway, "calendar_id", None)
            or "primary"
        )
        self.last_pull_stats = CalendarPullStats()
        self._series_link_store = series_link_store

    # ---- реакция на локальные изменения ---------------------------------------

    def handle_local_task_created(self, task: Task) -> None:
        record_local_create(self._store, task)

    def handle_local_task_updated(self, task: Task) -> None:
        record_local_update(self._store, task)

    def handle_local_task_deleted(self, task: Task) -> None:
        record_local_delete(self._store, task)

    # ---- цикл синхронизации -----------------------------------------------------

    def sync_once(self) -> None:
        """Один полный цикл: сначала push (локальное — наружу), потом pull.

        Такой порядок вместе с проверкой etag не даёт собственным правкам
        вернуться «удалёнными изменениями» и перезаписать задачу.
        """
        self.push_pending()
        self.pull_remote_changes()

    def push_pending(self, limit: int = 50) -> int:
        """Отправляет отложенные операции; возвращает число УСПЕШНЫХ push-ей
        (requeue/dead-letter не считаются) — для сводки ручного синка."""
        pushed = 0
        for op in self._store.list_due_ops(limit):
            try:
                self._push_op(op)
            except RetryableGatewayError as exc:
                self._store.requeue_op(op.id, str(exc))
            except TerminalGatewayError as exc:
                self._store.mark_terminal(op.id, str(exc))
            else:
                self._store.remove_op(op.id)
                pushed += 1
        return pushed

    def pull_remote_changes(self) -> int:
        """Забирает и применяет удалённые изменения; возвращает число
        полученных событий (включая эхо собственных push-ей)."""
        batch = self._gateway.list_changes(self._store.get_sync_cursor())
        self.last_pull_stats = CalendarPullStats(total_events=len(batch.events))
        for event in batch.events:
            self._apply_remote_event(event)
        self._store.set_sync_cursor(batch.next_cursor)
        return len(batch.events)

    # ---- push одной операции ------------------------------------------------------

    def _push_op(self, op: PendingOp) -> None:
        task = self._repository.get_by_uid(op.task_uid)

        if op.op == OpKind.DELETE.value:
            self._push_delete(op, task)
            return

        if task is None:
            return  # задачи больше нет — пушить нечего
        if task.is_deleted:
            # Задача умерла, пока операция ждала: доводим до delete.
            self._push_delete(op, task)
            return
        if task.start is None:
            # Страховка: unschedule снимает/заменяет операции ещё в сервисе
            # (_detach_schedule), сюда такие задачи попадать не должны.
            logger.debug("Пропуск push %s: у задачи %s нет даты", op.op, task.uid)
            return

        if task.google_calendar_event_id is None:
            created = self._gateway.insert_event(calendar_mapper.task_to_event(task))
            task.google_calendar_event_id = created.id
            task.google_calendar_etag = created.etag
            self._repository.update(task)
        else:
            patch = calendar_mapper.task_to_event_patch(task)
            updated = self._gateway.patch_event(task.google_calendar_event_id, patch)
            task.google_calendar_etag = updated.etag
            self._repository.update(task)

    def _push_delete(self, op: PendingOp, task: Optional[Task]) -> None:
        event_id = None
        if task is not None:
            event_id = task.google_calendar_event_id
        if event_id is None and op.payload_json:
            event_id = json.loads(op.payload_json).get("event_id")
        if event_id:
            self._gateway.delete_event(event_id)

    # ---- применение одного удалённого изменения --------------------------------------

    def _apply_remote_event(self, event: CalendarEvent) -> None:
        known_master = self._known_external_master(event)
        linked_master = self._linked_master(event.id)
        if event.is_recurring_master or known_master is not None or linked_master is not None:
            self.last_pull_stats.recurring_masters += 1
            if event.is_cancelled:
                self.last_pull_stats.cancelled_masters += 1
            self._apply_remote_master(event, known_master, linked_master)
            return

        if event.is_recurring_instance:
            self.last_pull_stats.recurring_instances += 1
            linked_parent = self._linked_master(event.recurring_event_id)
            if linked_parent is not None:
                self._quarantine_linked_instance(event, linked_parent)
                self.last_pull_stats.linked_instance_changes_quarantined += 1
                return
        else:
            self.last_pull_stats.ordinary_events += 1

        task = self._repository.get_by_google_event_id(event.id)

        if task is None:
            if event.is_cancelled:
                return  # незнакомое отменённое событие — не наше дело
            self._repository.add(calendar_mapper.event_to_new_task(event))
            return

        if self._store.has_pending_op(task.uid):
            # Политика №1: недопушенная локальная правка важнее remote.
            logger.debug("Pending-операция защищает задачу %s от pull", task.uid)
            return

        if event.is_cancelled:
            if not task.is_deleted:
                self._repository.delete(task.id)
            return

        if task.is_deleted:
            # Липкий тумбстоун: delete уже допушен, remote-правку игнорируем.
            return

        if event.etag is not None and event.etag == task.google_calendar_etag:
            return  # эхо нашего собственного push-а

        if event.updated_at is None or event.updated_at == task.updated_at:
            # Политика №4: ничья — локальная версия остаётся.
            logger.debug("Ничья updated_at по задаче %s: оставляем локальную", task.uid)
            return
        if event.updated_at > task.updated_at:
            calendar_mapper.apply_event_to_task(event, task)
            self._repository.update(task)
        else:
            # Локальная новее: не затираем, а пушим её в календарь.
            record_local_update(self._store, task)

    # ---- recurring-master catalog (read-only remote discovery) ----------------

    def _known_external_master(
        self, event: CalendarEvent
    ) -> Optional[ExternalCalendarSeries]:
        if self._external_series_repository is None or not event.id:
            return None
        return self._external_series_repository.get(
            self._external_series_provider,
            self._external_series_calendar_id,
            event.id,
        )

    def _linked_master(self, remote_event_id: Optional[str]):
        if self._series_link_store is None or not remote_event_id:
            return None
        return self._series_link_store.get_link_by_remote(
            self._external_series_provider,
            self._external_series_calendar_id,
            remote_event_id,
        )

    @staticmethod
    def _event_schedule(event: CalendarEvent) -> Optional[SeriesSchedule]:
        recurrence_start = event.recurrence_start or event.start
        if recurrence_start is None:
            return None
        if event.is_all_day:
            start_day = (recurrence_start if isinstance(recurrence_start, date)
                         and not isinstance(recurrence_start, datetime)
                         else recurrence_start.date())
            return SeriesSchedule(
                start_date=start_day,
                all_day=True,
                timezone_name=event.start_timezone or "UTC",
            )
        if not isinstance(recurrence_start, datetime):
            return None
        return SeriesSchedule(
            start_date=recurrence_start.date(),
            all_day=False,
            local_time=recurrence_start.time().replace(tzinfo=None),
            duration_minutes=None,
            timezone_name=event.start_timezone or "UTC",
        )

    def _apply_remote_master(
        self,
        event: CalendarEvent,
        existing: Optional[ExternalCalendarSeries],
        linked=None,
    ) -> None:
        repository = self._external_series_repository
        # Compatibility mode for older engine construction: the classification
        # still prevents an ordinary Task even when no catalog was supplied.
        if repository is None:
            return
        if not event.id:
            raise ValueError("Recurring master does not have a remote event id.")
        seen_at = utc_now()
        if event.is_cancelled:
            if existing is not None:
                repository.mark_deleted(
                    self._external_series_provider,
                    self._external_series_calendar_id,
                    event.id,
                    etag=event.etag,
                    remote_updated_at=event.updated_at,
                    seen_at=seen_at,
                )
            if linked is not None:
                self._series_link_store.cancel_pending_ops(linked.series_uid)
                self._series_link_store.set_link_status(
                    linked.series_uid,
                    SeriesLinkStatus.REMOTE_DELETED,
                    error="Связанный мастер Google удалён.",
                    remote_etag=event.etag,
                    remote_updated_at=event.updated_at,
                )
            return

        schedule = self._event_schedule(event)
        parsed = parse_google_recurrence(
            event.recurrence_lines, schedule=schedule
        )
        if not parsed.supported:
            self.last_pull_stats.unsupported_masters += 1
        catalog_start = event.recurrence_start or event.start
        start_value = catalog_start.isoformat() if catalog_start is not None else ""
        end_value = event.end.isoformat() if event.end is not None else ""
        remote_payload_hash = event.private_extended_properties.get(
            PLANNER_PAYLOAD_HASH_PROPERTY
        )
        remote_series_uid = event.private_extended_properties.get(
            PLANNER_SERIES_UID_PROPERTY
        )
        repository.upsert(ExternalCalendarSeries(
            provider=self._external_series_provider,
            calendar_id=self._external_series_calendar_id,
            remote_event_id=event.id,
            etag=event.etag,
            title=event.summary,
            description=event.description,
            start_kind=(EXTERNAL_START_ALL_DAY if event.is_all_day
                        else EXTERNAL_START_TIMED),
            start_value=start_value,
            end_value=end_value,
            timezone_name=event.start_timezone,
            recurrence_lines=event.recurrence_lines,
            parsed_rule=parsed.planner_rule,
            support_status=parsed.support.value,
            unsupported_reason=parsed.readable_reason or None,
            remote_status=event.status,
            remote_updated_at=event.updated_at,
            first_seen_at=seen_at,
            last_seen_at=seen_at,
            deleted_at=seen_at if event.is_cancelled else None,
            planner_owned=bool(remote_series_uid),
            linked_series_uid=(linked.series_uid if linked is not None else None),
            planner_payload_hash=remote_payload_hash,
        ))
        if linked is not None:
            etag_matches = (
                linked.remote_etag is None or event.etag == linked.remote_etag
            )
            hash_matches = (
                linked.last_synced_payload_hash is not None
                and remote_payload_hash == linked.last_synced_payload_hash
                and remote_series_uid == linked.series_uid
            )
            if etag_matches and hash_matches:
                linked.remote_etag = event.etag
                linked.remote_updated_at = event.updated_at
                linked.last_error = None
                # Preserve a pending local op; an echo cannot silently make it
                # synced before its write succeeds.
                if linked.link_status not in (
                    SeriesLinkStatus.PENDING_CREATE,
                    SeriesLinkStatus.PENDING_UPDATE,
                    SeriesLinkStatus.PENDING_DELETE,
                ):
                    linked.link_status = SeriesLinkStatus.SYNCED
                self._series_link_store.update_link(linked)
            else:
                self._series_link_store.cancel_pending_ops(linked.series_uid)
                self._series_link_store.set_link_status(
                    linked.series_uid,
                    SeriesLinkStatus.CONFLICT,
                    error=(
                        "Мастер Google изменён вне Planner. Локальная серия "
                        "сохранена; автоматическая перезапись отключена."
                    ),
                    remote_etag=event.etag,
                    remote_updated_at=event.updated_at,
                )

    def _quarantine_linked_instance(self, event: CalendarEvent, linked) -> None:
        if self._series_link_store is None or not event.id:
            return
        original_start = (
            event.original_start.isoformat() if event.original_start is not None else ""
        )
        payload = {
            "id": event.id,
            "status": event.status,
            "recurringEventId": event.recurring_event_id,
            "originalStartTime": original_start,
            "summary": event.summary,
            "description": event.description,
            "start": event.start.isoformat() if event.start is not None else None,
            "end": event.end.isoformat() if event.end is not None else None,
        }
        seen = utc_now()
        self._series_link_store.upsert_occurrence_change(
            RemoteOccurrenceChange(
                provider=linked.provider,
                calendar_id=linked.calendar_id,
                remote_master_event_id=linked.remote_event_id,
                remote_instance_event_id=event.id,
                original_start_value=original_start,
                status=event.status,
                payload_json=json.dumps(
                    payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                ),
                remote_etag=event.etag,
                remote_updated_at=event.updated_at,
                first_seen_at=seen,
                last_seen_at=seen,
            )
        )
