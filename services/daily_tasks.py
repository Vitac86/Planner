# planner/services/daily_tasks.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, date
from typing import List, Optional
from zoneinfo import ZoneInfo

from sqlmodel import select

from models.daily_task import DailyTask
from storage.db import get_session


@dataclass(frozen=True)
class _LocalContext:
    today: date
    today_str: str
    tz_name: str


class DailyTaskService:
    MAX_TASKS = 200

    def _current_context(self) -> _LocalContext:
        now = datetime.now().astimezone()
        tz = now.tzinfo or ZoneInfo("UTC")
        tz_name = getattr(tz, "key", None) or str(tz) or "UTC"
        today = now.date()
        return _LocalContext(today=today, today_str=today.isoformat(), tz_name=tz_name)

    def _is_today_in_schedule(self, weekdays_mask: int, weekday_index: int) -> bool:
        return bool(weekdays_mask & (1 << weekday_index))

    def _calculate_status(self, task: DailyTask, ctx: _LocalContext) -> str:
        if task.last_done_at == ctx.today_str:
            return "done_today"
        if self._is_today_in_schedule(task.weekdays, ctx.today.weekday()):
            return "active"
        return "inactive"

    def _recalculate_for_task(self, task: DailyTask, ctx: _LocalContext) -> None:
        task.status_today = self._calculate_status(task, ctx)
        task.last_status_calc_at = ctx.today_str
        task.timezone = ctx.tz_name
        task.updated_at = datetime.utcnow().isoformat()

    # ---------- CRUD ----------
    def list_all(self) -> List[DailyTask]:
        with get_session() as s:
            stmt = select(DailyTask)
            return list(s.exec(stmt))

    def create(self, *, title: str, weekdays: int) -> DailyTask:
        cleaned_title = (title or "").strip()
        if not cleaned_title:
            raise ValueError("Название не может быть пустым")
        if len(cleaned_title) > 120:
            raise ValueError("Название слишком длинное")
        if not weekdays:
            raise ValueError("Должен быть выбран хотя бы один день")

        ctx = self._current_context()
        with get_session() as s:
            total = len(s.exec(select(DailyTask)).all())
            if total >= self.MAX_TASKS:
                raise ValueError("Достигнут лимит ежедневных задач (200)")

            task = DailyTask(
                title=cleaned_title,
                weekdays=weekdays,
                timezone=ctx.tz_name,
            )
            self._recalculate_for_task(task, ctx)
            s.add(task)
            s.commit()
            s.refresh(task)
            return task

    def update(
        self,
        task_id: str,
        *,
        title: Optional[str] = None,
        weekdays: Optional[int] = None,
    ) -> Optional[DailyTask]:
        ctx = self._current_context()
        with get_session() as s:
            task = s.get(DailyTask, task_id)
            if not task:
                return None

            if title is not None:
                cleaned_title = title.strip()
                if not cleaned_title:
                    raise ValueError("Название не может быть пустым")
                if len(cleaned_title) > 120:
                    raise ValueError("Название слишком длинное")
                task.title = cleaned_title

            if weekdays is not None:
                if not weekdays:
                    raise ValueError("Должен быть выбран хотя бы один день")
                task.weekdays = weekdays

            self._recalculate_for_task(task, ctx)

            # Если убрали текущий день из расписания — снимаем отметку
            if not self._is_today_in_schedule(task.weekdays, ctx.today.weekday()):
                if task.last_done_at == ctx.today_str:
                    task.last_done_at = None
                task.status_today = "inactive"

            s.add(task)
            s.commit()
            s.refresh(task)
            return task

    def delete(self, task_id: str) -> None:
        with get_session() as s:
            task = s.get(DailyTask, task_id)
            if task:
                s.delete(task)
                s.commit()

    def toggle(self, task_id: str, *, done: bool, client_date: Optional[str] = None) -> Optional[DailyTask]:
        ctx = self._current_context()
        today_str = client_date or ctx.today_str
        with get_session() as s:
            task = s.get(DailyTask, task_id)
            if not task:
                return None

            task.timezone = ctx.tz_name
            if done:
                task.last_done_at = today_str
                task.status_today = "done_today"
                task.last_status_calc_at = today_str
            else:
                if task.last_status_calc_at != ctx.today_str or task.last_done_at != ctx.today_str:
                    raise ValueError("Снять отметку можно только в текущий день")
                task.last_done_at = None
                task.status_today = self._calculate_status(task, ctx)
                task.last_status_calc_at = ctx.today_str

            task.updated_at = datetime.utcnow().isoformat()
            s.add(task)
            s.commit()
            s.refresh(task)
            return task

    # ---------- Ролловер ----------
    def rollover_if_needed(self) -> bool:
        ctx = self._current_context()
        changed = False
        with get_session() as s:
            tasks = list(s.exec(select(DailyTask)))
            for task in tasks:
                if task.last_status_calc_at != ctx.today_str:
                    self._recalculate_for_task(task, ctx)
                    changed = True
                    s.add(task)
            if changed:
                s.commit()
        return changed


__all__ = ["DailyTaskService"]
