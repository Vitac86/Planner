"""Exercise integrated Qt bindings, shortcut focus and profile restoration."""
import os
from pathlib import Path
import subprocess
import sys
import textwrap


def test_window_preferences_drafts_and_undo_shortcut(tmp_path):
    root = Path(__file__).resolve().parents[1]
    script = textwrap.dedent('''
        import os
        from pathlib import Path
        from PySide6.QtCore import QObject, Qt, QMetaObject, Q_ARG
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtQuickControls2 import QQuickStyle
        from PySide6.QtTest import QTest
        import shiboken6
        from planner_desktop.storage.preferences_store import PreferencesStore
        from planner_desktop.storage.draft_store import DraftStore
        from planner_desktop.main_window import MainWindow

        profile = Path(os.environ["PLANNER_DESKTOP_DATA_DIR"])
        prefs = PreferencesStore(profile / "preferences.json")
        prefs.update(startup_page="calendar", calendar_mode="day",
                     work_start_hour=9, work_end_hour=19,
                     default_duration_minutes=90, show_completion_stats=False)
        QQuickStyle.setStyle("Material")
        app = QGuiApplication([])
        window = MainWindow()
        window.show()
        view = window.engine.rootObjects()[0]
        view.requestActivate()
        QTest.qWait(100)
        assert view.property("currentPage") == 1
        assert window.calendar_viewmodel.displayMode == "day"
        today = view.findChild(QObject, "todayPage")
        assert today.property("showCompletionStats") is False

        # Changing startup preference configures the next launch only.
        view.setProperty("currentPage", 3)
        window.preferences_viewmodel.setStartupPage("today")
        assert view.property("currentPage") == 3
        window.preferences_viewmodel.setStartupPage("last")

        assert window.today_viewmodel.addQuick("Вернуть задачу", 0)
        uid = window.repository.list_all()[0].uid
        assert window.today_viewmodel.deleteTask(uid)
        assert window.undo_viewmodel.canUndo
        view.setProperty("currentPage", 0)
        QTest.qWait(30)
        title = view.findChild(QObject, "quickAddTitle")
        assert title is not None
        title.setProperty("text", "Черновик")
        assert QMetaObject.invokeMethod(title, "forceActiveFocus")
        QTest.keyClick(view, Qt.Key_X)
        assert title.property("text") != "Черновик"
        QTest.keyClick(view, Qt.Key_Z, Qt.ControlModifier)
        assert title.property("text") == "Черновик"
        assert window.repository.get_by_uid(uid).is_deleted

        # Outside text input, the same shortcut restores the deleted task.
        preferences_switch = view.findChild(QObject, "completionStatsSwitch")
        view.setProperty("currentPage", 3)
        assert QMetaObject.invokeMethod(preferences_switch, "forceActiveFocus")
        QTest.keyClick(view, Qt.Key_Z, Qt.ControlModifier)
        QTest.qWait(30)
        assert not window.repository.get_by_uid(uid).is_deleted, window.undo_viewmodel.statusMessage

        # Error cards route to the common editor, which blocks window shortcuts.
        window.sync_errors_viewmodel.openTask(uid)
        QTest.qWait(30)
        editor = view.findChild(QObject, "syncErrorTaskEditor")
        assert editor is not None and editor.property("visible")
        assert view.property("anyDialogOpen")
        assert QMetaObject.invokeMethod(editor, "close")
        assert QMetaObject.invokeMethod(editor, "deleteRequested", Q_ARG(str, uid))
        confirmation = view.findChild(QObject, "syncErrorDeleteDialog")
        assert confirmation.property("visible") and view.property("anyDialogOpen")
        assert not window.repository.get_by_uid(uid).is_deleted
        assert QMetaObject.invokeMethod(confirmation, "close")

        view.setProperty("currentPage", 2)
        view.close()
        QTest.qWait(30)
        restored = PreferencesStore(profile / "preferences.json")
        assert restored.settings.last_page_index == 2
        assert restored.settings.startup_page == "last"
        assert DraftStore(profile / "drafts.json").load("quick-add", "")["title"] == "Черновик"
        window.settings_viewmodel.stopAutoSync()
        shiboken6.delete(window.engine)
    ''')
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=root,
        env={**os.environ, "QT_QPA_PLATFORM": "offscreen", "QT_QUICK_BACKEND": "software",
             "PLANNER_DESKTOP_DATA_DIR": str(tmp_path), "PLANNER_DESKTOP_DEMO": "0"},
        capture_output=True, text=True, timeout=45,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "ReferenceError:" not in result.stderr, result.stderr
    assert "TypeError:" not in result.stderr, result.stderr
