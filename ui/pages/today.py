# planner/ui/pages/today.py
import re
from datetime import datetime, date, time as dt_time
import flet as ft

from services.tasks import TaskService
from core.priorities import (
    DEFAULT_PRIORITY,
    priority_options,
    priority_label,
    priority_color,
    normalize_priority,
)
from core.settings import UI
from helpers import snooze
from helpers.datetime_utils import (
    parse_date_input,
    parse_time_input,
    snap_minutes,
)

GRID_STEP = UI.calendar.grid_step_minutes
MIN_DURATION = UI.calendar.min_block_duration_minutes


class TodayPage:
    LIST_SECTION_HEIGHT = max((UI.today.list_section_height or 0) + 100, 540)

    def __init__(self, app):
        self.app = app
        self.svc = TaskService()
        self.edit_dialog: ft.AlertDialog | None = None

        # ---------- Быстрый ввод ----------
        self.title_tf = ft.TextField(
            label="Название задачи",
            hint_text="Например: Позвонить Ивану",
            expand=True,
            prefix=ft.Icon(ft.Icons.TASK_ALT),
        )

        self.date_tf = ft.TextField(
            label="Дата", hint_text="напр.: 10.10.2025", width=160
        )
        self.time_tf = ft.TextField(
            label="Время", hint_text="чч:мм", width=120
        )


        # В 0.28.3 дата надёжно приходит через control.value, иногда только в on_dismiss
        self.date_picker_add = ft.DatePicker(
            first_date=date(2000, 1, 1),
            last_date=date(2100, 12, 31),
        )
        self.date_picker_add.on_change = lambda e: self._set_tf_date(
            self.date_tf, e.data or e.control.value
        )
        self.date_picker_add.on_dismiss = (
            lambda e, picker=self.date_picker_add: self._handle_date_picker_dismiss(
                picker, self.date_tf, e.control.value, keep=True
            )
        )

        # TimePicker
        self.time_picker_add = self._new_time_picker()

        for p in (self.date_picker_add, self.time_picker_add):
            if p not in self.app.page.overlay:
                self.app.page.overlay.append(p)

        self.date_btn = ft.IconButton(
            icon=ft.Icons.CALENDAR_MONTH,
            tooltip="Календарь",
            on_click=lambda e: self._open_date_picker(self.date_picker_add),
        )
        self.time_btn = ft.IconButton(
            icon=ft.Icons.SCHEDULE,
            tooltip="Выбрать время",
            on_click=lambda e: self._open_time_picker(self.time_picker_add, self.time_tf, keep=True),
        )

        self.dur_tf = ft.TextField(
            label="Длительность, мин",
            value=str(UI.today.default_duration_minutes),
            width=160,
            prefix=ft.Icon(ft.Icons.TIMER),
        )
        self.priority_dd = ft.Dropdown(
            label="Приоритет",
            width=160,
            value=str(DEFAULT_PRIORITY),
            options=[ft.dropdown.Option(key, label) for key, label in priority_options().items()],
        )
        self.to_calendar_cb = ft.Checkbox(
            label="Сразу в календарь",
            value=UI.today.add_to_calendar_by_default,
        )
        self.add_btn = ft.FilledButton("Добавить", icon=ft.Icons.ADD, on_click=self.on_add)

        quick_add = ft.Card(
            content=ft.Container(
                content=ft.Column(
                    [
                        ft.Text("Быстрый ввод", size=18, weight=ft.FontWeight.W_600),
                        ft.Row(
                            [
                                self.title_tf,
                                ft.Row([self.date_tf, self.date_btn], spacing=6),
                                ft.Row([self.time_tf, self.time_btn], spacing=6),
                                self.dur_tf,
                                self.priority_dd,
                                self.to_calendar_cb,
                                self.add_btn,
                            ],
                            alignment=ft.MainAxisAlignment.START,
                            vertical_alignment=ft.CrossAxisAlignment.END,
                        ),
                    ],
                    spacing=12,
                ),
                padding=16,
            )
        )

        self.today_list = ft.ListView(expand=True, spacing=12)
        self.unscheduled_list = ft.ListView(expand=True, spacing=12)

        today_card = ft.Card(
            content=ft.Container(
                padding=16,
                content=ft.Column(
                    [
                        ft.Text("Сегодня", size=18, weight=ft.FontWeight.W_600),
                        ft.Container(content=self.today_list, height=self.LIST_SECTION_HEIGHT),
                    ],
                    spacing=12,
                ),
            )
        )
        unscheduled_card = ft.Card(
            content=ft.Container(
                padding=16,
                content=ft.Column(
                    [
                        ft.Text("Без даты", size=18, weight=ft.FontWeight.W_600),
                        ft.Container(content=self.unscheduled_list, height=self.LIST_SECTION_HEIGHT),
                    ],
                    spacing=12,
                ),
            )
        )

        lists_row = ft.Row(
            [
                ft.Container(content=today_card, expand=True),
                ft.Container(content=unscheduled_card, expand=True),
            ],
            spacing=16,
            vertical_alignment=ft.CrossAxisAlignment.START,
        )

        self.view = ft.Container(
            content=ft.Column(
                [
                    ft.Text("Задачи", size=24, weight=ft.FontWeight.BOLD),
                    quick_add,
                    lists_row,
                ],
                spacing=16,
                expand=True,
            ),
            expand=True,
            padding=20,
        )

        self.refresh_lists()
    
    # --- вызов из меню/автообновления ---
    def activate_from_menu(self):
        self._close_local_overlays()
        self.load()

    def load(self):
        # алиас для унификации с календарём
        self.refresh_lists()

    # ---------- Утилиты ----------
    def _close_overlay_control(self, ctrl, *, remove: bool = True) -> bool:
        if not ctrl:
            return False
        changed = False
        try:
            if hasattr(ctrl, "open") and getattr(ctrl, "open", False):
                ctrl.open = False
                changed = True
        except Exception:
            pass
        try:
            overlay = getattr(self.app.page, "overlay", None) or []
            if remove and ctrl in overlay:
                overlay.remove(ctrl)
                changed = True
        except Exception:
            pass
        return changed

    def _close_local_overlays(self):
        changed = False
        if self.edit_dialog:
            dlg = self.edit_dialog
            self.edit_dialog = None
            self._close_alert_dialog(dlg)
            changed = True
        for ctrl in (self.date_picker_add, self.time_picker_add):
            if self._close_overlay_control(ctrl, remove=False):
                changed = True
        try:
            overlays_snapshot = list(getattr(self.app.page, "overlay", None) or [])
        except Exception:
            overlays_snapshot = []
        for ctrl in overlays_snapshot:
            if isinstance(ctrl, (ft.DatePicker, ft.TimePicker)) and ctrl not in (self.date_picker_add, self.time_picker_add):
                if self._close_overlay_control(ctrl):
                    changed = True
        if changed:
            self.app.cleanup_overlays()

    def _handle_date_picker_dismiss(self, picker: ft.DatePicker, tf: ft.TextField, value, *, keep: bool = False):
        self._close_overlay_control(picker, remove=not keep)
        self._set_tf_date(tf, value)
        self.app.cleanup_overlays()

    def _handle_time_picker_close(self, picker: ft.TimePicker, *, keep: bool = False):
        self._close_overlay_control(picker, remove=not keep)
        self.app.cleanup_overlays()

    def _clear_errors(self):
        for ctrl in (self.title_tf, self.date_tf, self.time_tf, self.dur_tf):
            ctrl.error_text = None
            ctrl.border_color = None

    def _mark_error(self, ctrl: ft.TextField, message: str):
        ctrl.error_text = message
        ctrl.border_color = ft.Colors.RED_400

    def _new_time_picker(self) -> ft.TimePicker:
        picker = ft.TimePicker(help_text="Выберите время")
        picker.on_change = lambda e, _picker=picker: self._time_picker_on_change(_picker, e)
        picker.on_dismiss = lambda e, _picker=picker: self._time_picker_on_dismiss(_picker, e)
        return picker

    def _open_date_picker(self, picker: ft.DatePicker):
        if picker not in self.app.page.overlay:
            self.app.page.overlay.append(picker)
        self.app.page.open(picker)

    def _open_time_picker(self, picker: ft.TimePicker, tf: ft.TextField, *, keep: bool = False):
        prev = tf.value
        parsed = self._parse_time_tf(tf.value)
        if parsed:
            base_time = parsed
        else:
            now = datetime.now()
            base_time = dt_time(now.hour, now.minute)
        try:
            picker.value = base_time
        except Exception:
            pass
        picker.data = {"tf": tf, "prev": prev, "applied": False, "keep": keep}
        if picker not in self.app.page.overlay:
            self.app.page.overlay.append(picker)
        self.app.page.open(picker)

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
        keep = data.get("keep", False)
        if not tf:
            self._handle_time_picker_close(picker, keep=keep)
            picker.data = None
            return
        value = e.data
        if value:
            self._handle_time_picker_close(picker, keep=keep)
            self._set_tf_time(tf, value)
            data["applied"] = True
        elif not data.get("applied"):
            tf.value = data.get("prev", tf.value)
            self._handle_time_picker_close(picker, keep=keep)
            self.app.page.update()
        else:
            self._handle_time_picker_close(picker, keep=keep)
        picker.data = None

    def _set_tf_date(self, tf: ft.TextField, value):
        from datetime import date as _date, datetime

        v = value  # сюда вы передаёте e.data or e.control.value

        # Если пришёл объект date
        if isinstance(v, _date):
            tf.value = v.strftime("%d.%m.%Y")

        # Если пришла строка
        elif isinstance(v, str) and v.strip():
            s = v.strip()

            # 1) ISO 'YYYY-MM-DD'
            try:
                tf.value = datetime.strptime(s, "%Y-%m-%d").strftime("%d.%m.%Y")
            except ValueError:
                # 2) 'YYYY-MM-DDTHH:MM:SS...' -> берём дату до 'T'
                if "T" in s:
                    try:
                        tf.value = datetime.strptime(s.split("T")[0], "%Y-%m-%d").strftime("%d.%m.%Y")
                    except ValueError:
                        pass
                else:
                    # 3) уже 'DD.MM.YYYY' — оставляем как есть, если валидно
                    try:
                        datetime.strptime(s, "%d.%m.%Y")
                        tf.value = s
                    except ValueError:
                        # не распознали — ничего не меняем
                        return

        # Обновляем UI
        self.app.page.update()

    def _set_tf_time(self, tf: ft.TextField, value):
        """
        Унифицирует значение из TimePicker в формат HH:MM.
        Поддерживает: datetime.time, "HH:MM", "HH:MM:SS".
        """
        if value in (None, ""):
            return

        # если пришёл time-объект
        try:
            tf.value = value.strftime("%H:%M")
            self.app.page.update()
            return
        except Exception:
            pass

        # строковые варианты
        s = str(value or "").strip()
        m = re.match(r"^(\d{1,2}):(\d{2})(?::(\d{2}))?$", s)
        if m:
            h = int(m.group(1))
            mm = int(m.group(2))
            if 0 <= h <= 23 and 0 <= mm <= 59:
                tf.value = f"{h:02d}:{mm:02d}"
        self.app.page.update()


    def _parse_date_tf(self, s: str):
        return parse_date_input(s)

    def _parse_time_tf(self, s: str):
        return parse_time_input(s)

    def _combine_dt(self, parsed_date, parsed_time):
        if parsed_date and parsed_time:
            result = datetime.combine(parsed_date, parsed_time)
            if GRID_STEP > 0:
                total_minutes = result.hour * 60 + result.minute
                snapped = snap_minutes(total_minutes, step=GRID_STEP, direction="nearest")
                result = result.replace(hour=(snapped // 60) % 24, minute=snapped % 60)
            return result
        if parsed_date:
            return datetime.combine(parsed_date, dt_time(0, 0))
        return None

    # ---------- CRUD ----------
    def on_add(self, _):
        self._clear_errors()
        title = (self.title_tf.value or "").strip()
        if not title:
            self._mark_error(self.title_tf, "Введите название задачи")
            self.app.page.update()
            return

        raw_date = (self.date_tf.value or "").strip()
        raw_time = (self.time_tf.value or "").strip()
        raw_duration = (self.dur_tf.value or "").strip()

        parsed_date = parse_date_input(raw_date) if raw_date else None
        if raw_date and not parsed_date:
            self._mark_error(self.date_tf, "Неверная дата. Пример: 10.10.2025")
            self.app.page.update()
            return

        parsed_time = parse_time_input(raw_time) if raw_time else None
        if raw_time and not parsed_time:
            self._mark_error(self.time_tf, "Неверное время. Пример: 09:30")
            self.app.page.update()
            return

        start_dt = self._combine_dt(parsed_date, parsed_time)
        duration = None

        if start_dt is not None:
            if raw_duration:
                try:
                    duration = int(raw_duration)
                except (TypeError, ValueError):
                    self._mark_error(self.dur_tf, "Введите длительность в минутах")
                    self.app.page.update()
                    return
            else:
                duration = UI.today.default_duration_minutes

            duration = max(duration or MIN_DURATION, MIN_DURATION)
            duration = snap_minutes(duration, step=GRID_STEP, direction="nearest")

        priority = normalize_priority(self.priority_dd.value)

        task = self.svc.add(
            title=title,
            start=start_dt,
            duration_minutes=duration,
            priority=priority,
        )

        msg = "Задача добавлена"
        if self.to_calendar_cb.value and start_dt and duration:
            try:
                ev = self.app.gcal.create_event_for_task(task, start_dt, duration)
                self.svc.set_event_id(task.id, ev["id"])
                msg = "Задача добавлена и запланирована в Google"
            except Exception as e:
                print("Google create event error:", e)
                self.app.notify_google_unavailable(e)
                msg = f"Создана локально, Google недоступен: {e}"

        self.title_tf.value = ""
        self.date_tf.value = ""
        self.time_tf.value = ""
        self.dur_tf.value = str(UI.today.default_duration_minutes)
        self.priority_dd.value = str(DEFAULT_PRIORITY)
        self.refresh_lists()
        self._toast(msg)

    def on_toggle_done(self, task_id: int, checked: bool):
        self.svc.set_status(task_id, "done" if checked else "todo")
        self.refresh_lists()

    def on_delete(self, task_id: int, gcal_event_id: str | None):
        if gcal_event_id:
            try:
                self.app.gcal.delete_event_by_id(gcal_event_id)
            except Exception as ex:
                print("Google delete event error:", ex)
                self.app.notify_google_unavailable(ex)
        self.svc.delete(task_id)
        self.refresh_lists()
        self._toast("Задача удалена")

    def on_edit_click(self, e: ft.ControlEvent):
        self.open_edit_dialog(int(e.control.data))

    def _apply_snooze(self, task_id: int, preset, toast_message: str):
        task = self.svc.get(task_id)
        if not task:
            return self._toast("Задача не найдена")
        result = preset(task)
        updated = self.svc.update(task_id, start=result.start, duration_minutes=result.duration_minutes)
        if updated and getattr(updated, "gcal_event_id", None):
            try:
                self.app.gcal.update_event_for_task(
                    updated.gcal_event_id,
                    updated,
                    result.start,
                    result.duration_minutes,
                )
            except Exception as ex:
                print("Google update event error:", ex)
                self.app.notify_google_unavailable(ex)
                self._toast(f"Google недоступен: {ex}")
        self.refresh_lists()
        self._toast(toast_message)

    # ---------- Рендер ----------
    def refresh_lists(self):
        from datetime import date as _date
        self.today_list.controls.clear()
        for t in self.svc.list_for_day(_date.today()):
            self.today_list.controls.append(self._row_for_task(t))
        self.unscheduled_list.controls.clear()
        for t in self.svc.list_unscheduled():
            self.unscheduled_list.controls.append(self._row_for_task(t))
        self.app.cleanup_overlays()
        self.app.page.update()

    def _row_for_task(self, t):
        meta = self._human_time(t)
        checkbox = ft.Checkbox(
            value=(t.status == "done"),
            on_change=lambda e, tid=t.id: self.on_toggle_done(tid, e.control.value),
        )

        checkbox_holder = ft.Container(
            width=52,
            alignment=ft.alignment.center,
            content=checkbox,
        )

        priority_marker = self._priority_marker(t.priority)

        title_text = ft.Text(
            t.title,
            weight=ft.FontWeight.W_600,
            size=15,
            max_lines=2,
            overflow=ft.TextOverflow.ELLIPSIS,
        )

        meta_items = []
        if getattr(t, "priority", 0) > 0:
            meta_items.append(
                ft.Container(
                    content=ft.Text(
                        priority_label(t.priority, short=True),
                        size=12,
                        weight=ft.FontWeight.W_500,
                        color=ft.Colors.WHITE,
                    ),
                    bgcolor=priority_color(t.priority),
                    padding=ft.padding.symmetric(horizontal=10, vertical=4),
                    border_radius=999,
                )
            )
        meta_items.append(
            ft.Text(
                meta,
                color=ft.Colors.BLUE_GREY_400,
                size=12,
            )
        )
        if t.gcal_event_id:
            meta_items.append(
                ft.Row(
                    controls=[
                        ft.Icon(ft.Icons.LINK, size=14),
                        ft.Text("Google", size=12, color=ft.Colors.BLUE_GREY_400),
                    ],
                    spacing=4,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                )
            )

        info_column = ft.Column(
            controls=[
                ft.Row(
                    controls=[priority_marker, title_text],
                    spacing=12,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                ft.Row(meta_items, spacing=12, wrap=True),
            ],
            spacing=6,
            alignment=ft.MainAxisAlignment.CENTER,
            expand=True,
        )

        actions = ft.Row(
            controls=[
                ft.PopupMenuButton(
                    icon=ft.Icons.SNOOZE,
                    tooltip="Snooze",
                    items=[
                        ft.PopupMenuItem("Snooze +15 мин", on_click=lambda e, tid=t.id: self._apply_snooze(tid, lambda task: snooze.minutes(task, 15), "Перенесено на +15 мин")),
                        ft.PopupMenuItem("Snooze +30 мин", on_click=lambda e, tid=t.id: self._apply_snooze(tid, lambda task: snooze.minutes(task, 30), "Перенесено на +30 мин")),
                        ft.PopupMenuItem("Snooze +60 мин", on_click=lambda e, tid=t.id: self._apply_snooze(tid, lambda task: snooze.minutes(task, 60), "Перенесено на +60 мин")),
                        ft.PopupMenuItem(text="—", disabled=True),
                        ft.PopupMenuItem("Сегодня вечером", on_click=lambda e, tid=t.id: self._apply_snooze(tid, snooze.tonight, "Перенесено на вечер")),
                        ft.PopupMenuItem("Завтра утром", on_click=lambda e, tid=t.id: self._apply_snooze(tid, snooze.tomorrow_morning, "Перенесено на завтра утром")),
                    ],
                ),
                ft.IconButton(
                    icon=ft.Icons.EDIT_OUTLINED,
                    tooltip="Редактировать",
                    data=t.id,
                    on_click=self.on_edit_click,
                    style=ft.ButtonStyle(padding=ft.padding.all(8)),
                ),
                ft.IconButton(
                    icon=ft.Icons.DELETE_OUTLINE,
                    tooltip="Удалить",
                    on_click=lambda e, tid=t.id, ev=t.gcal_event_id: self.on_delete(tid, ev),
                    style=ft.ButtonStyle(padding=ft.padding.all(8)),
                ),
            ],
            spacing=4,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

        content_row = ft.Row(
            controls=[checkbox_holder, info_column, actions],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

        return ft.Container(
            content=content_row,
            padding=ft.padding.symmetric(horizontal=16, vertical=12),
            border_radius=12,
            bgcolor=ft.Colors.SURFACE,
            border=ft.border.all(1, ft.Colors.with_opacity(0.08, ft.Colors.ON_SURFACE)),
        )

    # ---------- Диалог редактирования ----------
    def open_edit_dialog(self, task_id: int):
        t = self.svc.get(task_id)
        if not t:
            return self._toast("Задача не найдена")

        # --- поля без expand, фикс-ширины только там, где нужно ---
        title_tf = ft.TextField(label="Название", value=t.title, width=420)

        date_val = t.start.strftime("%d.%m.%Y") if t.start else ""
        time_val = t.start.strftime("%H:%M") if (t.start and t.start.time() != datetime.min.time()) else ""

        date_tf = ft.TextField(label="Дата", value=date_val, width=160)
        time_tf = ft.TextField(label="Время", value=time_val, width=120)


        # Пикеры (значения забираем из e.data или control.value)
        dp = ft.DatePicker(
            first_date=date(2000, 1, 1),
            last_date=date(2100, 12, 31),
        )
        dp.on_change = lambda e: self._set_tf_date(date_tf, e.data or e.control.value)
        dp.on_dismiss = lambda e, picker=dp: self._handle_date_picker_dismiss(picker, date_tf, e.control.value)
        tp = self._new_time_picker()
        for p in (dp, tp):
            if p not in self.app.page.overlay:
                self.app.page.overlay.append(p)

        date_btn = ft.IconButton(
            icon=ft.Icons.CALENDAR_MONTH,
            tooltip="Календарь",
            on_click=lambda e, _dp=dp: self._open_date_picker(_dp),
        )
        time_btn = ft.IconButton(
            icon=ft.Icons.SCHEDULE,
            tooltip="Выбрать время",
            on_click=lambda e, _tp=tp: self._open_time_picker(_tp, time_tf),
        )

        dur_tf = ft.TextField(
            label="Длительность, мин",
            value=(str(t.duration_minutes) if t.duration_minutes else ""),
            width=140
        )
        priority_dd = ft.Dropdown(
            label="Приоритет",
            width=160,
            value=str(getattr(t, "priority", 0)),
            options=[ft.dropdown.Option(key, label) for key, label in priority_options().items()],
        )
        notes_tf = ft.TextField(
            label="Заметки",
            value=(t.notes or ""),
            multiline=True,
            min_lines=3,
            max_lines=6,
        )

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

        def _finalize_dialog():
            dlg = self.edit_dialog
            self.edit_dialog = None
            self._close_alert_dialog(dlg)

        def on_save(_):
            new_title = (title_tf.value or "").strip()
            if not new_title:
                return self._toast("Введите название")
            date_val_input = (date_tf.value or "").strip()
            parsed_date_edit = self._parse_date_tf(date_val_input) if date_val_input else None
            if date_val_input and parsed_date_edit is None:
                return self._toast("Неверный формат даты. Пример: 10.10.2025")
            time_val_input = (time_tf.value or "").strip()
            parsed_time_edit = self._parse_time_tf(time_val_input) if time_val_input else None
            if time_val_input and parsed_time_edit is None:
                return self._toast("Неверный формат времени. Пример: 09:30")

            new_start = self._combine_dt(parsed_date_edit, parsed_time_edit)
            try:
                dur_val = (dur_tf.value or "").strip()
                new_dur = int(dur_val) if dur_val else None
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

            # gcal-sync
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
                if updated.gcal_event_id:
                    try:
                        self.app.gcal.delete_event_by_id(updated.gcal_event_id)
                    except Exception as e:
                        print("Google delete event error:", e)
                        self.app.notify_google_unavailable(e)
                    finally:
                        self.svc.set_event_id(task_id, None)

            _remove_pickers()
            _finalize_dialog()
            self.refresh_lists()
            self._toast("Сохранено")

        def on_cancel(_=None):
            _remove_pickers()
            _finalize_dialog()

        # --- КОМПАКТНАЯ ВЁРСТКА ---

        # ... всё, что выше (поля, пикеры, on_save/on_cancel) оставь как есть ...

        # компактная разметка без Wrap
        DATE_W, TIME_W, DUR_W = 140, 100, 120
        date_tf.width = DATE_W
        time_tf.width = TIME_W
        dur_tf.width  = DUR_W

        date_btn.icon_size = 18
        time_btn.icon_size = 18

        utils_row = ft.Row(
            controls=[date_tf, date_btn, time_tf, time_btn, dur_tf, priority_dd],
            spacing=8,
            run_spacing=12,
            wrap=True,
            alignment=ft.MainAxisAlignment.START,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
        buttons_row = ft.Row(
            [ft.TextButton("Отмена", on_click=on_cancel),
            ft.FilledButton("Сохранить", icon=ft.Icons.SAVE, on_click=on_save)],
            alignment=ft.MainAxisAlignment.END,
        )

        MAX_W = 520
        self.edit_dialog = ft.AlertDialog(
            modal=False,
            inset_padding=ft.padding.all(16),
            content_padding=ft.padding.all(12),
            title=ft.Text("Редактировать задачу"),
            content=ft.Container(
                width=MAX_W,  # вместо constraints
                content=ft.Column(
                    [title_tf, utils_row, notes_tf, buttons_row],
                    spacing=10,
                    tight=True,
                    scroll=ft.ScrollMode.ADAPTIVE,
                ),
            ),
        )

        if self.edit_dialog not in self.app.page.overlay:
            self.app.page.overlay.append(self.edit_dialog)
        self.edit_dialog.open = True
        self.edit_dialog.on_dismiss = on_cancel
        self.app.page.update()



    # ---------- Вспомогательное ----------
    def _human_time(self, t):
        if t.start and t.duration_minutes:
            return f"{t.start.strftime('%d.%m %H:%M')} · {t.duration_minutes} мин"
        if t.start:
            if t.start.time() == datetime.min.time():
                return "без времени"
            return t.start.strftime("%d.%m %H:%M")
        return "без времени"

    def _priority_marker(self, priority: int) -> ft.Control:
        if priority <= 0:
            return ft.Container(width=12)
        return ft.Container(
            width=12,
            height=12,
            border_radius=6,
            bgcolor=priority_color(priority),
            tooltip=priority_label(priority),
        )

    def _toast(self, text: str):
        self.app.page.snack_bar = ft.SnackBar(ft.Text(text))
        self.app.page.snack_bar.open = True
        self.app.page.update()

    def _close_alert_dialog(self, dlg: ft.AlertDialog | None):
        if not dlg:
            return
        try:
            dlg.open = False
        except Exception:
            pass
        try:
            if dlg in self.app.page.overlay:
                self.app.page.overlay.remove(dlg)
        except Exception:
            pass
        self.app.page.update()
        self.app.cleanup_overlays()
