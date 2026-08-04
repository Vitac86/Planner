"""Точка входа PySide6/QML десктопа Planner.

Запускается только через run_desktop.py в корне репозитория
(или start_planner.bat на Windows).
"""
from __future__ import annotations

import sys

from PySide6.QtGui import QGuiApplication
from PySide6.QtQuickControls2 import QQuickStyle

from planner_desktop.main_window import MainWindow


def main() -> int:
    QQuickStyle.setStyle("Material")  # светлая тема задаётся в Main.qml
    app = QGuiApplication(sys.argv)
    app.setApplicationName("Planner Desktop (experimental)")
    app.setOrganizationName("Planner")

    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
