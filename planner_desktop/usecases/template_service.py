"""Сценарии локальных шаблонов задач (Phase 3.2A).

Шаблон только предзаполняет общий редактор: применение шаблона не пишет
ничего в БД, пока пользователь не сохранит форму. Google-метаданные,
etag и recurrence-id в шаблоны не попадают. Calendar-очередь не участвует.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from planner_desktop.domain.recurrence import RecurrenceRule
from planner_desktop.domain.templates import (
    MAX_TEMPLATES,
    NAME_CONFLICT_ERROR,
    TEMPLATE_KIND_RECURRING,
    TOO_MANY_TEMPLATES_ERROR,
    TaskTemplate,
    clean_template_name,
    normalized_template_name,
    validate_template,
)
from planner_desktop.repositories.template_repository import TemplateRepository

TEMPLATE_NOT_FOUND_ERROR = "Шаблон не найден (возможно, уже удалён)."


@dataclass
class TemplateOperationResult:
    template: Optional[TaskTemplate] = None
    errors: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.template is not None and not self.errors


class TemplateService:
    """CRUD шаблонов + предзаполнение редактора."""

    def __init__(
        self,
        repository: TemplateRepository,
        tag_service=None,
    ) -> None:
        self.repository = repository
        self.tag_service = tag_service
        self._change_listeners: List[Callable[[], None]] = []

    def add_change_listener(self, listener: Callable[[], None]) -> None:
        self._change_listeners.append(listener)

    def _notify_changed(self) -> None:
        for listener in tuple(self._change_listeners):
            listener()

    # ---- CRUD -------------------------------------------------------------------

    def list_templates(self) -> List[TaskTemplate]:
        return self.repository.list_all()

    def get_template(self, uid: str) -> Optional[TaskTemplate]:
        template = self.repository.get_by_uid(uid)
        if template is None or template.is_deleted:
            return None
        return template

    def create_template(
        self,
        template: TaskTemplate,
        tag_ids: Optional[List[int]] = None,
    ) -> TemplateOperationResult:
        errors = validate_template(template)
        if errors:
            return TemplateOperationResult(errors=errors)
        tag_errors = self._validate_tag_ids(tag_ids)
        if tag_errors:
            return TemplateOperationResult(errors=tag_errors)
        template.name = clean_template_name(template.name)
        normalized = normalized_template_name(template.name)
        if self.repository.get_by_normalized_name(normalized) is not None:
            return TemplateOperationResult(errors=[NAME_CONFLICT_ERROR])
        if self.repository.count_active() >= MAX_TEMPLATES:
            return TemplateOperationResult(errors=[TOO_MANY_TEMPLATES_ERROR])
        try:
            if not template.is_recurring:
                template.rule = None
            created = self.repository.add(template)
            if tag_ids is not None:
                self._apply_tags(created, tag_ids)
        except Exception as exc:
            return TemplateOperationResult(
                errors=[f"Не удалось создать шаблон: {exc}"]
            )
        self._notify_changed()
        return TemplateOperationResult(template=created)

    def update_template(
        self,
        uid: str,
        template: TaskTemplate,
        tag_ids: Optional[List[int]] = None,
    ) -> TemplateOperationResult:
        """Правка шаблона НЕ мутирует уже созданные из него задачи/серии."""
        existing = self.get_template(uid)
        if existing is None:
            return TemplateOperationResult(errors=[TEMPLATE_NOT_FOUND_ERROR])
        errors = validate_template(template)
        if errors:
            return TemplateOperationResult(errors=errors)
        tag_errors = self._validate_tag_ids(tag_ids)
        if tag_errors:
            return TemplateOperationResult(errors=tag_errors)
        template.name = clean_template_name(template.name)
        if not template.is_recurring:
            template.rule = None
        normalized = normalized_template_name(template.name)
        conflict = self.repository.get_by_normalized_name(normalized)
        if conflict is not None and conflict.uid != uid:
            return TemplateOperationResult(errors=[NAME_CONFLICT_ERROR])
        template.id = existing.id
        template.uid = existing.uid
        template.created_at = existing.created_at
        try:
            updated = self.repository.update(template)
            if tag_ids is not None:
                self._apply_tags(updated, tag_ids)
        except Exception as exc:
            return TemplateOperationResult(
                errors=[f"Не удалось изменить шаблон: {exc}"]
            )
        self._notify_changed()
        return TemplateOperationResult(template=updated)

    def duplicate_template(self, uid: str) -> TemplateOperationResult:
        source = self.get_template(uid)
        if source is None:
            return TemplateOperationResult(errors=[TEMPLATE_NOT_FOUND_ERROR])
        name = self._unique_copy_name(source.name)
        clone = TaskTemplate(
            name=name,
            kind=source.kind,
            title=source.title,
            notes=source.notes,
            priority=source.priority,
            tags=tuple(source.tags),
            schedule_mode=source.schedule_mode,
            time_text=source.time_text,
            duration_minutes=source.duration_minutes,
            rule=source.rule,
        )
        tag_ids = list(self.repository.tag_ids_for_template(uid))
        return self.create_template(clone, tag_ids=tag_ids)

    def delete_template(self, uid: str) -> bool:
        """Удаляет ТОЛЬКО шаблон: созданные из него задачи/серии остаются."""
        deleted = self.repository.delete(uid)
        if deleted:
            self._notify_changed()
        return deleted

    # ---- применение (prefill, ничего не сохраняет) --------------------------------

    def editor_prefill(self, uid: str) -> Dict[str, Any]:
        """Данные предзаполнения общего редактора. Пустой dict — не найден.

        Google-идентификаторы/etag/recurrence-id не копируются: их в
        шаблоне физически нет.
        """
        template = self.get_template(uid)
        if template is None:
            return {}
        tag_ids = list(self.repository.tag_ids_for_template(uid))
        payload: Dict[str, Any] = {
            "exists": True,
            "uid": "",  # создание новой задачи/серии, не правка
            "templateUid": template.uid,
            "templateKind": template.kind,
            "title": template.title,
            "notes": template.notes,
            "priority": template.priority,
            "scheduled": template.schedule_mode != "none",
            "isAllDay": template.schedule_mode == "allday",
            "mode": template.schedule_mode,
            "dateText": "",  # дату подставляет форма («сегодня»/выбранный день)
            "timeText": template.time_text,
            "durationText": (
                str(template.duration_minutes)
                if template.duration_minutes else ""
            ),
            "completed": False,
            "isRecurringInstance": False,
            "tagIds": tag_ids,
            "recurring": template.kind == TEMPLATE_KIND_RECURRING,
        }
        if template.kind == TEMPLATE_KIND_RECURRING and template.rule is not None:
            rule = template.rule
            payload["rule"] = {
                "frequency": rule.frequency.value,
                "interval": rule.interval,
                "weekdays": list(rule.weekdays),
                "monthDay": rule.month_day or 0,
                "yearlyMonth": rule.yearly_month or 0,
                "yearlyDay": rule.yearly_day or 0,
                "endMode": rule.end_mode.value,
                "untilDate": (
                    rule.until_date.isoformat()
                    if rule.until_date is not None else ""
                ),
                "occurrenceCount": rule.occurrence_count or 0,
            }
        return payload

    # ---- внутреннее -----------------------------------------------------------------

    def _apply_tags(self, template: TaskTemplate, tag_ids: List[int]) -> None:
        if self.tag_service is not None:
            tags = self.tag_service.resolve_tag_ids(tag_ids)
            template.tags = tuple(tag.name for tag in tags)
        self.repository.set_template_tags(template.uid, list(tag_ids))

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

    def _unique_copy_name(self, base: str) -> str:
        candidate = clean_template_name(f"{base} (копия)")
        index = 2
        while (
            self.repository.get_by_normalized_name(
                normalized_template_name(candidate)
            )
            is not None
        ):
            candidate = clean_template_name(f"{base} (копия {index})")
            index += 1
        return candidate


__all__ = [
    "TEMPLATE_NOT_FOUND_ERROR",
    "TaskTemplate",
    "TemplateOperationResult",
    "TemplateService",
    "RecurrenceRule",
]
