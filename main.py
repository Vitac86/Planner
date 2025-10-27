# planner/main.py
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import flet as ft
from storage.db import init_db
from ui.app_shell import AppShell



def main(page: ft.Page):
    page.title = "Planner"
    page.theme_mode = "system"
    page.theme = ft.Theme(color_scheme_seed=ft.Colors.INDIGO)  # приятный базовый цвет
    page.appbar = ft.AppBar(title=ft.Text("Planner"), center_title=False)
    page.padding = 0
    page.window_min_width = 900
    page.window_min_height = 600

    init_db()
    shell = AppShell(page)
    shell.mount()

ft.app(target=main)
