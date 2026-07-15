"""Safe, deterministic bulk operations over ordinary desktop tasks."""
from __future__ import annotations

from dataclasses import dataclass, field
from copy import deepcopy
from datetime import datetime
from typing import Any, Iterable, List, Optional, Tuple

from planner_desktop.domain import scheduling
from planner_desktop.usecases.tag_service import TagService
from planner_desktop.usecases.task_service import DesktopTaskService


ACTION_COMPLETE = "complete"
ACTION_RESTORE = "restore"
ACTION_PRIORITY = "priority"
ACTION_ADD_TAG = "add_tag"
ACTION_REMOVE_TAG = "remove_tag"
ACTION_POSTPONE_TOMORROW = "postpone_tomorrow"
ACTION_UNSCHEDULE = "unschedule"
ACTION_DELETE = "delete"

STATUS_AFFECTED = "affected"
STATUS_SKIPPED = "skipped"
STATUS_FAILED = "failed"


@dataclass(frozen=True)
class BulkActionItemResult:
    uid: str
    status: str
    message: str = ""

    @property
    def affected(self) -> bool:
        return self.status == STATUS_AFFECTED


@dataclass(frozen=True)
class BulkActionResult:
    action: str
    items: Tuple[BulkActionItemResult, ...] = field(default_factory=tuple)
    busy_rejected: bool = False

    @property
    def affected_count(self) -> int:
        return sum(item.status == STATUS_AFFECTED for item in self.items)

    @property
    def skipped_count(self) -> int:
        return sum(item.status == STATUS_SKIPPED for item in self.items)

    @property
    def failed_count(self) -> int:
        return sum(item.status == STATUS_FAILED for item in self.items)

    @property
    def summary(self) -> str:
        if self.busy_rejected:
            return "Другая пакетная операция уже выполняется."
        return (
            f"Изменено: {self.affected_count}; "
            f"пропущено: {self.skipped_count}; "
            f"ошибок: {self.failed_count}."
        )


class BulkTaskService:
    """Runs a batch with one busy guard and per-task compensated mutations.

    Cross-connection repository/Calendar queue operations use the rollback
    already implemented by :class:`DesktopTaskService`. Independent tasks are
    reported separately: one failure never becomes a silent half-success and
    does not hide successful preceding items.
    """

    def __init__(
        self,
        task_service: DesktopTaskService,
        tag_service: Optional[TagService] = None,
    ) -> None:
        self.task_service = task_service
        self.tag_service = tag_service or getattr(task_service, "tag_service", None)
        self._busy = False

    @property
    def busy(self) -> bool:
        return self._busy

    def execute(
        self,
        action: str,
        task_uids: Iterable[str],
        value: Any = None,
        *,
        now: Optional[datetime] = None,
    ) -> BulkActionResult:
        ordered = self._ordered_unique(task_uids)
        if self._busy:
            return BulkActionResult(action=action, busy_rejected=True)
        self._busy = True
        try:
            items = tuple(
                self._apply_one(action, uid, value, now=now) for uid in ordered
            )
            return BulkActionResult(action=action, items=items)
        finally:
            self._busy = False

    @staticmethod
    def _ordered_unique(task_uids: Iterable[str]) -> Tuple[str, ...]:
        if isinstance(task_uids, (set, frozenset)):
            task_uids = sorted(task_uids)
        return tuple(dict.fromkeys(str(uid) for uid in task_uids if uid))

    def _apply_one(
        self, action: str, uid: str, value: Any, *, now: Optional[datetime]
    ) -> BulkActionItemResult:
        task = self.task_service.repository.get_by_uid(uid)
        if task is None or task.is_deleted:
            return BulkActionItemResult(uid, STATUS_SKIPPED, "Задача не найдена.")
        try:
            if action == ACTION_COMPLETE:
                if task.completed:
                    return BulkActionItemResult(uid, STATUS_SKIPPED, "Уже выполнена.")
                changed = self.task_service.complete_task(task.id, True)
            elif action == ACTION_RESTORE:
                if not task.completed:
                    return BulkActionItemResult(uid, STATUS_SKIPPED, "Уже активна.")
                changed = self.task_service.complete_task(task.id, False)
            elif action == ACTION_PRIORITY:
                previous_priority = task.priority
                result = self.task_service.set_priority(uid, int(value))
                if result.ok and result.task.priority == previous_priority:
                    return BulkActionItemResult(uid, STATUS_SKIPPED, "Приоритет не изменился.")
                if not result.ok:
                    return BulkActionItemResult(uid, STATUS_FAILED, " ".join(result.errors))
                changed = True
            elif action in (ACTION_ADD_TAG, ACTION_REMOVE_TAG):
                if self.tag_service is None:
                    return BulkActionItemResult(uid, STATUS_FAILED, "Сервис тегов недоступен.")
                changed = (
                    self.tag_service.add_tag(uid, int(value))
                    if action == ACTION_ADD_TAG
                    else self.tag_service.remove_tag(uid, int(value))
                )
                if not changed:
                    message = "Тег уже назначен." if action == ACTION_ADD_TAG else "Тег не назначен."
                    return BulkActionItemResult(uid, STATUS_SKIPPED, message)
            elif action == ACTION_POSTPONE_TOMORROW:
                if self.task_service._is_recurring_instance(task):
                    return BulkActionItemResult(
                        uid, STATUS_SKIPPED,
                        "Экземпляр повторяющегося события нельзя переносить.",
                    )
                if task.is_series_occurrence:
                    return BulkActionItemResult(
                        uid, STATUS_SKIPPED,
                        "Экземпляр локальной серии: измените расписание "
                        "через редактор серии.",
                    )
                result = self._compensated_schedule_call(
                    task,
                    lambda: self.task_service.postpone_task(
                        uid, scheduling.SNOOZE_TOMORROW, now=now
                    ),
                )
                if not result.ok:
                    return BulkActionItemResult(uid, STATUS_FAILED, " ".join(result.errors))
                changed = True
            elif action == ACTION_UNSCHEDULE:
                if task.start is None:
                    return BulkActionItemResult(uid, STATUS_SKIPPED, "Уже без даты.")
                if self.task_service._is_recurring_instance(task):
                    return BulkActionItemResult(
                        uid, STATUS_SKIPPED,
                        "Экземпляр повторяющегося события нельзя снять с расписания.",
                    )
                if task.is_series_occurrence:
                    return BulkActionItemResult(
                        uid, STATUS_SKIPPED,
                        "Экземпляр локальной серии: измените расписание "
                        "через редактор серии.",
                    )
                result = self._compensated_schedule_call(
                    task, lambda: self.task_service.unschedule_task(uid)
                )
                if not result.ok:
                    return BulkActionItemResult(uid, STATUS_FAILED, " ".join(result.errors))
                changed = True
            elif action == ACTION_DELETE:
                changed = self.task_service.delete_task_by_uid(uid)
            else:
                return BulkActionItemResult(uid, STATUS_FAILED, "Неизвестное действие.")

            return BulkActionItemResult(
                uid,
                STATUS_AFFECTED if changed else STATUS_FAILED,
                "" if changed else "Изменение не сохранено.",
            )
        except Exception as exc:
            return BulkActionItemResult(uid, STATUS_FAILED, str(exc))

    def _compensated_schedule_call(self, task, operation):
        """Rollback one task and its queue rows if a legacy path raises."""

        original = deepcopy(task)
        queue_snapshot = self.task_service._queue_snapshot(task.uid)
        try:
            return operation()
        except Exception:
            try:
                self.task_service._restore_repository_task(original)
            finally:
                self.task_service._restore_queue_snapshot(task.uid, queue_snapshot)
            raise


__all__ = [
    "ACTION_ADD_TAG",
    "ACTION_COMPLETE",
    "ACTION_DELETE",
    "ACTION_POSTPONE_TOMORROW",
    "ACTION_PRIORITY",
    "ACTION_REMOVE_TAG",
    "ACTION_RESTORE",
    "ACTION_UNSCHEDULE",
    "BulkActionItemResult",
    "BulkActionResult",
    "BulkTaskService",
]
