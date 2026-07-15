from pathlib import Path

import pytest

from planner_desktop.domain.keyboard import allow_shortcut, known_shortcuts
from planner_desktop.viewmodels.ui_state import UiStateViewModel


ROOT = Path(__file__).resolve().parents[1]


def source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_template_shortcut_is_registered_and_safely_routed():
    assert "new_from_template" in known_shortcuts()
    assert allow_shortcut(
        "new_from_template", typing=True, dialog_open=False
    )
    assert not allow_shortcut(
        "new_from_template", typing=False, dialog_open=True
    )
    ui = UiStateViewModel()
    assert ui.allowShortcut("new_from_template", True, False)


def test_main_qml_routes_ctrl_alt_n_to_template_picker():
    qml = source("planner_desktop/qml/Main.qml")
    assert 'sequence: "Ctrl+Alt+N"' in qml
    assert 'root._allow("new_from_template")' in qml
    assert "root._newTaskFromTemplateOnCurrentPage()" in qml


@pytest.mark.parametrize(
    "relative, markers",
    [
        (
            "planner_desktop/qml/components/RecurrencePresetBar.qml",
            ("activeFocusOnTab: true", "Keys.onReturnPressed", "Accessible.name"),
        ),
        (
            "planner_desktop/qml/components/SeriesScopeDialog.qml",
            ("onlyThisButton.forceActiveFocus()", "Accessible.description", "CloseOnEscape"),
        ),
        (
            "planner_desktop/qml/components/TemplatePicker.qml",
            ("activeFocusOnTab: true", "Keys.onReturnPressed", "Accessible.name"),
        ),
    ],
)
def test_recurrence_controls_are_keyboard_reachable_and_announced(relative, markers):
    qml = source(relative)
    for marker in markers:
        assert marker in qml


def test_series_save_has_no_implicit_all_future_default():
    editor = source("planner_desktop/qml/components/TaskEditorDialog.qml")
    scope = source("planner_desktop/qml/components/SeriesScopeDialog.qml")
    assert "_scopeDialogObject.openForSave" in editor
    assert "function _saveScoped(scope)" in editor
    assert 'scopeChosen("this_occurrence")' in scope
    assert 'scopeChosen("this_and_future")' in scope
    assert "standardButtons" not in scope
