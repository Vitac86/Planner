# services/sync.py
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional
from datetime import datetime, timezone, timedelta

from datetime_utils import parse_rfc3339, to_rfc3339

from services.tasks import TaskService
from core.settings import GOOGLE_SYNC

DELETE_ON_GOOGLE_CANCEL = GOOGLE_SYNC.delete_on_google_cancel  # True — удалять задачу; False — только снимать расписание (как сейчас)
_MARKER_RE = re.compile(r"planner_task_id\s*:\s*(\d+)", re.I)


class JsonTokenStore:
    """Простейшее хранение syncToken в файле (чтобы получать только изменения)."""
    def __init__(self, path: str | Path = GOOGLE_SYNC.sync_token_path):
        self.path = Path(path)

    def get_sync_token(self) -> Optional[str]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data.get("syncToken")
        except Exception:
            return None

    def set_sync_token(self, token: str):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"syncToken": token}), encoding="utf-8")


def _parse_marker(description: str | None) -> Optional[int]:
    if not description:
        return None
    m = _MARKER_RE.search(description)
    if not m:
        return None
    try:
        return int(m.group(1))
    except Exception:
        return None


def _strip_marker(description: str | None) -> str:
    if not description:
        return ""
    lines = [ln for ln in description.splitlines() if not _MARKER_RE.search(ln)]
    return "\n".join(lines).strip()


def _parse_g_datetime(obj: dict | None) -> Optional[datetime]:
    """start/end из Google: либо {'dateTime': '...Z'}, либо {'date': 'YYYY-MM-DD'}."""
    if not obj:
        return None
    if "dateTime" in obj and obj["dateTime"]:
        return parse_rfc3339(str(obj["dateTime"]))
    if "date" in obj and obj["date"]:
        # all-day -> в полночь локального дня (без tz; дальше ты сам решаешь как отображать)
        try:
            d = datetime.strptime(str(obj["date"]).strip(), "%Y-%m-%d")
            return d
        except Exception:
            # некоторые клиенты присылают 'YYYY-MM-DD' -> отработает
            try:
                y, m, dd = str(obj["date"]).split("-")
                return datetime(int(y), int(m), int(dd))
            except Exception:
                return None
    return None


class GoogleSync:
    """
    Двусторонняя связка:
      - Если событие в Google помечено marker'ом planner_task_id:NN — обновляем соответствующую локальную задачу.
      - Если marker'а нет, но мы видим event_id == Task.gcal_event_id — обновляем эту задачу.
      - Если marker'а нет и event_id незнаком — создаём новую локальную задачу и обратно проставляем marker в событие.
      - Если событие в Google отменено (status=cancelled) — снимаем расписание у соответствующей задачи (не удаляем).
    """
    def __init__(self, gcal_service, calendar_id: str, token_store: JsonTokenStore | None = None):
        self.service = gcal_service
        self.calendar_id = calendar_id
        self.store = token_store or JsonTokenStore()
        self.svc = TaskService()

    def pull(self) -> bool:
        """Возвращает True, если что-то изменилось в локальной базе."""
        if not self.service or not self.calendar_id:
            return False

        changed = False
        token = self.store.get_sync_token()

        params = dict(
            calendarId=self.calendar_id,
            singleEvents=True,
            showDeleted=True,
            maxResults=250,
        )
        if token:
            # инкрементальные изменения
            params["syncToken"] = token
        else:
            # первичная выгрузка за последние 6 мес.
            params["timeMin"] = to_rfc3339(datetime.now(timezone.utc) - timedelta(days=180))

        while True:
            resp = self.service.events().list(**params).execute()
            items = resp.get("items", [])

            for ev in items:
                ev_id = ev.get("id")
                status = ev.get("status")
                summary = ev.get("summary") or "Без названия"
                description = ev.get("description") or ""

                # cancelled -> снять расписание у связанной задачи (если есть)
                if status == "cancelled":
                    tid = _parse_marker(description)
                    target_task = self.svc.get(tid) if tid else self.svc.get_by_event_id(ev_id)
                    if target_task:
                        if DELETE_ON_GOOGLE_CANCEL:
                            self.svc.delete(target_task.id)
                        else:
                            self.svc.unschedule(target_task.id)
                        changed = True
                    continue

                # обычное событие
                dt_start = _parse_g_datetime(ev.get("start"))
                dt_end   = _parse_g_datetime(ev.get("end"))
                duration = None
                if dt_start and dt_end and dt_end > dt_start:
                    duration = int((dt_end - dt_start).total_seconds() // 60)

                # ищем задачу
                task = None
                tid = _parse_marker(description)
                if tid:
                    task = self.svc.get(tid)
                if task is None:
                    task = self.svc.get_by_event_id(ev_id)

                # текст заметок без служебного маркера
                notes = _strip_marker(description)

                if task:
                    # обновляем локально
                    self.svc.update(task.id, title=summary, notes=notes, start=dt_start, duration_minutes=duration)
                    if task.gcal_event_id != ev_id:
                        self.svc.set_event_id(task.id, ev_id)
                    changed = True

                    # убедимся, что в событии есть marker
                    if tid != task.id:
                        # аккуратно дописываем marker в описание, не трогая время
                        try:
                            new_desc = (notes + ("\n" if notes else "") + f"planner_task_id:{task.id}").strip()
                            self.service.events().patch(
                                calendarId=self.calendar_id,
                                eventId=ev_id,
                                body={"description": new_desc},
                            ).execute()
                        except Exception:
                            pass
                else:
                    # это новое событие «со стороны Google» — создаём задачу
                    new_task = self.svc.add(title=summary, start=dt_start, duration_minutes=duration, notes=notes)
                    self.svc.set_event_id(new_task.id, ev_id)
                    changed = True

                    # и проставим marker обратно в событии
                    try:
                        new_desc = (notes + ("\n" if notes else "") + f"planner_task_id:{new_task.id}").strip()
                        self.service.events().patch(
                            calendarId=self.calendar_id,
                            eventId=ev_id,
                            body={"description": new_desc},
                        ).execute()
                    except Exception:
                        pass

            # пагинация + syncToken
            if "nextPageToken" in resp:
                params["pageToken"] = resp["nextPageToken"]
                # не нужна timeMin/syncToken при пагинации
                params.pop("timeMin", None)
                params.pop("syncToken", None)
                continue

            if "nextSyncToken" in resp:
                self.store.set_sync_token(resp["nextSyncToken"])
            break

        return changed
