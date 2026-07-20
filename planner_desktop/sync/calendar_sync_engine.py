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
        occurrence_sync_store=None,
        series_repository=None,
        split_store=None,
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
        self._occurrence_sync_store = occurrence_sync_store
        self._series_repository = series_repository
        self._split_store = split_store

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
                if self._quarantine_linked_instance(event, linked_parent):
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
        # Phase 3.2B3C1: an active remote split plan classifies its two
        # masters BEFORE ordinary B3A conflict handling.  Expected split
        # echoes update plan ETags; unexpected changes mark the plan conflict;
        # neither master ever becomes an ordinary Task or an unowned external
        # master while the plan is active.
        if self._handle_split_master_pull(event):
            return
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
                mark_remote_deleted = getattr(
                    self._series_link_store, "mark_remote_deleted", None
                )
                if callable(mark_remote_deleted):
                    # v9: one transaction cancels pending work, supersedes a
                    # pending explicit resolution and records the dead master.
                    mark_remote_deleted(
                        linked.series_uid,
                        error="Связанный мастер Google удалён.",
                        remote_etag=event.etag,
                        remote_updated_at=event.updated_at,
                    )
                else:  # pragma: no cover - legacy store compatibility
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
                # A recorded conflict or dead master never self-heals from an
                # echo: foreign edits do not update the private markers, so a
                # marker match here proves nothing.  Only an explicit user
                # resolution (Phase 3.2B3A) clears these states.
                if linked.link_status in (
                    SeriesLinkStatus.CONFLICT,
                    SeriesLinkStatus.REMOTE_DELETED,
                ):
                    return
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
                self._persist_linked_master_mismatch(event, linked)

    def _handle_split_master_pull(self, event: CalendarEvent) -> bool:
        """Split-aware master classification; True when fully handled here."""
        store = self._split_store
        if store is None or not event.id:
            return False
        plan = store.get_active_plan_by_source_remote(event.id)
        if plan is not None:
            return self._handle_split_source_pull(event, plan)
        plan = store.get_plan_by_successor_remote(event.id)
        if plan is not None and plan.is_active:
            return self._handle_split_successor_pull(event, plan)
        return False

    @staticmethod
    def _pulled_master_hash(event: CalendarEvent) -> Optional[str]:
        from planner_desktop.domain.google_series_split import (
            master_content_fingerprint,
        )
        from planner_desktop.sync.calendar_series_mapper import (
            master_event_to_owned_payload,
        )

        try:
            # RRULE-normalized: an echo of our own write hashes equally even
            # after Google canonicalizes the stored recurrence line.
            return master_content_fingerprint(
                master_event_to_owned_payload(event)
            )
        except (TypeError, ValueError):
            return None

    def _handle_split_source_pull(self, event: CalendarEvent, plan) -> bool:
        store = self._split_store
        if event.is_cancelled:
            # Recorded without automatic recreation (Part 12).
            store.mark_conflict(
                plan.id,
                "Исходный мастер отменён в Google во время разделения.",
            )
            self.last_pull_stats.split_conflicts_detected += 1
            return True
        actual_hash = self._pulled_master_hash(event)
        if actual_hash == plan.source_trimmed_payload_hash:
            # Expected echo of our own trim: refresh the acknowledged ETag.
            if event.etag:
                store.update_remote_etags(
                    plan.id, source_trimmed_remote_etag=event.etag
                )
            return True
        if actual_hash == plan.source_original_payload_hash:
            return True  # pre-trim state, nothing unexpected yet
        store.mark_conflict(
            plan.id,
            "Исходный мастер изменён вне Planner во время разделения; "
            "план остановлен.",
        )
        self.last_pull_stats.split_conflicts_detected += 1
        return True

    def _handle_split_successor_pull(self, event: CalendarEvent, plan) -> bool:
        from planner_desktop.domain.google_series_split import (
            RemoteSeriesSplitStatus,
        )

        store = self._split_store
        if event.is_cancelled:
            if plan.state in (
                RemoteSeriesSplitStatus.SUCCESSOR_REMOVED_FOR_ROLLBACK,
                RemoteSeriesSplitStatus.ROLLBACK_PENDING,
            ):
                return True  # expected rollback deletion echo
            if plan.state in (
                RemoteSeriesSplitStatus.SUCCESSOR_CREATED,
                RemoteSeriesSplitStatus.LOCAL_FINALIZE_PENDING,
            ):
                store.mark_conflict(
                    plan.id,
                    "Мастер-преемник отменён в Google до завершения "
                    "разделения; автоматическое пересоздание отключено.",
                )
                self.last_pull_stats.split_conflicts_detected += 1
            return True
        actual_hash = self._pulled_master_hash(event)
        if actual_hash == plan.successor_payload_hash:
            # Expected echo of our own insert, associated with the reserved
            # successor series UID; never an unowned external master.
            if event.etag:
                store.update_remote_etags(
                    plan.id, successor_remote_etag=event.etag
                )
            return True
        if plan.state in (
            RemoteSeriesSplitStatus.SUCCESSOR_CREATED,
            RemoteSeriesSplitStatus.LOCAL_FINALIZE_PENDING,
        ):
            store.mark_conflict(
                plan.id,
                "Мастер-преемник изменён вне Planner до завершения "
                "разделения; план остановлен.",
            )
            self.last_pull_stats.split_conflicts_detected += 1
        return True

    def _persist_linked_master_mismatch(self, event: CalendarEvent, linked) -> None:
        """Unexpected remote master state for an active link (Phase 3.2B3A).

        Never overwrites the local series and never queues an automatic
        UPDATE.  A live conflict gets its stored snapshot/etag/hash base
        refreshed (a stale acknowledged decision becomes superseded inside
        ``record_conflict``); a ``remote_deleted`` master that reappeared at
        the old id is only recorded as a diagnostic — no automatic relink.
        """
        from planner_desktop.sync.calendar_series_mapper import (
            remote_master_snapshot_json,
        )

        record_conflict = getattr(self._series_link_store, "record_conflict", None)
        if not callable(record_conflict):  # pragma: no cover - legacy store
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
            return
        snapshot_json = remote_master_snapshot_json(event)
        remote_payload_hash = event.private_extended_properties.get(
            PLANNER_PAYLOAD_HASH_PROPERTY
        )
        if linked.link_status is SeriesLinkStatus.REMOTE_DELETED:
            self._series_link_store.note_remote_reappeared(
                linked.series_uid,
                remote_etag=event.etag,
                remote_updated_at=event.updated_at,
                remote_snapshot_json=snapshot_json,
            )
            return
        record_conflict(
            linked.series_uid,
            reason=(
                "Мастер Google изменён вне Planner. Локальная серия "
                "сохранена; автоматическая перезапись отключена."
            ),
            remote_etag=event.etag,
            remote_payload_hash=remote_payload_hash,
            remote_snapshot_json=snapshot_json,
            remote_updated_at=event.updated_at,
        )

    def _quarantine_linked_instance(self, event: CalendarEvent, linked) -> bool:
        if self._series_link_store is None or not event.id:
            return False
        if (
            self._occurrence_sync_store is not None
            and self._series_repository is not None
        ):
            return self._handle_owned_linked_instance(event, linked)
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
        return True

    def _handle_owned_linked_instance(self, event: CalendarEvent, linked) -> bool:
        """Match exact originalStartTime and quarantine without Task import."""
        from planner_desktop.domain.google_occurrence import (
            OccurrenceSyncStatus,
            canonical_occurrence_payload_fingerprint,
            google_original_start_to_occurrence_key,
            local_occurrence_to_google_original_start,
        )

        series = self._series_repository.get_by_uid(linked.series_uid)
        if series is None or series.is_deleted:
            return False
        raw = dict(event.raw_payload or {})
        if not raw:
            original = (
                {"date": event.original_start.isoformat()}
                if isinstance(event.original_start, date)
                and not isinstance(event.original_start, datetime)
                else {
                    "dateTime": (
                        event.original_start.isoformat()
                        if event.original_start is not None else ""
                    ),
                    "timeZone": (
                        event.original_start_timezone
                        or series.schedule.timezone_name
                    ),
                }
            )
            raw = {
                "id": event.id,
                "etag": event.etag,
                "status": event.status,
                "recurringEventId": event.recurring_event_id,
                "originalStartTime": original,
                "summary": event.summary,
                "description": event.description,
                "start": (
                    {"date": event.start.isoformat()}
                    if isinstance(event.start, date)
                    and not isinstance(event.start, datetime)
                    else {
                        "dateTime": event.start.isoformat() if event.start else "",
                        "timeZone": event.start_timezone or series.schedule.timezone_name,
                    }
                ),
                "end": (
                    {"date": event.end.isoformat()}
                    if isinstance(event.end, date)
                    and not isinstance(event.end, datetime)
                    else {
                        "dateTime": event.end.isoformat() if event.end else "",
                        "timeZone": event.end_timezone or series.schedule.timezone_name,
                    }
                ),
            }
        original = raw.get("originalStartTime") or {}
        try:
            key = google_original_start_to_occurrence_key(series, original)
            identity = local_occurrence_to_google_original_start(series, key)
        except ValueError:
            # Wrong kind/timezone/slot is still retained for diagnostics, but
            # cannot be attached to an arbitrary local occurrence.
            seen = utc_now()
            self._occurrence_sync_store.upsert_occurrence_change(
                RemoteOccurrenceChange(
                    provider=linked.provider,
                    calendar_id=linked.calendar_id,
                    remote_master_event_id=linked.remote_event_id,
                    remote_instance_event_id=event.id,
                    original_start_value=json.dumps(
                        original, sort_keys=True, separators=(",", ":")
                    ),
                    status=event.status,
                    payload_json=json.dumps(
                        raw, ensure_ascii=False, sort_keys=True,
                        separators=(",", ":")
                    ),
                    remote_etag=event.etag,
                    remote_updated_at=event.updated_at,
                    first_seen_at=seen,
                    last_seen_at=seen,
                    resolution_status="unresolved",
                    resolution_error=(
                        "originalStartTime не соответствует локальной серии"
                    ),
                )
            )
            return True
        occurrence_link = self._occurrence_sync_store.ensure_occurrence_link(
            series.uid, key, linked, identity
        )
        remote_hash = canonical_occurrence_payload_fingerprint(raw)
        cancelled = event.is_cancelled
        echo = (
            occurrence_link.last_synced_remote_hash == remote_hash
            and (
                (cancelled and occurrence_link.sync_status is OccurrenceSyncStatus.CANCELLED)
                or (
                    not cancelled
                    and occurrence_link.sync_status
                    in (
                        OccurrenceSyncStatus.SYNCED_EXCEPTION,
                        OccurrenceSyncStatus.PENDING_UPDATE,
                    )
                )
            )
        )
        if echo:
            occurrence_link.remote_instance_event_id = event.id
            occurrence_link.remote_etag = event.etag
            occurrence_link.remote_updated_at = event.updated_at
            occurrence_link.is_cancelled_remote = cancelled
            self._occurrence_sync_store.update_occurrence_link(occurrence_link)
            resolved = self._occurrence_sync_store.resolve_matching_quarantine(
                series.uid, key, resolution_kind="echo"
            )
            self.last_pull_stats.occurrence_quarantine_resolved += resolved
            return False
        seen = utc_now()
        change = self._occurrence_sync_store.upsert_occurrence_change(
            RemoteOccurrenceChange(
                provider=linked.provider,
                calendar_id=linked.calendar_id,
                remote_master_event_id=linked.remote_event_id,
                remote_instance_event_id=event.id,
                original_start_value=identity.value,
                status=event.status,
                payload_json=json.dumps(
                    raw, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                ),
                remote_etag=event.etag,
                remote_updated_at=event.updated_at,
                first_seen_at=seen,
                last_seen_at=seen,
                matched_series_uid=series.uid,
                matched_occurrence_key=key,
                resolution_status="unresolved",
            )
        )
        self._occurrence_sync_store.record_remote_conflict(
            series.uid,
            key,
            reason=(
                "Экземпляр Google отменён вне Planner."
                if cancelled else "Экземпляр Google изменён вне Planner."
            ),
            snapshot=raw,
            remote_instance_event_id=event.id,
            remote_etag=event.etag,
            remote_updated_at=event.updated_at,
            cancelled=cancelled,
        )
        self.last_pull_stats.occurrence_conflicts_detected += 1
        if cancelled:
            self.last_pull_stats.occurrence_remote_cancellations += 1
        return True
