"""Capture the seven required Phase 3.2A smoke screenshots.

Run after ``seed_phase3_2a_smoke`` with an explicitly isolated
``PLANNER_DESKTOP_DATA_DIR``. The script loads only local SQLite/QML state and
never invokes connect or manual sync.
"""
from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import Q_ARG, QEventLoop, QMetaObject, QObject, QTimer, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtQuickControls2 import QQuickStyle

from planner_desktop.main_window import MainWindow


SCREENSHOT_DIR = Path(__file__).resolve().parents[1] / "docs" / "screenshots"


def _wait(milliseconds: int = 280) -> None:
    loop = QEventLoop()
    QTimer.singleShot(milliseconds, loop.quit)
    loop.exec()


def _invoke(obj: QObject, method: str, value: str | None = None) -> None:
    if value is None:
        ok = QMetaObject.invokeMethod(obj, method, Qt.DirectConnection)
    else:
        ok = QMetaObject.invokeMethod(
            obj, method, Qt.DirectConnection, Q_ARG("QVariant", value)
        )
    if not ok:
        raise RuntimeError(f"Could not invoke {method}")
    _wait()


def _capture(root, name: str, width: int = 1240, height: int = 800) -> None:
    root.resize(width, height)
    root.update()
    _wait(420)
    image = root.grabWindow()
    path = SCREENSHOT_DIR / name
    if image.isNull() or not image.save(str(path)):
        raise RuntimeError(f"Could not save {path}")
    print(f"saved={path} size={image.width()}x{image.height()}")


def _occurrence(window: MainWindow, series_uid: str, prefix: str, *, exception=None):
    for row in window.repository.list_by_series(series_uid):
        if not row.is_deleted and row.occurrence_key.startswith(prefix):
            if exception is None or row.is_series_exception is exception:
                return row
    raise RuntimeError(f"Occurrence not found: {series_uid} {prefix}")


def main() -> int:
    if not os.environ.get("PLANNER_DESKTOP_DATA_DIR"):
        raise SystemExit(
            "Set PLANNER_DESKTOP_DATA_DIR to an isolated smoke directory first."
        )

    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    QQuickStyle.setStyle("Material")
    app = QGuiApplication([])
    window = MainWindow()
    warnings = []
    window.engine.warnings.connect(lambda items: warnings.extend(items))
    window.show()
    root = window.engine.rootObjects()[0]
    _wait(500)

    if window.repository.schema_version() != 6:
        raise RuntimeError("Smoke profile did not open schema v6")
    if len(window.recurrence_service.list_series()) < 7:
        raise RuntimeError("Expected all seeded local series")
    if len(window.template_service.list_templates()) != 2:
        raise RuntimeError("Expected ordinary and recurring templates")
    if not window.settings_viewmodel.hasSyncQueue:
        raise RuntimeError("Manual-sync queue controls are unavailable")

    ordinary = _occurrence(
        window, "smoke-series-daily", "2026-07-16", exception=False
    )
    exception = _occurrence(
        window, "smoke-series-daily", "2026-07-15", exception=True
    )
    tombstones = [
        row for row in window.repository.list_by_series("smoke-series-weekdays")
        if row.is_deleted and row.occurrence_key.startswith("2026-07-16")
    ]
    if len(tombstones) != 1:
        raise RuntimeError("Deleted occurrence did not survive restart")
    if any(
        row.google_calendar_event_id or row.google_calendar_recurring_event_id
        for series in window.recurrence_service.list_series()
        for row in window.repository.list_by_series(series.uid)
    ):
        raise RuntimeError("Local series acquired Google metadata")

    root.setProperty("currentPage", 0)
    editor = root.findChild(QObject, "todayEditorDialog")
    if editor is None:
        raise RuntimeError("Today task editor was not found")

    _invoke(editor, "openForEdit", ordinary.uid)
    _capture(root, "recurrence_editor_phase3_2a.png")

    _invoke(editor, "submit")
    scope = root.findChild(QObject, "seriesScopeDialog")
    if scope is None:
        raise RuntimeError("Series scope dialog was not found")
    if not scope.property("visible"):
        raise RuntimeError("Series scope dialog did not open")
    _capture(root, "recurrence_scope_dialog_phase3_2a.png")
    _invoke(scope, "close")
    _invoke(editor, "close")

    root.setProperty("currentPage", 1)
    window.calendar_viewmodel.selectDate("2026-07-15")
    window.calendar_viewmodel.refresh()
    _wait()
    _capture(root, "calendar_local_series_phase3_2a.png")

    root.setProperty("currentPage", 0)
    _invoke(editor, "openForEdit", exception.uid)
    _capture(root, "recurrence_exception_phase3_2a.png")
    _invoke(editor, "close")

    _invoke(editor, "openForCreate", "")
    _invoke(editor, "openTemplatePicker")
    picker = root.findChild(QObject, "taskTemplatePicker")
    if picker is None or not picker.property("visible"):
        raise RuntimeError("Template picker did not open")
    _capture(root, "template_picker_phase3_2a.png")
    _invoke(picker, "close")
    _invoke(editor, "close")

    root.setProperty("currentPage", 3)
    window.settings_viewmodel.refresh()
    settings = root.findChild(QObject, "settingsPage")
    if settings is None:
        raise RuntimeError("Settings page was not found")
    _invoke(settings, "scrollToTemplates")
    _capture(root, "settings_templates_phase3_2a.png")

    root.setProperty("currentPage", 0)
    _invoke(editor, "openForEdit", ordinary.uid)
    _capture(root, "recurrence_compact_phase3_2a.png", 680, 560)
    _invoke(editor, "close")

    if warnings:
        for warning in warnings:
            print(f"qml-warning={warning.toString()}")
        raise RuntimeError(f"QML emitted {len(warnings)} warning(s)")
    print("qml_warnings=0")
    print("restart_persistence=true")
    print("manual_sync_controls_present=true")
    print("automatic_google_calls=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
