"""Exercise actual QML fields across closing, saving and process restarts."""
import os
from pathlib import Path
import subprocess
import sys
import textwrap


ROOT = Path(__file__).resolve().parents[1]
SETUP = '''
from datetime import datetime
from pathlib import Path
from PySide6.QtCore import QObject, QMetaObject, QUrl, QEventLoop, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlComponent, QQmlEngine
from planner_desktop.domain.task import Task
from planner_desktop.repositories.fake_task_repository import FakeTaskRepository
from planner_desktop.storage.draft_store import DraftStore
from planner_desktop.viewmodels.drafts_viewmodel import DraftsViewModel
from planner_desktop.viewmodels.today_viewmodel import TodayViewModel
from planner_desktop.viewmodels.ui_state import UiStateViewModel

app = QGuiApplication([])
repo = FakeTaskRepository(seed=False)
original = repo.add(Task(uid="existing", title="Original task", notes="Original notes"))
vm = TodayViewModel(repo, now_provider=lambda: datetime(2026, 7, 15, 10))
store = DraftStore(Path(__import__("sys").argv[1]) / "drafts.json")
drafts = DraftsViewModel(store, task_exists=lambda uid: bool(
    repo.get_by_uid(uid) and not repo.get_by_uid(uid).is_deleted))
engine = QQmlEngine()
engine.rootContext().setContextProperty("todayVm", vm)
engine.rootContext().setContextProperty("draftsVm", drafts)
ui = UiStateViewModel()
engine.rootContext().setContextProperty("uiVm", ui)

def wait():
    loop = QEventLoop()
    QTimer.singleShot(200, loop.quit)
    loop.exec()

def invoke(obj, name):
    assert QMetaObject.invokeMethod(obj, name), name
    wait()

def field(obj, name):
    result = obj.findChild(QObject, name)
    assert result is not None, name
    return result
'''


def _run(script, path):
    result = subprocess.run(
        [sys.executable, "-c", SETUP + textwrap.dedent(script), str(path)],
        cwd=ROOT, env={**os.environ, "QT_QPA_PLATFORM": "offscreen",
                      "QT_QUICK_BACKEND": "software"},
        capture_output=True, text=True, timeout=45,
    )
    assert result.returncode == 0, result.stdout + result.stderr


EDITOR = '''
component = QQmlComponent(engine)
component.setData(b"""
import QtQuick
import QtQuick.Controls
import "planner_desktop/qml/components"
ApplicationWindow {
    width: 900; height: 1000; visible: true
    function createTask() { editor.openForCreate("") }
    function editTask() { editor.openForEdit("existing") }
    TaskEditorDialog { id: editor; objectName: "editor"; vm: todayVm }
}
""", QUrl.fromLocalFile(str(Path("draft_test.qml").resolve())))
window = component.create()
assert window is not None, [error.toString() for error in component.errors()]
editor = field(window, "editor")
'''


def test_editor_drafts_close_restart_validation_success_and_discard(tmp_path):
    _run(EDITOR + textwrap.dedent('''
        invoke(window, "createTask")
        field(editor, "editorTitle").setProperty("text", "New task draft")
        field(editor, "editorNotes").setProperty("text", "Unsaved notes")
        editor.setProperty("schedMode", "timed")
        field(editor, "editorDate").setProperty("dateText", "2026-07-20")
        field(editor, "editorTime").setProperty("timeText", "14:30")
        field(editor, "editorDuration").setProperty("durationText", "45")
        editor.setProperty("recurEnabled", True)
        rule = field(editor, "editorRecurrence")
        rule.setProperty("interval", 3)
        rule.setProperty("currentPreset", "custom")
        rule.setProperty("customVisible", True)
        invoke(rule, "ruleEdited")
        invoke(editor, "close")
        assert store.load("editor:new")["rule"]["interval"] == 3
        assert (Path(__import__("sys").argv[1]) / "drafts.json").exists()

        invoke(window, "editTask")
        field(editor, "editorTitle").setProperty("text", "Edit task draft")
        field(editor, "editorNotes").setProperty("text", "Edit notes")
        invoke(editor, "close")
        assert store.load("editor:task", "existing")["title"] == "Edit task draft"
        assert repo.get_by_uid("existing").title == "Original task"
        assert len(repo.list_all()) == 1
    '''), tmp_path)

    _run(EDITOR + textwrap.dedent('''
        invoke(window, "createTask")
        assert editor.property("draftRestored")
        assert field(editor, "editorTitle").property("text") == "New task draft"
        assert field(editor, "editorNotes").property("text") == "Unsaved notes"
        assert field(editor, "editorDate").property("dateText") == "2026-07-20"
        assert field(editor, "editorTime").property("timeText") == "14:30"
        assert field(editor, "editorDuration").property("durationText") == "45"
        assert editor.property("recurEnabled")
        assert field(editor, "editorRecurrence").property("interval") == 3
        assert field(editor, "editorDraftNotice").property("text") == "Черновик восстановлен"
        invoke(editor, "close")

        invoke(window, "editTask")
        assert field(editor, "editorTitle").property("text") == "Edit task draft"
        assert field(editor, "editorNotes").property("text") == "Edit notes"
        assert not editor.property("recurEnabled")
        field(editor, "editorTitle").setProperty("text", "")
        invoke(editor, "submit")
        assert editor.property("visible"), "Validation failure must keep editor open"
        assert vm.editorError
        invoke(editor, "close")
        assert store.load("editor:task", "existing")["title"] == ""
        assert repo.get_by_uid("existing").title == "Original task"

        invoke(window, "editTask")
        assert field(editor, "editorTitle").property("text") == ""
        field(editor, "editorTitle").setProperty("text", "Saved edit")
        invoke(editor, "submit")
        assert repo.get_by_uid("existing").title == "Saved edit"
        assert store.load("editor:task", "existing") == {}
        assert store.load("editor:new")["title"] == "New task draft"

        invoke(window, "createTask")
        editor.setProperty("recurEnabled", False)
        invoke(editor, "submit")
        assert len(repo.list_all()) == 2
        assert store.load("editor:new") == {}

        invoke(window, "createTask")
        field(editor, "editorTitle").setProperty("text", "Discard me")
        invoke(editor, "close")
        invoke(window, "createTask")
        assert editor.property("draftRestored")
        invoke(editor, "discardDraft")
        assert field(editor, "editorTitle").property("text") == ""
        assert not editor.property("draftRestored")
        invoke(editor, "close")
        assert store.load("editor:new") == {}

        invoke(window, "editTask")
        field(editor, "editorTitle").setProperty("text", "Deleted task draft")
        invoke(editor, "close")
        repo.delete(original.id)
        invoke(window, "editTask")
        assert not editor.property("visible")
        assert store.load("editor:task", "existing") == {}
    '''), tmp_path)


QUICK = '''
component = QQmlComponent(engine, QUrl.fromLocalFile(str(
    Path("planner_desktop/qml/components/QuickAdd.qml").resolve())))
form = component.create()
assert form is not None, [error.toString() for error in component.errors()]
'''


def test_quick_add_draft_restart_validation_save_and_discard(tmp_path):
    _run(QUICK + textwrap.dedent('''
        form.setProperty("expanded", True)
        field(form, "quickAddTitle").setProperty("text", "Quick draft")
        field(form, "quickAddNotes").setProperty("text", "Quick notes")
        field(form, "quickAddCalendar").setProperty("checked", True)
        field(form, "quickAddDate").setProperty("dateText", "2026-07-22")
        field(form, "quickAddTime").setProperty("timeText", "11:30")
        field(form, "quickAddDuration").setProperty("durationText", "90")
        form.setProperty("priority", 2)
        form.setProperty("expanded", False)
        drafts.flush()  # The same shutdown flush wired by MainWindow.
        assert len(repo.list_all()) == 1
    '''), tmp_path)

    _run(QUICK + textwrap.dedent('''
        assert form.property("draftRestored")
        assert not form.property("expanded")
        assert field(form, "quickAddTitle").property("text") == "Quick draft"
        assert field(form, "quickAddNotes").property("text") == "Quick notes"
        assert field(form, "quickAddDate").property("dateText") == "2026-07-22"
        assert field(form, "quickAddTime").property("timeText") == "11:30"
        assert field(form, "quickAddDuration").property("durationText") == "90"
        assert form.property("priority") == 2
        field(form, "quickAddTitle").setProperty("text", "")
        invoke(form, "submit")
        assert len(repo.list_all()) == 1
        assert store.load("quick-add")["notes"] == "Quick notes"
        field(form, "quickAddTitle").setProperty("text", "Saved quick")
        invoke(form, "submit")
        assert len(repo.list_all()) == 2
        assert repo.list_all()[1].duration_minutes == 90
        assert store.load("quick-add") == {}
        field(form, "quickAddTitle").setProperty("text", "Discard quick")
        invoke(form, "clearForm")
        assert store.load("quick-add") == {}
        assert field(form, "quickAddTitle").property("text") == ""
        assert not form.property("draftRestored")
        drafts.flush()
    '''), tmp_path)


def test_quick_add_duration_tracks_preferences_until_explicitly_chosen(tmp_path):
    preferences = '''
from planner_desktop.viewmodels.preferences_viewmodel import PreferencesViewModel
prefs = PreferencesViewModel()
engine.rootContext().setContextProperty("prefsVm", prefs)
'''
    _run(preferences + QUICK + textwrap.dedent('''
        duration = field(form, "quickAddDuration")
        assert duration.property("durationText") == "60"
        prefs.setDefaultDurationMinutes(90)
        assert duration.property("durationText") == "90"
        assert store.load("quick-add") == {}, "Defaults alone are not a draft"
        field(form, "quickAddTitle").setProperty("text", "Duration draft")
        prefs.setDefaultDurationMinutes(120)
        assert duration.property("durationText") == "120"
        duration.setProperty("durationText", "45")
        prefs.setDefaultDurationMinutes(30)
        assert duration.property("durationText") == "45"
        drafts.flush()
    '''), tmp_path)
    _run(preferences + QUICK + textwrap.dedent('''
        duration = field(form, "quickAddDuration")
        assert form.property("draftRestored")
        assert duration.property("durationText") == "45"
        prefs.setDefaultDurationMinutes(90)
        assert duration.property("durationText") == "45"
        invoke(form, "clearForm")
        assert duration.property("durationText") == "90"
        prefs.setDefaultDurationMinutes(30)
        assert duration.property("durationText") == "30"
    '''), tmp_path)
