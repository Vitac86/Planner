# planner/ui/pages/today.py
import re
from datetime import datetime, date, timedelta
import flet as ft

from services.tasks import TaskService


class TodayPage:
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
            on_change=lambda e: self._set_tf_date(self.date_tf, e.data or e.control.value),
            on_dismiss=lambda e: self._set_tf_date(self.date_tf, e.control.value),
        )

        # TimePicker
        self.time_picker_add = ft.TimePicker(
            help_text="Выберите время",
            on_change=lambda e: self._set_tf_time(self.time_tf, e.data or e.control.value),
            on_dismiss=lambda e: self._set_tf_time(self.time_tf, e.control.value),
        )

        for p in (self.date_picker_add, self.time_picker_add):
            if p not in self.app.page.overlay:
                self.app.page.overlay.append(p)

        self.date_btn = ft.IconButton(
            icon=ft.Icons.CALENDAR_MONTH, tooltip="Календарь",
            on_click=lambda e: self.app.page.open(self.date_picker_add)
        )
        self.time_btn = ft.IconButton(
            icon=ft.Icons.SCHEDULE, tooltip="Выбрать время",
            on_click=lambda e: self.app.page.open(self.time_picker_add)
        )

        self.dur_tf = ft.TextField(label="Длительность, мин", value="30", width=160, prefix=ft.Icon(ft.Icons.TIMER))
        self.to_calendar_cb = ft.Checkbox(label="Сразу в календарь", value=True)
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

        self.today_list = ft.ListView(expand=False, spacing=8)
        self.unscheduled_list = ft.ListView(expand=False, spacing=8)

        today_card = ft.Card(
            content=ft.Container(
                content=ft.Column([ft.Text("Сегодня", size=18, weight=ft.FontWeight.W_600), self.today_list], spacing=12),
                padding=16,
            )
        )
        unscheduled_card = ft.Card(
            content=ft.Container(
                content=ft.Column([ft.Text("Без даты", size=18, weight=ft.FontWeight.W_600), self.unscheduled_list], spacing=12),
                padding=16,
            )
        )

        self.view = ft.Container(
            content=ft.Column(
                [ft.Text("Задачи", size=24, weight=ft.FontWeight.BOLD), quick_add, today_card, unscheduled_card],
                spacing=16, expand=True,
            ),
            expand=True, padding=20,
        )

        self.refresh_lists()
    
    # --- вызов из меню/автообновления ---
    def activate_from_menu(self):
        self.load()

    def load(self):
        # алиас для унификации с календарём
        self.refresh_lists()

    # ---------- Утилиты ----------
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
        """
        Возвращает (hour, minute) или None. Допускает секунды.
        """
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
            return datetime(d.year, d.month, d.day)  # без времени
        if not d and t:
            now = datetime.now()
            cand = datetime(now.year, now.month, now.day, t[0], t[1])
            if cand < now - timedelta(minutes=1):
                cand += timedelta(days=1)
            return cand
        return None

    # ---------- CRUD ----------
    def on_add(self, _):
        title = (self.title_tf.value or "").strip()
        if not title:
            return self._toast("Введите название задачи")

        if self.date_tf.value and self._parse_date_tf(self.date_tf.value) is None:
            return self._toast("Неверный формат даты. Пример: 10.10.2025")
        if self.time_tf.value and self._parse_time_tf(self.time_tf.value) is None:
            return self._toast("Неверный формат времени. Пример: 09:30")

        start_dt = self._combine_dt(self.date_tf.value, self.time_tf.value)

        try:
            duration = int(self.dur_tf.value) if self.dur_tf.value else None
        except ValueError:
            return self._toast("Длительность должна быть числом (мин)")

        task = self.svc.add(title=title, start=start_dt, duration_minutes=duration)

        msg = "Задача добавлена"
        if self.to_calendar_cb.value and start_dt and duration:
            try:
                ev = self.app.gcal.create_event_for_task(task, start_dt, duration)
                self.svc.set_event_id(task.id, ev["id"])
                msg = "Задача добавлена и запланирована в Google"
            except Exception as e:
                msg = f"Создана локально, Google недоступен: {e}"

        self.title_tf.value = ""
        self.date_tf.value = ""
        self.time_tf.value = ""
        self.dur_tf.value = "30"
        self.refresh_lists()
        self._toast(msg)

    def on_toggle_done(self, task_id: int, checked: bool):
        self.svc.set_status(task_id, "done" if checked else "todo")
        self.refresh_lists()

    def on_delete(self, task_id: int, gcal_event_id: str | None):
        if gcal_event_id:
            try:
                self.app.gcal.delete_event_by_id(gcal_event_id)
            except Exception:
                pass
        self.svc.delete(task_id)
        self.refresh_lists()
        self._toast("Задача удалена")

    def on_edit_click(self, e: ft.ControlEvent):
        self.open_edit_dialog(int(e.control.data))

    # ---------- Рендер ----------
    def refresh_lists(self):
        from datetime import date as _date
        self.today_list.controls.clear()
        for t in self.svc.list_for_day(_date.today()):
            self.today_list.controls.append(self._row_for_task(t))
        self.unscheduled_list.controls.clear()
        for t in self.svc.list_unscheduled():
            self.unscheduled_list.controls.append(self._row_for_task(t))
        self.app.page.update()

    def _row_for_task(self, t):
        meta = self._human_time(t)
        right = ft.Row(
            controls=[
                ft.Text(meta, italic=True),
                ft.Icon(ft.Icons.LINK) if t.gcal_event_id else ft.Container(),
                ft.IconButton(icon=ft.Icons.EDIT_OUTLINED, tooltip="Редактировать", data=t.id, on_click=self.on_edit_click),
                ft.IconButton(icon=ft.Icons.DELETE_OUTLINE, tooltip="Удалить",
                              on_click=lambda e, tid=t.id, ev=t.gcal_event_id: self.on_delete(tid, ev)),
            ],
            spacing=8, alignment=ft.MainAxisAlignment.END,
        )
        return ft.Row(
            controls=[
                ft.Checkbox(label=t.title, value=(t.status == "done"),
                            on_change=lambda e, tid=t.id: self.on_toggle_done(tid, e.control.value)),
                right,
            ],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
        )

    # ---------- Диалог редактирования ----------
    def open_edit_dialog(self, task_id: int):
        t = self.svc.get(task_id)
        if not t:
            return self._toast("Задача не найдена")

        # --- поля без expand, фикс-ширины только там, где нужно ---
        title_tf = ft.TextField(label="Название", value=t.title)

        date_val = t.start.strftime("%d.%m.%Y") if t.start else ""
        time_val = t.start.strftime("%H:%M") if (t.start and t.start.time() != datetime.min.time()) else ""

        date_tf = ft.TextField(label="Дата", value=date_val, width=160)
        time_tf = ft.TextField(label="Время", value=time_val, width=120)


        # Пикеры (значения забираем из e.data или control.value)
        dp = ft.DatePicker(
            first_date=date(2000, 1, 1),
            last_date=date(2100, 12, 31),
            on_change=lambda e: self._set_tf_date(date_tf, e.data or e.control.value),
            on_dismiss=lambda e: self._set_tf_date(date_tf, e.control.value),
        )
        tp = ft.TimePicker(
            help_text="Выберите время",
            on_change=lambda e: self._set_tf_time(time_tf, e.data or e.control.value),
            on_dismiss=lambda e: self._set_tf_time(time_tf, e.control.value),
        )
        for p in (dp, tp):
            if p not in self.app.page.overlay:
                self.app.page.overlay.append(p)

        date_btn = ft.IconButton(icon=ft.Icons.CALENDAR_MONTH, tooltip="Календарь",
                                on_click=lambda e, _dp=dp: self.app.page.open(_dp))
        time_btn = ft.IconButton(icon=ft.Icons.SCHEDULE, tooltip="Выбрать время",
                                on_click=lambda e, _tp=tp: self.app.page.open(_tp))

        dur_tf = ft.TextField(
            label="Длительность, мин",
            value=(str(t.duration_minutes) if t.duration_minutes else ""),
            width=140
        )
        notes_tf = ft.TextField(
            label="Заметки", value=(t.notes or ""),
            multiline=True, min_lines=3, max_lines=6
        )

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
                duration_minutes=new_dur
            )

            # gcal-sync
            if new_start is not None and new_dur is not None:
                if updated.gcal_event_id:
                    try:
                        self.app.gcal.update_event_for_task(updated.gcal_event_id, updated, new_start, new_dur)
                    except Exception as e:
                        self._toast(f"Google: не удалось обновить: {e}")
                else:
                    try:
                        ev = self.app.gcal.create_event_for_task(updated, new_start, new_dur)
                        self.svc.set_event_id(task_id, ev["id"])
                    except Exception as e:
                        self._toast(f"Google: не удалось создать: {e}")
            else:
                if updated.gcal_event_id:
                    try:
                        self.app.gcal.delete_event_by_id(updated.gcal_event_id)
                    finally:
                        self.svc.set_event_id(task_id, None)

            self.edit_dialog.open = False
            if self.edit_dialog in self.app.page.overlay:
                self.app.page.overlay.remove(self.edit_dialog)
            self.edit_dialog = None
            self.app.page.update()
            self.refresh_lists()
            self._toast("Сохранено")

        def on_cancel(_):
            self.edit_dialog.open = False
            if self.edit_dialog in self.app.page.overlay:
                self.app.page.overlay.remove(self.edit_dialog)
            self.edit_dialog = None
            self.app.page.update()

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
            [date_tf, date_btn, time_tf, time_btn, dur_tf],
            spacing=8,
            vertical_alignment=ft.CrossAxisAlignment.END,
        )
        buttons_row = ft.Row(
            [ft.TextButton("Отмена", on_click=on_cancel),
            ft.FilledButton("Сохранить", icon=ft.Icons.SAVE, on_click=on_save)],
            alignment=ft.MainAxisAlignment.END,
        )

        MAX_W = 480
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
                ),
            ),
        )

        if self.edit_dialog not in self.app.page.overlay:
            self.app.page.overlay.append(self.edit_dialog)
        self.edit_dialog.open = True
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

    def _toast(self, text: str):
        self.app.page.snack_bar = ft.SnackBar(ft.Text(text))
        self.app.page.snack_bar.open = True
        self.app.page.update()
