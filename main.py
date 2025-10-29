# planner/main.py
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import flet as ft

from core.settings import APP_NAME, UI
from storage.db import init_db
from ui.app_shell import AppShell



def main(page: ft.Page):
    page.title = UI.app_title
    page.theme_mode = UI.theme_mode
    page.theme = ft.Theme(color_scheme_seed=UI.color_scheme_seed)
    page.appbar = ft.AppBar(title=ft.Text(APP_NAME), center_title=False)
    page.padding = 0
    page.window_min_width = UI.window_min_width
    page.window_min_height = UI.window_min_height

    init_db()
    shell = AppShell(page)
    shell.mount()

ft.app(target=main)
