"""Единственная точка входа приложения Planner (PySide6 + QML).

    python run_desktop.py

На Windows то же самое делает start_planner.bat (двойной клик в
Проводнике). Возвращает код выхода Qt-приложения.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from planner_desktop.app import main

if __name__ == "__main__":
    sys.exit(main())
