"""Pure Unicode-aware task search and deterministic ranking policy."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
import re
import unicodedata
from typing import Iterable, List, Optional, Sequence, Tuple

from planner_desktop.domain.tags import normalized_tag_name
from planner_desktop.domain.task import Task


STATUS_ACTIVE = "active"
STATUS_COMPLETED = "completed"
STATUS_ALL = "all"
VALID_STATUSES = (STATUS_ACTIVE, STATUS_COMPLETED, STATUS_ALL)

SCOPE_ALL = "all"
SCOPE_TODAY = "today"
SCOPE_THIS_WEEK = "this_week"
SCOPE_SCHEDULED = "scheduled"
SCOPE_UNDATED = "undated"
SCOPE_ALL_DAY = "all_day"
VALID_SCOPES = (
    SCOPE_ALL,
    SCOPE_TODAY,
    SCOPE_THIS_WEEK,
    SCOPE_SCHEDULED,
    SCOPE_UNDATED,
    SCOPE_ALL_DAY,
)

# Вид задачи (Phase 3.2A): обычная / экземпляр локальной серии /
# экземпляр Google-серии.
KIND_ALL = "all"
KIND_ORDINARY = "ordinary"
KIND_LOCAL_SERIES = "local_series"
KIND_GOOGLE_SERIES = "google_series"
VALID_KINDS = (KIND_ALL, KIND_ORDINARY, KIND_LOCAL_SERIES, KIND_GOOGLE_SERIES)

_TOKEN_RE = re.compile(r'"([^"\\]*(?:\\.[^"\\]*)*)"|(\S+)')


def normalize_search_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return " ".join(normalized.split())


def query_terms(query: str) -> Tuple[str, ...]:
    """Split words while treating a simple quoted phrase as one term."""

    terms: List[str] = []
    for match in _TOKEN_RE.finditer(str(query or "")):
        raw = match.group(1) if match.group(1) is not None else match.group(2)
        term = normalize_search_text(raw.replace(r'\"', '"'))
        if term:
            terms.append(term)
    return tuple(terms)


@dataclass(frozen=True)
class SearchFilters:
    status: str = STATUS_ALL
    scope: str = SCOPE_ALL
    priority: Optional[int] = None
    tags: Tuple[str, ...] = field(default_factory=tuple)
    kind: str = KIND_ALL

    def __post_init__(self) -> None:
        if self.status not in VALID_STATUSES:
            raise ValueError(f"Unknown search status: {self.status}")
        if self.scope not in VALID_SCOPES:
            raise ValueError(f"Unknown search scope: {self.scope}")
        if self.priority is not None and int(self.priority) not in (0, 1, 2, 3):
            raise ValueError("Priority filter must be 0..3 or None")
        if self.kind not in VALID_KINDS:
            raise ValueError(f"Unknown search kind: {self.kind}")
        object.__setattr__(
            self,
            "tags",
            tuple(dict.fromkeys(normalized_tag_name(tag) for tag in self.tags)),
        )

    @property
    def active_count(self) -> int:
        return (
            int(self.status != STATUS_ALL)
            + int(self.scope != SCOPE_ALL)
            + int(self.priority is not None)
            + int(self.kind != KIND_ALL)
            + len(self.tags)
        )


@dataclass(frozen=True)
class SearchMatch:
    task: Task
    rank: int
    matched_fields: Tuple[str, ...]


def _task_tag_values(task: Task) -> Tuple[str, ...]:
    return tuple(normalize_search_text(tag) for tag in task.tags)


def _all_terms_in(terms: Sequence[str], values: Iterable[str]) -> bool:
    materialized = tuple(values)
    return all(any(term in value for value in materialized) for term in terms)


def _matches_filters(task: Task, filters: SearchFilters, today: date) -> bool:
    if filters.status == STATUS_ACTIVE and task.completed:
        return False
    if filters.status == STATUS_COMPLETED and not task.completed:
        return False
    if filters.priority is not None and task.priority != filters.priority:
        return False

    is_local_series = task.series_uid is not None
    is_google_series = task.google_calendar_recurring_event_id is not None
    if filters.kind == KIND_ORDINARY and (is_local_series or is_google_series):
        return False
    if filters.kind == KIND_LOCAL_SERIES and not is_local_series:
        return False
    if filters.kind == KIND_GOOGLE_SERIES and not is_google_series:
        return False

    if filters.scope == SCOPE_TODAY:
        if task.start is None or task.start.date() != today:
            return False
    elif filters.scope == SCOPE_THIS_WEEK:
        week_start = today - timedelta(days=today.weekday())
        if task.start is None or not (week_start <= task.start.date() < week_start + timedelta(days=7)):
            return False
    elif filters.scope == SCOPE_SCHEDULED and task.start is None:
        return False
    elif filters.scope == SCOPE_UNDATED and task.start is not None:
        return False
    elif filters.scope == SCOPE_ALL_DAY and (
        task.start is None or not task.is_all_day
    ):
        return False

    if filters.tags:
        task_tags = set(_task_tag_values(task))
        if not set(filters.tags).issubset(task_tags):
            return False
    return True


def match_task(task: Task, query: str) -> Optional[SearchMatch]:
    """Return a ranked match, or ``None`` when not all query terms match."""

    terms = query_terms(query)
    if not terms:
        return SearchMatch(task, 5, ())

    title = normalize_search_text(task.title)
    notes = normalize_search_text(task.notes)
    tags = _task_tag_values(task)
    all_values = (title, *tags, notes)
    if not _all_terms_in(terms, all_values):
        return None

    joined_query = " ".join(terms)
    title_all = all(term in title for term in terms)
    title_tags_all = _all_terms_in(terms, (title, *tags))

    if title == joined_query:
        rank = 0
    elif title.startswith(joined_query):
        rank = 1
    elif title_all:
        rank = 2
    elif title_tags_all:
        rank = 3
    else:
        rank = 4

    fields: List[str] = []
    if any(term in title for term in terms):
        fields.append("title")
    if any(any(term in tag for tag in tags) for term in terms):
        fields.append("tags")
    if any(term in notes for term in terms):
        fields.append("notes")
    return SearchMatch(task, rank, tuple(fields))


def text_matches_query(
    query: str, title: str, notes: str = "", tags: Sequence[str] = ()
) -> bool:
    """Все ли термы запроса встречаются в переданных текстах.

    Используется для поиска по ОПРЕДЕЛЕНИЯМ серий (Phase 3.2A), где нет
    Task-объекта; семантика термов та же, что в match_task.
    """
    terms = query_terms(query)
    if not terms:
        return True
    values = (
        normalize_search_text(title),
        *(normalize_search_text(tag) for tag in tags),
        normalize_search_text(notes),
    )
    return _all_terms_in(terms, values)


def _timestamp(value: datetime) -> float:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.timestamp()


def _sort_key(match: SearchMatch) -> tuple:
    task = match.task
    scheduled = task.start is not None
    start_key = _timestamp(task.start) if task.start is not None else float("inf")
    return (
        match.rank,
        int(task.completed),
        int(not scheduled),
        start_key,
        -_timestamp(task.updated_at),
        task.uid,
    )


def search_tasks(
    tasks: Iterable[Task],
    query: str = "",
    filters: Optional[SearchFilters] = None,
    *,
    now: Optional[datetime] = None,
) -> List[SearchMatch]:
    """Search active repository results entirely in Python.

    SQLite ``lower()`` is intentionally not involved, so Russian and other
    Unicode casefold behavior is identical in SQLite and in-memory tests.
    """

    policy = filters or SearchFilters()
    today = (now or datetime.now()).date()
    matches: List[SearchMatch] = []
    for task in tasks:
        if task.is_deleted or not _matches_filters(task, policy, today):
            continue
        match = match_task(task, query)
        if match is not None:
            matches.append(match)
    return sorted(matches, key=_sort_key)


__all__ = [
    "KIND_ALL",
    "KIND_GOOGLE_SERIES",
    "KIND_LOCAL_SERIES",
    "KIND_ORDINARY",
    "VALID_KINDS",
    "text_matches_query",
    "SCOPE_ALL",
    "SCOPE_ALL_DAY",
    "SCOPE_SCHEDULED",
    "SCOPE_THIS_WEEK",
    "SCOPE_TODAY",
    "SCOPE_UNDATED",
    "STATUS_ACTIVE",
    "STATUS_ALL",
    "STATUS_COMPLETED",
    "SearchFilters",
    "SearchMatch",
    "match_task",
    "normalize_search_text",
    "query_terms",
    "search_tasks",
]

