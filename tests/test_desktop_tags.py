from dataclasses import FrozenInstanceError

import pytest

from planner_desktop.domain.tags import (
    MAX_TAG_NAME_LENGTH,
    Tag,
    TagValidationError,
    clean_tag_name,
    normalized_tag_name,
)
from planner_desktop.domain.task import Task


def test_tag_name_trim_and_cyrillic_casefold_normalization():
    assert clean_tag_name("  Проект  ") == "Проект"
    assert normalized_tag_name("  ПРОЕКТ  ") == normalized_tag_name("проект")


@pytest.mark.parametrize("name", ["", "   ", "x" * (MAX_TAG_NAME_LENGTH + 1)])
def test_tag_name_validation(name):
    with pytest.raises(TagValidationError):
        clean_tag_name(name)


def test_tag_preserves_display_capitalization_and_is_immutable():
    tag = Tag("Важное", "ignored")
    assert tag.name == "Важное"
    assert tag.normalized_name == "важное"
    with pytest.raises(FrozenInstanceError):
        tag.name = "другое"


def test_task_tags_default_is_immutable_and_constructor_compatible():
    task = Task(title="Без тегов")
    assert task.tags == ()
    assert Task(title="С тегом", tags=("Работа",)).tags == ("Работа",)

