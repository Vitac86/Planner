# planner/ui/pages/history.py
from __future__ import annotations

from datetime import datetime, date
from typing import Optional, List

import flet as ft

from services.tasks import TaskService
from core.priorities import (
    priority_options,
    priority_label,
    priority_color,
    priority_bgcolor,
    normalize_priority,
)

_STATUS_LABELS = {
    "todo": "К выполнению",
    "doing": "В работе",
    "done": "Выполнено",
}


class HistoryPage:
    def __init__(self, app):
        self.app = app
        self.svc = TaskService()

        self.search_tf = ft.TextField(
            label="Поиск",
            hint_text="Введите текст для поиска по названию и заметкам",
            expand=True,
            prefix=ft.Icon(ft.Icons.SEARCH),
            on_submit=self._on_filters_changed,
            on_change=self._on_filters_changed,
        )

        self.start_tf = ft.TextField(label="Дата c", width=150)
        self.end_tf = ft.TextField(label="Дата по", width=150)

        self.start_picker = ft.DatePicker(
            first_date=date(2000, 1, 1),
            last_date=date(2100, 12, 31),
            on_change=lambda e: self._set_date(self.start_tf, e.data or e.control.value),
            on_dismiss=lambda e: self._set_date(self.start_tf, e.control.value),
        )
        self.end_picker = ft.DatePicker(
            first_date=date(2000, 1, 1),
            last_date=date(2100, 12, 31),
            on_change=lambda e: self._set_date(self.end_tf, e.data or e.control.value),
            on_dismiss=lambda e: self._set_date(self.end_tf, e.control.value),
        )

        for picker in (self.start_picker, self.end_picker):
            if picker not in self.app.page.overlay:
                self.app.page.overlay.append(picker)

        self.start_btn = ft.IconButton(
            icon=ft.Icons.CALENDAR_MONTH,
            tooltip="Выбрать дату",
            on_click=lambda e: self.app.page.open(self.start_picker),
        )
        self.end_btn = ft.IconButton(
            icon=ft.Icons.CALENDAR_MONTH,
            tooltip="Выбрать дату",
            on_click=lambda e: self.app.page.open(self.end_picker),
        )

        status_options = [
            ft.dropdown.Option("all", "Любой статус"),
            ft.dropdown.Option("todo", _STATUS_LABELS["todo"]),
            ft.dropdown.Option("doing", _STATUS_LABELS["doing"]),
            ft.dropdown.Option("done", _STATUS_LABELS["done"]),
        ]
        self.status_dd = ft.Dropdown(
            label="Статус",
            width=180,
            value="all",
            options=status_options,
            on_change=self._on_filters_changed,
        )

        priority_opts = [ft.dropdown.Option(key, label) for key, label in priority_options().items()]
        priority_opts.insert(0, ft.dropdown.Option("-1", "Любой приоритет"))
        self.priority_dd = ft.Dropdown(
            label="Приоритет",
            width=200,
            value="-1",
            options=priority_opts,
            on_change=self._on_filters_changed,
        )

        self.reset_btn = ft.TextButton("Сбросить", icon=ft.Icons.REFRESH, on_click=self._on_reset)

        filters_row = ft.Column(
            controls=[
                ft.Row([self.search_tf], alignment=ft.MainAxisAlignment.START),
                ft.Row(
                    [
                        ft.Row([self.start_tf, self.start_btn], spacing=6),
                        ft.Row([self.end_tf, self.end_btn], spacing=6),
                        self.status_dd,
                        self.priority_dd,
                        self.reset_btn,
                    ],
                    alignment=ft.MainAxisAlignment.START,
                    vertical_alignment=ft.CrossAxisAlignment.END,
                    spacing=12,
                ),
            ],
            spacing=12,
        )

        self.result_info = ft.Text("", size=12, color="#6B7280")
        self.result_list = ft.ListView(expand=True, spacing=8)

        self.view = ft.Container(
            content=ft.Column(
                [
                    ft.Text("История", size=24, weight=ft.FontWeight.BOLD),
                    filters_row,
                    self.result_info,
                    ft.Container(content=self.result_list, expand=True),
                ],
                spacing=16,
                expand=True,
            ),
            expand=True,
            padding=20,
        )

        self.run_search()

    def activate_from_menu(self):
        # Пересчитать результаты при возвращении на вкладку.
        self.run_search()

    # ---------- Filters ----------
    def _on_filters_changed(self, _):
        self.run_search()

    def _on_reset(self, _):
        self.search_tf.value = ""
        self.start_tf.value = ""
        self.end_tf.value = ""
        self.status_dd.value = "all"
        self.priority_dd.value = "-1"
        self.app.page.update()
        self.run_search()

    # ---------- Data ----------
    def run_search(self):
        start_date = self._parse_date(self.start_tf.value)
        end_date = self._parse_date(self.end_tf.value)

        priority_value = self.priority_dd.value
        priority = None if priority_value in (None, "-1") else normalize_priority(priority_value)

        tasks = self.svc.search_history(
            query=self.search_tf.value or "",
            start_date=start_date,
            end_date=end_date,
            status=self.status_dd.value,
            priority=priority,
        )

        self._render_results(tasks)
        self.app.page.update()

    def _render_results(self, tasks: List):
        total = len(tasks)
        if total == 0:
            self.result_info.value = "Ничего не найдено"
        else:
            self.result_info.value = f"Найдено {total} задач"

        self.result_list.controls.clear()
        for t in tasks:
            self.result_list.controls.append(self._task_card(t))

    # ---------- Helpers ----------
    def _task_card(self, task):
        title = task.title or "(без названия)"
        priority = getattr(task, "priority", 0)
        start = getattr(task, "start", None)
        created = getattr(task, "created_at", None)
        updated = getattr(task, "updated_at", None)
        duration = getattr(task, "duration_minutes", None)

        subtitle_parts: List[str] = []
        if start:
            if isinstance(start, datetime) and start.time() == datetime.min.time():
                subtitle_parts.append(start.strftime("Начало: %d.%m.%Y"))
            else:
                subtitle_parts.append(start.strftime("Начало: %d.%m.%Y %H:%M"))
        if duration:
            subtitle_parts.append(f"Длительность: {duration} мин")
        status_label = _STATUS_LABELS.get(getattr(task, "status", ""), "Неизвестно")
        subtitle_parts.append(f"Статус: {status_label}")
        subtitle_parts.append(f"Приоритет: {priority_label(priority, short=False)}")
        if created:
            subtitle_parts.append(f"Создано: {created.strftime('%d.%m.%Y %H:%M')}")
        if updated and (not created or updated != created):
            subtitle_parts.append(f"Обновлено: {updated.strftime('%d.%m.%Y %H:%M')}")

        note = (task.notes or "").strip()
        note_text = ft.Text(note, size=12, color="#6B7280")
        note_block = ft.Container()
        if note:
            note_block = ft.Container(
                content=note_text,
                padding=ft.padding.only(top=8),
            )

        badge = self._priority_badge(priority)

        body = ft.Column(
            [
                ft.Row(
                    [
                        badge,
                        ft.Text(title, size=16, weight=ft.FontWeight.W_600),
                    ],
                    spacing=12,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                ft.Text(" · ".join(subtitle_parts), size=12, color="#6B7280"),
                note_block,
            ],
            spacing=4,
        )

        bgcolor = ft.Colors.with_opacity(0.04, priority_color(priority)) if priority else "#F1F5F9"

        return ft.Card(
            content=ft.Container(
                content=body,
                padding=16,
                bgcolor=bgcolor,
            )
        )

    def _priority_badge(self, priority: int):
        if priority <= 0:
            return ft.Container(width=0)
        return ft.Container(
            content=ft.Text(
                priority_label(priority, short=True),
                size=11,
                weight=ft.FontWeight.W_600,
                color=priority_color(priority),
            ),
            bgcolor=priority_bgcolor(priority),
            padding=ft.padding.symmetric(horizontal=8, vertical=4),
            border_radius=ft.border_radius.all(8),
        )

    def _set_date(self, tf: ft.TextField, value):
        v = value
        if isinstance(v, date):
            tf.value = v.strftime("%d.%m.%Y")
        elif isinstance(v, str):
            try:
                tf.value = datetime.strptime(v[:10], "%Y-%m-%d").strftime("%d.%m.%Y")
            except ValueError:
                try:
                    tf.value = datetime.strptime(v, "%d.%m.%Y").strftime("%d.%m.%Y")
                except ValueError:
                    return
        self.app.page.update()

    def _parse_date(self, text: Optional[str]) -> Optional[date]:
        text = (text or "").strip()
        if not text:
            return None
        try:
            dt = datetime.strptime(text, "%d.%m.%Y")
            return dt.date()
        except ValueError:
            return None
