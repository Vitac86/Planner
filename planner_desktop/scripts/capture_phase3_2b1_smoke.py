"""Capture the six required Phase 3.2B1 read-only catalog screenshots."""
from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QEventLoop, QObject, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtQuickControls2 import QQuickStyle

from planner_desktop.main_window import MainWindow


SCREENSHOT_DIR = Path(__file__).resolve().parents[1] / "docs" / "screenshots"


def _wait(milliseconds: int = 280) -> None:
    loop = QEventLoop()
    QTimer.singleShot(milliseconds, loop.quit)
    loop.exec()


def _capture(root, name: str, width: int = 1240, height: int = 900) -> None:
    root.resize(width, height)
    _wait()
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
    root.setProperty("currentPage", 3)
    window.settings_viewmodel.refresh()
    _wait(450)

    settings = root.findChild(QObject, "settingsPage")
    catalog = root.findChild(QObject, "settingsGoogleSeriesCatalog")
    diagnostics = root.findChild(QObject, "settingsDiagnosticsPanel")
    if settings is None or catalog is None or diagnostics is None:
        raise RuntimeError("Required Settings smoke objects were not found")
    base = float(catalog.property("y")) - 18

    _set_scroll(settings, base)
    _capture(root, "google_series_catalog_phase3_2b1.png", 1240, 980)

    _set_scroll(settings, base + 150)
    _capture(root, "google_series_supported_phase3_2b1.png", 980, 760)

    _set_scroll(settings, base + 390)
    _capture(root, "google_series_unsupported_phase3_2b1.png", 980, 760)

    _set_scroll(settings, base + 650)
    _capture(root, "google_series_cancelled_phase3_2b1.png", 980, 760)

    root.resize(660, 760)
    _wait()
    # Layout reflow changes the catalog y coordinate.
    base = float(catalog.property("y")) - 12
    _set_scroll(settings, base)
    _capture(root, "google_series_compact_phase3_2b1.png", 660, 760)

    root.resize(1240, 900)
    _wait()
    _set_scroll(settings, float(diagnostics.property("y")) - 24)
    _capture(root, "google_series_diagnostics_phase3_2b1.png", 1240, 900)

    if warnings:
        for warning in warnings:
            print(f"qml-warning={warning.toString()}")
        raise RuntimeError(f"QML emitted {len(warnings)} warning(s)")
    print("qml_warnings=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
