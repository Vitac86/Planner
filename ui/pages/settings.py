# ui/pages/settings.py
import flet as ft


class SettingsPage:
    def __init__(self, app):
        self.app = app

        self.status_calendar = ft.Text("Google: не подключено")
        if getattr(self.app.gcal, "calendar_id", None):
            self.status_calendar.value = (
                f"Google Calendar: подключено (календарь: {self.app.gcal.calendar_id})"
            )

        self.status_tasks = ft.Text(self.app.tasks_sync_status())

        self.connect_btn = ft.ElevatedButton(
            "Переподключить Google",
            icon=ft.Icons.LINK,
            on_click=self.connect_google,
        )
        self.resync_btn = ft.OutlinedButton(
            "Полная ресинхронизация",
            icon=ft.Icons.SYNC,
            on_click=self.full_resync,
        )
        self.cleanup_btn = ft.TextButton(
            "Очистить заметки от служебного JSON",
            icon=ft.Icons.CLEANING_SERVICES,
            on_click=self.cleanup_notes,
        )

        content = ft.Column(
            controls=[
                ft.Text("Настройки", size=24, weight=ft.FontWeight.BOLD),
                self.status_calendar,
                self.status_tasks,
                ft.Row([self.connect_btn, self.resync_btn], spacing=12),
                self.cleanup_btn,
            ],
            expand=True,
            spacing=16,
        )

        self.view = ft.Container(content=content, expand=True, padding=20)

    def connect_google(self, _):
        try:
            self.app.connect_google_services()
            self.status_calendar.value = (
                f"Google Calendar: подключено (календарь: {self.app.gcal.calendar_id})"
            )
            self.status_tasks.value = self.app.tasks_sync_status()
            self.app.page.snack_bar = ft.SnackBar(ft.Text("Google подключён"))
            self.app.page.snack_bar.open = True
            self.app.page.update()
        except Exception as e:
            self.status_calendar.value = f"Ошибка: {e}"
            self.app.page.update()

    def full_resync(self, _):
        try:
            self.app.full_resync_google_tasks()
            self.status_tasks.value = self.app.tasks_sync_status()
            self.app.page.snack_bar = ft.SnackBar(ft.Text("Полная синхронизация завершена"))
            self.app.page.snack_bar.open = True
            self.app.page.update()
        except Exception as e:
            self.status_tasks.value = f"Ошибка: {e}"
            self.app.page.update()

    def cleanup_notes(self, _):
        try:
            cleaned = self.app.cleanup_task_notes()
            self.app.page.snack_bar = ft.SnackBar(ft.Text(f"Очищено задач: {cleaned}"))
            self.app.page.snack_bar.open = True
            self.app.page.update()
        except Exception as e:
            self.status_tasks.value = f"Ошибка очистки: {e}"
            self.app.page.update()
