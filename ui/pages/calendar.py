# ui/pages/calendar.py
from __future__ import annotations
import re

import json
import flet as ft
from datetime import datetime, date, timedelta, time as dt_time
from typing import Dict, Tuple, List, Optional

from services.tasks import TaskService
from core.priorities import (
    priority_options,
    priority_label,
    priority_color,
    priority_bgcolor,
    normalize_priority,
)
from core.settings import UI
from helpers import snooze

# ===== настройки =====
CAL_UI = UI.calendar
THEME = UI.theme

DAY_START = CAL_UI.day_start
DAY_END = CAL_UI.day_end

ROW_MIN_H = CAL_UI.row_min_height  # минимальная высота строки часа
DAY_COL_W = CAL_UI.day_column_width
HOURS_COL_W = CAL_UI.hours_column_width
SIDE_PANEL_W = CAL_UI.side_panel_width
HEADER_H = CAL_UI.header_height

CHIP_EST_H = CAL_UI.chip_estimated_height  # ожидаемая высота «чипа»
CELL_VPAD = CAL_UI.cell_vertical_padding
CHIPS_SPACING = CAL_UI.chips_spacing

IMPORT_NEW_GCAL = CAL_UI.import_new_from_google

DIALOG_WIDTH_NARROW = CAL_UI.dialog_width_narrow
DIALOG_WIDTH_WIDE = CAL_UI.dialog_width_wide


def _color(value: str, fallback: str = "") -> str:
    try:
        return getattr(ft.Colors, value)
    except Exception:
        return value or fallback


CLR_OUTLINE = _color(THEME.outline, "#E5E7EB")
CLR_SURFVAR = _color(THEME.surface_variant, "#F1F5F9")
CLR_TEXTSUB = _color(THEME.text_subtle, "#6B7280")
CLR_TODAY_BG = THEME.today_bg
CLR_NOW_LINE = THEME.now_line
CLR_CHIP = THEME.chip
CLR_CHIP_TXT = THEME.chip_text
CLR_UNS_BG = THEME.unscheduled_bg
CLR_BACKDROP = THEME.backdrop  # для клика-вне

NOW_ANCHOR_KEY = "now-anchor"


class CalendarPage:
    """
    - Один тип сущности: задача.
    - Ячейки часа растягиваются по содержимому.
    - Вертикальный скролл только у тела; шапка закреплена.
    - Горизонтальный скролл синхронный (шапка↔тело), левый столбец времени закреплён.
    - ESC и клик мимо окна закрывают диалог. Никаких «призраков» в overlay.
    """

    def __init__(self, app):
        self.app = app
        self.svc = TaskService()
        self._priority_options = [ft.dropdown.Option(key, label) for key, label in priority_options().items()]

        self.week_start: date = self._monday_of(date.today())

        # индекс задач: (day_idx, hour) -> [task dicts]
        self.idx: Dict[Tuple[int, int], List[dict]] = {}
        # рассчитанные высоты строк по каждому часу
        self.row_h: Dict[int, int] = {}

        # DnD
        self.current_drag_task_id: Optional[int] = None

        # автопрокрутка к «сейчас» после построения
        self._need_scroll_now = True

        # ссылки для синхронизации скролла
        self._hrow_header_ref: ft.Ref[ft.Row] = ft.Ref[ft.Row]()
        self._hrow_body_ref: ft.Ref[ft.Row]   = ft.Ref[ft.Row]()
        self._vcol_ref: ft.Ref[ft.Column]     = ft.Ref[ft.Column]()

        # защита от петель при синхронизации скролла
        self._syncing_hscroll = False

        # текущая подложка (чтобы клик-вне закрывал окно и не оставался «призрак»)
        self._backdrop: Optional[ft.Control] = None

        # ---------- Шапка экрана ----------
        self.title_text = ft.Text("", size=24, weight=ft.FontWeight.BOLD)
        self.home_btn   = ft.IconButton(icon=ft.Icons.HOME,          tooltip="Текущая неделя",  on_click=lambda e: self.go_home())
        self.prev_btn   = ft.IconButton(icon=ft.Icons.CHEVRON_LEFT,  tooltip="Назад на неделю", on_click=lambda e: self.shift_week(-1))
        self.next_btn   = ft.IconButton(icon=ft.Icons.CHEVRON_RIGHT, tooltip="Вперёд на неделю",on_click=lambda e: self.shift_week(1))

        header = ft.Row(
            controls=[ft.Row([self.prev_btn, self.home_btn, self.next_btn], spacing=6),
                      self.title_text,
                      ft.Container()],  # пустой правый край
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
        )

        # ---------- Без даты ----------
        self.unscheduled_list = ft.ListView(expand=True, spacing=6)
        self.side_panel = ft.Container(
            width=SIDE_PANEL_W,
            content=ft.Column(
                [ft.Text("Без даты", size=16, weight=ft.FontWeight.W_600),
                 ft.Divider(height=1),
                 self.unscheduled_list],
                expand=True, spacing=8),
            padding=10,
            border=ft.border.all(0.5, CLR_OUTLINE),
            border_radius=8,
        )

        # ---------- Область сетки ----------
        self.grid = ft.Container(expand=True)

        self.view = ft.Container(
            content=ft.Column(
                [header, ft.Divider(height=1), ft.Row([self.side_panel, self.grid], expand=True, spacing=12)],
                spacing=12, expand=True),
            expand=True, padding=20,
        )

        self.load()

    def _mark_undated_dirty(self, task_id: int) -> None:
        sync = getattr(self.app, "undated_sync", None)
        if not sync:
            return
        try:
            sync.mark_dirty(task_id)
        except Exception as exc:
            print("undated mark dirty error:", exc)

    def _remove_undated_mapping(self, task_id: int, *, delete_remote: bool = False) -> None:
        sync = getattr(self.app, "undated_sync", None)
        if not sync:
            return
        try:
            sync.remove_mapping(task_id, delete_remote=delete_remote)
        except Exception as exc:
            print("undated remove mapping error:", exc)

    # ===== публичное: вызывать из бокового меню =====
    def activate_from_menu(self):
        self._close_any_dialog()
        self.week_start = self._monday_of(date.today())
        self._need_scroll_now = True
        self.load()

    # ===== Диалоги через overlay (как у тебя раньше) =====
    def _cleanup_backdrop(self):
        try:
            if self._backdrop and self._backdrop in self.app.page.overlay:
                self.app.page.overlay.remove(self._backdrop)
        except Exception:
            pass
        self._backdrop = None

    def _open_dialog(self, dlg: ft.AlertDialog):
        self._cleanup_backdrop()
        self._backdrop = ft.Container(
            expand=True,
            bgcolor=ft.Colors.with_opacity(0.001, CLR_BACKDROP),
            on_click=lambda e, d=dlg: self._close_dialog(d),
            data="backdrop",   # <<< метка, чтобы потом удалить
        )
        self.app.page.overlay.append(self._backdrop)

        dlg.modal = False
        dlg.on_dismiss = lambda e, d=dlg: self._close_dialog(d)
        if dlg not in self.app.page.overlay:
            self.app.page.overlay.append(dlg)
        dlg.open = True
        self.app.page.update()
        self._sweep_overlay()  # <<< сразу подчистить

    def _close_dialog(self, dlg: ft.AlertDialog | None):
        if not dlg:
            return
        cleanup_meta = getattr(dlg, "data", None)
        if isinstance(cleanup_meta, dict):
            fn = cleanup_meta.get("on_close")
            if callable(fn):
                try:
                    fn()
                finally:
                    cleanup_meta["on_close"] = None
        try:
            dlg.open = False
        except Exception:
            pass
        try:
            if dlg in self.app.page.overlay:
                self.app.page.overlay.remove(dlg)
        except Exception:
            pass
        self._cleanup_backdrop()
        self._sweep_overlay()
        self.app.page.update()
        self.app.cleanup_overlays()

    def _close_any_dialog(self):
        # на всякий случай закрыть всё
        try:
            overlays = list(self.app.page.overlay)
        except Exception:
            overlays = []

        for ctrl in overlays:
            if isinstance(ctrl, ft.AlertDialog):
                self._close_dialog(ctrl)

        self._cleanup_backdrop()
        self._sweep_overlay()
        self.app.page.update()
        self.app.cleanup_overlays()
    def _delete_task(self, task_id: int):
        t = self.svc.get(task_id)
        if not t:
            return self._toast("Задача не найдена")
        # удаляем событие в Google, если привязано
        if getattr(t, "gcal_event_id", None):
            try:
                self.app.gcal.delete_event_by_id(t.gcal_event_id)
            except Exception as ex:
                print("Google delete event error:", ex)
                self.app.notify_google_unavailable(ex)
        self._remove_undated_mapping(task_id, delete_remote=True)
        self.svc.delete(task_id)
        self.load()
        self._toast("Удалено")


    # ===== Даты / навигация =====
    def _monday_of(self, d: date) -> date:
        return d - timedelta(days=d.weekday())

    def _week_days(self) -> List[date]:
        return [self.week_start + timedelta(days=i) for i in range(7)]

    def go_home(self):
        self._close_any_dialog()
        self.week_start = self._monday_of(date.today())
        self._need_scroll_now = True
        self.load()

    def shift_week(self, delta_weeks: int):
        self._close_any_dialog()
        base = self.week_start or self._monday_of(date.today())
        self.week_start = self._monday_of(base + timedelta(days=7 * delta_weeks))
        self._need_scroll_now = True
        self.load()
    
    def _sweep_overlay(self):
        # убираем закрытые диалоги и осиротевшие подложки
        ov = self.app.page.overlay or []
        changed = False
        for c in list(ov):
            if isinstance(c, ft.AlertDialog) and not getattr(c, "open", False):
                try:
                    ov.remove(c); changed = True
                except Exception:
                    pass
            elif isinstance(c, ft.Container) and getattr(c, "data", None) == "backdrop":
                # оставляем подложку только если есть открытый AlertDialog
                if not any(getattr(d, "open", False) for d in ov if isinstance(d, ft.AlertDialog)):
                    try:
                        ov.remove(c); changed = True
                    except Exception:
                        pass
        if changed:
            self.app.page.update()
            self.app.cleanup_overlays()

    # ===== Загрузка =====
    def load(self):
        ws = self.week_start
        we = ws + timedelta(days=6)
        self.title_text.value = f"Неделя {ws.strftime('%d.%m')} — {we.strftime('%d.%m.%Y')}"
        self._sync_from_google(ws, we)


        # индекс задач за неделю
        self.idx.clear()
        for i in range(7):
            d = ws + timedelta(days=i)
            for t in self.svc.list_for_day(d):
                st = getattr(t, "start", None)
                if not isinstance(st, datetime):
                    continue
                dur = getattr(t, "duration_minutes", None) or 30
                di = (st.date() - ws).days
                if 0 <= di < 7:
                    self.idx.setdefault((di, st.hour), []).append(
                        {
                            "title": t.title,
                            "task_id": t.id,
                            "duration": dur,
                            "gcal_event_id": getattr(t, "gcal_event_id", None),
                            "priority": getattr(t, "priority", 0),
                        }
                    )

        # высоты строк
        self.row_h = {}
        for h in range(DAY_START, DAY_END + 1):
            max_n = 0
            for di in range(7):
                max_n = max(max_n, len(self.idx.get((di, h), [])))
            height = ROW_MIN_H if max_n <= 0 else max(ROW_MIN_H, CELL_VPAD + max_n * CHIP_EST_H + (max_n - 1) * CHIPS_SPACING)
            self.row_h[h] = height

        for key, tasks in self.idx.items():
            tasks.sort(key=lambda item: (-item.get("priority", 0), item.get("title", "").lower()))

        self._build_unscheduled()
        self.grid.content = self._build_week_grid()
        self.app.page.update()

        if self._need_scroll_now:
            self._need_scroll_now = False
            self._scroll_to_now()
        self.app.cleanup_overlays()
    def _sync_from_google(self, ws: date, we: date):
        """Подтягивает изменения из Google за окно недели (с небольшим буфером)
        и обновляет/создаёт/отвязывает локальные задачи.
        """
        g = getattr(self.app, "gcal", None)
        if not g or not getattr(g, "calendar_id", None):
            return

        # Берём окно чуть шире текущей недели
        rng_start = ws - timedelta(days=3)
        rng_end   = we + timedelta(days=3)

        try:
            # ожидаем, что list_range(start_dt, end_dt, show_deleted=True) у тебя есть
            events = g.list_range(
                datetime(rng_start.year, rng_start.month, rng_start.day, 0, 0, 0),
                datetime(rng_end.year, rng_end.month, rng_end.day, 23, 59, 59),
                show_deleted=True  # важно для отслеживания удалений
            )
        except Exception as ex:
            self._toast(f"Google sync: {ex}")
            return

        # --- утилита парсинга дат ---
        def _parse_ev_datetime(s: str | None):
            if not s:
                return None
            try:
                return datetime.fromisoformat(s.replace("Z", "+00:00"))
            except Exception:
                return None

        # карты по id событий
        ev_map = {}
        for ev in events or []:
            eid = ev.get("id")
            if not eid:
                continue
            status = ev.get("status", "confirmed")
            if status == "cancelled":
                ev_map[eid] = {"deleted": True}
                continue

            st = ev.get("start") or {}
            en = ev.get("end") or {}

            # all-day: Google присылает "date" вместо "dateTime"
            if "dateTime" in st and "dateTime" in en:
                dt_start = _parse_ev_datetime(st.get("dateTime"))
                dt_end   = _parse_ev_datetime(en.get("dateTime"))
                dur = None
                if dt_start and dt_end:
                    dur = int((dt_end - dt_start).total_seconds() // 60)
                ev_map[eid] = {
                    "title": ev.get("summary") or "",
                    "start": dt_start,
                    "duration": dur,
                    "allday": False,
                }
            else:
                # all-day -> ставим дату без времени (твой Today/Календарь понимают это как "без времени")
                try:
                    d = date.fromisoformat((st.get("date") or "").strip())
                    dt_start = datetime(d.year, d.month, d.day)
                except Exception:
                    dt_start = None
                ev_map[eid] = {
                    "title": ev.get("summary") or "",
                    "start": dt_start,
                    "duration": None,
                    "allday": True,
                }

        # --- Собираем локальные связанные задачи в нашем окне + «без даты» ---
        linked_tasks: list = []
        for i in range((rng_end - rng_start).days + 1):
            d = rng_start + timedelta(days=i)
            for t in self.svc.list_for_day(d):
                if getattr(t, "gcal_event_id", None):
                    linked_tasks.append(t)
        for t in self.svc.list_unscheduled():
            if getattr(t, "gcal_event_id", None):
                linked_tasks.append(t)

        # --- Обновляем связанные задачи по данным из Google ---
        seen_event_ids = set()
        for t in linked_tasks:
            eid = getattr(t, "gcal_event_id", None)
            info = ev_map.get(eid)
            if not eid:
                continue
            seen_event_ids.add(eid)

            # Событие удалили со стороны Google
            if not info or info.get("deleted"):
                try:
                    # по умолчанию — не удаляем задачу, а отвязываем и убираем расписание
                    self.svc.update(t.id, start=None)
                    self._mark_undated_dirty(t.id)
                    self.svc.set_event_id(t.id, None)
                except Exception:
                    pass
                continue

            # Обновления полей
            upd = {}
            if (t.title or "") != (info["title"] or ""):
                upd["title"] = info["title"] or ""

            if info["allday"]:
                # день без времени
                if not t.start or t.start.date() != info["start"].date() or (t.start.hour != 0 or t.start.minute != 0):
                    upd["start"] = datetime(info["start"].year, info["start"].month, info["start"].day)
                if getattr(t, "duration_minutes", None) is not None:
                    upd["duration_minutes"] = None
            else:
                if not t.start or t.start != info["start"]:
                    upd["start"] = info["start"]
                if getattr(t, "duration_minutes", None) != info["duration"]:
                    upd["duration_minutes"] = info["duration"]

            if upd:
                try:
                    self.svc.update(t.id, **upd)
                except Exception:
                    pass

        # --- (опционально) импорт новых событий как задач ---
        if IMPORT_NEW_GCAL:
            for eid, info in ev_map.items():
                if eid in seen_event_ids:
                    continue
                if info.get("deleted"):
                    continue
                try:
                    # создаём новую задачу, сразу привязываем eid
                    new_task = self.svc.add(
                        title=info.get("title") or "(без названия)",
                        start=info.get("start"),
                        duration_minutes=info.get("duration"),
                    )
                    self.svc.set_event_id(new_task.id, eid)
                except Exception:
                    pass


    # ===== Без даты =====
    def _build_unscheduled(self):
        self.unscheduled_list.controls.clear()
        for t in self.svc.list_unscheduled():
            badge = self._priority_badge(t.priority)
            chip = ft.Container(
                content=ft.Row(
                    [badge, ft.Text(t.title, size=12, no_wrap=True, overflow=ft.TextOverflow.ELLIPSIS, color=CLR_CHIP_TXT)],
                    spacing=8,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                padding=8,
                bgcolor=priority_bgcolor(t.priority) if t.priority else CLR_UNS_BG,
                border=ft.border.all(0.5, CLR_OUTLINE), border_radius=8,
                width=SIDE_PANEL_W - 20,
            )
            drag = ft.Draggable(
                group="task",
                data=str(t.id),
                on_drag_start=lambda e, tid=t.id: self._remember_drag(tid),
                content=chip,
                content_feedback=ft.Container(
                    content=ft.Text(t.title, size=12),
                    padding=8, bgcolor="#ffffff", border_radius=6,
                    border=ft.border.all(0.5, CLR_OUTLINE),
                ),
            )
            self.unscheduled_list.controls.append(drag)

    def _remember_drag(self, task_id: int):
        self.current_drag_task_id = task_id
        self.app.page.update()

    # ===== Сетка =====
    def _build_week_grid(self) -> ft.Control:
        days = self._week_days()
        today = date.today()
        now = datetime.now()

        # --- Шапка дней (в собственном viewport) ---
        header_cells: List[ft.Control] = []
        for i, d in enumerate(days):
            header_cells.append(
                ft.Container(
                    width=DAY_COL_W, height=HEADER_H,
                    content=ft.Column(
                        [ft.Text(d.strftime("%a"), size=14, weight=ft.FontWeight.W_600),
                         ft.Text(d.strftime("%d.%m"), size=12, color=CLR_TEXTSUB)],
                        spacing=2, horizontal_alignment=ft.CrossAxisAlignment.CENTER),
                    alignment=ft.alignment.center,
                    bgcolor=CLR_TODAY_BG if d == today else None,
                    border=ft.border.only(right=ft.BorderSide(0.5, CLR_OUTLINE)) if i < 6 else None,
                )
            )
        hrow_header = ft.Row(controls=header_cells, spacing=0, ref=self._hrow_header_ref, scroll=ft.ScrollMode.ALWAYS)
        header_viewport = ft.Container(  # ограничиваем ширину, чтобы работал скролл
            height=HEADER_H, expand=True, content=hrow_header, clip_behavior=ft.ClipBehavior.HARD_EDGE
        )

        # --- Левый столбец часов (закреплён) ---
        hours_controls: List[ft.Control] = []
        for h in range(DAY_START, DAY_END + 1):
            hours_controls.append(
                ft.Container(
                    content=ft.Text(f"{h:02d}:00", size=12, color=CLR_TEXTSUB),
                    width=HOURS_COL_W, height=self.row_h[h],
                    alignment=ft.alignment.center_right,
                    padding=ft.padding.only(right=8),
                    border=ft.border.only(bottom=ft.BorderSide(0.6, CLR_OUTLINE)),
                )
            )
        hours_col = ft.Column(controls=hours_controls, spacing=0, width=HOURS_COL_W)

        # --- Колонки дней (тело), тоже в viewport по X ---
        day_cols: List[ft.Control] = []
        for di, d in enumerate(days):
            is_today_col = (d == today)
            col_rows: List[ft.Control] = []
            for h in range(DAY_START, DAY_END + 1):
                tasks = self.idx.get((di, h), [])
                is_now = is_today_col and (h == now.hour)
                slot = self._slot_body(tasks, is_now, d, h)
                drop = ft.DragTarget(group="task", content=slot,
                                     on_accept=lambda e, _d=d, _h=h: self._on_drop_accept(_d, _h, e))
                cell = ft.Container(
                    content=drop, width=DAY_COL_W, height=self.row_h[h],
                    bgcolor=CLR_TODAY_BG if is_today_col else None,
                    border=ft.border.only(
                        right=ft.BorderSide(0.5, CLR_OUTLINE) if di < 6 else None,
                        bottom=ft.BorderSide(0.6, CLR_OUTLINE),
                    ),
                )
                col_rows.append(cell)
            day_cols.append(ft.Container(content=ft.Column(col_rows, spacing=0), width=DAY_COL_W))

        hrow_body = ft.Row(controls=day_cols, spacing=0, ref=self._hrow_body_ref, scroll=ft.ScrollMode.ALWAYS)
        body_viewport = ft.Container(expand=True, content=hrow_body, clip_behavior=ft.ClipBehavior.HARD_EDGE)

        # --- синхронизация скролла шапки и тела ---
        hrow_header.on_scroll = self._on_header_hscroll
        hrow_body.on_scroll   = self._on_body_hscroll

        # --- вертикальный скролл: часы слева (фикс), тело справа (viewport по X) ---
        vscroll_body = ft.Column(
            controls=[ft.Row([hours_col, body_viewport], spacing=0)],
            spacing=0, expand=True, scroll=ft.ScrollMode.ALWAYS, ref=self._vcol_ref
        )

        # --- финальная сборка ---
        top_header = ft.Row(
            controls=[ft.Container(width=HOURS_COL_W, height=HEADER_H), header_viewport],
            spacing=0,
        )

        return ft.Container(
            content=ft.Column(
                controls=[top_header, ft.Divider(height=1, color=CLR_OUTLINE), vscroll_body],
                spacing=0, expand=True),
            expand=True,
            border_radius=8,
            border=ft.border.all(0.5, CLR_OUTLINE),
            padding=8,
            bgcolor="#fff",
        )

    # синхронизация горизонтального скролла (без «рывков»)
    def _on_body_hscroll(self, e: ft.OnScrollEvent):
        if self._syncing_hscroll:
            return
        try:
            self._syncing_hscroll = True
            hdr = self._hrow_header_ref.current
            if hdr:
                hdr.scroll_to(offset=e.pixels, duration=0)
        finally:
            self._syncing_hscroll = False

    def _on_header_hscroll(self, e: ft.OnScrollEvent):
        if self._syncing_hscroll:
            return
        try:
            self._syncing_hscroll = True
            body = self._hrow_body_ref.current
            if body:
                body.scroll_to(offset=e.pixels, duration=0)
        finally:
            self._syncing_hscroll = False

    def _slot_body(self, tasks: List[dict], is_now_hour: bool, day: date, hour: int) -> ft.Control:
        chips: List[ft.Control] = []
        if is_now_hour:
            chips.append(ft.Container(key=NOW_ANCHOR_KEY, height=1, width=1))
            chips.append(ft.Container(height=2, bgcolor=CLR_NOW_LINE))

        if tasks:
            for t in tasks:
                chips.append(self._build_chip(t, day, hour))
        else:
            # кликабельная площадь заполняет весь слот по высоте
            chips.append(
                ft.Container(
                    on_click=lambda e, d=day, h=hour: self.open_quick_add(d, h),
                    width=DAY_COL_W,
                    height=max(8, self.row_h.get(hour, ROW_MIN_H) - 8),
                )
            )

        return ft.Container(
            content=ft.Column(chips, spacing=CHIPS_SPACING),
            padding=ft.padding.only(left=6, right=6, top=4, bottom=4),
            width=DAY_COL_W, expand=True,
            bgcolor=CLR_SURFVAR if tasks else None,
        )

    def _build_chip(self, t: dict, day: date, hour: int) -> ft.Control:
        title = t.get("title", "")
        tid = t.get("task_id")
        dur = t.get("duration", 30)
        priority = t.get("priority", 0)

        chip_body = ft.Container(
            content=ft.Row(
                [
                    self._priority_badge(priority),
                    ft.Text(
                        title,
                        size=11,
                        color=CLR_CHIP_TXT,
                        no_wrap=True,
                        overflow=ft.TextOverflow.ELLIPSIS,
                    ),
                ],
                spacing=6,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            bgcolor=priority_bgcolor(priority) if priority else CLR_CHIP,
            border=ft.border.all(0.5, CLR_OUTLINE),
            border_radius=8,
            padding=6, width=DAY_COL_W-12,
        )
        gd = ft.GestureDetector(
            content=chip_body,
            on_tap=lambda e, _tid=tid: self._open_edit_dialog(_tid, title, dur),
            on_secondary_tap=lambda e, _tid=tid, _title=title, _dur=dur, _d=day, _h=hour:
                self._open_chip_menu(_tid, _title, _dur, _d, _h),
        )
        return ft.Draggable(
            group="task",
            data=str(tid),
            on_drag_start=lambda e, t_id=tid: self._remember_drag(t_id),
            content=gd,
            content_feedback=ft.Container(
                content=ft.Text(title, size=12),
                padding=8, bgcolor="#ffffff", border_radius=6, border=ft.border.all(0.5, CLR_OUTLINE),
            ),
        )

    # ===== Контекстное меню чипа =====
    def _open_chip_menu(self, task_id: int, title: str, duration: int, day: date, hour: int):
        dlg = None

        def close(_=None):
            self._close_dialog(dlg)
            self._sweep_overlay()  

        def act_edit(_):
            close(); self._open_edit_dialog(task_id, title, duration)

        def act_move(_):
            close(); self._schedule_task(task_id, day, hour)

        def _apply_snooze(action, success_message: str):
            close()
            task = self.svc.get(task_id)
            if not task:
                return self._toast("Задача не найдена")
            result = action(task)
            self._reschedule(task_id, result.start, result.duration_minutes)
            self._toast(success_message)

        def act_snooze15(_):
            _apply_snooze(lambda task: snooze.minutes(task, 15), "Перенесено на +15 мин")

        def act_snooze30(_):
            _apply_snooze(lambda task: snooze.minutes(task, 30), "Перенесено на +30 мин")

        def act_snooze60(_):
            _apply_snooze(lambda task: snooze.minutes(task, 60), "Перенесено на +60 мин")

        def act_evening(_):
            _apply_snooze(snooze.tonight, "Перенесено на вечер")

        def act_tomorrow(_):
            _apply_snooze(snooze.tomorrow_morning, "Перенесено на завтра утром")

        def act_delete(_):
            close(); self._delete_task(task_id)

        content = ft.Column(
            controls=[
                ft.TextButton("Редактировать", on_click=act_edit),
                ft.TextButton("Перенести…", on_click=act_move),
                ft.Divider(height=1),
                ft.TextButton("Snooze +15 мин", on_click=act_snooze15),
                ft.TextButton("Snooze +30 мин", on_click=act_snooze30),
                ft.TextButton("Snooze +60 мин", on_click=act_snooze60),
                ft.TextButton("Сегодня вечером", on_click=act_evening),
                ft.TextButton("Завтра утром", on_click=act_tomorrow),
                ft.Divider(height=1),
                ft.TextButton("Удалить", icon=ft.Icons.DELETE_OUTLINE, on_click=act_delete),
            ],
            tight=True, spacing=4, width=240,
        )
        dlg = ft.AlertDialog(modal=False, title=ft.Text(title), content=content)
        self._open_dialog(dlg)

    # ===== DnD =====
    def _on_drop_accept(self, day: date, hour: int, e):
        task_id = self.current_drag_task_id
        if task_id is None:
            s = str(e.data or "").strip()
            if s.isdigit():
                task_id = int(s)
            else:
                try:
                    payload = json.loads(s)
                    if isinstance(payload, dict) and "task_id" in payload:
                        task_id = int(payload["task_id"])
                except Exception:
                    task_id = None
        self.current_drag_task_id = None
        if task_id is None:
            return self._toast("Не удалось определить задачу")
        self._schedule_task(task_id, day, hour)

    # ===== Планирование и быстрый блок =====
    def _schedule_task(self, task_id: int, day: date, hour: int):
        start_dt = datetime(day.year, day.month, day.day, hour, 0, 0)
        task = self.svc.get(task_id)
        if not task:
            return self._toast("Задача не найдена")
        dur_value = task.duration_minutes or 30
        dur_tf = ft.TextField(label="Длительность, мин", value=str(dur_value), width=140)
        priority_dd = ft.Dropdown(
            label="Приоритет",
            width=220,
            value=str(getattr(task, "priority", 0)),
            options=self._priority_options,
        )
        dlg = None

        def on_save(_):
            try:
                duration = int(dur_tf.value)
                if duration <= 0:
                    raise ValueError
            except Exception:
                return self._toast("Длительность должна быть > 0")

            priority = normalize_priority(priority_dd.value)

            t = self.svc.update(
                task_id,
                start=start_dt,
                duration_minutes=duration,
                priority=priority,
            )
            if t:
                self._remove_undated_mapping(task_id, delete_remote=True)
            try:
                if t and getattr(t, "gcal_event_id", None):
                    self.app.gcal.update_event_for_task(t.gcal_event_id, t, start_dt, duration)
                elif t:
                    ev = self.app.gcal.create_event_for_task(t, start_dt, duration)
                    self.svc.set_event_id(task_id, ev["id"])
            except Exception as ex:
                print("Google calendar save error:", ex)
                self.app.notify_google_unavailable(ex)
                self._toast(f"Google недоступен: {ex}")

            self._close_dialog(dlg)
            self._sweep_overlay()
            self.load()

        def on_cancel(_):
            self._close_dialog(dlg)
            self._sweep_overlay()

        dlg = ft.AlertDialog(
            modal=False,
            inset_padding=ft.padding.all(16),
            content_padding=ft.padding.all(12),
            title=ft.Text(f"Запланировать — {start_dt.strftime('%a, %d.%m %H:00')}"),
            content=ft.Container(
                width=DIALOG_WIDTH_NARROW,
                content=ft.Column([dur_tf, priority_dd], spacing=12, tight=True),
            ),
            actions=[
                ft.TextButton("Отмена", on_click=on_cancel),
                ft.FilledButton("Сохранить", icon=ft.Icons.SAVE, on_click=on_save),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )

        self._open_dialog(dlg)

    def open_quick_add(self, day: date, hour: int):
        start_dt = datetime(day.year, day.month, day.day, hour, 0, 0)
        title_tf = ft.TextField(label="Название", width=DIALOG_WIDTH_NARROW - 40)
        dur_tf = ft.TextField(label="Длительность, мин", value="30", width=140)
        priority_dd = ft.Dropdown(
            label="Приоритет",
            width=180,
            value=str(0),
            options=self._priority_options,
        )
        dlg = None

        def on_save(_):
            title = (title_tf.value or "").strip()
            if not title:
                return self._toast("Введите название")
            try:
                duration = int(dur_tf.value)
                if duration <= 0:
                    raise ValueError
            except Exception:
                return self._toast("Длительность должна быть > 0")
            priority = normalize_priority(priority_dd.value)

            task = self.svc.add(title=title, start=start_dt, duration_minutes=duration, priority=priority)
            try:
                ev = self.app.gcal.create_event_for_task(task, start_dt, duration)
                self.svc.set_event_id(task.id, ev["id"])
                self._toast("Создано и в календаре")
            except Exception as ex:
                print("Google create event error:", ex)
                self.app.notify_google_unavailable(ex)
                self._toast(f"Создано локально (Google недоступен): {ex}")

            self._close_dialog(dlg)
            self._sweep_overlay()
            self.load()

        def on_cancel(_):
            self._close_dialog(dlg)
            self._sweep_overlay()

        dlg = ft.AlertDialog(
            modal=False,
            inset_padding=ft.padding.all(16),
            content_padding=ft.padding.all(12),
            title=ft.Text(f"Быстрый блок — {start_dt.strftime('%a, %d.%m %H:00')}"),
            content=ft.Container(
                width=DIALOG_WIDTH_NARROW,
                content=ft.Column([title_tf, dur_tf, priority_dd], spacing=12, tight=True),
            ),
            actions=[
                ft.TextButton("Отмена", on_click=on_cancel),
                ft.FilledButton("Сохранить", icon=ft.Icons.SAVE, on_click=on_save),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        self._open_dialog(dlg)

    # ===== Редактирование / Snooze / Удаление =====
    # ui/pages/calendar.py  (внутри класса CalendarPage)
    def _open_edit_dialog(
        self,
        task_id: int,
        current_title: str | None = None,
        current_duration: int | None = None,
    ):
        # --- берём актуальные данные задачи ---
        t = None
        try:
            t = self.svc.get(task_id)
        except Exception:
            pass
        if t is None:
            return self._toast("Задача не найдена")

        title_init = current_title if current_title is not None else (t.title or "")
        dur_init   = current_duration if current_duration is not None else (t.duration_minutes or 30)
        start_init = getattr(t, "start", None)

        date_str = start_init.strftime("%d.%m.%Y") if isinstance(start_init, datetime) else ""
        time_str = start_init.strftime("%H:%M")     if isinstance(start_init, datetime) else ""

        # --- поля формы (без expand) ---
        DATE_W, TIME_W, DUR_W = 140, 100, 120

        title_tf = ft.TextField(label="Название", value=title_init, width=DIALOG_WIDTH_WIDE - 80)
        date_tf  = ft.TextField(label="Дата",  value=date_str, width=DATE_W, read_only=True)
        time_tf  = ft.TextField(label="Время", value=time_str, width=TIME_W, read_only=True)
        dur_tf   = ft.TextField(label="Длительность, мин", value=str(dur_init), width=DUR_W)
        priority_dd = ft.Dropdown(
            label="Приоритет",
            width=200,
            value=str(getattr(t, "priority", 0)),
            options=self._priority_options,
        )

        # заметки (авто-увеличение по числу строк)
        notes_tf = ft.TextField(
            label="Заметки",
            value=(t.notes or ""),
            multiline=True,
            min_lines=3,
            max_lines=6,
        )
        def _autogrow(_=None):
            s = notes_tf.value or ""
            # считаем количество визуальных строк (по \n)
            lines = max(3, min(12, s.count("\n") + 1))
            if notes_tf.max_lines != lines:
                notes_tf.max_lines = lines
                self.app.page.update()
        notes_tf.on_change = _autogrow
        _autogrow()  # подстроиться под начальный текст

        # --- пикеры ---
        dp = ft.DatePicker(
            first_date=date(2000, 1, 1),
            last_date=date(2100, 12, 31),
            on_change=lambda e: self._set_tf_date(date_tf, e.data or e.control.value),
            on_dismiss=lambda e: self._set_tf_date(date_tf, e.control.value),
        )
        tp = self._new_time_picker()
        for p in (dp, tp):
            if p not in self.app.page.overlay:
                self.app.page.overlay.append(p)

        date_btn = ft.IconButton(
            icon=ft.Icons.CALENDAR_MONTH,
            tooltip="Выбрать дату",
            icon_size=18,
            on_click=lambda e, _dp=dp: self.app.page.open(_dp),
        )
        time_btn = ft.IconButton(
            icon=ft.Icons.SCHEDULE,
            tooltip="Выбрать время",
            icon_size=18,
            on_click=lambda e, _tp=tp: self._open_time_picker(_tp, time_tf),
        )

        # --- сохранение / отмена ---
        dlg = None

        def _remove_pickers():
            for ctrl in (dp, tp):
                try:
                    ctrl.open = False
                except Exception:
                    pass
                try:
                    if ctrl in self.app.page.overlay:
                        self.app.page.overlay.remove(ctrl)
                except Exception:
                    pass

        def on_save(_):
            new_title = (title_tf.value or "").strip()
            if not new_title:
                return self._toast("Введите название")

            if date_tf.value and self._parse_date_tf(date_tf.value) is None:
                return self._toast("Неверный формат даты. Пример: 10.10.2025")
            if time_tf.value and self._parse_time_tf(time_tf.value) is None:
                return self._toast("Неверный формат времени. Пример: 09:30")

            new_start = self._combine_dt(date_tf.value, time_tf.value)
            try:
                new_dur = int(dur_tf.value) if dur_tf.value.strip() else None
            except ValueError:
                return self._toast("Длительность должна быть числом (мин)")

            updated = self.svc.update(
                task_id,
                title=new_title,
                notes=notes_tf.value,
                start=new_start,
                duration_minutes=new_dur,
                priority=normalize_priority(priority_dd.value),
            )
            if updated:
                if new_start is None:
                    self._mark_undated_dirty(updated.id)
                else:
                    self._remove_undated_mapping(updated.id, delete_remote=True)

            # --- Google Calendar sync ---
            if new_start is not None and new_dur is not None:
                if updated.gcal_event_id:
                    try:
                        self.app.gcal.update_event_for_task(updated.gcal_event_id, updated, new_start, new_dur)
                    except Exception as e:
                        print("Google update event error:", e)
                        self.app.notify_google_unavailable(e)
                        self._toast(f"Google: не удалось обновить: {e}")
                else:
                    try:
                        ev = self.app.gcal.create_event_for_task(updated, new_start, new_dur)
                        self.svc.set_event_id(task_id, ev["id"])
                    except Exception as e:
                        print("Google create event error:", e)
                        self.app.notify_google_unavailable(e)
                        self._toast(f"Google: не удалось создать: {e}")
            else:
                # если дата/время/длительность очищены — удаляем привязанное событие
                if updated.gcal_event_id:
                    try:
                        self.app.gcal.delete_event_by_id(updated.gcal_event_id)
                    except Exception as e:
                        print("Google delete event error:", e)
                        self.app.notify_google_unavailable(e)
                    finally:
                        self.svc.set_event_id(task_id, None)

            _remove_pickers()
            self._close_dialog(dlg)
            self._sweep_overlay()
            self.load()
            self._toast("Сохранено")

        def on_cancel(_=None):
            _remove_pickers()
            self._close_dialog(dlg)
            self._sweep_overlay()

        # --- компактная вёрстка (без Wrap) ---
        utils_row = ft.Row(
            [date_tf, date_btn, time_tf, time_btn, dur_tf, priority_dd],
            spacing=8,
            vertical_alignment=ft.CrossAxisAlignment.END,
        )
        buttons_row = ft.Row(
            [ft.TextButton("Отмена", on_click=on_cancel),
            ft.FilledButton("Сохранить", icon=ft.Icons.SAVE, on_click=on_save)],
            alignment=ft.MainAxisAlignment.END,
        )

        dlg = ft.AlertDialog(
            modal=False,
            inset_padding=ft.padding.all(16),
            content_padding=ft.padding.all(12),
            title=ft.Text("Редактировать задачу"),
            content=ft.Container(
                width=DIALOG_WIDTH_WIDE,
                content=ft.Column(
                    [title_tf, utils_row, notes_tf, buttons_row],
                    spacing=10,
                    tight=True,
                    scroll=ft.ScrollMode.ADAPTIVE,
                ),
            ),
        )

        dlg.data = {"on_close": _remove_pickers}
        self._open_dialog(dlg)



    def _reschedule(self, task_id: int, start_dt: datetime, duration: int):
        t = self.svc.update(task_id, start=start_dt, duration_minutes=duration)
        if t:
            self._remove_undated_mapping(task_id, delete_remote=True)
        try:
            if t and getattr(t, "gcal_event_id", None):
                self.app.gcal.update_event_for_task(t.gcal_event_id, t, start_dt, duration)
            elif t:
                ev = self.app.gcal.create_event_for_task(t, start_dt, duration)
                self.svc.set_event_id(task_id, ev["id"])
        except Exception as ex:
            print("Google calendar save error:", ex)
            self.app.notify_google_unavailable(ex)
            self._toast(f"Google недоступен: {ex}")
        self.load()

    # ===== автопрокрутка к сегодняшнему дню и текущему часу =====
    def _scroll_to_now(self):
        # вертикаль
        try:
            now = datetime.now()
            top_offset = 0
            for h in range(DAY_START, min(now.hour, DAY_END + 1)):
                top_offset += self.row_h.get(h, 0)
            if self._vcol_ref.current:
                self._vcol_ref.current.scroll_to(offset=top_offset, duration=300)
        except Exception:
            pass

        # горизонталь
        try:
            day_idx = (date.today() - self.week_start).days
            if 0 <= day_idx < 7:
                offset_x = DAY_COL_W * day_idx
                if self._hrow_body_ref.current:
                    self._hrow_body_ref.current.scroll_to(offset=offset_x, duration=300)
                if self._hrow_header_ref.current:
                    self._hrow_header_ref.current.scroll_to(offset=offset_x, duration=300)
        except Exception:
            pass

    # публичная обёртка, чтобы дергать из AppShell    
    def scroll_to_now(self):
        self._scroll_to_now()

    # ===== Вспомогательное для форм =====
    def _new_time_picker(self) -> ft.TimePicker:
        picker = ft.TimePicker(help_text="Выберите время")
        picker.on_change = lambda e, _picker=picker: self._time_picker_on_change(_picker, e)
        picker.on_dismiss = lambda e, _picker=picker: self._time_picker_on_dismiss(_picker, e)
        return picker

    def _open_time_picker(self, picker: ft.TimePicker, tf: ft.TextField):
        prev = tf.value
        parsed = self._parse_time_tf(tf.value)
        if parsed:
            base_time = dt_time(parsed[0], parsed[1])
        else:
            now = datetime.now()
            base_time = dt_time(now.hour, now.minute)
        try:
            picker.value = base_time
        except Exception:
            pass
        picker.data = {"tf": tf, "prev": prev, "applied": False}
        if picker not in self.app.page.overlay:
            self.app.page.overlay.append(picker)
        try:
            picker.open = True
        except Exception:
            pass
        try:
            picker.pick_time()
        except Exception:
            pass
        self.app.page.update()

    def _time_picker_on_change(self, picker: ft.TimePicker, e: ft.ControlEvent):
        data = picker.data or {}
        tf = data.get("tf")
        if not tf:
            return
        value = e.data or picker.value
        if value:
            self._set_tf_time(tf, value)
            data["applied"] = True
            picker.data = data

    def _time_picker_on_dismiss(self, picker: ft.TimePicker, e: ft.ControlEvent):
        data = picker.data or {}
        tf = data.get("tf")
        if not tf:
            picker.data = None
            return
        value = e.data
        if value:
            self._set_tf_time(tf, value)
            data["applied"] = True
        elif not data.get("applied"):
            tf.value = data.get("prev", tf.value)
            self.app.page.update()
        picker.data = None

    def _set_tf_date(self, tf: ft.TextField, value):
        from datetime import date as _date, datetime as _dt

        v = value
        if isinstance(v, _date):
            tf.value = v.strftime("%d.%m.%Y")
        elif isinstance(v, str) and v.strip():
            s = v.strip()
            try:
                tf.value = _dt.strptime(s, "%Y-%m-%d").strftime("%d.%m.%Y")
            except ValueError:
                if "T" in s:
                    try:
                        tf.value = _dt.strptime(s.split("T")[0], "%Y-%m-%d").strftime("%d.%m.%Y")
                    except ValueError:
                        pass
                else:
                    try:
                        _dt.strptime(s, "%d.%m.%Y")
                        tf.value = s
                    except ValueError:
                        return
        self.app.page.update()

    def _set_tf_time(self, tf: ft.TextField, value):
        if value in (None, ""):
            return
        try:
            tf.value = value.strftime("%H:%M")
            self.app.page.update()
            return
        except Exception:
            pass

        s = str(value or "").strip()
        m = re.match(r"^(\d{1,2}):(\d{2})(?::(\d{2}))?$", s)
        if m:
            h = int(m.group(1))
            mm = int(m.group(2))
            if 0 <= h <= 23 and 0 <= mm <= 59:
                tf.value = f"{h:02d}:{mm:02d}"
        self.app.page.update()

    def _parse_date_tf(self, s: str):
        s = (s or "").strip()
        m = re.match(r"^\s*(\d{1,2})\.(\d{1,2})\.(\d{4})\s*$", s)
        if not m:
            return None
        d, mth, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            return date(y, mth, d)
        except ValueError:
            return None

    def _parse_time_tf(self, s: str):
        s = (s or "").strip()
        m = re.match(r"^\s*(\d{1,2}):(\d{2})(?::\d{2})?\s*$", s)
        if not m:
            return None
        h, minute = int(m.group(1)), int(m.group(2))
        if 0 <= h <= 23 and 0 <= minute <= 59:
            return h, minute
        return None

    def _combine_dt(self, date_str: str, time_str: str):
        d = self._parse_date_tf(date_str)
        t = self._parse_time_tf(time_str)
        if d and t:
            return datetime(d.year, d.month, d.day, t[0], t[1])
        if d and not t:
            return datetime(d.year, d.month, d.day)
        if t and not d:
            today = date.today()
            return datetime(today.year, today.month, today.day, t[0], t[1])
        return None

    # авто-увеличение высоты многострочного TextField
    def _autogrow_textfield(self, tf: ft.TextField, *, min_lines=2, max_lines=14, wrap_at=60):
        text = tf.value or ""
        # грубо оцениваем количество строк с учётом переносов
        lines = text.splitlines() or [""]
        est = sum((len(l) // wrap_at) + 1 for l in lines)
        h = max(min_lines, min(est, max_lines))
        tf.min_lines = h
        tf.max_lines = h


    # ===== сервис =====
    def _priority_badge(self, priority: int) -> ft.Control:
        if priority <= 0:
            return ft.Container(width=0)
        return ft.Container(
            content=ft.Text(
                priority_label(priority, short=True),
                size=10,
                weight=ft.FontWeight.W_500,
                color=priority_color(priority),
            ),
            bgcolor=priority_bgcolor(priority),
            padding=ft.padding.symmetric(horizontal=6, vertical=2),
            border_radius=ft.border_radius.all(6),
        )

    def _toast(self, text: str):
        self.app.page.snack_bar = ft.SnackBar(ft.Text(text))
        self.app.page.snack_bar.open = True
        self.app.page.update()
    
