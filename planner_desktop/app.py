"""Точка входа нового PySide6/QML десктопа.

Запускается только через run_desktop.py в корне репозитория.
Старое Flet-приложение (main.py) не импортируется и не затрагивается.
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
