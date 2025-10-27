# planner/services/tasks.py
from __future__ import annotations

import re
from datetime import datetime, date, timedelta
from typing import Iterable, List, Optional

from sqlmodel import select
from sqlalchemy import and_, or_, case

from storage.db import get_session
from models.task import Task
from core.priorities import normalize_priority


class TaskService:
    def add(
        self,
        title: str,
        notes: Optional[str] = None,
        start: Optional[datetime] = None,
        duration_minutes: Optional[int] = None,
        priority: int = 0,
    ) -> Task:
        with get_session() as s:
            t = Task(
                title=title.strip(),
                notes=notes or None,
                start=start,
                duration_minutes=duration_minutes or None,
                priority=normalize_priority(priority),
            )
            s.add(t)
            s.commit()
            s.refresh(t)
            return t

    def get(self, task_id: int) -> Optional[Task]:
        with get_session() as s:
            return s.get(Task, task_id)

    def update(
        self,
        task_id: int,
        *,
        title: Optional[str] = None,
        notes: Optional[str] = None,
        start: Optional[datetime] = None,
        duration_minutes: Optional[int] = None,
        priority: Optional[int] = None,
    ) -> Optional[Task]:
        with get_session() as s:
            t = s.get(Task, task_id)
            if not t:
                return None
            if title is not None:
                t.title = title.strip()
            if notes is not None:
                t.notes = notes or None
            if start is not None or start is None:
                t.start = start
            if duration_minutes is not None or duration_minutes is None:
                t.duration_minutes = duration_minutes
            if priority is not None:
                t.priority = normalize_priority(priority)
            t.updated_at = datetime.utcnow()
            s.add(t)
            s.commit()
            s.refresh(t)
            return t

    def set_event_id(self, task_id: int, event_id: Optional[str]):
        with get_session() as s:
            t = s.get(Task, task_id)
            if t:
                t.gcal_event_id = event_id
                t.updated_at = datetime.utcnow()
                s.add(t)
                s.commit()

    def set_status(self, task_id: int, status: str):
        with get_session() as s:
            t = s.get(Task, task_id)
            if t:
                t.status = status
                t.updated_at = datetime.utcnow()
                s.add(t)
                s.commit()

    def delete(self, task_id: int):
        with get_session() as s:
            t = s.get(Task, task_id)
            if t:
                s.delete(t)
                s.commit()

    def list_for_day(self, d: date) -> Iterable[Task]:
        start = datetime(d.year, d.month, d.day, 0, 0, 0)
        end = start + timedelta(days=1)
        with get_session() as s:
            stmt = (
                select(Task)
                .where(and_(Task.status != "done", Task.start >= start, Task.start < end))
                .order_by(Task.start.asc(), Task.priority.desc(), Task.created_at.desc())
            )
            return list(s.exec(stmt))

    def list_unscheduled(self) -> Iterable[Task]:
        with get_session() as s:
            stmt = (
                select(Task)
                .where(and_(Task.status != "done", Task.start == None))  # noqa: E711
                .order_by(Task.priority.desc(), Task.created_at.desc())
            )
            return list(s.exec(stmt))

    def get_by_event_id(self, gcal_event_id: str | None):
        if not gcal_event_id:
            return None
        with get_session() as s:
            stmt = select(Task).where(Task.gcal_event_id == gcal_event_id)
            return s.exec(stmt).first()

    def unschedule(self, task_id: int):
        """Снять расписание и отвязать от Google-события (но задачу не удалять)."""
        with get_session() as s:
            t = s.get(Task, task_id)
            if not t:
                return None
            t.start = None
            t.duration_minutes = None
            t.gcal_event_id = None
            s.add(t)
            s.commit()
            s.refresh(t)
            return t

    # ---------- History & search ----------
    def search_history(
        self,
        *,
        query: str = "",
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        status: Optional[str] = None,
        priority: Optional[int] = None,
    ) -> List[Task]:
        """Return tasks filtered by the provided parameters.

        The text search is performed in Python so we can support
        transliteration-aware matching (Cyrillic/Latin/translit).
        """

        with get_session() as s:
            stmt = select(Task)

            if start_date:
                start_dt = datetime(start_date.year, start_date.month, start_date.day, 0, 0, 0)
                stmt = stmt.where(
                    or_(
                        and_(Task.start != None, Task.start >= start_dt),  # noqa: E711
                        and_(Task.start == None, Task.created_at >= start_dt),  # noqa: E711
                    )
                )

            if end_date:
                end_dt = datetime(end_date.year, end_date.month, end_date.day, 23, 59, 59)
                stmt = stmt.where(
                    or_(
                        and_(Task.start != None, Task.start <= end_dt),  # noqa: E711
                        and_(Task.start == None, Task.created_at <= end_dt),  # noqa: E711
                    )
                )

            if status and status not in ("all", ""):
                stmt = stmt.where(Task.status == status)

            if priority is not None and priority >= 0:
                stmt = stmt.where(Task.priority == normalize_priority(priority))

            stmt = stmt.order_by(
                case((Task.start == None, 1), else_=0),  # noqa: E711
                Task.start.desc(),
                Task.updated_at.desc(),
            )

            tasks = list(s.exec(stmt))

        if not query or not query.strip():
            return tasks

        return [t for t in tasks if self._match_query(query, f"{t.title} {t.notes or ''}")]

    # --- text helpers -------------------------------------------------
    _RE_SPACES = re.compile(r"\s+")
    _RE_ALLOWED = re.compile(r"[^0-9a-zа-яё\s]")

    _RU_TO_LAT = {
        "а": "a",
        "б": "b",
        "в": "v",
        "г": "g",
        "д": "d",
        "е": "e",
        "ё": "yo",
        "ж": "zh",
        "з": "z",
        "и": "i",
        "й": "y",
        "к": "k",
        "л": "l",
        "м": "m",
        "н": "n",
        "о": "o",
        "п": "p",
        "р": "r",
        "с": "s",
        "т": "t",
        "у": "u",
        "ф": "f",
        "х": "kh",
        "ц": "ts",
        "ч": "ch",
        "ш": "sh",
        "щ": "shch",
        "ъ": "",
        "ы": "y",
        "ь": "",
        "э": "e",
        "ю": "yu",
        "я": "ya",
    }

    _LAT_MULTI = [
        ("shch", "щ"),
        ("zh", "ж"),
        ("kh", "х"),
        ("ts", "ц"),
        ("ch", "ч"),
        ("sh", "ш"),
        ("yo", "ё"),
        ("yu", "ю"),
        ("ya", "я"),
        ("ye", "е"),
    ]

    _LAT_SINGLE = {
        "a": "а",
        "b": "б",
        "c": "к",
        "d": "д",
        "e": "е",
        "f": "ф",
        "g": "г",
        "h": "х",
        "i": "и",
        "j": "ж",
        "k": "к",
        "l": "л",
        "m": "м",
        "n": "н",
        "o": "о",
        "p": "п",
        "q": "к",
        "r": "р",
        "s": "с",
        "t": "т",
        "u": "у",
        "v": "в",
        "w": "в",
        "x": "кс",
        "y": "й",
        "z": "з",
    }

    def _normalize_base(self, text: str) -> str:
        cleaned = self._RE_ALLOWED.sub(" ", (text or "").lower())
        cleaned = cleaned.replace("ё", "е")
        return self._RE_SPACES.sub(" ", cleaned).strip()

    def _variants(self, text: str) -> List[str]:
        base = self._normalize_base(text)
        if not base:
            return [""]
        ru_to_lat = self._normalize_base(self._translit_ru_to_lat(base))
        lat_to_ru = self._normalize_base(self._translit_lat_to_ru(base))
        variants = {base}
        if ru_to_lat:
            variants.add(ru_to_lat)
        if lat_to_ru:
            variants.add(lat_to_ru)
        return list(variants)

    def _match_query(self, query: str, haystack: str) -> bool:
        tokens = [tok for tok in self._RE_SPACES.split(self._normalize_base(query)) if tok]
        if not tokens:
            return True
        haystack_variants = self._variants(haystack)
        for token in tokens:
            token_variants = self._variants(token)
            if not any(tv and tv in hv for hv in haystack_variants for tv in token_variants):
                return False
        return True

    def _translit_ru_to_lat(self, text: str) -> str:
        return "".join(self._RU_TO_LAT.get(ch, ch) for ch in text)

    def _translit_lat_to_ru(self, text: str) -> str:
        res: List[str] = []
        i = 0
        while i < len(text):
            matched = False
            for seq, repl in self._LAT_MULTI:
                if text.startswith(seq, i):
                    res.append(repl)
                    i += len(seq)
                    matched = True
                    break
            if matched:
                continue
            ch = text[i]
            res.append(self._LAT_SINGLE.get(ch, ch))
            i += 1
        return "".join(res)
