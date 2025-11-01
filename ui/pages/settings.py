# ui/pages/settings.py
from datetime import timezone
import flet as ft


class SettingsPage:
    def __init__(self, app):
        self.app = app

        self.status_calendar = ft.Text()
        self.status_tasks = ft.Text()
        self.last_calendar_pull = ft.Text()
        self.last_tasks_pull = ft.Text()
        self.last_push = ft.Text()

        self.connect_btn = ft.ElevatedButton(
            "Переподключить Google",
            icon=ft.Icons.LINK,
            on_click=self.connect_google,
        )
        self.reset_token_btn = ft.OutlinedButton(
            "Сбросить syncToken",
            icon=ft.Icons.REMOVE_CIRCLE_OUTLINE,
            on_click=self.reset_sync_token,
        )
        self.resync_btn = ft.OutlinedButton(
            "Полная ресинхронизация",
            icon=ft.Icons.SYNC,
            on_click=self.full_resync,
        )
        self.refresh_log_btn = ft.TextButton(
            "Обновить лог",
            icon=ft.Icons.ARTICLE,
            on_click=self.refresh_log,
        )

        self.log_view = ft.Text("", selectable=True)

        content = ft.Column(
            controls=[
                ft.Text("Настройки", size=24, weight=ft.FontWeight.BOLD),
                self.status_calendar,
                self.status_tasks,
                self.last_calendar_pull,
                self.last_tasks_pull,
                self.last_push,
                ft.Row([self.connect_btn, self.reset_token_btn, self.resync_btn], spacing=12),
                ft.Column([
                    ft.Text("Лог синхронизации", size=18, weight=ft.FontWeight.W_600),
                    ft.Container(self.log_view, height=200, padding=10, bgcolor=ft.colors.SURFACE_VARIANT),
                    self.refresh_log_btn,
                ], spacing=8),
            ],
            expand=True,
            spacing=16,
        )

        self.view = ft.Container(content=content, expand=True, padding=20)
        self.refresh_status()

    def _format_dt(self, value) -> str:
        if not value:
            return "—"
        if getattr(value, "tzinfo", None) is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")

    def refresh_status(self):
        status = self.app.sync_status() or {}
        calendar = status.get("calendar", {})
        tasks = status.get("tasks", {})

        calendar_id = calendar.get("calendarId") or "—"
        token_state = "да" if calendar.get("syncToken") else "нет"
        self.status_calendar.value = f"Google Calendar: {calendar_id} (syncToken: {token_state})"
        self.last_calendar_pull.value = (
            "Последний pull Calendar: " + self._format_dt(calendar.get("lastPullAt"))
        )

        tasklist = tasks.get("tasklist") or "—"
        self.status_tasks.value = f"Google Tasks: {tasklist}"
        updated_min = tasks.get("updatedMin")
        suffix = f" (updatedMin: {self._format_dt(updated_min)})" if updated_min else ""
        self.last_tasks_pull.value = (
            "Последний pull Tasks: " + self._format_dt(tasks.get("lastPullAt")) + suffix
        )
        self.last_push.value = "Последний push: " + self._format_dt(status.get("lastPushAt"))

        self.log_view.value = self.app.read_sync_log()

    def connect_google(self, _):
        try:
            self.app.connect_google_services()
            self.refresh_status()
            self.app.page.snack_bar = ft.SnackBar(ft.Text("Google подключён"))
            self.app.page.snack_bar.open = True
            self.app.page.update()
        except Exception as e:
            self.status_calendar.value = f"Ошибка: {e}"
            self.app.page.update()

    def reset_sync_token(self, _):
        try:
            self.app.reset_calendar_sync()
            self.refresh_status()
            self.app.page.snack_bar = ft.SnackBar(ft.Text("syncToken сброшен"))
            self.app.page.snack_bar.open = True
            self.app.page.update()
        except Exception as e:
            self.status_calendar.value = f"Ошибка сброса: {e}"
            self.app.page.update()

    def full_resync(self, _):
        try:
            self.app.force_full_resync()
            self.refresh_status()
            self.app.page.snack_bar = ft.SnackBar(ft.Text("Полная синхронизация завершена"))
            self.app.page.snack_bar.open = True
            self.app.page.update()
        except Exception as e:
            self.status_tasks.value = f"Ошибка: {e}"
            self.app.page.update()

    def refresh_log(self, _):
        self.log_view.value = self.app.read_sync_log()
        self.app.page.update()
