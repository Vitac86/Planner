"""Capture the six required Phase 3.1 smoke screenshots.

The script requires an explicitly isolated ``PLANNER_DESKTOP_DATA_DIR`` and
never starts manual sync. It is intended to run after ``seed_phase3_smoke``.
"""
from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import Q_ARG, QEventLoop, QMetaObject, QObject, QTimer, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtQuickControls2 import QQuickStyle

from planner_desktop.main_window import MainWindow


SCREENSHOT_DIR = Path(__file__).resolve().parents[1] / "docs" / "screenshots"


def _wait(milliseconds: int = 240) -> None:
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


def _capture(root, name: str, width: int = 1240, height: int = 800) -> None:
    root.resize(width, height)
    _wait()
    image = root.grabWindow()
    path = SCREENSHOT_DIR / name
    if image.isNull() or not image.save(str(path)):
        raise RuntimeError(f"Could not save {path}")
    print(f"saved={path} size={image.width()}x{image.height()}")


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
    _wait(400)

    search_vm = window.search_viewmodel
    search_vm.clearFilters()
    search_vm.clearSelection()
    search_vm.setQuery("отчёт")
    search_vm.openSearch()
    _wait()
    _capture(root, "global_search_phase3_1.png")

    search_vm.setStatusFilter("active")
    search_vm.toggleTagFilter("Работа")
    _capture(root, "search_filters_phase3_1.png")

    search_vm.closeSearch()
    root.setProperty("currentPage", 0)
    editor = root.findChild(QObject, "todayEditorDialog")
    if editor is None:
        raise RuntimeError("Today task editor was not found")
    _invoke(editor, "openForEdit", "smoke-ru-report")
    _capture(root, "task_tags_editor_phase3_1.png")
    _invoke(editor, "close")

    root.setProperty("currentPage", 3)
    window.settings_viewmodel.refresh()
    _capture(root, "settings_tag_management_phase3_1.png")

    root.setProperty("currentPage", 0)
    search_vm.clearFilters()
    search_vm.clearSelection()
    search_vm.setQuery("задача для прокрутки")
    search_vm.openSearch()
    for uid in ("smoke-scroll-00", "smoke-scroll-01", "smoke-scroll-02"):
        search_vm.selectTaskWithModifiers(uid, True, False)
    _capture(root, "bulk_actions_phase3_1.png")

    search_vm.clearSelection()
    search_vm.clearFilters()
    search_vm.setQuery("отчёт")
    _capture(root, "search_compact_phase3_1.png", 680, 560)

    if warnings:
        for warning in warnings:
            print(f"qml-warning={warning.toString()}")
        raise RuntimeError(f"QML emitted {len(warnings)} warning(s)")
    print("qml_warnings=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
