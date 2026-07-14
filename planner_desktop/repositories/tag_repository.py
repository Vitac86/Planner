"""Repository contract and in-memory adapter for local task tags."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from typing import Dict, Iterable, List, Optional, Protocol, Tuple

from planner_desktop.domain.tags import Tag


class TagRepository(Protocol):
    def add(self, tag: Tag) -> Tag: ...
    def update(self, tag: Tag) -> Tag: ...
    def get(self, tag_id: int) -> Optional[Tag]: ...
    def get_by_normalized_name(self, normalized_name: str) -> Optional[Tag]: ...
    def list_all(self) -> List[Tag]: ...
    def delete(self, tag_id: int) -> bool: ...
    def list_for_task(self, task_uid: str) -> List[Tag]: ...
    def set_for_task(
        self, task_uid: str, tag_ids: Iterable[int], created_at: datetime
    ) -> None: ...
    def task_counts(self) -> Dict[int, int]: ...


class InMemoryTagRepository:
    """Small deterministic adapter used by unit tests and demo mode."""

    def __init__(self) -> None:
        self._tags: Dict[int, Tag] = {}
        self._task_tags: Dict[str, Dict[int, datetime]] = {}
        self._next_id = 1

    def add(self, tag: Tag) -> Tag:
        if self.get_by_normalized_name(tag.normalized_name) is not None:
            raise ValueError("duplicate normalized tag name")
        tag_id = tag.id if tag.id is not None else self._next_id
        if tag_id in self._tags:
            raise ValueError("duplicate tag id")
        stored = replace(tag, id=tag_id)
        self._tags[tag_id] = stored
        self._next_id = max(self._next_id, tag_id + 1)
        return stored

    def update(self, tag: Tag) -> Tag:
        if tag.id is None or tag.id not in self._tags:
            raise KeyError("tag not found")
        conflict = self.get_by_normalized_name(tag.normalized_name)
        if conflict is not None and conflict.id != tag.id:
            raise ValueError("duplicate normalized tag name")
        self._tags[tag.id] = tag
        return tag

    def get(self, tag_id: int) -> Optional[Tag]:
        return self._tags.get(int(tag_id))

    def get_by_normalized_name(self, normalized_name: str) -> Optional[Tag]:
        return next(
            (tag for tag in self._tags.values()
             if tag.normalized_name == normalized_name),
            None,
        )

    def list_all(self) -> List[Tag]:
        return sorted(
            self._tags.values(), key=lambda tag: (tag.normalized_name, tag.id or 0)
        )

    def delete(self, tag_id: int) -> bool:
        tag_id = int(tag_id)
        if tag_id not in self._tags:
            return False
        del self._tags[tag_id]
        for links in self._task_tags.values():
            links.pop(tag_id, None)
        return True

    def list_for_task(self, task_uid: str) -> List[Tag]:
        ids = self._task_tags.get(task_uid, {})
        return sorted(
            (self._tags[tag_id] for tag_id in ids if tag_id in self._tags),
            key=lambda tag: (tag.normalized_name, tag.id or 0),
        )

    def set_for_task(
        self, task_uid: str, tag_ids: Iterable[int], created_at: datetime
    ) -> None:
        unique: Tuple[int, ...] = tuple(dict.fromkeys(int(item) for item in tag_ids))
        missing = [tag_id for tag_id in unique if tag_id not in self._tags]
        if missing:
            raise KeyError(f"unknown tag ids: {missing}")
        previous = self._task_tags.get(task_uid, {})
        self._task_tags[task_uid] = {
            tag_id: previous.get(tag_id, created_at) for tag_id in unique
        }

    def task_counts(self) -> Dict[int, int]:
        counts = {tag_id: 0 for tag_id in self._tags}
        for links in self._task_tags.values():
            for tag_id in links:
                if tag_id in counts:
                    counts[tag_id] += 1
        return counts


__all__ = ["InMemoryTagRepository", "TagRepository"]

