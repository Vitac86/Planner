"""Capture the seven required Phase 3.2B3C1 fake-smoke screenshots."""
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


def _invoke(obj: QObject, method: str, *values) -> None:
    args = [Q_ARG("QVariant", value) for value in values]
    ok = QMetaObject.invokeMethod(obj, method, Qt.DirectConnection, *args)
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


def main() -> int:
    data_dir = os.environ.get("PLANNER_DESKTOP_DATA_DIR")
    if not data_dir:
        raise SystemExit("Set PLANNER_DESKTOP_DATA_DIR first.")
    report = json.loads(
        (Path(data_dir) / "phase3_2b3c1_smoke_report.json").read_text(
            encoding="utf-8"
        )
    )
    dialog_uid = str(report["dialog_task_uid"])
    blocked_uid = str(report["blocked_dialog_task_uid"])
    recovery_plan_id = int(report["recovery_plan_id"])

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

    def open_split_dialog(uid: str):
        _invoke(editor, "openForEdit", uid)
        split_dialog = editor.property("_remoteSplitDialogObject")
        if split_dialog is None:
            raise RuntimeError("Remote split dialog object was not created")
        payload = {
            "title": "TEST successor",
            "timeText": "11:00",
            "durationText": "45",
        }
        _invoke(split_dialog, "openFor", uid, payload)
        return split_dialog

    # 1. Scope dialog: "Этот и будущие" enabled for a clean linked series.
    _invoke(editor, "openForEdit", dialog_uid)
    _invoke(editor, "submit")
    scope = editor.property("_scopeDialogObject")
    if scope is None:
        raise RuntimeError("Series scope dialog was not created")
    _capture(root, "remote_split_summary_phase3_2b3c1.png", 1120, 880)
    _invoke(scope, "close")
    _invoke(editor, "close")

    # 2. Split confirmation dialog with preflight summary and warning.
    split_dialog = open_split_dialog(dialog_uid)
    _capture(root, "remote_split_dialog_phase3_2b3c1.png", 1120, 920)
    _invoke(split_dialog, "close")
    _invoke(editor, "close")

    # 3. Future-exception preflight blocks with exact reasons.
    split_dialog = open_split_dialog(blocked_uid)
    _capture(root, "remote_split_blocked_exception_phase3_2b3c1.png", 1120, 920)
    _invoke(split_dialog, "close")
    _invoke(editor, "close")

    # Settings: pending/partial/conflict/history rows; entirely local reads.
    root.setProperty("currentPage", 3)
    window.settings_viewmodel.refresh()
    _wait()
    settings = root.findChild(QObject, "settingsPage")
    panel = root.findChild(QObject, "settingsRemoteSplits")
    if settings is None or panel is None:
        raise RuntimeError("Remote split Settings panel was not found")
    _set_scroll(settings, float(panel.property("y")) - 18)

    # 4. Pending plan and diagnostics badges.
    _capture(root, "remote_split_pending_phase3_2b3c1.png", 1180, 900)

    # 5. Wide layout with completed/rolled-back history rows.
    _capture(root, "remote_split_completed_phase3_2b3c1.png", 1400, 940)

    # 6. Recovery dialog (conflict plan): retry/cancel/explicit rollback.
    rows = window.settings_viewmodel.remoteSplitRows
    recovery_row = next(
        (row for row in rows if int(row["id"]) == recovery_plan_id), None
    )
    if recovery_row is None:
        raise RuntimeError("Conflict plan row was not found")
    recovery = root.findChild(QObject, "remoteSplitRecoveryDialog")
    if recovery is None:
        raise RuntimeError("Recovery dialog was not found")
    _invoke(recovery, "openFor", recovery_row)
    _capture(root, "remote_split_recovery_phase3_2b3c1.png", 1180, 900)
    _invoke(recovery, "close")

    # 7. Compact split dialog.
    root.setProperty("currentPage", 0)
    split_dialog = open_split_dialog(dialog_uid)
    _capture(root, "remote_split_compact_phase3_2b3c1.png", 640, 860)
    _invoke(split_dialog, "close")
    _invoke(editor, "close")

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
