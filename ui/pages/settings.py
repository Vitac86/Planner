# ui/pages/settings.py
import flet as ft

class SettingsPage:
    def __init__(self, app):
        self.app = app
        self.status = ft.Text("Google: не подключено")
        self.tasks_status = ft.Text("Tasks: не подключено")

        self.scope_list = ft.Column(spacing=2)
        self.scope_section = ft.Column(
            controls=[
                ft.Text("Активные Google scopes:", size=12, color=ft.Colors.BLUE_GREY_400),
                self.scope_list,
            ],
            spacing=4,
        )

        self.connect_btn = ft.ElevatedButton(
            "Подключить Google",
            icon=ft.Icons.LINK,          # в Flet 0.28+: ft.Icons.*
            on_click=self.connect_google
        )

        self.reconnect_btn = ft.OutlinedButton(
            "Переподключить Google",
            icon=ft.Icons.REFRESH,
            on_click=self.reconnect_google,
        )

        self.sync_tasks_btn = ft.ElevatedButton(
            "Синхронизировать Google Tasks",
            icon=ft.Icons.SYNC,
            on_click=self.sync_google_tasks,
        )

        content = ft.Column(
            controls=[
                ft.Text("Настройки", size=24, weight=ft.FontWeight.BOLD),
                self.status,
                self.tasks_status,
                self.connect_btn,
                self.reconnect_btn,
                self.sync_tasks_btn,
                self.scope_section,
            ],
            expand=True,
            spacing=16,
        )

        # В Flet 0.28 отступы задаём контейнером:
        self.view = ft.Container(content=content, expand=True, padding=20)

        self.refresh_state()

    def connect_google(self, _):
        try:
            self.app.gcal.connect()
            if hasattr(self.app, "undated_sync"):
                self.app.undated_sync.reset_cache()
            self.app.page.snack_bar = ft.SnackBar(ft.Text("Google подключён"))
            self.app.page.snack_bar.open = True
            self.refresh_state()
            self.app.page.update()
        except Exception as e:
            self.status.value = f"Ошибка: {e}"
            self.app.page.update()

    def reconnect_google(self, _):
        try:
            if hasattr(self.app, "auth"):
                self.app.auth.reset_credentials()
            if hasattr(self.app, "gcal"):
                self.app.gcal.service = None
            self.status.value = "Google: требуется авторизация"
            self.app.page.update()
            self.connect_google(_)
        except Exception as e:
            self.status.value = f"Ошибка: {e}"
            self.app.page.update()

    def sync_google_tasks(self, _):
        sync = getattr(self.app, "undated_sync", None)
        if not sync:
            return
        try:
            changed = sync.sync()
            message = "Синхронизация задач выполнена"
            if changed:
                message += " (есть обновления)"
            self.app.page.snack_bar = ft.SnackBar(ft.Text(message))
            self.app.page.snack_bar.open = True
        except Exception as e:
            self.tasks_status.value = f"Tasks: ошибка синхронизации — {e}"
        finally:
            self.refresh_state()
            self.app.page.update()

    def refresh_state(self) -> None:
        scopes = []
        if hasattr(self.app, "auth"):
            try:
                scopes = self.app.auth.get_active_scopes()
            except Exception:
                scopes = []

        if scopes:
            self.status.value = (
                f"Google: подключено (календарь: {self.app.gcal.calendar_id})"
            )
            scope_controls = [
                ft.Text(scope, size=12, color=ft.Colors.BLUE_GREY_200)
                for scope in scopes
            ]
        else:
            self.status.value = "Google: не подключено"
            scope_controls = [ft.Text("—", size=12, color=ft.Colors.BLUE_GREY_200)]

        sync = getattr(self.app, "undated_sync", None)
        if sync:
            try:
                self.tasks_status.value = sync.status_text()
            except Exception:
                self.tasks_status.value = "Tasks: ошибка состояния"
        else:
            self.tasks_status.value = "Tasks: недоступно"

        self.sync_tasks_btn.disabled = not bool(scopes)
        self.scope_list.controls = scope_controls
        self.app.page.update()
