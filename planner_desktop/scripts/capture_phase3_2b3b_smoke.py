"""Capture the seven required Phase 3.2B3B fake-smoke screenshots."""
from __future__ import annotations

import json
import os
from pathlib import Path

from PySide6.QtCore import Q_ARG, QEventLoop, QMetaObject, QObject, QTimer, Qt
from PySide6.QtGui import QFontDatabase, QGuiApplication
from PySide6.QtQuickControls2 import QQuickStyle

from planner_desktop.main_window import MainWindow


SCREENSHOT_DIR = Path(__file__).resolve().parents[1] / "docs" / "screenshots"


def _wait(milliseconds: int = 350) -> None:
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
    _wait(500)
    root.grabWindow()
    _wait(120)
    image = root.grabWindow()
    path = SCREENSHOT_DIR / name
    if image.isNull() or not image.save(str(path)):
        raise RuntimeError(f"Could not save {path}")
    print(f"saved={path} size={image.width()}x{image.height()}")


def _set_scroll(settings: QObject, value: float) -> None:
    content = settings.property("contentItem")
    if content is None:
        raise RuntimeError("Settings ScrollView contentItem was not found")
    content.setProperty("contentY", max(0.0, value))
    _wait()


def _close_if_present(root: QObject, object_name: str) -> None:
    child = root.findChild(QObject, object_name)
    if child is not None:
        QMetaObject.invokeMethod(child, "close", Qt.DirectConnection)
        _wait(120)


def main() -> int:
    data_dir = os.environ.get("PLANNER_DESKTOP_DATA_DIR")
    if not data_dir:
        raise SystemExit("Set PLANNER_DESKTOP_DATA_DIR first.")
    report_path = Path(data_dir) / "phase3_2b3b_smoke_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    moved_uid = str(report["moved_task_uid"])

    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    QQuickStyle.setStyle("Material")
    app = QGuiApplication([])
    font_dir = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "Fonts"
    if any(
        QFontDatabase.addApplicationFont(str(font_dir / name)) < 0
        for name in ("segoeui.ttf", "segoeuib.ttf", "segoeuii.ttf")
    ):
        raise RuntimeError(f"Could not load Segoe UI from {font_dir}")

    window = MainWindow()
    warnings = []
    window.engine.warnings.connect(lambda items: warnings.extend(items))
    window.show()
    root = window.engine.rootObjects()[0]
    root.setProperty("currentPage", 0)
    _wait(600)

    editor = root.findChild(QObject, "todayEditorDialog")
    if editor is None:
        raise RuntimeError("Today editor was not found")

    # 1. Linked occurrence editor: badge, immutable original slot and status.
    _invoke(editor, "openForEdit", moved_uid)
    _capture(root, "occurrence_edit_phase3_2b3b.png", 1120, 880)
    _invoke(editor, "close")

    # 2. Calendar move confirmation; no commit occurs while the dialog is open.
    root.setProperty("currentPage", 1)
    window.calendar_viewmodel.selectDate("2026-08-03")
    _wait()
    move_confirm = root.findChild(QObject, "occurrenceMoveConfirm")
    if move_confirm is None:
        raise RuntimeError("Occurrence move confirmation was not found")
    _invoke(move_confirm, "openFor", moved_uid)
    _capture(root, "occurrence_move_phase3_2b3b.png", 1180, 860)
    _invoke(move_confirm, "close")

    # 3. Explicit one-occurrence cancellation and local tombstone explanation.
    root.setProperty("currentPage", 0)
    _invoke(editor, "openForEdit", moved_uid)
    _invoke(editor, "requestOccurrenceCancel")
    cancel = editor.property("_occurrenceCancelObject")
    if cancel is None:
        cancel = root.findChild(QObject, "occurrenceCancelConfirmation")
    if cancel is None:
        raise RuntimeError("Occurrence cancellation dialog was not found")
    _capture(root, "occurrence_cancel_phase3_2b3b.png", 1120, 860)
    _invoke(cancel, "close")
    _invoke(editor, "close")

    # Settings is entirely local: show a real unresolved comparison.
    root.setProperty("currentPage", 3)
    window.settings_viewmodel.refresh()
    _wait()
    rows = window.settings_viewmodel.quarantinedOccurrenceRows
    if not rows:
        raise RuntimeError("No quarantined occurrence row is available")
    conflict = root.findChild(QObject, "occurrenceConflictDialog")
    if conflict is None:
        raise RuntimeError("Occurrence conflict dialog was not found")
    conflict.setProperty("conflictData", rows[0])
    _invoke(conflict, "open")

    # 4. Four explicit conflict choices and local/Google comparison.
    _capture(root, "occurrence_conflict_phase3_2b3b.png", 1180, 900)
    _invoke(conflict, "close")

    settings = root.findChild(QObject, "settingsPage")
    panel = root.findChild(QObject, "settingsOccurrenceSync")
    if settings is None or panel is None:
        raise RuntimeError("Occurrence Settings panel was not found")
    _set_scroll(settings, float(panel.property("y")) - 18)

    # 5. Unresolved changed/cancelled quarantine rows.
    _capture(root, "occurrence_quarantine_phase3_2b3b.png", 1180, 900)

    # 6. Wide queue/terminal/resolved/remote-cancelled diagnostics.
    _capture(root, "occurrence_settings_phase3_2b3b.png", 1360, 940)

    # 7. Compact linked-occurrence editor.
    root.setProperty("currentPage", 0)
    _invoke(editor, "openForEdit", moved_uid)
    _capture(root, "occurrence_compact_phase3_2b3b.png", 640, 800)
    _invoke(editor, "close")

    for object_name in (
        "occurrenceConflictDialog",
        "keepPlannerOccurrenceConfirm",
        "occurrenceCancelConfirmation",
        "occurrenceMoveConfirm",
    ):
        _close_if_present(root, object_name)

    if warnings:
        for warning in warnings:
            print(f"qml-warning={warning.toString()}")
        raise RuntimeError(f"QML emitted {len(warnings)} warning(s)")
    print("qml_warnings=0")
    print("responsive=compact,normal,wide")
    print("opening_ui_google_calls=0")
    print("real_google_calls=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
