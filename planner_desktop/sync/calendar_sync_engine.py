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
from typing import TYPE_CHECKING, Optional

from planner_desktop.domain.task import Task
from planner_desktop.sync import calendar_mapper
from planner_desktop.sync.sync_types import (
    CalendarEvent,
    OpKind,
    PendingOp,
    RetryableGatewayError,
    TerminalGatewayError,
)

if TYPE_CHECKING:  # только подсказки типов: движок зависит от поведения, не модулей
    from planner_desktop.repositories import TaskRepository
    from planner_desktop.storage.calendar_sync_store import CalendarSyncStore
    from planner_desktop.sync.calendar_contract import CalendarGateway

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
    ) -> None:
        self._repository = repository
        self._store = store
        self._gateway = gateway

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

    def push_pending(self, limit: int = 50) -> None:
        for op in self._store.list_due_ops(limit):
            try:
                self._push_op(op)
            except RetryableGatewayError as exc:
                self._store.requeue_op(op.id, str(exc))
            except TerminalGatewayError as exc:
                self._store.mark_terminal(op.id, str(exc))
            else:
                self._store.remove_op(op.id)

    def pull_remote_changes(self) -> None:
        batch = self._gateway.list_changes(self._store.get_sync_cursor())
        for event in batch.events:
            self._apply_remote_event(event)
        self._store.set_sync_cursor(batch.next_cursor)

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
