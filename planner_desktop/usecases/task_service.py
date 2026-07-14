"""Сценарии работы с задачами (use-case-слой нового десктопа).

ViewModel-и больше не решают, что делать с Calendar-очередью: сервис
выполняет операцию в репозитории и, если передана очередь
(CalendarSyncStore), ставит отложенную Calendar-операцию по правилам
из sync/calendar_sync_engine.py (record_local_*). Сам сервис НИКОГДА
не ходит в Google и сеть — push выполняет движок синхронизации
отдельно, когда появится реальный шлюз.

Продуктовые правила фазы 1:

- Calendar-операции ставятся только задачам с датой (timed или all-day);
- галочка «выполнено» — локальная: Calendar не имеет понятия
  «выполнено», событие остаётся в календаре как есть, операция
  в очередь не ставится;
- удаление задачи — тумбстоун; delete-операция ставится только если
  событие уже существовало (иначе снимается недопушенный create);
- unschedule (запланирована -> без даты): для не-допушенной задачи
  снимается pending create; для привязанной одиночной задачи ставится
  delete события (payload несёт event_id), задача отвязывается и
  остаётся локальной; для ЭКЗЕМПЛЯРА повторяющегося события —
  запрещено с человекочитаемой ошибкой (уроки dead-letter старого
  приложения: слепые операции над экземплярами серий опасны).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from typing import List, Optional, Set

from planner_desktop.domain.commands import (
    TaskEditorCommand,
    apply_editor_fields,
    build_task_from_editor,
    schedule_from_command,
    validate_editor,
)
from planner_desktop.domain import scheduling
from planner_desktop.domain.task import Task
from planner_desktop.repositories import TaskRepository
from planner_desktop.storage.calendar_sync_store import CalendarSyncStore
from planner_desktop.sync.calendar_sync_engine import (
    record_local_create,
    record_local_delete,
    record_local_update,
)

TASK_NOT_FOUND_ERROR = "Задача не найдена (возможно, уже удалена)."
UNSCHEDULE_RECURRING_ERROR = (
    "Снять дату у экземпляра повторяющегося события пока нельзя: "
    "операция небезопасна и сознательно не реализована."
)
POSTPONE_RECURRING_ERROR = (
    "Перенести экземпляр повторяющегося события пока нельзя: "
    "перенос экземпляров серий небезопасен (уроки dead-letter старого "
    "приложения) и сознательно не реализован."
)
RESCHEDULE_RECURRING_ERROR = (
    "Изменить расписание экземпляра повторяющегося события нельзя. "
    "Название, заметки, приоритет и выполненное состояние менять можно."
)
TASK_PRESET_UNAVAILABLE_ERROR = "Это действие планирования недоступно для задачи."
INVALID_DURATION_ERROR = "Длительность должна быть больше нуля."


@dataclass
class TaskOperationResult:
    """Результат операции редактора: задача либо список ошибок для формы."""

    task: Optional[Task] = None
    errors: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.task is not None and not self.errors


class DesktopTaskService:
    """CRUD задач + постановка Calendar-операций в локальную очередь."""

    def __init__(
        self,
        repository: TaskRepository,
        calendar_queue: Optional[CalendarSyncStore] = None,
    ) -> None:
        self.repository = repository
        self._queue = calendar_queue

    # ---- базовый CRUD ----------------------------------------------------------

    def create_task(self, task: Task) -> Task:
        created = self.repository.add(task)
        if self._queue is not None:
            record_local_create(self._queue, created)
        return created

    def update_task(self, task: Task) -> Task:
        updated = self.repository.update(task)
        if self._queue is not None:
            record_local_update(self._queue, updated)
        return updated

    def complete_task(self, task_id: int, completed: bool = True) -> bool:
        """Локальная галочка: Calendar-операция сознательно не ставится."""
        return self.repository.complete(task_id, completed)

    def toggle_completed(self, uid: str) -> bool:
        """Как complete_task: выполнено/не выполнено в календарь не уходит."""
        return self.repository.toggle_completed(uid)

    def delete_task(self, task_id: int) -> bool:
        """Тумбстоун в репозитории + delete/отмена операций в очереди."""
        deleted = self.repository.delete(task_id)
        if deleted and self._queue is not None:
            tombstone = self.repository.get(task_id)
            if tombstone is not None:
                record_local_delete(self._queue, tombstone)
        return deleted

    def delete_task_by_uid(self, uid: str) -> bool:
        task = self.repository.get_by_uid(uid)
        if task is None or task.is_deleted or task.id is None:
            return False
        return self.delete_task(task.id)

    def get_task(self, uid: str) -> Optional[Task]:
        """Живая задача по uid (тумбстоуны не отдаются)."""
        task = self.repository.get_by_uid(uid)
        if task is None or task.is_deleted:
            return None
        return task

    # ---- форма редактора (создание и правка) ------------------------------------

    def create_from_editor(self, command: TaskEditorCommand) -> TaskOperationResult:
        """Создание задачи из TaskEditorDialog; ошибки — списком, без исключений."""
        errors = validate_editor(command)
        if errors:
            return TaskOperationResult(errors=errors)
        task = self.create_task(build_task_from_editor(command))
        return TaskOperationResult(task=task)

    def edit_task(self, uid: str, command: TaskEditorCommand) -> TaskOperationResult:
        """Правка существующей задачи, включая переходы расписания.

        - запланирована и осталась запланированной -> update (или create,
          если событие ещё не создано) через record_local_update;
        - без даты -> запланирована: то же (появится create);
        - запланирована -> без даты: unschedule-переход (см. правила модуля).
        """
        task = self.repository.get_by_uid(uid)
        if task is None or task.is_deleted:
            return TaskOperationResult(errors=[TASK_NOT_FOUND_ERROR])

        errors = validate_editor(command)
        if errors:
            return TaskOperationResult(errors=errors)

        becoming_unscheduled = not command.add_to_calendar and task.start is not None
        recurring = task.google_calendar_recurring_event_id is not None
        if recurring and not self._recurring_schedule_matches(task, command):
            error = (
                UNSCHEDULE_RECURRING_ERROR
                if becoming_unscheduled
                else RESCHEDULE_RECURRING_ERROR
            )
            return TaskOperationResult(errors=[error])

        old_title = task.title
        old_notes = task.notes
        old_schedule = (task.start, task.end, task.duration_minutes, task.is_all_day)

        apply_editor_fields(command, task)
        calendar_text_changed = task.title != old_title or task.notes != old_notes

        if becoming_unscheduled:
            self._detach_schedule(task)
            return TaskOperationResult(task=self.repository.update(task))

        # У recurring-instance расписание только проверяется выше, но не
        # пересобирается из минутной формы: так текстовая правка не теряет
        # секунды/timezone исходного Calendar-события.
        if recurring:
            updated = (
                self.update_task(task)
                if calendar_text_changed
                else self.repository.update(task)
            )
            return TaskOperationResult(task=updated)

        if command.add_to_calendar:
            start, end, duration, is_all_day = schedule_from_command(command)
            task.start = start
            task.end = end
            task.duration_minutes = duration
            task.is_all_day = is_all_day

        schedule_changed = old_schedule != (
            task.start, task.end, task.duration_minutes, task.is_all_day
        )
        # Приоритет и completed — строго локальные поля. Calendar update нужен
        # только для текста события или реального изменения расписания.
        updated = (
            self.update_task(task)
            if calendar_text_changed or schedule_changed
            else self.repository.update(task)
        )
        return TaskOperationResult(task=updated)

    @staticmethod
    def _recurring_schedule_matches(
        task: Task, command: TaskEditorCommand
    ) -> bool:
        """Совпадает ли расписание формы с видимым расписанием instance.

        Форма хранит точность до минуты, поэтому сравниваются те же значения,
        которые пользователь видел в editor payload. Само расписание recurring
        при сохранении не переназначается.
        """
        if task.start is None or not command.add_to_calendar:
            return False
        if bool(command.is_all_day) != task.is_all_day:
            return False

        start, end, duration, is_all_day = schedule_from_command(command)
        if is_all_day:
            return (
                start is not None
                and end is not None
                and task.end is not None
                and start.date() == task.start.date()
                and end.date() == task.end.date()
                and task.duration_minutes is None
            )
        if start is None or end is None or task.end is None:
            return False
        return (
            start.strftime("%Y-%m-%d %H:%M")
            == task.start.strftime("%Y-%m-%d %H:%M")
            and end.strftime("%Y-%m-%d %H:%M")
            == task.end.strftime("%Y-%m-%d %H:%M")
            and duration == task.duration_minutes
        )

    # ---- расписание --------------------------------------------------------------

    def schedule_task(
        self,
        uid: str,
        start: datetime,
        *,
        duration_minutes: Optional[int] = None,
        is_all_day: bool = False,
    ) -> Optional[Task]:
        """Назначить/перенести дату задачи (например, из календарной сетки)."""
        task = self.get_task(uid)
        if task is None or task.google_calendar_recurring_event_id is not None:
            return None
        if duration_minutes is not None and duration_minutes <= 0:
            return None
        task.is_all_day = is_all_day
        if is_all_day:
            start = start.replace(hour=0, minute=0, second=0, microsecond=0)
            task.start = start
            task.duration_minutes = None
            task.end = start + timedelta(days=1)
        else:
            task.start = start
            previous_duration = (
                task.duration_minutes
                if task.duration_minutes is not None and task.duration_minutes > 0
                else 60
            )
            minutes = (
                duration_minutes
                if duration_minutes is not None
                else previous_duration
            )
            task.duration_minutes = minutes
            task.end = start + timedelta(minutes=minutes)
        return self.update_task(task)

    def apply_scheduling_preset(
        self, uid: str, preset: str, now: Optional[datetime] = None
    ) -> TaskOperationResult:
        """Применить quick scheduling preset к существующей задаче.

        Расчёт даты остаётся в ``domain.scheduling``; сохранение проходит
        только через ``schedule_task``/``unschedule_task``, поэтому Calendar
        queue получает те же create/update/delete, что и остальные use cases.
        """
        task = self.get_task(uid)
        if task is None:
            return TaskOperationResult(errors=[TASK_NOT_FOUND_ERROR])
        if task.google_calendar_recurring_event_id is not None:
            return TaskOperationResult(errors=[RESCHEDULE_RECURRING_ERROR])
        if preset == scheduling.PRESET_UNSCHEDULE and task.start is None:
            return TaskOperationResult(errors=[TASK_PRESET_UNAVAILABLE_ERROR])
        if preset == scheduling.PRESET_PLUS_HOUR and (
            task.start is None or task.is_all_day
        ):
            return TaskOperationResult(errors=[scheduling.PLUS_HOUR_NEEDS_TIME_ERROR])

        current = now or datetime.now()
        mode = (
            scheduling.MODE_NONE
            if task.start is None
            else scheduling.MODE_ALL_DAY
            if task.is_all_day
            else scheduling.MODE_TIMED
        )
        state = scheduling.EditorState(
            mode=mode,
            date_text=(task.start.strftime("%Y-%m-%d") if task.start else ""),
            time_text=(
                task.start.strftime("%H:%M")
                if task.start is not None and not task.is_all_day
                else ""
            ),
        )
        result = scheduling.apply_editor_preset(
            preset, state, today=current.date(), now=current
        )
        if not result.ok:
            return TaskOperationResult(errors=[result.error])
        if result.mode == scheduling.MODE_NONE:
            return self.unschedule_task(uid)

        target_date = datetime.strptime(result.date_text, "%Y-%m-%d").date()
        if result.mode == scheduling.MODE_ALL_DAY:
            start = datetime.combine(target_date, time.min)
            duration = None
            is_all_day = True
        else:
            target_time = datetime.strptime(result.time_text, "%H:%M").time()
            start = datetime.combine(target_date, target_time)
            duration = (
                task.duration_minutes
                if task.duration_minutes is not None and task.duration_minutes > 0
                else scheduling.DEFAULT_DURATION_MINUTES
            )
            is_all_day = False

        updated = self.schedule_task(
            uid, start, duration_minutes=duration, is_all_day=is_all_day
        )
        if updated is None:
            return TaskOperationResult(errors=[INVALID_DURATION_ERROR])
        return TaskOperationResult(task=updated)

    def postpone_task(
        self, uid: str, action: str, now: Optional[datetime] = None
    ) -> TaskOperationResult:
        """Снуз/перенос задачи по действию меню (domain/scheduling.py).

        - «Без даты» идёт через unschedule_task (те же правила очереди);
        - экземпляры повторяющихся событий переносить запрещено — с
          человекочитаемой ошибкой, ничего не меняется и не ставится;
        - привязанная задача получает update, недопушенная — остаётся
          с create, недатированная при первом планировании — create
          (всё через record_local_update внутри update_task).
        """
        task = self.get_task(uid)
        if task is None:
            return TaskOperationResult(errors=[TASK_NOT_FOUND_ERROR])
        if action == scheduling.SNOOZE_UNSCHEDULE:
            return self.unschedule_task(uid)
        if task.google_calendar_recurring_event_id is not None:
            return TaskOperationResult(errors=[POSTPONE_RECURRING_ERROR])
        try:
            plan = scheduling.compute_postpone(
                action,
                start=task.start,
                is_all_day=task.is_all_day,
                duration_minutes=task.duration_minutes,
                now=now or datetime.now(),
            )
        except ValueError as exc:
            return TaskOperationResult(errors=[str(exc)])
        updated = self.schedule_task(
            uid,
            plan.start,
            duration_minutes=plan.duration_minutes,
            is_all_day=plan.is_all_day,
        )
        if updated is None:
            return TaskOperationResult(errors=[TASK_NOT_FOUND_ERROR])
        return TaskOperationResult(task=updated)

    def restore_task(self, uid: str) -> bool:
        """Вернуть выполненную задачу в работу (снять галочку).

        Локальная операция: как и complete, в Calendar-очередь ничего
        не ставится.
        """
        task = self.get_task(uid)
        if task is None or not task.completed or task.id is None:
            return False
        return self.repository.complete(task.id, False)

    def unschedule_task(self, uid: str) -> TaskOperationResult:
        """Запланирована -> без даты. Экземпляры повторяющихся серий — запрещены."""
        task = self.get_task(uid)
        if task is None:
            return TaskOperationResult(errors=[TASK_NOT_FOUND_ERROR])
        if task.start is None:
            return TaskOperationResult(task=task)  # уже без даты
        if task.google_calendar_recurring_event_id is not None:
            return TaskOperationResult(errors=[UNSCHEDULE_RECURRING_ERROR])
        self._detach_schedule(task)
        return TaskOperationResult(task=self.repository.update(task))

    def _detach_schedule(self, task: Task) -> None:
        """Снять расписание и привязку к календарю + правильные операции в очереди.

        Порядок важен: event_id читается ДО отвязки. Push-движок для delete
        берёт event_id из payload, поэтому отвязанная задача не мешает
        удалению события.
        """
        if self._queue is not None:
            if task.google_calendar_event_id is not None:
                self._queue.enqueue_delete(
                    task.uid, payload={"event_id": task.google_calendar_event_id}
                )
            else:
                # События ещё нет — просто снимаем недопушенный create/update.
                self._queue.cancel_pending_ops(task.uid)
        task.start = None
        task.end = None
        task.duration_minutes = None
        task.is_all_day = False
        task.google_calendar_event_id = None
        task.google_calendar_etag = None
        task.google_calendar_recurring_event_id = None
        task.google_calendar_original_start = None

    # ---- статистика очереди (для бейджей и настроек) -----------------------------

    @property
    def has_sync_queue(self) -> bool:
        return self._queue is not None

    @property
    def calendar_queue(self) -> Optional[CalendarSyncStore]:
        """Локальная очередь Calendar-операций (для ManualSyncService)."""
        return self._queue

    def count_pending_ops(self) -> int:
        return self._queue.count_pending_ops() if self._queue is not None else 0

    def count_terminal_ops(self) -> int:
        return self._queue.count_terminal_ops() if self._queue is not None else 0

    def pending_ops_breakdown(self) -> dict:
        """{'create': n, 'update': n, 'delete': n} ожидающих операций."""
        if self._queue is None:
            return {"create": 0, "update": 0, "delete": 0}
        return self._queue.count_pending_by_op()

    def last_local_change(self) -> Optional[datetime]:
        """Момент последнего локального изменения, ждущего синка (или None)."""
        return (
            self._queue.latest_pending_created_at()
            if self._queue is not None
            else None
        )

    def pending_task_uids(self) -> Set[str]:
        return self._queue.list_pending_uids() if self._queue is not None else set()

    def sync_cursor(self) -> Optional[str]:
        return self._queue.get_sync_cursor() if self._queue is not None else None

    def get_sync_state(self, key: str) -> Optional[str]:
        """Значение из desktop_sync_state (сводка последнего синка и т.п.)."""
        return self._queue.get_state(key) if self._queue is not None else None

    # ---- диагностика (для панели «Настройки») -----------------------------------

    def schema_version(self) -> int:
        getter = getattr(self.repository, "schema_version", None)
        return int(getter()) if callable(getter) else 0

    def count_active_tasks(self) -> int:
        getter = getattr(self.repository, "count_active", None)
        if callable(getter):
            return int(getter())
        return len(self.repository.list_all())
