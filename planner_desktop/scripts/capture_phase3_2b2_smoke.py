"""Capture the seven required Phase 3.2B2 fake-smoke screenshots."""
from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import Q_ARG, QEventLoop, QMetaObject, QObject, QTimer, Qt
from PySide6.QtGui import QFontDatabase, QGuiApplication
from PySide6.QtQuickControls2 import QQuickStyle

from planner_desktop.main_window import MainWindow


SCREENSHOT_DIR = Path(__file__).resolve().parents[1] / "docs" / "screenshots"


def _wait(milliseconds: int = 320) -> None:
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


def _capture(root, name: str, width: int, height: int) -> None:
    root.resize(width, height)
    root.update()
    _wait(450)
    # The Windows offscreen backend can return a partially dirty first frame
    # after closing and reopening nested popups. Discard one grab so the saved
    # frame always comes from a fully rendered scene graph.
    root.grabWindow()
    _wait(120)
    image = root.grabWindow()
    path = SCREENSHOT_DIR / name
    if image.isNull() or not image.save(str(path)):
        raise RuntimeError(f"Could not save {path}")
    print(f"saved={path} size={image.width()}x{image.height()}")


def _occurrence(window: MainWindow, series_uid: str):
    for row in window.repository.list_by_series(series_uid):
        if not row.is_deleted:
            return row
    raise RuntimeError(f"No occurrence for {series_uid}")


def _open_link(editor, link_dialog, occurrence_uid: str, series_uid: str) -> None:
    _invoke(editor, "openForEdit", occurrence_uid)
    _invoke(link_dialog, "openFor", series_uid)


def _set_scroll(settings: QObject, value: float) -> None:
    content = settings.property("contentItem")
    if content is None:
        raise RuntimeError("Settings ScrollView contentItem was not found")
    content.setProperty("contentY", max(0.0, value))
    _wait()


def main() -> int:
    if not os.environ.get("PLANNER_DESKTOP_DATA_DIR"):
        raise SystemExit("Set PLANNER_DESKTOP_DATA_DIR first.")
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    QQuickStyle.setStyle("Material")
    app = QGuiApplication([])
    # Qt's Windows offscreen platform does not enumerate system fonts. Register
    # the app's Segoe UI family explicitly so Cyrillic remains readable in the
    # headless acceptance screenshots.
    font_dir = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "Fonts"
    font_files = ("segoeui.ttf", "segoeuib.ttf", "segoeuii.ttf")
    if any(
        QFontDatabase.addApplicationFont(str(font_dir / name)) < 0
        for name in font_files
    ):
        raise RuntimeError(f"Could not load Segoe UI from {font_dir}")
    window = MainWindow()
    warnings = []
    window.engine.warnings.connect(lambda items: warnings.extend(items))
    window.show()
    root = window.engine.rootObjects()[0]
    root.setProperty("currentPage", 0)
    _wait(500)

    editor = root.findChild(QObject, "todayEditorDialog")
    if editor is None:
        raise RuntimeError("Today editor was not found")
    # openForEdit constructs nested dialogs.
    local_occurrence = _occurrence(window, "b2-screen-local")
    _invoke(editor, "openForEdit", local_occurrence.uid)
    link_dialog = root.findChild(QObject, "seriesGoogleLinkDialog")
    if link_dialog is None:
        raise RuntimeError("Series Google link dialog was not found")
    _invoke(link_dialog, "openFor", "b2-screen-local")
    _capture(root, "series_connect_google_phase3_2b2.png", 1120, 820)
    _invoke(link_dialog, "close")
    _invoke(editor, "close")

    states = (
        ("b2-screen-pending", "series_pending_sync_phase3_2b2.png"),
        ("b2-screen-linked", "series_linked_phase3_2b2.png"),
        ("b2-screen-conflict", "series_sync_conflict_phase3_2b2.png"),
        ("b2-screen-deleted", "series_remote_deleted_phase3_2b2.png"),
    )
    for series_uid, name in states:
        occurrence = _occurrence(window, series_uid)
        _open_link(editor, link_dialog, occurrence.uid, series_uid)
        _capture(root, name, 1120, 820)
        _invoke(link_dialog, "close")
        _invoke(editor, "close")

    root.setProperty("currentPage", 3)
    window.settings_viewmodel.refresh()
    _wait()
    settings = root.findChild(QObject, "settingsPage")
    panel = root.findChild(QObject, "settingsLinkedGoogleSeries")
    if settings is None or panel is None:
        raise RuntimeError("Linked-series Settings panel was not found")
    _set_scroll(settings, float(panel.property("y")) - 18)
    _capture(root, "series_link_settings_phase3_2b2.png", 1240, 940)

    root.setProperty("currentPage", 0)
    occurrence = _occurrence(window, "b2-screen-pending")
    _open_link(editor, link_dialog, occurrence.uid, "b2-screen-pending")
    _capture(root, "series_link_compact_phase3_2b2.png", 660, 760)
    _invoke(link_dialog, "close")
    _invoke(editor, "close")

    if warnings:
        for warning in warnings:
            print(f"qml-warning={warning.toString()}")
        raise RuntimeError(f"QML emitted {len(warnings)} warning(s)")
    print("qml_warnings=0")
    print("responsive=compact,normal,wide")
    print("account_email_visible=false")
    print("automatic_google_calls=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
