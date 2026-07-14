"""Use cases for local-only tags and task/tag associations."""
from __future__ import annotations

from dataclasses import replace
import sqlite3
from typing import Iterable, List, Optional, Sequence

from planner_desktop.domain.tags import (
    MAX_TAGS_PER_TASK,
    Tag,
    TagLimitError,
    TagNameConflictError,
    TagSummary,
    clean_tag_name,
    normalized_tag_name,
)
from planner_desktop.domain.task import utc_now
from planner_desktop.repositories import TaskRepository
from planner_desktop.repositories.tag_repository import TagRepository


class TagService:
    """Tag management that never touches the Calendar queue."""

    def __init__(
        self,
        repository: TagRepository,
        task_repository: Optional[TaskRepository] = None,
    ) -> None:
        self.repository = repository
        self.task_repository = task_repository

    def list_tags(self) -> List[Tag]:
        return self.repository.list_all()

    def list_with_counts(self) -> List[TagSummary]:
        counts = self.repository.task_counts()
        return [TagSummary(tag, counts.get(tag.id or -1, 0))
                for tag in self.list_tags()]

    def create(self, name: str) -> Tag:
        display_name = clean_tag_name(name)
        normalized = normalized_tag_name(display_name)
        if self.repository.get_by_normalized_name(normalized) is not None:
            raise TagNameConflictError("Тег с таким названием уже существует.")
        try:
            return self.repository.add(Tag(display_name, normalized))
        except (sqlite3.IntegrityError, ValueError) as exc:
            raise TagNameConflictError(
                "Тег с таким названием уже существует."
            ) from exc

    def get_or_create(self, name: str) -> Tag:
        normalized = normalized_tag_name(name)
        existing = self.repository.get_by_normalized_name(normalized)
        return existing if existing is not None else self.create(name)

    def rename(self, tag_id: int, new_name: str) -> Tag:
        current = self.repository.get(int(tag_id))
        if current is None:
            raise KeyError("Тег не найден.")
        display_name = clean_tag_name(new_name)
        normalized = normalized_tag_name(display_name)
        conflict = self.repository.get_by_normalized_name(normalized)
        if conflict is not None and conflict.id != current.id:
            raise TagNameConflictError("Тег с таким названием уже существует.")
        updated = replace(
            current,
            name=display_name,
            normalized_name=normalized,
            updated_at=utc_now(),
        )
        try:
            return self.repository.update(updated)
        except (sqlite3.IntegrityError, ValueError) as exc:
            raise TagNameConflictError(
                "Тег с таким названием уже существует."
            ) from exc

    def delete(self, tag_id: int) -> bool:
        return self.repository.delete(int(tag_id))

    def tags_for_task(self, task_uid: str) -> List[Tag]:
        return self.repository.list_for_task(task_uid)

    def set_task_tags(self, task_uid: str, tag_ids: Iterable[int]) -> List[Tag]:
        task = self._live_task(task_uid)
        tags = self.resolve_tag_ids(tag_ids)
        unique = tuple(tag.id for tag in tags if tag.id is not None)
        self.repository.set_for_task(task_uid, unique, utc_now())
        if task is not None:
            task.tags = tuple(tag.name for tag in tags)
        return self.tags_for_task(task_uid)

    def resolve_tag_ids(self, tag_ids: Iterable[int]) -> List[Tag]:
        """Validate a picker/bulk payload before any task mutation."""

        unique = tuple(dict.fromkeys(int(item) for item in tag_ids))
        if len(unique) > MAX_TAGS_PER_TASK:
            raise TagLimitError(
                f"У задачи может быть не больше {MAX_TAGS_PER_TASK} тегов."
            )
        tags: List[Tag] = []
        for tag_id in unique:
            tag = self.repository.get(tag_id)
            if tag is None:
                raise KeyError(f"Тег {tag_id} не найден.")
            tags.append(tag)
        return tags

    def add_tag(self, task_uid: str, tag_id: int) -> bool:
        current = self.tags_for_task(task_uid)
        if any(tag.id == int(tag_id) for tag in current):
            return False
        self.set_task_tags(task_uid, [*(tag.id for tag in current), int(tag_id)])
        return True

    def remove_tag(self, task_uid: str, tag_id: int) -> bool:
        current = self.tags_for_task(task_uid)
        remaining = [tag.id for tag in current if tag.id != int(tag_id)]
        if len(remaining) == len(current):
            return False
        self.set_task_tags(task_uid, remaining)
        return True

    def copy_task_tags(self, source_uid: str, target_uid: str) -> List[Tag]:
        return self.set_task_tags(
            target_uid,
            [tag.id for tag in self.tags_for_task(source_uid) if tag.id is not None],
        )

    def tag_ids_for_names(self, names: Sequence[str]) -> List[int]:
        ids: List[int] = []
        for name in names:
            tag = self.repository.get_by_normalized_name(normalized_tag_name(name))
            if tag is not None and tag.id is not None:
                ids.append(tag.id)
        return ids

    def _live_task(self, uid: str):
        if self.task_repository is None:
            return None
        task = self.task_repository.get_by_uid(uid)
        if task is None or task.is_deleted:
            raise KeyError("Задача не найдена (возможно, уже удалена).")
        return task


__all__ = ["TagService"]
