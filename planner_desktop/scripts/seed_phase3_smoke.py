"""Seed the explicitly isolated Phase 3.1 visual-smoke profile.

Run only with ``PLANNER_DESKTOP_DATA_DIR`` set. The helper never opens the
legacy Planner database/token and performs no Google or network operation.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os

from planner_desktop.domain.task import Task
from planner_desktop.storage.calendar_sync_store import CalendarSyncStore
from planner_desktop.storage.paths import get_desktop_db_path
from planner_desktop.storage.sqlite_task_repository import SQLiteTaskRepository
from planner_desktop.storage.tag_repository import SQLiteTagRepository
from planner_desktop.usecases.tag_service import TagService
from planner_desktop.usecases.task_service import DesktopTaskService


DAY = datetime(2026, 7, 14)


def _task(uid: str, title: str, **kwargs) -> Task:
    return Task(
        uid=uid,
        title=title,
        updated_at=datetime(2026, 7, 14, 6, tzinfo=timezone.utc),
        **kwargs,
    )


def main() -> int:
    if not os.environ.get("PLANNER_DESKTOP_DATA_DIR"):
        raise SystemExit(
            "Set PLANNER_DESKTOP_DATA_DIR to an isolated smoke directory first."
        )
    db_path = get_desktop_db_path()
    repository = SQLiteTaskRepository(db_path)
    queue = CalendarSyncStore(db_path)
    tag_repository = SQLiteTagRepository(db_path)
    tags = TagService(tag_repository, repository)
    service = DesktopTaskService(
        repository, calendar_queue=queue, tag_service=tags
    )

    tag_map = {
        name: tags.get_or_create(name)
        for name in ("Работа", "Личное", "Срочно", "Клиент Альфа", "Research")
    }
    seeds = [
        (_task("smoke-ru-report", "Подготовить квартальный отчёт",
               notes="Сверить цифры и отправить клиенту",
               start=DAY.replace(hour=9), end=DAY.replace(hour=10),
               duration_minutes=60, priority=3), ("Работа", "Срочно"), True),
        (_task("smoke-en-report", "Review quarterly report",
               notes="English title with overlapping report terms",
               start=DAY.replace(hour=11), end=DAY.replace(hour=11, minute=45),
               duration_minutes=45, priority=2), ("Research",), True),
        (_task("smoke-notes", "Позвонить Анне",
               notes="В заметках находится уникальный поиск: бюджет проекта"),
         ("Клиент Альфа",), False),
        (_task("smoke-undated", "Разобрать идеи без даты",
               notes="Локальная задача для панели без даты", priority=1),
         ("Личное", "Research"), False),
        (_task("smoke-all-day", "День стратегического планирования",
               start=DAY, end=DAY + timedelta(days=1), is_all_day=True,
               priority=2), ("Работа",), True),
        (_task("smoke-completed", "Отправить вчерашний отчёт",
               completed=True,
               completed_at=datetime(2026, 7, 13, 17, tzinfo=timezone.utc)),
         ("Работа",), False),
        (_task("smoke-linked", "Встреча с Google-связью",
               start=DAY.replace(hour=14), end=DAY.replace(hour=15),
               duration_minutes=60, google_calendar_event_id="synthetic-linked",
               google_calendar_etag="synthetic-etag"), ("Клиент Альфа",), False),
        (_task("smoke-recurring", "Синтетический экземпляр серии",
               start=DAY.replace(hour=16), end=DAY.replace(hour=16, minute=30),
               duration_minutes=30, google_calendar_event_id="synthetic-instance",
               google_calendar_recurring_event_id="synthetic-series",
               google_calendar_original_start=DAY.replace(
                   hour=16, tzinfo=timezone.utc)), ("Работа",), False),
    ]
    for index in range(18):
        seeds.append((
            _task(
                f"smoke-scroll-{index:02d}",
                f"Задача для прокрутки {index + 1:02d}",
                notes="Повторяющиеся слова для проверки поиска и списка",
                priority=index % 4,
            ),
            (("Личное",) if index % 2 else ("Работа",)),
            False,
        ))

    for task, tag_names, through_service in seeds:
        if repository.get_by_uid(task.uid) is None:
            (service.create_task(task) if through_service else repository.add(task))
        tag_ids = [tag_map[name].id for name in tag_names]
        tags.set_task_tags(task.uid, tag_ids)

    print(f"db={db_path}")
    print(f"tasks={len(repository.list_all())}")
    print(f"tags={len(tags.list_tags())}")
    print(f"pending_calendar_ops={queue.count_pending_ops()}")
    tag_repository.close()
    queue.close()
    repository.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
