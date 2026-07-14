"""Phase 2.2 Calendar shortcut policy remains text/dialog safe."""
import pytest

from planner_desktop.domain.keyboard import allow_shortcut


INTERACTION_SHORTCUTS = [
    "calendar_move_slot",
    "calendar_move_day",
    "calendar_resize",
    "calendar_to_all_day",
    "calendar_unschedule",
]


@pytest.mark.parametrize("name", INTERACTION_SHORTCUTS)
def test_interaction_shortcuts_allowed_in_calendar_context(name):
    assert allow_shortcut(name, typing=False, dialog_open=False)


@pytest.mark.parametrize("name", INTERACTION_SHORTCUTS)
def test_interaction_shortcuts_ignore_text_fields(name):
    assert not allow_shortcut(name, typing=True, dialog_open=False)


@pytest.mark.parametrize("name", INTERACTION_SHORTCUTS)
def test_interaction_shortcuts_ignore_dialogs(name):
    assert not allow_shortcut(name, typing=False, dialog_open=True)
