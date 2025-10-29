# ui/app_shell.py
from __future__ import annotations

import asyncio
import flet as ft

from core.settings import UI, GOOGLE_SYNC

# страницы
from .pages.today import TodayPage
from .pages.calendar import CalendarPage
from .pages.settings import SettingsPage
from .pages.history import HistoryPage

# Google
from services.google_auth import GoogleAuth
from services.google_calendar import GoogleCalendar
from services.google_tasks import GoogleTasks
from services.sync_service import SyncService, SYNC_LOG_PATH
from services.pending_ops_queue import PendingOpsQueue
from services.sync_token_storage import SyncTokenStorage
from services.tasks import TaskService


class AppShell:
    def __init__(self, page: ft.Page):
        self.page = page

        # базовые настройки окна
        self.page.title = UI.app_title
        self.page.horizontal_alignment = ft.CrossAxisAlignment.STRETCH
        self.page.vertical_alignment = ft.MainAxisAlignment.START

        # --- Google Auth + Calendar (важно: до создания страниц) ---
        # при необходимости можно передать пути: GoogleAuth(secrets_path=..., token_path=...)
        self.auth = GoogleAuth()
        self.gcal = GoogleCalendar(self.auth, calendar_id="primary")
        self.gtasks = GoogleTasks(self.auth)
        self.sync_service = SyncService(
            self.gcal,
            self.gtasks,
            TaskService(),
            SyncTokenStorage(),
            PendingOpsQueue(),
        )
        TaskService.subscribe("after_create", self.sync_service.on_task_created)
        TaskService.subscribe("after_update", self.sync_service.on_task_updated)
        TaskService.subscribe("after_delete", self.sync_service.on_task_deleted)

        # --- страницы ---
        self._today = TodayPage(self)
        self._calendar = CalendarPage(self)
        self._history = HistoryPage(self)
        self._settings = SettingsPage(self)  # использует self.gcal

        # контейнер контента
        self.content = ft.Container(expand=True)

        # левое меню
        self.nav = ft.NavigationRail(
            selected_index=0,
            label_type=ft.NavigationRailLabelType.ALL,
            min_width=90,
            min_extended_width=200,
            group_alignment=-0.9,
            on_change=self.on_nav_change,
            destinations=[
                ft.NavigationRailDestination(
                    icon=ft.Icons.CHECK_CIRCLE_OUTLINE,
                    selected_icon=ft.Icons.CHECK_CIRCLE,
                    label="Сегодня",
                ),
                ft.NavigationRailDestination(
                    icon=ft.Icons.CALENDAR_MONTH_OUTLINED,
                    selected_icon=ft.Icons.CALENDAR_MONTH,
                    label="Календарь",
                ),
                ft.NavigationRailDestination(
                    icon=ft.Icons.HISTORY_EDU_OUTLINED,
                    selected_icon=ft.Icons.HISTORY,
                    label="История",
                ),
                ft.NavigationRailDestination(
                    icon=ft.Icons.SETTINGS_OUTLINED,
                    selected_icon=ft.Icons.SETTINGS,
                    label="Настройки",
                ),
            ],
        )

        # корневой лэйаут
        self.root = ft.Row(
            controls=[
                ft.Container(self.nav, width=88, bgcolor=UI.theme.safe_surface_bg),
                ft.VerticalDivider(width=1),
                self.content,
            ],
            expand=True,
            spacing=0,
        )

        # автообновление активной страницы
        self._auto_task: asyncio.Task | None = None
        self._active_view: str | None = None  # "today" | "calendar" | "history" | "settings"

    def cleanup_overlays(self):
        """Remove closed overlays (dialogs, pickers, backdrops) to avoid "ghost" windows."""
        overlays = getattr(self.page, "overlay", None) or []
        changed = False

        def _close_and_remove(ctrl):
            nonlocal changed
            try:
                if hasattr(ctrl, "open"):
                    ctrl.open = False
            except Exception:
                pass
            try:
                overlays.remove(ctrl)
                changed = True
            except Exception:
                pass

        for ctrl in list(overlays):
            if isinstance(ctrl, (ft.AlertDialog, ft.DatePicker, ft.TimePicker)):
                if not getattr(ctrl, "open", False):
                    _close_and_remove(ctrl)

        has_dialog = any(
            getattr(ctrl, "open", False) for ctrl in overlays if isinstance(ctrl, ft.AlertDialog)
        )

        if not has_dialog:
            for ctrl in list(overlays):
                if getattr(ctrl, "data", None) == "backdrop":
                    _close_and_remove(ctrl)

        if changed:
            self.page.update()

    # ---------- утилиты ----------
    def _has_open_overlay(self) -> bool:
        """Если открыт любой диалог/оверлей — пропускаем автообновление."""
        try:
            if getattr(self.page, "dialog", None) and getattr(self.page.dialog, "open", False):
                return True
        except Exception:
            pass
        try:
            return any(getattr(c, "open", False) for c in (self.page.overlay or []))
        except Exception:
            return False

    def _pull_from_google(self) -> bool:
        """
        Подтягиваем изменения из Google -> локально.
        Возвращает True, если локальная база изменилась (для логов/отладки).
        """
        try:
            return self.sync_service.pull_all()
        except Exception as e:
            print("Google sync error:", e)
            return False

    def _push_to_google(self):
        if not GOOGLE_SYNC.enabled:
            return
        try:
            self.sync_service.push_queue_worker()
        except Exception as e:
            print("Google Calendar push error:", e)

    def _start_auto_refresh(
        self, view_name: str, refresh_fn, period_sec: int | None = None
    ):
        """Периодически дергаем pull + refresh_fn, пока активен указанный view."""
        if not GOOGLE_SYNC.enabled or not UI.auto_refresh.enabled:
            refresh_fn()
            return
        self._stop_auto_refresh()
        self._active_view = view_name

        interval = period_sec or GOOGLE_SYNC.auto_pull_interval_sec or UI.auto_refresh.interval_sec

        async def _loop():
            # первый прогон — сразу: подтянуть изменения и перерисовать
            try:
                self._pull_from_google()
                refresh_fn()
                self._push_to_google()
            except Exception as e:
                print("auto refresh (initial):", e)

            while self._active_view == view_name:
                await asyncio.sleep(interval)
                if self._active_view != view_name:
                    break
                if self._has_open_overlay():
                    continue
                try:
                    self._pull_from_google()
                    refresh_fn()
                    self._push_to_google()
                except Exception as e:
                    print("auto refresh:", e)

        self._auto_task = self.page.run_task(_loop)

    def _stop_auto_refresh(self):
        try:
            if self._auto_task:
                self._auto_task.cancel()
        except Exception:
            pass
        self._auto_task = None
        self._active_view = None

    # ---------- монтаж ----------
    def mount(self):
        self.page.controls.clear()
        self.page.add(self.root)

        # стартуем со «Сегодня»
        self.content.content = self._today.view
        self.page.update()

        # 1) Подтянуть последние изменения из Google,
        # 2) отрисовать страницу,
        # 3) запустить автообновление.
        self._pull_from_google()
        self._today.activate_from_menu()
        self._start_auto_refresh("today", self._today.load)

    # ---------- переключение вкладок ----------
    def on_nav_change(self, e: ft.ControlEvent):
        idx = int(e.control.selected_index)

        if idx == 0:  # Сегодня
            self.content.content = self._today.view
            self._pull_from_google()
            self._today.activate_from_menu()
            self._start_auto_refresh("today", self._today.load)

        elif idx == 1:  # Календарь
            self.content.content = self._calendar.view
            self._pull_from_google()
            self._calendar.activate_from_menu()
            try:
                self._calendar.scroll_to_now()  # к текущему часу
            except Exception:
                pass
            self._start_auto_refresh("calendar", self._calendar.load)

        elif idx == 2:  # История
            self.content.content = self._history.view
            self._stop_auto_refresh()
            self._history.activate_from_menu()

        else:  # Настройки (используем полноценную страницу настроек)
            self.content.content = self._settings.view
            self._stop_auto_refresh()

        self.page.update()

    # ---------- ручной вызов синка (если где-то используете) ----------
    def current_page_auto_sync(self):
        if self._has_open_overlay():
            return
        self._pull_from_google()
        self._push_to_google()
        if self._active_view == "calendar":
            self._calendar.load()
        elif self._active_view == "today":
            self._today.load()

    # ---------- публичные утилиты для страниц ----------
    def push_tasks_to_google(self) -> None:
        """Expose push-to-Google routine for UI pages."""
        self._push_to_google()

    def connect_google_services(self) -> bool:
        try:
            self.auth.ensure_credentials()
            self.gcal.connect()
            self.gtasks.connect()
            return True
        except Exception as exc:
            print("Google connect error:", exc)
            raise

    def sync_status(self) -> dict:
        try:
            return self.sync_service.status()
        except Exception as exc:
            print("Sync status error:", exc)
            return {}

    def reset_calendar_sync(self) -> None:
        try:
            self.sync_service.reset_calendar_sync_token()
        except Exception as exc:
            print("Reset calendar token error:", exc)

    def force_full_resync(self) -> None:
        try:
            self.sync_service.force_full_resync()
        except Exception as exc:
            print("Full resync error:", exc)
            raise

    def read_sync_log(self, lines: int = 100) -> str:
        path = SYNC_LOG_PATH
        try:
            with open(path, "r", encoding="utf-8") as fh:
                content = fh.readlines()
        except FileNotFoundError:
            return "Лог синхронизации пока не создан."
        content = [line.rstrip("\n") for line in content[-lines:]]
        return "\n".join(content)
