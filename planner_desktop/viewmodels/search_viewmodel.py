"""Qt adapter for global task search; business policy stays Qt-free."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from PySide6.QtCore import Property, Signal, Slot

from planner_desktop.domain.task_search import (
    SCOPE_ALL,
    STATUS_ALL,
    SearchFilters,
    SearchMatch,
)
from planner_desktop.usecases.search_service import SearchService
from planner_desktop.usecases.task_service import DesktopTaskService
from planner_desktop.viewmodels.task_actions import TaskActionsViewModel
from planner_desktop.viewmodels.task_rows import task_to_row


class SearchViewModel(TaskActionsViewModel):
    resultsChanged = Signal()
    queryChanged = Signal()
    filtersChanged = Signal()
    openChanged = Signal()
    focusSearchRequested = Signal()
    editRequested = Signal(str)

    def __init__(
        self,
        service: DesktopTaskService,
        search_service: Optional[SearchService] = None,
        parent=None,
        *,
        now_provider: Optional[Callable[[], datetime]] = None,
        **kwargs,
    ) -> None:
        super().__init__(service, parent, now_provider=now_provider, **kwargs)
        self._search = search_service or SearchService(service.repository)
        self._query = ""
        self._filters = SearchFilters()
        self._matches: List[SearchMatch] = []
        self._open = False
        self._recompute(emit=False)

    def _emit_data_changed(self) -> None:
        self._recompute(emit=True)

    def _visible_task_uids(self) -> List[str]:
        return [match.task.uid for match in self._matches]

    def _recompute(self, *, emit: bool) -> None:
        selected = self._selected_uid
        self._matches = self._search.search(
            self._query, self._filters, now=self._now()
        )
        if self._selection.set_visible(match.task.uid for match in self._matches):
            self.taskSelectionChanged.emit()
        visible = {match.task.uid for match in self._matches}
        if selected and selected not in visible:
            self._selected_uid = ""
            self.selectedTaskChanged.emit()
        if emit:
            self.resultsChanged.emit()

    @Property(bool, notify=openChanged)
    def isOpen(self) -> bool:
        return self._open

    @Property(str, notify=queryChanged)
    def query(self) -> str:
        return self._query

    @Property(int, notify=resultsChanged)
    def resultCount(self) -> int:
        return len(self._matches)

    @Property(bool, notify=resultsChanged)
    def hasResults(self) -> bool:
        return bool(self._matches)

    @Property(bool, notify=resultsChanged)
    def emptyQueryAndFilters(self) -> bool:
        return not self._query.strip() and self._filters.active_count == 0

    @Property("QVariantList", notify=resultsChanged)
    def results(self) -> List[Dict[str, Any]]:
        pending = self._service.pending_task_uids()
        rows: List[Dict[str, Any]] = []
        for match in self._matches:
            row = task_to_row(match.task, pending)
            row.update({"rank": match.rank, "matchedFields": list(match.matched_fields)})
            rows.append(row)
        return rows

    @Property(str, notify=filtersChanged)
    def statusFilter(self) -> str:
        return self._filters.status

    @Property(str, notify=filtersChanged)
    def scopeFilter(self) -> str:
        return self._filters.scope

    @Property(int, notify=filtersChanged)
    def priorityFilter(self) -> int:
        return -1 if self._filters.priority is None else self._filters.priority

    @Property("QVariantList", notify=filtersChanged)
    def tagFilters(self) -> List[str]:
        return list(self._filters.tags)

    @Property(int, notify=filtersChanged)
    def activeFilterCount(self) -> int:
        return self._filters.active_count

    @Slot()
    def openSearch(self) -> None:
        if not self._open:
            self._open = True
            self.openChanged.emit()
        self.focusSearchRequested.emit()

    @Slot()
    def closeSearch(self) -> None:
        if self._open:
            self._open = False
            self.openChanged.emit()

    @Slot(str)
    def setQuery(self, value: str) -> None:
        value = str(value or "")
        if value == self._query:
            return
        self._query = value
        self.queryChanged.emit()
        self._recompute(emit=True)

    @Slot(str)
    def setStatusFilter(self, value: str) -> None:
        self._set_filters(replace(self._filters, status=value))

    @Slot(str)
    def setScopeFilter(self, value: str) -> None:
        self._set_filters(replace(self._filters, scope=value))

    @Slot(int)
    def setPriorityFilter(self, value: int) -> None:
        self._set_filters(
            replace(self._filters, priority=None if value < 0 else int(value))
        )

    @Slot("QVariantList")
    def setTagFilters(self, values) -> None:
        self._set_filters(replace(self._filters, tags=tuple(str(v) for v in values)))

    @Slot(str)
    def toggleTagFilter(self, value: str) -> None:
        from planner_desktop.domain.tags import normalized_tag_name

        normalized = normalized_tag_name(value)
        tags = list(self._filters.tags)
        if normalized in tags:
            tags.remove(normalized)
        else:
            tags.append(normalized)
        self._set_filters(replace(self._filters, tags=tuple(tags)))

    @Slot()
    def clearFilters(self) -> None:
        self._set_filters(SearchFilters(status=STATUS_ALL, scope=SCOPE_ALL))

    def _set_filters(self, filters: SearchFilters) -> None:
        if filters == self._filters:
            return
        self._filters = filters
        self.filtersChanged.emit()
        self._recompute(emit=True)
        self._prune_selection()

    @Slot(int)
    def moveResultSelection(self, delta: int) -> None:
        if not self._matches:
            self.clearSelection()
            return
        uids = [match.task.uid for match in self._matches]
        if self._selected_uid not in uids:
            index = 0 if delta >= 0 else len(uids) - 1
        else:
            index = (uids.index(self._selected_uid) + int(delta)) % len(uids)
        self.selectTask(uids[index])

    @Slot()
    def openSelectedResult(self) -> None:
        if self._selected_uid:
            self.editRequested.emit(self._selected_uid)

    @Slot()
    def refresh(self) -> None:
        self._recompute(emit=True)
        self.selectedTaskChanged.emit()


__all__ = ["SearchViewModel"]
