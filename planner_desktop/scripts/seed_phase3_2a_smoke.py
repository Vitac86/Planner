"""Seed and exercise the isolated Phase 3.2A recurrence smoke profile.

The helper requires ``PLANNER_DESKTOP_DATA_DIR``. It never imports a Google
gateway, never starts manual sync, and does not read the legacy app.db/token.
It is idempotent so restart persistence can be checked against the same data.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
import os

from planner_desktop.domain.commands import TaskEditorCommand
from planner_desktop.domain.recurrence import (
    RecurrenceEndMode,
    RecurrenceFrequency,
    RecurrenceRule,
    SeriesSchedule,
    TaskSeries,
)
from planner_desktop.domain.task import Task
from planner_desktop.domain.templates import (
    SCHEDULE_MODE_ALL_DAY,
    SCHEDULE_MODE_NONE,
    TEMPLATE_KIND_ORDINARY,
    TEMPLATE_KIND_RECURRING,
    TaskTemplate,
)
from planner_desktop.storage.calendar_sync_store import CalendarSyncStore
from planner_desktop.storage.paths import get_desktop_db_path
from planner_desktop.storage.series_repository import SQLiteSeriesRepository
from planner_desktop.storage.sqlite_task_repository import SQLiteTaskRepository
from planner_desktop.storage.tag_repository import SQLiteTagRepository
from planner_desktop.storage.template_repository import SQLiteTemplateRepository
from planner_desktop.usecases.recurrence_service import RecurrenceService
from planner_desktop.usecases.tag_service import TagService
from planner_desktop.usecases.task_service import DesktopTaskService
from planner_desktop.usecases.template_service import TemplateService


START = date(2026, 7, 1)
RANGE_END = date(2026, 8, 31)
ZONE = "Europe/Moscow"


def _schedule(*, all_day: bool, at: time | None = None, minutes=45):
    return SeriesSchedule(
        start_date=START,
        all_day=all_day,
        local_time=None if all_day else at,
        duration_minutes=None if all_day else minutes,
        timezone_name=ZONE,
    )


def _command(day: int, hour: int, title: str) -> TaskEditorCommand:
    return TaskEditorCommand(
        title=title,
        notes="Проверка явной области изменений Phase 3.2A",
        add_to_calendar=True,
        is_all_day=False,
        date_text=f"2026-07-{day:02d}",
        time_text=f"{hour:02d}:30",
        duration_text="45",
        priority=2,
    )


def _row_for(tasks, series_uid: str, day: str):
    return next(
        row for row in tasks.list_by_series(series_uid)
        if row.occurrence_key and row.occurrence_key.startswith(day)
    )


def main() -> int:
    if not os.environ.get("PLANNER_DESKTOP_DATA_DIR"):
        raise SystemExit(
            "Set PLANNER_DESKTOP_DATA_DIR to an isolated smoke directory first."
        )

    db_path = get_desktop_db_path()
    tasks = SQLiteTaskRepository(db_path)
    queue = CalendarSyncStore(db_path)
    series_repository = SQLiteSeriesRepository(db_path)
    tag_repository = SQLiteTagRepository(db_path)
    template_repository = SQLiteTemplateRepository(db_path)
    tags = TagService(tag_repository, tasks)
    recurrence = RecurrenceService(series_repository, tasks, tag_service=tags)
    templates = TemplateService(template_repository, tag_service=tags)
    task_service = DesktopTaskService(tasks, calendar_queue=queue, tag_service=tags)

    work = tags.get_or_create("Работа")
    focus = tags.get_or_create("Фокус")
    personal = tags.get_or_create("Личное")
    tag_ids = [work.id, focus.id]
    pending_before_local = queue.count_pending_ops()

    definitions = [
        TaskSeries(
            uid="smoke-series-daily",
            title="Ежедневный фокус",
            notes="Timed daily series",
            priority=3,
            schedule=_schedule(all_day=False, at=time(9), minutes=60),
            rule=RecurrenceRule(RecurrenceFrequency.DAILY),
        ),
        TaskSeries(
            uid="smoke-series-weekdays",
            title="Проверка входящих по будням",
            schedule=_schedule(all_day=False, at=time(11), minutes=30),
            rule=RecurrenceRule(
                RecurrenceFrequency.WEEKLY, weekdays=(0, 1, 2, 3, 4)
            ),
        ),
        TaskSeries(
            uid="smoke-series-multiday",
            title="Командный ритм Вт/Чт",
            schedule=_schedule(all_day=False, at=time(14), minutes=45),
            rule=RecurrenceRule(
                RecurrenceFrequency.WEEKLY, weekdays=(1, 3)
            ),
        ),
        TaskSeries(
            uid="smoke-series-monthly31",
            title="Закрытие месяца 31 числа",
            schedule=_schedule(all_day=True),
            rule=RecurrenceRule(
                RecurrenceFrequency.MONTHLY, month_day=31
            ),
        ),
        TaskSeries(
            uid="smoke-series-yearly",
            title="Годовщина проекта",
            schedule=SeriesSchedule(
                date(2026, 7, 15), True, timezone_name=ZONE
            ),
            rule=RecurrenceRule(
                RecurrenceFrequency.YEARLY,
                yearly_month=7,
                yearly_day=15,
            ),
        ),
        TaskSeries(
            uid="smoke-series-until",
            title="Серия до даты",
            schedule=_schedule(all_day=False, at=time(16), minutes=30),
            rule=RecurrenceRule(
                RecurrenceFrequency.DAILY,
                interval=2,
                end_mode=RecurrenceEndMode.UNTIL,
                until_date=date(2026, 7, 21),
            ),
        ),
        TaskSeries(
            uid="smoke-series-count",
            title="Серия из пяти повторений",
            schedule=SeriesSchedule(
                date(2026, 7, 13), False, time(17), 30, ZONE
            ),
            rule=RecurrenceRule(
                RecurrenceFrequency.DAILY,
                end_mode=RecurrenceEndMode.COUNT,
                occurrence_count=5,
            ),
        ),
    ]
    for item in definitions:
        if series_repository.get_by_uid(item.uid) is None:
            result = recurrence.create_series(
                item,
                tag_ids=(tag_ids if item.uid == "smoke-series-daily"
                         else [personal.id]),
            )
            if not result.ok:
                raise RuntimeError(result.errors)

    recurrence.ensure_occurrences(START, RANGE_END)

    daily_exception = _row_for(
        tasks, "smoke-series-daily", "2026-07-15"
    )
    if not daily_exception.is_series_exception:
        result = recurrence.edit_occurrence(
            daily_exception.uid,
            _command(15, 10, "Ежедневный фокус — исключение"),
            tag_ids=[work.id, personal.id],
        )
        if not result.ok:
            raise RuntimeError(result.errors)

    deleted = _row_for(
        tasks, "smoke-series-weekdays", "2026-07-16"
    )
    if not deleted.is_deleted and not recurrence.delete_occurrence(deleted.uid):
        raise RuntimeError("Could not create deleted-occurrence tombstone")

    split_source = series_repository.get_by_uid("smoke-series-multiday")
    if split_source is not None and split_source.rule.end_mode == RecurrenceEndMode.NEVER:
        selected = _row_for(
            tasks, "smoke-series-multiday", "2026-07-16"
        )
        result = recurrence.edit_this_and_future(
            selected.uid,
            _command(16, 15, "Командный ритм после разделения"),
            rule=RecurrenceRule(
                RecurrenceFrequency.WEEKLY, weekdays=(3,)
            ),
            tag_ids=[work.id],
        )
        if not result.ok:
            raise RuntimeError(result.errors)

    completed = _row_for(tasks, "smoke-series-count", "2026-07-14")
    if not completed.completed:
        tasks.complete(completed.id, True)

    if template_repository.get_by_normalized_name("обычная задача") is None:
        result = templates.create_template(
            TaskTemplate(
                uid="smoke-template-ordinary",
                name="Обычная задача",
                kind=TEMPLATE_KIND_ORDINARY,
                title="Подготовить краткий отчёт",
                notes="Локальный шаблон без Google-метаданных",
                priority=2,
                schedule_mode=SCHEDULE_MODE_NONE,
            ),
            tag_ids=[work.id],
        )
        if not result.ok:
            raise RuntimeError(result.errors)
    if template_repository.get_by_normalized_name("утренняя серия") is None:
        result = templates.create_template(
            TaskTemplate(
                uid="smoke-template-recurring",
                name="Утренняя серия",
                kind=TEMPLATE_KIND_RECURRING,
                title="Утренний обзор",
                priority=1,
                schedule_mode=SCHEDULE_MODE_ALL_DAY,
                rule=RecurrenceRule(RecurrenceFrequency.DAILY),
            ),
            tag_ids=[focus.id],
        )
        if not result.ok:
            raise RuntimeError(result.errors)

    if tasks.get_by_uid("smoke-google-recurring") is None:
        tasks.add(Task(
            uid="smoke-google-recurring",
            title="Синтетический экземпляр Google-серии",
            start=datetime(2026, 7, 15, 18),
            end=datetime(2026, 7, 15, 18, 30),
            duration_minutes=30,
            google_calendar_event_id="synthetic-instance",
            google_calendar_recurring_event_id="synthetic-series",
            google_calendar_original_start=datetime(
                2026, 7, 15, 18, tzinfo=timezone.utc
            ),
        ))

    local_after = queue.count_pending_ops()
    if local_after != pending_before_local:
        raise RuntimeError(
            f"Local-series queue delta is {local_after - pending_before_local}, expected 0"
        )

    # Exercise the existing ordinary-task drag path once. Unlike local series,
    # this correctly creates one ordinary Calendar update operation.
    ordinary = tasks.get_by_uid("smoke-ordinary-drag")
    if ordinary is None:
        ordinary = tasks.add(Task(
            uid="smoke-ordinary-drag",
            title="Обычная задача для drag",
            start=datetime(2026, 7, 15, 12),
            end=datetime(2026, 7, 15, 12, 30),
            duration_minutes=30,
            google_calendar_event_id="synthetic-ordinary-event",
        ))
        moved = task_service.move_timed_task(
            ordinary.uid,
            datetime(2026, 7, 15, 12, 15),
            end=datetime(2026, 7, 15, 12, 45),
        )
        if not moved.ok:
            raise RuntimeError(moved.errors)

    # A direct local-series drag remains refused before repository/queue write.
    exception = tasks.get_by_uid(daily_exception.uid)
    local_drag = task_service.move_timed_task(
        exception.uid,
        exception.start + timedelta(minutes=15),
        end=exception.end + timedelta(minutes=15),
    )
    if local_drag.ok:
        raise RuntimeError("Local-series drag unexpectedly succeeded")

    recurrence.ensure_occurrences(START, RANGE_END)
    if not tasks.get_by_uid(deleted.uid).is_deleted:
        raise RuntimeError("Deleted occurrence was regenerated")

    print(f"db={db_path}")
    print(f"schema_version={tasks.schema_version()}")
    print(f"active_series={len(recurrence.list_series())}")
    print(f"templates={len(templates.list_templates())}")
    print(f"local_series_queue_delta={local_after - pending_before_local}")
    print(f"ordinary_drag_ok=true pending_calendar_ops={queue.count_pending_ops()}")
    print("local_series_drag_refused=true")
    print("deleted_occurrence_persisted=true")
    print("automatic_google_calls=0")

    template_repository.close()
    tag_repository.close()
    series_repository.close()
    queue.close()
    tasks.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
