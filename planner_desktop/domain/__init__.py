"""Чистая доменная модель десктопа: без Qt, без ORM, без Google-клиентов."""

from .task import Task
from .commands import QuickAddCommand, build_task, validate_quick_add

__all__ = ["Task", "QuickAddCommand", "build_task", "validate_quick_add"]
