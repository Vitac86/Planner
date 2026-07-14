from datetime import datetime, timedelta, timezone

from planner_desktop.domain.task import Task
from planner_desktop.domain.task_search import (
    SCOPE_ALL_DAY,
    SCOPE_SCHEDULED,
    SCOPE_THIS_WEEK,
    SCOPE_TODAY,
    SCOPE_UNDATED,
    STATUS_ACTIVE,
    STATUS_COMPLETED,
    SearchFilters,
    query_terms,
    search_tasks,
)


NOW = datetime(2026, 7, 14, 12, 0)


def task(title, *, uid, notes="", tags=(), start=None, completed=False,
         priority=0, all_day=False, updated_hour=8):
    return Task(
        uid=uid,
        title=title,
        notes=notes,
        tags=tuple(tags),
        start=start,
        is_all_day=all_day,
        completed=completed,
        priority=priority,
        updated_at=datetime(2026, 7, 14, updated_hour, tzinfo=timezone.utc),
    )


def test_cyrillic_title_notes_and_tags_search():
    tasks = [
        task("Подготовить отчёт", uid="title"),
        task("Позвонить", uid="notes", notes="обсудить ОТЧЁТ"),
        task("Купить билеты", uid="tag", tags=("Отчётность",)),
    ]
    assert [m.task.uid for m in search_tasks(tasks, "отчёт", now=NOW)] == [
        "title", "tag", "notes"
    ]


def test_multi_word_and_simple_quoted_phrase_are_and_queries():
    tasks = [
        task("Годовой отчёт проекта", uid="both"),
        task("Годовой план", uid="split", tags=("Отчёт",)),
        task("Отчёт", uid="missing"),
    ]
    assert query_terms('"годовой отчёт" проект') == ("годовой отчёт", "проект")
    assert [m.task.uid for m in search_tasks(tasks, "годовой отчёт", now=NOW)] == [
        "both", "split"
    ]
    assert [m.task.uid for m in search_tasks(
        tasks, '"годовой отчёт" проект', now=NOW)] == ["both"]


def test_ranking_and_tie_break_are_deterministic():
    tasks = [
        task("Отчёт квартальный", uid="prefix"),
        task("Срочный отчёт", uid="contains"),
        task("План", uid="tag", tags=("Отчёт",)),
        task("Заметка", uid="notes", notes="отчёт"),
        task("Отчёт", uid="exact-b", updated_hour=9),
        task("Отчёт", uid="exact-a", updated_hour=10),
    ]
    matches = search_tasks(reversed(tasks), "ОТЧЁТ", now=NOW)
    assert [(m.task.uid, m.rank) for m in matches] == [
        ("exact-a", 0), ("exact-b", 0), ("prefix", 1),
        ("contains", 2), ("tag", 3), ("notes", 4),
    ]


def test_status_schedule_priority_and_tag_filters_with_empty_query():
    tasks = [
        task("Сегодня", uid="today", start=datetime(2026, 7, 14, 9),
             priority=2, tags=("Работа",)),
        task("All day", uid="all-day", start=datetime(2026, 7, 15), all_day=True),
        task("Неделя", uid="week", start=datetime(2026, 7, 19, 18)),
        task("Позже", uid="later", start=datetime(2026, 7, 21, 9)),
        task("Без даты", uid="undated", completed=True),
    ]
    find = lambda f: [m.task.uid for m in search_tasks(tasks, "", f, now=NOW)]
    assert find(SearchFilters(status=STATUS_ACTIVE)) == ["today", "all-day", "week", "later"]
    assert find(SearchFilters(status=STATUS_COMPLETED)) == ["undated"]
    assert find(SearchFilters(scope=SCOPE_TODAY)) == ["today"]
    assert find(SearchFilters(scope=SCOPE_THIS_WEEK)) == ["today", "all-day", "week"]
    assert find(SearchFilters(scope=SCOPE_SCHEDULED)) == ["today", "all-day", "week", "later"]
    assert find(SearchFilters(scope=SCOPE_UNDATED)) == ["undated"]
    assert find(SearchFilters(scope=SCOPE_ALL_DAY)) == ["all-day"]
    assert find(SearchFilters(priority=2, tags=("РАБОТА",))) == ["today"]

