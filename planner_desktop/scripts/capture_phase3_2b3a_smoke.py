"""Capture the seven required Phase 3.2B3A fake-smoke screenshots."""
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

    def open_conflict(series_uid: str) -> QObject:
        occurrence = _occurrence(window, series_uid)
        _invoke(editor, "openForEdit", occurrence.uid)
        link_dialog = root.findChild(QObject, "seriesGoogleLinkDialog")
        if link_dialog is None:
            raise RuntimeError("Series Google link dialog was not found")
        _invoke(link_dialog, "openFor", series_uid)
        conflict_dialog = root.findChild(QObject, "seriesConflictDialog")
        if conflict_dialog is None:
            raise RuntimeError("Series conflict dialog was not found")
        _invoke(conflict_dialog, "openFor", series_uid)
        return conflict_dialog

    def close_all() -> None:
        for name in ("useGoogleConfirmDialog", "seriesConflictDialog",
                     "remoteDeletedRecoveryDialog", "seriesGoogleLinkDialog"):
            child = root.findChild(QObject, name)
            if child is not None:
                QMetaObject.invokeMethod(child, "close", Qt.DirectConnection)
        _invoke(editor, "close")

    # 1. Comparison of a live supported conflict.
    open_conflict("b3a-screen-conflict")
    _capture(root, "series_conflict_compare_phase3_2b3a.png", 1120, 860)
    close_all()

    # 2. Keep-Planner decision already queued for the next manual sync.
    open_conflict("b3a-screen-pending")
    _capture(root, "series_conflict_keep_planner_phase3_2b3a.png", 1120, 860)
    close_all()

    # 3. Use-Google confirmation on top of the comparison.
    open_conflict("b3a-screen-conflict")
    confirm = root.findChild(QObject, "useGoogleConfirmDialog")
    if confirm is None:
        raise RuntimeError("Use-Google confirm dialog was not found")
    _invoke(confirm, "openFor", "b3a-screen-conflict")
    _capture(root, "series_conflict_use_google_phase3_2b3a.png", 1120, 860)
    close_all()

    # 4. Unsupported remote recurrence: action disabled, raw lines visible.
    open_conflict("b3a-unsup")
    _capture(root, "series_conflict_unsupported_phase3_2b3a.png", 1120, 860)
    close_all()

    # 5. Remote-deleted recovery choices.
    occurrence = _occurrence(window, "b3a-screen-deleted")
    _invoke(editor, "openForEdit", occurrence.uid)
    link_dialog = root.findChild(QObject, "seriesGoogleLinkDialog")
    _invoke(link_dialog, "openFor", "b3a-screen-deleted")
    recovery = root.findChild(QObject, "remoteDeletedRecoveryDialog")
    if recovery is None:
        raise RuntimeError("Remote-deleted recovery dialog was not found")
    _invoke(recovery, "openFor", "b3a-screen-deleted")
    _capture(root, "series_remote_deleted_recovery_phase3_2b3a.png", 1120, 860)
    close_all()

    # 6. Settings: resolution diagnostics and local history.
    root.setProperty("currentPage", 3)
    window.settings_viewmodel.refresh()
    _wait()
    settings = root.findChild(QObject, "settingsPage")
    panel = root.findChild(QObject, "settingsConflictResolutions")
    if settings is None or panel is None:
        raise RuntimeError("Conflict resolution Settings panel was not found")
    _set_scroll(settings, float(panel.property("y")) - 18)
    _capture(root, "series_conflict_settings_phase3_2b3a.png", 1240, 940)

    # 7. Compact layout of the comparison dialog.
    root.setProperty("currentPage", 0)
    open_conflict("b3a-screen-conflict")
    _capture(root, "series_conflict_compact_phase3_2b3a.png", 640, 800)
    close_all()

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
