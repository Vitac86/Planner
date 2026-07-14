"""Repository-backed global task search use case."""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from planner_desktop.domain.task_search import SearchFilters, SearchMatch, search_tasks
from planner_desktop.repositories import TaskRepository


class SearchService:
    def __init__(self, repository: TaskRepository) -> None:
        self.repository = repository

    def search(
        self,
        query: str = "",
        filters: Optional[SearchFilters] = None,
        *,
        now: Optional[datetime] = None,
    ) -> List[SearchMatch]:
        return search_tasks(
            self.repository.list_all(), query, filters, now=now
        )


__all__ = ["SearchService"]

