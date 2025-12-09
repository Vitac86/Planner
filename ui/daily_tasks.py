# planner/ui/daily_tasks.py
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import locale
from typing import List

import flet as ft

from core.settings import UI
from ui import compat
from ui.dialogs import close_alert_dialog, open_alert_dialog
from services.daily_tasks import DailyTaskService
from models.daily_task import DailyTask


WEEKDAY_LABELS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]

try:
    locale.setlocale(locale.LC_COLLATE, "ru_RU.UTF-8")
except locale.Error:
    # если локаль недоступна в окружении — используем системную по умолчанию
    pass


class DailyTasksPanel:
    def __init__(self, app_shell):
        self.app = app_shell
        self.svc = DailyTaskService()
        self._tasks: list[DailyTask] = []
        self._dialog: ft.AlertDialog | None = None
        self._rollover_task: asyncio.Task | None = None

        self._list_holder = ft.ResponsiveRow(run_spacing=10, spacing=14)

        self.view = ft.Card(
            content=ft.Container(
                padding=12,
                content=ft.Column(
                    [
                        ft.Text("Daily Tasks", size=18, weight=ft.FontWeight.W_600),
                        self._list_holder,
                    ],
                    spacing=12,
                ),
            )
        )

    # ---------- Data ----------
    def refresh(self):
        self.svc.rollover_if_needed()
        self._tasks = self.svc.list_all()
        self._render_list()
        self._ensure_rollover_timer()

    # ---------- Rendering ----------
    def _render_list(self):
        controls = [self._build_item(task) for task in self._sorted_tasks()]
        add_button = self._add_button()

        if not controls:
            controls = [add_button, self._empty_state()]
        else:
            controls.append(add_button)

        self._list_holder.controls = [
            ft.Container(ctrl, col={"xs": 12, "md": 12, "lg": 6, "xl": 6})
            for ctrl in controls
        ]
        self.app.page.update()

    def _sorted_tasks(self) -> List[DailyTask]:
        def group_key(task: DailyTask) -> int:
            return {"active": 0, "done_today": 1, "inactive": 2}.get(task.status_today, 3)

        def title_key(task: DailyTask) -> str:
            return locale.strxfrm(task.title.casefold())

        return sorted(self._tasks, key=lambda t: (group_key(t), title_key(t)))

    def _weekday_flags(self, task: DailyTask) -> str:
        parts = []
        for i, label in enumerate(WEEKDAY_LABELS):
            if task.weekdays & (1 << i):
                parts.append(label)
        return ", ".join(parts)

    def _build_item(self, task: DailyTask) -> ft.Control:
        checked = task.status_today == "done_today"
        is_inactive = task.status_today == "inactive"

        checkbox = ft.Checkbox(
            value=checked,
            on_change=lambda e, tid=task.id: self._on_toggle(tid, e.control.value),
            tooltip="Отметить как выполнено",
            disabled=is_inactive,
            semantics_label=f"Отметить ежедневную задачу {task.title}",
        )

        title_color = UI.theme.text_subtle if checked else None
        title_opacity = 0.7 if checked else 1.0

        title = compat.strike_text(task.title, tooltip=task.title, strike=getattr(task, "done", False))
        title.max_lines = 1
        title.overflow = ft.TextOverflow.ELLIPSIS
        title.size = 14
        title.weight = ft.FontWeight.W_600
        title.color = ft.Colors.with_opacity(title_opacity, title_color or ft.Colors.ON_SURFACE)

        subtitle = ft.Text(
            self._weekday_flags(task),
            size=12,
            color=ft.Colors.BLUE_GREY_400,
        )

        edit_btn = ft.IconButton(
            icon=ft.Icons.EDIT_OUTLINED,
            tooltip="Редактировать",
            on_click=lambda e, tid=task.id: self._open_dialog(task_id=tid),
            style=ft.ButtonStyle(padding=ft.padding.all(6)),
        )
        delete_btn = ft.IconButton(
            icon=ft.Icons.DELETE_OUTLINE,
            tooltip="Удалить",
            on_click=lambda e, tid=task.id: self._confirm_delete(tid),
            style=ft.ButtonStyle(padding=ft.padding.all(6)),
        )

        actions = ft.Row([edit_btn, delete_btn], spacing=4, alignment=ft.MainAxisAlignment.END)

        item = ft.Container(
            content=ft.Row(
                [
                    ft.Container(width=42, alignment=ft.alignment.center, content=checkbox),
                    ft.Column([title, subtitle], spacing=4, expand=True),
                    actions,
                ],
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            ),
            padding=ft.padding.symmetric(horizontal=12, vertical=10),
            bgcolor=ft.Colors.SURFACE,
            border_radius=10,
            border=ft.border.all(1, ft.Colors.with_opacity(0.05, ft.Colors.ON_SURFACE)),
            animate_opacity=150,
        )

        if is_inactive:
            item.opacity = 0.7
        return item

    def _on_toggle(self, task_id: str, checked: bool):
        try:
            self.svc.toggle(task_id, done=checked)
        except ValueError as e:
            self._toast(str(e))
        self.refresh()

    def _add_button(self) -> ft.Control:
        return ft.TextButton(
            text="+Добавить",
            icon=ft.Icons.ADD,
            style=ft.ButtonStyle(
                bgcolor=ft.Colors.with_opacity(0.06, ft.Colors.ON_SURFACE),
                color=ft.Colors.BLUE_GREY_600,
                padding=ft.padding.symmetric(vertical=12, horizontal=16),
                shape=ft.RoundedRectangleBorder(radius=10),
            ),
            height=46,
            on_click=lambda _: self._open_dialog(),
        )

    def _empty_state(self) -> ft.Control:
        return ft.Container(
            padding=ft.padding.symmetric(vertical=8, horizontal=12),
            content=ft.Row(
                [
                    ft.Icon(ft.Icons.INFO_OUTLINE, color=ft.Colors.BLUE_GREY_300),
                    ft.Text("У вас пока нет ежедневных задач", color=ft.Colors.BLUE_GREY_400),
                ],
                spacing=8,
            ),
            border_radius=8,
            border=ft.border.all(1, ft.Colors.with_opacity(0.05, ft.Colors.ON_SURFACE)),
        )

    # ---------- Dialogs ----------
    def _open_dialog(self, task_id: str | None = None):
        task = None
        if task_id:
            for t in self._tasks:
                if t.id == task_id:
                    task = t
                    break

        title_value = task.title if task else ""
        weekdays_value = task.weekdays if task else (1 << 7) - 1

        title_tf = ft.TextField(label="Название", value=title_value, autofocus=True, max_length=120)

        weekday_checkboxes = []
        for idx, label in enumerate(WEEKDAY_LABELS):
            weekday_checkboxes.append(
                ft.Checkbox(
                    label=label,
                    value=bool(weekdays_value & (1 << idx)),
                    on_change=lambda e: None,
                )
            )

        def collect_weekdays() -> int:
            mask = 0
            for i, cb in enumerate(weekday_checkboxes):
                if cb.value:
                    mask |= 1 << i
            return mask

        save_btn: ft.TextButton | None = None

        def on_save(_):
            nonlocal save_btn
            try:
                if save_btn:
                    save_btn.disabled = True
                title = (title_tf.value or "").strip()
                if not title:
                    self.app.toast("Укажите название", ok=False)
                    return

                mask = collect_weekdays()
                if mask == 0:
                    self.app.toast("Выберите хотя бы один день недели", ok=False)
                    return

                if task:
                    self.svc.update(task.id, title=title, weekdays=mask)
                else:
                    self.svc.create(title=title, weekdays=mask)

                self.refresh()
                self.app.toast("Сохранено")
            except Exception as ex:
                self.app.toast(f"Ошибка: {ex}", ok=False)
            finally:
                if save_btn:
                    save_btn.disabled = False
                close_alert_dialog(self.app.page)

        dialog_content = ft.Container(
            width=420,
            content=ft.Column(
                [
                    title_tf,
                    ft.Text("Дни недели", weight=ft.FontWeight.W_600),
                    ft.Row(
                        controls=weekday_checkboxes,
                        wrap=True,
                        spacing=12,
                        run_spacing=8,
                    ),
                ],
                spacing=12,
                tight=True,
            ),
        )

        save_btn = ft.FilledButton("Сохранить", icon=ft.Icons.SAVE, on_click=on_save)
        actions = [
            ft.TextButton("Отмена", on_click=lambda e: close_alert_dialog(self.app.page)),
            save_btn,
        ]

        self.app.page.snack_bar.open = False
        open_alert_dialog(
            self.app.page,
            title="Редактировать задачу" if task else "Новая ежедневная задача",
            content=dialog_content,
            actions=actions,
        )

    def _confirm_delete(self, task_id: str):
        def on_delete(_):
            try:
                self.svc.delete(task_id)
                self.refresh()
                self.app.toast("Удалено")
            except Exception as ex:
                self.app.toast(f"Ошибка: {ex}", ok=False)
            finally:
                close_alert_dialog(self.app.page)

        actions = [
            ft.TextButton("Отмена", on_click=lambda e: close_alert_dialog(self.app.page)),
            ft.FilledButton("Удалить", icon=ft.Icons.DELETE_OUTLINE, on_click=on_delete),
        ]

        self.app.page.snack_bar.open = False
        open_alert_dialog(
            self.app.page,
            title="Удалить задачу?",
            content=ft.Text("Действие нельзя отменить"),
            actions=actions,
        )

    # ---------- Helpers ----------
    def _toast(self, text: str):
        self.app.toast(text)

    # ---------- Rollover scheduling ----------
    def _seconds_until_midnight(self) -> float:
        now = datetime.now().astimezone()
        tomorrow = now.date() + timedelta(days=1)
        midnight = datetime.combine(tomorrow, datetime.min.time(), tzinfo=now.tzinfo)
        return max((midnight - now).total_seconds(), 1.0)

    async def _rollover_loop(self):
        while True:
            await asyncio.sleep(self._seconds_until_midnight())
            try:
                self.svc.rollover_if_needed()
                self._tasks = self.svc.list_all()
                self._render_list()
            except Exception:
                # Фолбэк на случай ошибок планировщика, чтобы не падало приложение
                pass

    def _ensure_rollover_timer(self):
        if self._rollover_task and not self._rollover_task.done():
            return
        try:
            self._rollover_task = self.app.page.run_task(self._rollover_loop)
        except Exception:
            self._rollover_task = asyncio.create_task(self._rollover_loop())
