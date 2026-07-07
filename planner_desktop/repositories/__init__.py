"""Репозитории нового десктопа. Пока только фейковый in-memory вариант."""

from .fake_task_repository import FakeTaskRepository

__all__ = ["FakeTaskRepository"]
