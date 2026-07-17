"""Сценарии локальных повторяющихся серий (Phase 3.2A).

Единственный слой, который превращает TaskSeries в материализованные
Task-строки и выполняет области правки «только этот» / «этот и все
будущие». Правила:

- НИ ОДНА операция серии не пишет в Calendar-очередь и не ходит в сеть:
  материализация и правки идут напрямую через репозиторий задач;
- материализация идемпотентна: занятые occurrence_key (живые, exception,
  выполненные, тумбстоуны) не пересоздаются;
- «только этот» помечает экземпляр exception и сохраняет ключ;
- «этот и все будущие» — атомарный SQLite split; для in-memory
  репозиториев есть компенсирующий fallback
  (см. docs/RECURRENCE_ARCHITECTURE.md);
- удаление/остановка не разрушают выполненную историю.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import date
from typing import Callable, List, Optional

from planner_desktop.domain.commands import (
    TaskEditorCommand,
    apply_editor_fields,
    schedule_from_command,
    validate_editor,
)
from planner_desktop.domain.recurrence import (
    MAX_OCCURRENCES_PER_CALL,
    RecurrenceRule,
    SeriesSchedule,
    TaskSeries,
    generate_occurrences,
    occurrence_key,
    replace_series,
    validate_rule,
)
from planner_desktop.domain.series_calendar_link import (
    LINKED_OCCURRENCE_CHANGE_ERROR,
    SeriesLinkStatus,
)
from planner_desktop.domain.task import Task
from planner_desktop.repositories import TaskRepository
from planner_desktop.repositories.series_repository import SeriesRepository

SERIES_NOT_FOUND_ERROR = "Серия не найдена (возможно, уже удалена)."
OCCURRENCE_NOT_FOUND_ERROR = "Экземпляр серии не найден."
NOT_A_SERIES_OCCURRENCE_ERROR = "Задача не является экземпляром локальной серии."
LINKED_TASK_SERIES_ERROR = (
    "Задача привязана к событию Google Calendar: превратить её в локальную "
    "серию в этой фазе нельзя. Создайте новую повторяющуюся задачу."
)
GOOGLE_RECURRING_ADOPT_ERROR = (
    "Это экземпляр повторяющегося события Google Calendar: локальная серия "
    "не может его принять. Управляйте серией в Google Calendar."
)
SPLIT_FAILED_ERROR = "Не удалось изменить серию"


def slot_date_from_key(key: str) -> Optional[date]:
    """Локальная дата слота из occurrence_key (первые 10 символов ISO)."""
    try:
        return date.fromisoformat(str(key)[:10])
    except (TypeError, ValueError):
        return None


@dataclass
class SeriesOperationResult:
    series: Optional[TaskSeries] = None
    errors: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.series is not None and not self.errors


@dataclass
class OccurrenceOperationResult:
    task: Optional[Task] = None
    errors: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.task is not None and not self.errors


@dataclass
class SeriesSplitResult:
    """Структурированный итог «этот и все будущие»."""

    old_series: Optional[TaskSeries] = None
    new_series: Optional[TaskSeries] = None
    moved_task: Optional[Task] = None
    replaced_count: int = 0
    errors: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.new_series is not None and not self.errors


@dataclass
class EnsureResult:
    """Итог материализации диапазона."""

    created: int = 0
    existing: int = 0
    skipped: int = 0

    def merge(self, other: "EnsureResult") -> None:
        self.created += other.created
        self.existing += other.existing
        self.skipped += other.skipped


class RecurrenceService:
    """CRUD серий + материализация + области правки. Без Calendar-очереди."""

    def __init__(
        self,
        series_repository: SeriesRepository,
        task_repository: TaskRepository,
        tag_service=None,
    ) -> None:
        self.series_repository = series_repository
        self.task_repository = task_repository
        self.tag_service = tag_service
        # Attached by MainWindow after the schema-v8 store is built.  Kept
        # optional so Phase 3.2A in-memory tests retain local-only behavior.
        self.series_link_service = None
        # Phase 3.2B3A: explicit conflict/remote-deleted resolution use cases.
        self.series_conflict_service = None
        # Phase 3.2B3B dedicated recurring-instance state/queue.  It remains
        # optional for in-memory Phase 3.2A tests.
        self.occurrence_sync_store = None
        #: Слушатели «серии изменились» (материализатор сбрасывает кэш).
        self._change_listeners: List[Callable[[], None]] = []

    # ---- события ---------------------------------------------------------------

    def add_change_listener(self, listener: Callable[[], None]) -> None:
        self._change_listeners.append(listener)

    def _notify_changed(self) -> None:
        for listener in self._change_listeners:
            listener()

    # ---- CRUD серий --------------------------------------------------------------

    def create_series(
        self,
        series: TaskSeries,
        tag_ids: Optional[List[int]] = None,
    ) -> SeriesOperationResult:
        errors = self._validate(series)
        if errors:
            return SeriesOperationResult(errors=errors)
        tag_errors = self._validate_tag_ids(tag_ids)
        if tag_errors:
            return SeriesOperationResult(errors=tag_errors)
        if not (series.title or "").strip():
            return SeriesOperationResult(
                errors=["Название задачи не может быть пустым."]
            )
        try:
            created = self.series_repository.add(series)
            if tag_ids is not None:
                self._apply_series_tags(created, tag_ids)
        except Exception as exc:
            return SeriesOperationResult(
                errors=[f"Не удалось создать серию: {exc}"]
            )
        self._notify_changed()
        return SeriesOperationResult(series=created)

    def update_series(
        self,
        uid: str,
        *,
        title: Optional[str] = None,
        notes: Optional[str] = None,
        priority: Optional[int] = None,
        schedule: Optional[SeriesSchedule] = None,
        rule: Optional[RecurrenceRule] = None,
        tag_ids: Optional[List[int]] = None,
    ) -> SeriesOperationResult:
        """Правка определения серии.

        Изменение расписания/правила поднимает ревизию и заменяет будущие
        невыполненные не-exception материализованные экземпляры (удаляет их;
        новые создаст материализатор). Выполненные и exception-строки
        не трогаются.
        """
        series = self.series_repository.get_by_uid(uid)
        if series is None or series.is_deleted:
            return SeriesOperationResult(errors=[SERIES_NOT_FOUND_ERROR])

        schedule_changed = schedule is not None and schedule != series.schedule
        rule_changed = rule is not None and rule != series.rule
        updated = replace_series(
            series,
            title=series.title if title is None else title.strip(),
            notes=series.notes if notes is None else notes.strip(),
            priority=series.priority if priority is None else int(priority),
            schedule=schedule or series.schedule,
            rule=rule or series.rule,
            revision=series.revision + (1 if schedule_changed or rule_changed else 0),
        )
        errors = self._validate(updated)
        if errors:
            return SeriesOperationResult(errors=errors)
        tag_errors = self._validate_tag_ids(tag_ids)
        if tag_errors:
            return SeriesOperationResult(errors=tag_errors)

        original = replace_series(series)
        removed: List[Task] = []
        try:
            self.series_repository.update(updated)
            if tag_ids is not None:
                self._apply_series_tags(updated, tag_ids)
            if schedule_changed or rule_changed:
                removed = self._remove_replaceable_occurrences(
                    uid, from_slot=None
                )
            if self.series_link_service is not None:
                self.series_link_service.on_series_updated(original, updated)
        except Exception as exc:
            self._restore_series(original)
            self._restore_tasks(removed)
            return SeriesOperationResult(
                errors=[f"Не удалось изменить серию: {exc}"]
            )
        self._notify_changed()
        return SeriesOperationResult(series=updated)

    def get_series(self, uid: str) -> Optional[TaskSeries]:
        series = self.series_repository.get_by_uid(uid)
        if series is None or series.is_deleted:
            return None
        return series

    def list_series(self, include_inactive: bool = False) -> List[TaskSeries]:
        return self.series_repository.list_all(include_inactive)

    def stop_series(
        self, uid: str, from_date: Optional[date] = None
    ) -> SeriesOperationResult:
        """Остановить генерацию: серия завершается ПЕРЕД from_date (по
        умолчанию — сегодня), будущие невыполненные не-exception экземпляры
        удаляются, история сохраняется."""
        series = self.series_repository.get_by_uid(uid)
        if series is None or series.is_deleted:
            return SeriesOperationResult(errors=[SERIES_NOT_FOUND_ERROR])
        boundary = from_date or date.today()
        stopped = series.with_end_before(boundary)
        if stopped.rule.until_date is not None and (
            stopped.rule.until_date < series.schedule.start_date
        ):
            stopped = replace_series(stopped, active=False)
        original = replace_series(series)
        removed: List[Task] = []
        try:
            self.series_repository.update(stopped)
            removed = self._remove_replaceable_occurrences(
                uid, from_slot=boundary
            )
            if self.series_link_service is not None:
                self.series_link_service.on_series_updated(original, stopped)
        except Exception as exc:
            self._restore_series(original)
            self._restore_tasks(removed)
            return SeriesOperationResult(
                errors=[f"Не удалось остановить серию: {exc}"]
            )
        self._notify_changed()
        return SeriesOperationResult(series=stopped)

    def delete_series(self, uid: str) -> SeriesOperationResult:
        """Тумбстоун серии. Невыполненные не-exception экземпляры удаляются;
        выполненная история и exception-строки остаются навсегда."""
        series = self.series_repository.get_by_uid(uid)
        if series is None or series.is_deleted:
            return SeriesOperationResult(errors=[SERIES_NOT_FOUND_ERROR])
        if (
            self.series_link_service is not None
            and self.series_link_service.is_linked(uid)
        ):
            from planner_desktop.usecases.series_calendar_link_service import (
                SERIES_DELETE_REQUIRES_INTENT,
            )
            return SeriesOperationResult(errors=[SERIES_DELETE_REQUIRES_INTENT])
        original = replace_series(series)
        removed: List[Task] = []
        try:
            removed = self._remove_replaceable_occurrences(uid, from_slot=None)
            if not self.series_repository.delete(uid):
                raise RuntimeError("Серия уже удалена")
        except Exception as exc:
            self._restore_series(original)
            self._restore_tasks(removed)
            return SeriesOperationResult(
                errors=[f"Не удалось удалить серию: {exc}"]
            )
        self._notify_changed()
        return SeriesOperationResult(series=series)

    def duplicate_series(self, uid: str) -> SeriesOperationResult:
        """Независимая копия ОПРЕДЕЛЕНИЯ серии (экземпляры не копируются —
        их создаст материализация)."""
        series = self.get_series(uid)
        if series is None:
            return SeriesOperationResult(errors=[SERIES_NOT_FOUND_ERROR])
        clone = TaskSeries(
            title=series.title,
            schedule=series.schedule,
            rule=series.rule,
            notes=series.notes,
            priority=series.priority,
            tags=tuple(series.tags),
        )
        tag_ids = list(self.series_repository.tag_ids_for_series(uid))
        return self.create_series(clone, tag_ids=tag_ids)

    # ---- материализация ------------------------------------------------------------

    def ensure_occurrences(
        self,
        range_start: date,
        range_end: date,
        *,
        series_uid: Optional[str] = None,
        limit_per_series: int = MAX_OCCURRENCES_PER_CALL,
    ) -> EnsureResult:
        """Идемпотентно материализует экземпляры всех активных серий
        в [range_start, range_end]. Никогда не трогает существующие строки
        и не ставит Calendar-операций."""
        result = EnsureResult()
        if range_end < range_start:
            return result
        series_list = (
            [self.get_series(series_uid)] if series_uid
            else self.list_series()
        )
        for series in series_list:
            if series is None or not series.active or series.is_deleted:
                continue
            result.merge(
                self._ensure_series(
                    series, range_start, range_end, limit_per_series
                )
            )
        if result.created:
            self._notify_changed()
        return result

    def _ensure_series(
        self,
        series: TaskSeries,
        range_start: date,
        range_end: date,
        limit: int,
    ) -> EnsureResult:
        result = EnsureResult()
        specs = generate_occurrences(
            series.schedule, series.rule, range_start, range_end, limit=limit
        )
        if not specs:
            return result
        rows = self.task_repository.list_by_series(series.uid)
        by_key = {row.occurrence_key: row for row in rows}
        series_tag_ids = list(
            self.series_repository.tag_ids_for_series(series.uid)
        )
        for spec in specs:
            existing = by_key.get(spec.occurrence_key)
            if existing is not None:
                if existing.is_deleted:
                    result.skipped += 1  # тумбстоун защищает слот
                else:
                    result.existing += 1
                continue
            task = Task(
                title=series.title,
                notes=series.notes,
                tags=tuple(series.tags),
                start=spec.start,
                end=spec.end,
                duration_minutes=(
                    None if spec.all_day else series.schedule.duration_minutes
                    or int((spec.end - spec.start).total_seconds() // 60)
                ),
                is_all_day=spec.all_day,
                priority=series.priority,
                series_uid=series.uid,
                occurrence_key=spec.occurrence_key,
                series_revision=series.revision,
            )
            created = self.task_repository.add(task)
            if series_tag_ids and self.tag_service is not None:
                try:
                    self.tag_service.set_task_tags(created.uid, series_tag_ids)
                except Exception:
                    # Do not leave an untagged occurrence occupying the key:
                    # the next bounded materialization must be able to retry.
                    self.task_repository.hard_delete_by_uid(created.uid)
                    raise
            result.created += 1
        return result

    # ---- области правки ---------------------------------------------------------------

    def edit_occurrence(
        self,
        uid: str,
        command: TaskEditorCommand,
        *,
        tag_ids: Optional[List[int]] = None,
    ) -> OccurrenceOperationResult:
        """«Только этот»: правка одной Task-строки + пометка exception.

        occurrence_key и series_uid сохраняются; Calendar-очередь не
        участвует. Регенерация exception не перезапишет: ключ занят.
        """
        task = self.task_repository.get_by_uid(uid)
        if task is None or task.is_deleted:
            return OccurrenceOperationResult(errors=[OCCURRENCE_NOT_FOUND_ERROR])
        if task.series_uid is None:
            return OccurrenceOperationResult(
                errors=[NOT_A_SERIES_OCCURRENCE_ERROR]
            )
        linked = bool(
            self.series_link_service is not None
            and self.series_link_service.is_linked(task.series_uid)
        )
        series = self.series_repository.get_by_uid(task.series_uid)
        if series is None or series.is_deleted:
            return OccurrenceOperationResult(errors=[SERIES_NOT_FOUND_ERROR])
        series_link = (
            self.series_link_service.get_link(task.series_uid)
            if linked else None
        )
        if linked and (
            self.occurrence_sync_store is None
            or series_link is None
            or series_link.link_status in (
                SeriesLinkStatus.DETACHED,
                SeriesLinkStatus.REMOTE_DELETED,
                SeriesLinkStatus.TERMINAL_ERROR,
            )
        ):
            return OccurrenceOperationResult(errors=[LINKED_OCCURRENCE_CHANGE_ERROR])
        errors = validate_editor(command)
        if errors:
            return OccurrenceOperationResult(errors=errors)
        tag_errors = self._validate_tag_ids(tag_ids)
        if tag_errors:
            return OccurrenceOperationResult(errors=tag_errors)
        original = deepcopy(task)
        original_tag_ids = self._task_tag_ids(uid)
        try:
            if linked and not command.add_to_calendar:
                raise ValueError(
                    "Экземпляр связанной серии нельзя снять с расписания; "
                    "используйте отмену только этого экземпляра."
                )
            apply_editor_fields(command, task)
            if command.add_to_calendar:
                start, end, duration, is_all_day = schedule_from_command(command)
                task.start = start
                task.end = end
                task.duration_minutes = duration
                task.is_all_day = is_all_day
            if linked and task.is_all_day != series.schedule.all_day:
                raise ValueError(
                    "Преобразование отдельного экземпляра между событием на "
                    "весь день и событием со временем отложено до Phase 3.2B3C."
                )
            task.is_series_exception = True
            updated = self.task_repository.update(task)
            if tag_ids is not None and self.tag_service is not None:
                self.tag_service.set_task_tags(updated.uid, tag_ids)
            if linked:
                from planner_desktop.domain.google_occurrence import (
                    canonical_occurrence_payload_fingerprint,
                    local_occurrence_to_google_original_start,
                )
                from planner_desktop.sync.calendar_series_occurrence_mapper import (
                    build_desired_occurrence_payload,
                )

                identity = local_occurrence_to_google_original_start(
                    series, str(updated.occurrence_key)
                )
                occurrence_link = self.occurrence_sync_store.ensure_occurrence_link(
                    series.uid,
                    str(updated.occurrence_key),
                    series_link,
                    identity,
                )
                desired = build_desired_occurrence_payload(
                    updated, series, series_link.link_generation
                )
                before = build_desired_occurrence_payload(
                    original, series, series_link.link_generation
                )
                desired_hash = canonical_occurrence_payload_fingerprint(desired)
                before_hash = canonical_occurrence_payload_fingerprint(before)
                if desired_hash != before_hash:
                    self.occurrence_sync_store.enqueue_update(
                        series.uid,
                        str(updated.occurrence_key),
                        desired,
                        desired_payload_hash=desired_hash,
                        allow_cancelled_restore=bool(original.is_deleted),
                    )
        except Exception as exc:
            self._restore_tasks([original])
            self._restore_task_tags(uid, original_tag_ids)
            return OccurrenceOperationResult(
                errors=[f"Не удалось изменить экземпляр: {exc}"]
            )
        return OccurrenceOperationResult(task=updated)

    def edit_this_and_future(
        self,
        uid: str,
        command: TaskEditorCommand,
        rule: Optional[RecurrenceRule] = None,
        timezone_name: Optional[str] = None,
        tag_ids: Optional[List[int]] = None,
    ) -> SeriesSplitResult:
        """«Этот и все будущие»: split серии по исходному слоту экземпляра.

        Транзакционно через компенсацию: при любой ошибке серия, экземпляры
        и связи тегов восстанавливаются, возвращается структурированная
        ошибка. Прошлое (выполненные, exception) не трогается.
        """
        task = self.task_repository.get_by_uid(uid)
        if task is None or task.is_deleted:
            return SeriesSplitResult(errors=[OCCURRENCE_NOT_FOUND_ERROR])
        if task.series_uid is None:
            return SeriesSplitResult(errors=[NOT_A_SERIES_OCCURRENCE_ERROR])
        series = self.series_repository.get_by_uid(task.series_uid)
        if series is None or series.is_deleted:
            return SeriesSplitResult(errors=[SERIES_NOT_FOUND_ERROR])
        if (
            self.series_link_service is not None
            and self.series_link_service.is_linked(series.uid)
        ):
            return SeriesSplitResult(errors=[LINKED_OCCURRENCE_CHANGE_ERROR])
        errors = validate_editor(command)
        if errors:
            return SeriesSplitResult(errors=errors)
        tag_errors = self._validate_tag_ids(tag_ids)
        if tag_errors:
            return SeriesSplitResult(errors=tag_errors)
        if not command.add_to_calendar:
            return SeriesSplitResult(
                errors=["У повторяющейся серии должна быть дата начала."]
            )

        boundary = slot_date_from_key(task.occurrence_key)
        if boundary is None:
            return SeriesSplitResult(
                errors=["Не удалось определить исходный слот экземпляра."]
            )

        start, end, duration, is_all_day = schedule_from_command(command)
        new_schedule = SeriesSchedule(
            start_date=start.date(),
            all_day=is_all_day,
            local_time=None if is_all_day else start.time(),
            duration_minutes=duration,
            timezone_name=timezone_name or series.schedule.timezone_name,
        )
        new_rule = rule or series.rule
        # Новый до-раскола until в прошлом не имеет смысла для новой серии.
        new_series = TaskSeries(
            title=command.title.strip(),
            schedule=new_schedule,
            rule=new_rule,
            notes=command.notes.strip(),
            priority=int(command.priority),
            tags=tuple(series.tags),
        )
        validation = self._validate(new_series)
        if validation:
            return SeriesSplitResult(errors=validation)

        truncated = series.with_end_before(boundary)
        if truncated.rule.until_date is not None and (
            truncated.rule.until_date < series.schedule.start_date
        ):
            truncated = replace_series(truncated, active=False)

        original_series = replace_series(series)
        original_task = deepcopy(task)
        original_task_tag_ids = self._task_tag_ids(task.uid)
        requested_tag_ids = list(tag_ids) if tag_ids is not None else None
        series_tag_ids = list(
            self.series_repository.tag_ids_for_series(series.uid)
        )
        removed = self._replaceable_occurrences(
            series.uid, from_slot=boundary, exclude_uid=task.uid
        )

        moved_candidate = deepcopy(task)
        apply_editor_fields(command, moved_candidate)
        moved_candidate.start = start
        moved_candidate.end = end
        moved_candidate.duration_minutes = duration
        moved_candidate.is_all_day = is_all_day
        moved_candidate.series_uid = new_series.uid
        moved_candidate.occurrence_key = occurrence_key(
            new_schedule, new_schedule.start_date
        )
        moved_candidate.series_revision = new_series.revision
        moved_candidate.is_series_exception = False
        if requested_tag_ids is not None and self.tag_service is not None:
            resolved = self.tag_service.resolve_tag_ids(requested_tag_ids)
            moved_candidate.tags = tuple(item.name for item in resolved)

        # SQLite gets a real storage transaction spanning both series rows,
        # the selected Task, future replacements and tag associations.
        atomic_split = getattr(
            self.series_repository, "split_series_atomic", None
        )
        if callable(atomic_split):
            try:
                created, moved = atomic_split(
                    truncated=truncated,
                    new_series=new_series,
                    moved_task=moved_candidate,
                    removed_task_uids=[item.uid for item in removed],
                    series_tag_ids=series_tag_ids,
                    moved_task_tag_ids=requested_tag_ids,
                )
            except Exception as exc:
                return SeriesSplitResult(
                    errors=[f"{SPLIT_FAILED_ERROR}: {exc}"]
                )
            self._notify_changed()
            return SeriesSplitResult(
                old_series=truncated,
                new_series=created,
                moved_task=moved,
                replaced_count=len(removed),
            )

        removed = []
        created_series_uid: Optional[str] = None
        try:
            self.series_repository.update(truncated)
            created = self.series_repository.add(new_series)
            created_series_uid = created.uid
            if series_tag_ids:
                self.series_repository.set_series_tags(
                    created.uid, series_tag_ids
                )
            # Будущие невыполненные не-exception экземпляры старой серии
            # (кроме выбранного) удаляются — новые создаст материализация.
            removed = self._remove_replaceable_occurrences(
                series.uid, from_slot=boundary, exclude_uid=task.uid
            )
            # Выбранный экземпляр переходит к новой серии.
            # The in-memory adapter stores object references while SQLite
            # stores detached rows. Mutating the fetched entity before the
            # repository call keeps both adapters on the same contract.
            task.__dict__.update(deepcopy(moved_candidate.__dict__))
            moved = self.task_repository.update(task)
            if requested_tag_ids is not None and self.tag_service is not None:
                self.tag_service.set_task_tags(moved.uid, requested_tag_ids)
        except Exception as exc:
            rollback_errors = self._rollback_split(
                original_series,
                original_task,
                removed,
                created_series_uid,
                original_task_tag_ids,
            )
            message = f"{SPLIT_FAILED_ERROR}: {exc}"
            if rollback_errors:
                message += " (ошибка отката: " + "; ".join(rollback_errors) + ")"
            return SeriesSplitResult(errors=[message])
        self._notify_changed()
        return SeriesSplitResult(
            old_series=truncated,
            new_series=created,
            moved_task=moved,
            replaced_count=len(removed),
        )

    def stop_this_and_future(self, uid: str) -> SeriesOperationResult:
        """Завершить серию ПЕРЕД исходным слотом выбранного экземпляра."""
        task = self.task_repository.get_by_uid(uid)
        if task is None or task.series_uid is None:
            return SeriesOperationResult(errors=[OCCURRENCE_NOT_FOUND_ERROR])
        if (
            self.series_link_service is not None
            and self.series_link_service.is_linked(task.series_uid)
        ):
            return SeriesOperationResult(errors=[LINKED_OCCURRENCE_CHANGE_ERROR])
        boundary = slot_date_from_key(task.occurrence_key)
        if boundary is None:
            return SeriesOperationResult(
                errors=["Не удалось определить исходный слот экземпляра."]
            )
        return self.stop_series(task.series_uid, from_date=boundary)

    def delete_occurrence(self, uid: str) -> bool:
        """Тумбстоун одного экземпляра: слот защищён от регенерации,
        Calendar-очередь не участвует."""
        task = self.task_repository.get_by_uid(uid)
        if task is None or task.is_deleted or task.series_uid is None:
            return False
        linked = bool(
            self.series_link_service is not None
            and self.series_link_service.is_linked(task.series_uid)
        )
        if not linked:
            return self.task_repository.delete(task.id)
        if self.occurrence_sync_store is None:
            return False
        series = self.series_repository.get_by_uid(task.series_uid)
        series_link = self.series_link_service.get_link(task.series_uid)
        if series is None or series_link is None or task.id is None:
            return False
        from planner_desktop.domain.google_occurrence import (
            local_occurrence_to_google_original_start,
        )
        from planner_desktop.sync.calendar_series_occurrence_mapper import (
            build_desired_occurrence_payload,
        )

        snapshot = deepcopy(task)
        try:
            identity = local_occurrence_to_google_original_start(
                series, str(task.occurrence_key)
            )
            self.occurrence_sync_store.ensure_occurrence_link(
                series.uid, str(task.occurrence_key), series_link, identity
            )
            payload = build_desired_occurrence_payload(
                task, series, series_link.link_generation
            )
            if not self.task_repository.delete(task.id):
                return False
            self.occurrence_sync_store.enqueue_cancel(
                series.uid, str(task.occurrence_key), payload
            )
        except Exception:
            self._restore_tasks([snapshot])
            return False
        return True

    # ---- статистика для настроек --------------------------------------------------------

    def diagnostics(self) -> dict:
        series_list = self.list_series(include_inactive=True)
        occurrences = 0
        exceptions = 0
        for series in series_list:
            rows = [
                row for row in self.task_repository.list_by_series(series.uid)
                if not row.is_deleted
            ]
            occurrences += len(rows)
            exceptions += sum(1 for row in rows if row.is_series_exception)
        return {
            "active_series": sum(1 for s in series_list if s.active),
            "occurrences": occurrences,
            "exceptions": exceptions,
        }

    # ---- внутреннее ------------------------------------------------------------------------

    @staticmethod
    def _validate(series: TaskSeries) -> List[str]:
        return list(validate_rule(series.rule, series.schedule).errors)

    def _apply_series_tags(
        self, series: TaskSeries, tag_ids: List[int]
    ) -> None:
        if self.tag_service is not None:
            tags = self.tag_service.resolve_tag_ids(tag_ids)
            series.tags = tuple(tag.name for tag in tags)
        self.series_repository.set_series_tags(series.uid, list(tag_ids))

    def _validate_tag_ids(self, tag_ids: Optional[List[int]]) -> List[str]:
        if not tag_ids:
            return []
        if self.tag_service is None:
            return ["Сервис тегов недоступен."]
        try:
            self.tag_service.resolve_tag_ids(tag_ids)
        except Exception as exc:
            return [str(exc)]
        return []

    def _task_tag_ids(self, uid: str) -> Optional[List[int]]:
        if self.tag_service is None:
            return None
        return [
            int(tag.id)
            for tag in self.tag_service.tags_for_task(uid)
            if tag.id is not None
        ]

    def _restore_task_tags(
        self, uid: str, tag_ids: Optional[List[int]]
    ) -> None:
        if self.tag_service is None or tag_ids is None:
            return
        try:
            self.tag_service.set_task_tags(uid, tag_ids)
        except Exception:
            pass

    def _remove_replaceable_occurrences(
        self,
        series_uid: str,
        *,
        from_slot: Optional[date],
        exclude_uid: Optional[str] = None,
    ) -> List[Task]:
        """Физически удаляет заменяемые экземпляры: живые, невыполненные,
        не-exception (и не раньше from_slot, если он задан). Возвращает
        снимки удалённых строк для компенсации."""
        removed: List[Task] = []
        for row in self._replaceable_occurrences(
            series_uid,
            from_slot=from_slot,
            exclude_uid=exclude_uid,
        ):
            snapshot = deepcopy(row)
            if self.task_repository.hard_delete_by_uid(row.uid):
                removed.append(snapshot)
        return removed

    def _replaceable_occurrences(
        self,
        series_uid: str,
        *,
        from_slot: Optional[date],
        exclude_uid: Optional[str] = None,
    ) -> List[Task]:
        """Return future live, uncompleted, non-exception rows eligible for
        replacement.  The read-only form is used to plan an atomic SQLite
        split; ``_remove_replaceable_occurrences`` applies the same policy for
        in-memory repositories and other adapters.
        """
        result: List[Task] = []
        for row in self.task_repository.list_by_series(series_uid):
            if row.is_deleted or row.completed or row.is_series_exception:
                continue
            if exclude_uid is not None and row.uid == exclude_uid:
                continue
            if from_slot is not None:
                slot = slot_date_from_key(row.occurrence_key)
                if slot is None or slot < from_slot:
                    continue
            result.append(row)
        return result

    def _restore_series(self, snapshot: TaskSeries) -> None:
        try:
            self.series_repository.update(replace_series(snapshot))
        except Exception:
            pass

    def _restore_tasks(self, snapshots: List[Task]) -> None:
        for snapshot in snapshots:
            try:
                current = self.task_repository.get_by_uid(snapshot.uid)
                if current is None:
                    self.task_repository.add(deepcopy(snapshot))
                else:
                    restored = deepcopy(snapshot)
                    restored.id = current.id
                    self.task_repository.update(restored)
            except Exception:
                continue

    def _rollback_split(
        self,
        original_series: TaskSeries,
        original_task: Task,
        removed: List[Task],
        created_series_uid: Optional[str],
        original_task_tag_ids: Optional[List[int]] = None,
    ) -> List[str]:
        errors: List[str] = []
        try:
            self.series_repository.update(replace_series(original_series))
        except Exception as exc:
            errors.append(str(exc))
        self._restore_tasks(removed + [original_task])
        self._restore_task_tags(original_task.uid, original_task_tag_ids)
        if created_series_uid is not None:
            try:
                self.series_repository.delete(created_series_uid)
            except Exception as exc:
                errors.append(str(exc))
        return errors


__all__ = [
    "EnsureResult",
    "GOOGLE_RECURRING_ADOPT_ERROR",
    "LINKED_TASK_SERIES_ERROR",
    "NOT_A_SERIES_OCCURRENCE_ERROR",
    "OCCURRENCE_NOT_FOUND_ERROR",
    "OccurrenceOperationResult",
    "RecurrenceService",
    "SERIES_NOT_FOUND_ERROR",
    "SeriesOperationResult",
    "SeriesSplitResult",
    "slot_date_from_key",
]
