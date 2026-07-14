"""Calendar grid keyboard routing stays out of text inputs and dialogs."""
import pytest

from planner_desktop.domain.keyboard import allow_shortcut


CALENDAR_KEYS = [
    "calendar_prev_day",
    "calendar_next_day",
    "calendar_prev_period",
    "calendar_next_period",
    "calendar_today",
    "calendar_prev_event",
    "calendar_next_event",
]


@pytest.mark.parametrize("name", CALENDAR_KEYS)
def test_calendar_grid_keys_are_allowed_in_plain_grid_context(name):
    assert allow_shortcut(name, typing=False, dialog_open=False) is True


@pytest.mark.parametrize("name", CALENDAR_KEYS)
def test_calendar_grid_keys_ignore_text_input_context(name):
    assert allow_shortcut(name, typing=True, dialog_open=False) is False


@pytest.mark.parametrize("name", CALENDAR_KEYS)
def test_calendar_grid_keys_ignore_open_dialog_context(name):
    assert allow_shortcut(name, typing=False, dialog_open=True) is False
