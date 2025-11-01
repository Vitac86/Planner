# planner/services/tasks.py
from __future__ import annotations

import json
import re
from datetime import datetime, date, timedelta
from typing import Iterable, List, Optional

from sqlmodel import select
from sqlalchemy import and_, or_, case

from storage.db import get_session
from models.task import Task
from core.priorities import normalize_priority
from datetime_utils import ensure_utc, utc_now


class TaskService:
    _listeners = {
        "after_create": set(),
        "after_update": set(),
        "after_delete": set(),
    }

    @classmethod
    def subscribe(cls, event: str, callback):
        if event not in cls._listeners:
            raise ValueError(f"Unsupported event: {event}")
        cls._listeners[event].add(callback)

    @classmethod
    def unsubscribe(cls, event: str, callback):
        if event not in cls._listeners:
            return
        cls._listeners[event].discard(callback)

    @classmethod
    def _emit(cls, event: str, task_id: int):
        listeners = list(cls._listeners.get(event, []))
        for listener in listeners:
            try:
                listener(task_id)
            except Exception:
                pass

    def add(
        self,
        title: str,
        notes: Optional[str] = None,
        start: Optional[datetime] = None,
        duration_minutes: Optional[int] = None,
        priority: int = 0,
        *,
        emit: bool = True,
    ) -> Task:
        with get_session() as s:
            t = Task(
                title=title.strip(),
                notes=notes or None,
                start=ensure_utc(start),
                duration_minutes=duration_minutes or None,
                priority=normalize_priority(priority),
            )
            s.add(t)
            s.commit()
            s.refresh(t)
            if emit:
                try:
                    self._emit("after_create", t.id)
                except Exception:
                    pass
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
        emit: bool = True,
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
                t.start = ensure_utc(start)
            if duration_minutes is not None or duration_minutes is None:
                t.duration_minutes = duration_minutes
            if priority is not None:
                t.priority = normalize_priority(priority)
            t.updated_at = utc_now()
            s.add(t)
            s.commit()
            s.refresh(t)
            if emit:
                try:
                    self._emit("after_update", t.id)
                except Exception:
                    pass
            return t

    def set_event_id(self, task_id: int, event_id: Optional[str]):
        with get_session() as s:
            t = s.get(Task, task_id)
            if t:
                t.gcal_event_id = event_id
                t.updated_at = utc_now()
                s.add(t)
                s.commit()

    def set_status(self, task_id: int, status: str):
        with get_session() as s:
            t = s.get(Task, task_id)
            if t:
                t.status = status
                t.updated_at = utc_now()
                s.add(t)
                s.commit()

    def delete(self, task_id: int, *, emit: bool = True):
        with get_session() as s:
            t = s.get(Task, task_id)
            if t:
                if emit:
                    try:
                        self._emit("after_delete", task_id)
                    except Exception:
                        pass
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
            status_order = case(
                (Task.status == "todo", 0),
                (Task.status == "doing", 1),
                (Task.status == None, 2),  # noqa: E711
                (Task.status == "", 2),
                else_=3,
            )
            stmt = (
                select(Task)
                .where(and_(Task.status != "done", Task.start == None))  # noqa: E711
                .order_by(Task.priority.desc(), status_order, Task.created_at.desc())
            )
            return list(s.exec(stmt))

    def list_unscheduled_updated_since(self, since: Optional[datetime]) -> Iterable[Task]:
        with get_session() as s:
            stmt = select(Task).where(Task.start == None)  # noqa: E711
            if since is not None:
                stmt = stmt.where(Task.updated_at > since)
            stmt = stmt.where(Task.status != "done").order_by(Task.updated_at.desc())
            return list(s.exec(stmt))

    def get_by_event_id(self, gcal_event_id: str | None):
        if not gcal_event_id:
            return None
        with get_session() as s:
            stmt = select(Task).where(Task.gcal_event_id == gcal_event_id)
            return s.exec(stmt).first()

    def get_by_gtasks_id(self, gtasks_id: str | None):
        if not gtasks_id:
            return None
        with get_session() as s:
            stmt = select(Task).where(Task.gtasks_id == gtasks_id)
            return s.exec(stmt).first()

    def create_from_sync(
        self,
        *,
        title: str,
        notes: Optional[str] = None,
        start: Optional[datetime] = None,
        duration_minutes: Optional[int] = None,
        priority: int = 0,
        status: Optional[str] = None,
        gcal_event_id: Optional[str] = None,
        gcal_etag: Optional[str] = None,
        gcal_updated: Optional[datetime] = None,
        gtasks_id: Optional[str] = None,
        gtasks_updated: Optional[datetime] = None,
    ) -> Task:
        with get_session() as s:
            task = Task(
                title=title.strip() or "Задача",
                notes=notes or None,
                start=ensure_utc(start),
                duration_minutes=duration_minutes,
                priority=normalize_priority(priority),
                status=status or "todo",
                gcal_event_id=gcal_event_id,
                gcal_etag=gcal_etag,
                gcal_updated=ensure_utc(gcal_updated),
                gtasks_id=gtasks_id,
                gtasks_updated=ensure_utc(gtasks_updated),
            )
            if task.start is None:
                task.duration_minutes = None
            s.add(task)
            s.commit()
            s.refresh(task)
            return task

    def update_from_sync(
        self,
        task_id: int,
        *,
        updated_at: Optional[datetime] = None,
        **fields,
    ) -> Optional[Task]:
        with get_session() as s:
            task = s.get(Task, task_id)
            if not task:
                return None
            for key, value in fields.items():
                if hasattr(task, key):
                    if isinstance(value, datetime):
                        setattr(task, key, ensure_utc(value))
                    else:
                        setattr(task, key, value)
            if updated_at is not None:
                task.updated_at = ensure_utc(updated_at)
            else:
                task.updated_at = utc_now()
            s.add(task)
            s.commit()
            s.refresh(task)
            return task

    def delete_from_sync(self, task_id: int) -> None:
        self.delete(task_id, emit=False)

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

    # ---------- Metadata helpers ----------
    def clean_notes_metadata(self) -> int:
        """Strip JSON metadata blocks from notes. Returns number of tasks changed."""

        changed = 0
        with get_session() as s:
            stmt = select(Task).where(Task.notes != None)  # noqa: E711
            tasks = list(s.exec(stmt))
            for task in tasks:
                original = task.notes or ""
                cleaned = self._strip_metadata(original)
                if cleaned != original:
                    task.notes = cleaned or None
                    task.updated_at = datetime.utcnow()
                    s.add(task)
                    changed += 1
            if changed:
                s.commit()
        return changed

    def _strip_metadata(self, notes: str) -> str:
        candidate = (notes or "").strip()
        if not candidate:
            return ""

        # Fast path: JSON on a single line
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                user_note = parsed.get("note") or parsed.get("text") or parsed.get("user_notes")
                if isinstance(user_note, str):
                    return user_note.strip()
                return ""
        except Exception:
            pass

        # Remove leading JSON block if present on the first line
        lines = notes.splitlines()
        if lines:
            first = lines[0].strip()
            if first.startswith("{") and first.endswith("}"):
                try:
                    parsed = json.loads(first)
                    rest = "\n".join(lines[1:]).strip()
                    user_note = parsed.get("note") or parsed.get("text") or parsed.get("user_notes")
                    if isinstance(user_note, str):
                        return user_note.strip()
                    return rest
                except Exception:
                    return "\n".join(lines[1:]).strip()
        return notes

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
