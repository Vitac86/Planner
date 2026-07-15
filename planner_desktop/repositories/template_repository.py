"""Контракт репозитория локальных шаблонов задач (Phase 3.2A).

SQLite-реализация — planner_desktop/storage/template_repository.py;
InMemoryTemplateRepository — для тестов и демо-режима.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Protocol, Sequence, Tuple

from planner_desktop.domain.templates import (
    TaskTemplate,
    normalized_template_name,
)


class TemplateRepository(Protocol):
    """Минимальный интерфейс хранилища шаблонов."""

    def add(self, template: TaskTemplate) -> TaskTemplate: ...

    def update(self, template: TaskTemplate) -> TaskTemplate: ...

    def get_by_uid(self, uid: str) -> Optional[TaskTemplate]: ...

    def get_by_normalized_name(self, name: str) -> Optional[TaskTemplate]: ...

    def list_all(self) -> List[TaskTemplate]: ...

    def delete(self, uid: str) -> bool: ...

    def set_template_tags(self, template_uid: str, tag_ids: Sequence[int]) -> None: ...

    def tag_ids_for_template(self, template_uid: str) -> List[int]: ...

    def count_active(self) -> int: ...


class InMemoryTemplateRepository:
    """Хранит шаблоны в памяти процесса (тесты/демо)."""

    def __init__(self) -> None:
        self._templates: List[TaskTemplate] = []
        self._tags: Dict[str, Tuple[int, ...]] = {}
        self._next_id = 1

    def add(self, template: TaskTemplate) -> TaskTemplate:
        normalized = normalized_template_name(template.name)
        if self.get_by_normalized_name(normalized) is not None:
            raise ValueError("Шаблон с таким названием уже существует.")
        template.id = self._next_id
        self._next_id += 1
        self._templates.append(template)
        return template

    def update(self, template: TaskTemplate) -> TaskTemplate:
        for index, existing in enumerate(self._templates):
            if existing.uid == template.uid:
                template.touch()
                self._templates[index] = template
                return template
        raise KeyError("Шаблон не найден")

    def get_by_uid(self, uid: str) -> Optional[TaskTemplate]:
        for template in self._templates:
            if template.uid == uid:
                return template
        return None

    def get_by_normalized_name(self, name: str) -> Optional[TaskTemplate]:
        for template in self._templates:
            if (
                not template.is_deleted
                and normalized_template_name(template.name) == name
            ):
                return template
        return None

    def list_all(self) -> List[TaskTemplate]:
        return [t for t in self._templates if not t.is_deleted]

    def delete(self, uid: str) -> bool:
        template = self.get_by_uid(uid)
        if template is None or template.is_deleted:
            return False
        template.mark_deleted()
        return True

    def set_template_tags(self, template_uid: str, tag_ids: Sequence[int]) -> None:
        self._tags[template_uid] = tuple(dict.fromkeys(int(i) for i in tag_ids))

    def tag_ids_for_template(self, template_uid: str) -> List[int]:
        return list(self._tags.get(template_uid, ()))

    def count_active(self) -> int:
        return len(self.list_all())


__all__ = ["InMemoryTemplateRepository", "TemplateRepository"]
