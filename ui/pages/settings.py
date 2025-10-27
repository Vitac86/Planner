# ui/pages/settings.py
import flet as ft

class SettingsPage:
    def __init__(self, app):
        self.app = app
        self.status = ft.Text("Google: не подключено")
        if getattr(self.app.gcal, "calendar_id", None):
            self.status.value = f"Google: подключено (календарь: {self.app.gcal.calendar_id})"

        self.connect_btn = ft.ElevatedButton(
            "Подключить Google",
            icon=ft.Icons.LINK,          # в Flet 0.28+: ft.Icons.*
            on_click=self.connect_google
        )

        content = ft.Column(
            controls=[
                ft.Text("Настройки", size=24, weight=ft.FontWeight.BOLD),
                self.status,
                self.connect_btn,
            ],
            expand=True,
            spacing=16,
        )

        # В Flet 0.28 отступы задаём контейнером:
        self.view = ft.Container(content=content, expand=True, padding=20)

    def connect_google(self, _):
        try:
            self.app.gcal.connect()
            self.status.value = f"Google: подключено (календарь: {self.app.gcal.calendar_id})"
            self.app.page.snack_bar = ft.SnackBar(ft.Text("Google подключён"))
            self.app.page.snack_bar.open = True
            self.app.page.update()
        except Exception as e:
            self.status.value = f"Ошибка: {e}"
            self.app.page.update()
