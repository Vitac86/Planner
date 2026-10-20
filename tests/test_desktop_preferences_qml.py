"""Exercise real preferences controls and durable settings in an isolated Qt GUI."""
import os
from pathlib import Path
import subprocess
import sys
import textwrap


def test_preferences_panel_controls_save_and_restore_values(tmp_path):
    root = Path(__file__).resolve().parents[1]
    script = textwrap.dedent('''
        import sys
        from pathlib import Path
        from PySide6.QtCore import QObject, QMetaObject, QPointF, QUrl, Q_ARG
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtQml import QQmlComponent, QQmlEngine
        from PySide6.QtQuick import QQuickItem, QQuickWindow
        from PySide6.QtQuickControls2 import QQuickStyle
        from PySide6.QtTest import QTest
        from planner_desktop.storage.preferences_store import PreferencesStore
        from planner_desktop.viewmodels.preferences_viewmodel import PreferencesViewModel

        app = QGuiApplication([])
        QQuickStyle.setStyle("Material")
        path = Path(sys.argv[1])
        vm = PreferencesViewModel(PreferencesStore(path))
        engine = QQmlEngine()
        component = QQmlComponent(engine, QUrl.fromLocalFile(str(
            Path("planner_desktop/qml/components/PreferencesPanel.qml").resolve()
        )))
        panel = component.createWithInitialProperties({"vm": vm, "width": 560})
        assert panel is not None, [error.toString() for error in component.errors()]

        def control(name):
            result = panel.findChild(QObject, name)
            assert result is not None, name
            return result

        switch = control("completionStatsSwitch")
        switch.setProperty("checked", False)
        assert QMetaObject.invokeMethod(switch, "toggled")
        assert vm.showCompletionStats is False

        page = control("startupPageChoice")
        page.setProperty("currentIndex", 1)
        assert QMetaObject.invokeMethod(page, "activated", Q_ARG(int, 1))
        assert vm.startupPage == "calendar"

        for name, value in (("workStartHourInput", 9),
                            ("workEndHourInput", 18),
                            ("defaultDurationInput", 45)):
            widget = control(name)
            widget.setProperty("value", value)
            assert QMetaObject.invokeMethod(widget, "valueModified")
        assert (vm.workStartHour, vm.workEndHour) == (9, 18)
        assert vm.defaultDurationMinutes == 45
        restored = PreferencesViewModel(PreferencesStore(path))
        assert restored.showCompletionStats is False
        assert restored.startupPage == "calendar"
        assert restored.defaultDurationMinutes == 45

        # External state changes still update controls after user interactions.
        assert vm.setShowCompletionStats(True)
        assert switch.property("checked") is True
        assert vm.setDefaultDurationMinutes(30)
        assert control("defaultDurationInput").property("value") == 30
        assert vm.errorMessage == ""

        # The production Material style needs room for both step buttons and
        # editable numbers, even at the minimum 680px application width.
        window = QQuickWindow()
        window.resize(680, 560)
        panel.setParentItem(window.contentItem())
        panel.setWidth(680 - 236 - 32)  # Sidebar and compact Settings margins.
        panel.setHeight(panel.property("implicitHeight"))
        assert vm.setWorkHours(12, 24)
        assert vm.setDefaultDurationMinutes(1440)
        window.show()
        QTest.qWait(100)
        for name, text in (("workStartHourInput", "12"),
                           ("workEndHourInput", "24"),
                           ("defaultDurationInput", "1440")):
            widget = control(name)
            content = widget.property("contentItem")
            assert content.property("text") == text
            assert content.property("width") >= content.property("contentWidth")
            right = widget.mapToItem(panel, QPointF(widget.width(), 0)).x()
            assert right <= panel.width()
        window.close()
    ''')
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path / "preferences.json")],
        cwd=root, env={**os.environ, "QT_QPA_PLATFORM": "offscreen", "QT_QUICK_BACKEND": "software"},
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
