"""Чистая доменная модель нового десктопа: без Flet, без Qt, без SQLModel."""

from .task import Task
from .commands import QuickAddCommand, build_task, validate_quick_add

__all__ = ["Task", "QuickAddCommand", "build_task", "validate_quick_add"]
