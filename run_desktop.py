"""Запуск нового экспериментального десктопа (PySide6 + QML).

    python run_desktop.py

Старое Flet-приложение запускается по-прежнему через main.py и этим
файлом никак не затрагивается.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from planner_desktop.app import main

if __name__ == "__main__":
    sys.exit(main())
