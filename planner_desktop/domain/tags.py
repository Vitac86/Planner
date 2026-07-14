"""Pure domain rules for local Planner Desktop tags."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import unicodedata
from typing import Optional

from planner_desktop.domain.task import utc_now


MAX_TAG_NAME_LENGTH = 32
MAX_TAGS_PER_TASK = 10


class TagError(ValueError):
    """Base class for user-correctable tag errors."""


class TagValidationError(TagError):
    """A tag name does not satisfy the local tag policy."""


class TagNameConflictError(TagError):
    """Another tag already has the same Unicode/casefold name."""


class TagLimitError(TagError):
    """A task would exceed :data:`MAX_TAGS_PER_TASK`."""


def clean_tag_name(value: str) -> str:
    """Trim a display name and validate its deterministic character limit.

    Internal whitespace and user-facing capitalization are intentionally kept.
    NFKC is used only for comparison, not for the displayed value.
    """

    name = str(value or "").strip()
    if not name:
        raise TagValidationError("Название тега не может быть пустым.")
    if len(name) > MAX_TAG_NAME_LENGTH:
        raise TagValidationError(
            f"Название тега не может быть длиннее {MAX_TAG_NAME_LENGTH} символов."
        )
    return name


def normalized_tag_name(value: str) -> str:
    """Unicode-aware comparison key using Python ``casefold`` semantics."""

    return unicodedata.normalize("NFKC", clean_tag_name(value)).casefold()


@dataclass(frozen=True)
class Tag:
    name: str
    normalized_name: str
    id: Optional[int] = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        display_name = clean_tag_name(self.name)
        normalized = normalized_tag_name(display_name)
        object.__setattr__(self, "name", display_name)
        object.__setattr__(self, "normalized_name", normalized)


@dataclass(frozen=True)
class TagSummary:
    tag: Tag
    task_count: int
