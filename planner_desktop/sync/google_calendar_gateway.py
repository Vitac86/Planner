"""Реальный шлюз Google Calendar нового десктопа (контракт CalendarGateway).

Единственное место, где события CalendarEvent конвертируются в тела
Calendar API v3 и обратно. Сам модуль НЕ импортирует Google-клиенты и не
делает сети при импорте: готовый сервис Calendar API (`service`)
ИНЪЕЦИРУЕТСЯ снаружи (см. sync/google_auth.py — там OAuth и discovery),
а в тестах вместо него подставляется фейковый объект той же формы
(`service.events().insert(...).execute()`).

Правила формы (те же, что в calendar_contract.py и calendar_mapper.py):

1. Событие со временем -> {"start": {"dateTime", "timeZone"},
   "end": {"dateTime", "timeZone"}}; dateTime сериализуется в UTC.
2. All-day -> {"start": {"date"}, "end": {"date"}}, конец ЭКСКЛЮЗИВНЫЙ.
3. Формы не смешиваются. При PATCH смена/подтверждение формы всегда
   сопровождается явным null-ом противоположного поля
   ({"date": ..., "dateTime": None}) — урок исторической петли HTTP 400
   старого приложения: остаток другой формы делает тело неоднозначным.
4. Экземпляры повторяющихся событий по start/end вслепую не патчатся —
   это гарантирует маппер (task_to_event_patch опускает start/end);
   если Google всё же ответит 400, ошибка станет terminal (dead-letter),
   а не бесконечным ретраем.

Pull (list_changes):

- инкрементальный обход через syncToken (курсор движка) с пагинацией
  по nextPageToken; showDeleted=True, чтобы приходили отменённые события
  (в т.ч. удалённые с телефона);
- singleEvents=False: повторяющиеся серии приходят «мастером», а их
  изменённые/отменённые экземпляры — отдельными событиями с
  recurringEventId + originalStartTime (наша модель безопасна именно
  для этого случая); безграничного разворота бесконечных серий нет;
- истёкший syncToken (HTTP 410) обрабатывается детерминированно:
  один полный пересбор без токена в том же вызове, возвращается свежий
  nextSyncToken.

Классификация ошибок — без импорта googleapiclient (duck-typing по
`exc.resp.status` / `exc.status_code`): 403(rate)/408/429/5xx и сетевые
исключения -> RetryableGatewayError (бэкофф, потом dead-letter);
остальные HTTP -> TerminalGatewayError (сразу dead-letter).
"""
from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Dict, List, Mapping, Optional, Tuple

from planner_desktop.sync.sync_types import (
    EVENT_STATUS_CONFIRMED,
    CalendarEvent,
    RemoteChangeBatch,
    RetryableGatewayError,
    TerminalGatewayError,
)

logger = logging.getLogger(__name__)

DEFAULT_CALENDAR_ID = "primary"
LIST_PAGE_SIZE = 250

# 403 у Google бывает и квотой (retryable), и запретом (terminal);
# различаем по тексту причины — маркеры из документации Calendar API.
_RETRYABLE_403_MARKERS = ("ratelimitexceeded", "usagelimits", "quotaexceeded")
_RETRYABLE_STATUSES = {408, 429, 500, 502, 503, 504}


class _SyncTokenExpired(Exception):
    """Внутренний маркер: syncToken протух (HTTP 410 на list)."""


# ---- классификация ошибок (без импорта googleapiclient) ---------------------------

def _http_status(exc: BaseException) -> Optional[int]:
    """HTTP-статус из исключения любого Google-клиента (duck-typing)."""
    resp = getattr(exc, "resp", None)
    if resp is not None:
        status = getattr(resp, "status", None)
        if status is not None:
            try:
                return int(status)
            except (TypeError, ValueError):
                return None
    status = getattr(exc, "status_code", None)
    if status is not None:
        try:
            return int(status)
        except (TypeError, ValueError):
            return None
    return None


def _classify(exc: BaseException, context: str) -> Exception:
    """HTTP/сетевая ошибка -> Retryable/Terminal ошибка шлюза."""
    status = _http_status(exc)
    detail = f"{context}: {exc}"
    if status is None:
        # Сеть/таймаут/DNS — временная беда, операцию можно повторить.
        return RetryableGatewayError(detail)
    if status in _RETRYABLE_STATUSES:
        return RetryableGatewayError(f"HTTP {status}. {detail}")
    if status == 403 and any(m in str(exc).replace(" ", "").lower()
                             for m in _RETRYABLE_403_MARKERS):
        return RetryableGatewayError(f"HTTP 403 (квота). {detail}")
    return TerminalGatewayError(f"HTTP {status}. {detail}")


# ---- сериализация CalendarEvent -> тело Calendar API -------------------------------

def _to_utc_rfc3339(value: datetime) -> str:
    """Локальный naive datetime -> RFC3339 в UTC (форма dateTime)."""
    if value.tzinfo is None:
        value = value.astimezone()  # naive трактуем как локальное время
    return value.astimezone(timezone.utc).isoformat()


def times_to_body(
    start: Any, end: Any, is_all_day: bool, *, explicit_null: bool = False
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Пара тел start/end. explicit_null добавляет null противоположной
    формы — обязательно для PATCH, чтобы формы не смешивались."""
    if is_all_day:
        start_day = start if isinstance(start, date) and not isinstance(start, datetime) \
            else start.date()
        end_day = end if isinstance(end, date) and not isinstance(end, datetime) \
            else end.date()
        if end_day <= start_day:
            end_day = start_day + timedelta(days=1)  # конец эксклюзивный
        start_body: Dict[str, Any] = {"date": start_day.isoformat()}
        end_body: Dict[str, Any] = {"date": end_day.isoformat()}
        if explicit_null:
            start_body["dateTime"] = None
            end_body["dateTime"] = None
        return start_body, end_body

    start_body = {"dateTime": _to_utc_rfc3339(start), "timeZone": "UTC"}
    end_body = {"dateTime": _to_utc_rfc3339(end), "timeZone": "UTC"}
    if explicit_null:
        start_body["date"] = None
        end_body["date"] = None
    return start_body, end_body


def event_to_insert_body(event: CalendarEvent) -> Dict[str, Any]:
    """CalendarEvent -> тело events.insert."""
    body: Dict[str, Any] = {
        "summary": event.summary or "",
        "description": event.description or "",
    }
    body["start"], body["end"] = times_to_body(event.start, event.end, event.is_all_day)
    return body


def patch_to_body(patch: Mapping[str, Any]) -> Dict[str, Any]:
    """Частичный патч в именах полей CalendarEvent -> тело events.patch.

    start/end кладутся только если пришли в патче (маппер сознательно
    опускает их для экземпляров повторяющихся событий); при их наличии
    противоположная форма явно null-ится.
    """
    body: Dict[str, Any] = {}
    if "summary" in patch:
        body["summary"] = patch["summary"] or ""
    if "description" in patch:
        body["description"] = patch["description"] or ""
    if "start" in patch or "end" in patch:
        body["start"], body["end"] = times_to_body(
            patch["start"], patch["end"], bool(patch.get("is_all_day")),
            explicit_null=True,
        )
    return body


# ---- парсинг тела Calendar API -> CalendarEvent -------------------------------------

def _parse_rfc3339(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _parse_timed(value: str) -> datetime:
    """dateTime -> локальный naive datetime (так задачи хранятся в БД)."""
    return _parse_rfc3339(value).astimezone().replace(tzinfo=None)


def _parse_updated(value: Optional[str]) -> Optional[datetime]:
    """updated -> aware UTC (сравнивается с Task.updated_at = utc_now())."""
    if not value:
        return None
    return _parse_rfc3339(value).astimezone(timezone.utc)


def _parse_original_start(item: Mapping[str, Any]) -> Optional[datetime]:
    original = item.get("originalStartTime") or {}
    if original.get("dateTime"):
        return _parse_rfc3339(original["dateTime"])
    if original.get("date"):
        return datetime.combine(date.fromisoformat(original["date"]), time.min)
    return None


def payload_to_event(item: Mapping[str, Any]) -> CalendarEvent:
    """Событие Calendar API v3 -> CalendarEvent.

    Отменённые события приходят усечёнными (часто только id/status) —
    парсим их без start/end. У all-day события end остаётся ЭКСКЛЮЗИВНОЙ
    датой (семантика Google сохраняется как есть).
    """
    start_raw = item.get("start") or {}
    end_raw = item.get("end") or {}
    is_all_day = "date" in start_raw

    start: Any = None
    end: Any = None
    if is_all_day:
        start = date.fromisoformat(start_raw["date"])
        end = (date.fromisoformat(end_raw["date"])
               if end_raw.get("date") else start + timedelta(days=1))
    elif start_raw.get("dateTime"):
        start = _parse_timed(start_raw["dateTime"])
        end = _parse_timed(end_raw["dateTime"]) if end_raw.get("dateTime") else None

    return CalendarEvent(
        id=item.get("id"),
        etag=item.get("etag"),
        summary=item.get("summary", "") or "",
        description=item.get("description", "") or "",
        start=start,
        end=end,
        is_all_day=is_all_day,
        status=item.get("status", EVENT_STATUS_CONFIRMED) or EVENT_STATUS_CONFIRMED,
        updated_at=_parse_updated(item.get("updated")),
        recurring_event_id=item.get("recurringEventId"),
        original_start=_parse_original_start(item),
    )


# ---- сам шлюз -----------------------------------------------------------------------

class GoogleCalendarGateway:
    """CalendarGateway поверх инъецированного сервиса Calendar API v3.

    ``service`` — то, что возвращает googleapiclient discovery.build()
    (или фейк той же формы в тестах). Конструктор сети не делает; каждый
    метод — ровно те вызовы, которые описаны в контракте.
    """

    def __init__(self, service: Any, calendar_id: str = DEFAULT_CALENDAR_ID) -> None:
        self._service = service
        self._calendar_id = calendar_id

    # ---- push -------------------------------------------------------------------

    def insert_event(self, event: CalendarEvent) -> CalendarEvent:
        body = event_to_insert_body(event)
        try:
            created = self._service.events().insert(
                calendarId=self._calendar_id, body=body,
            ).execute()
        except Exception as exc:  # классифицируем в ошибки шлюза
            raise _classify(exc, "insert_event") from exc
        return payload_to_event(created)

    def patch_event(self, event_id: str, patch: Mapping[str, Any]) -> CalendarEvent:
        body = patch_to_body(patch)
        try:
            updated = self._service.events().patch(
                calendarId=self._calendar_id, eventId=event_id, body=body,
            ).execute()
        except Exception as exc:
            raise _classify(exc, f"patch_event {event_id}") from exc
        return payload_to_event(updated)

    def delete_event(self, event_id: str) -> None:
        try:
            self._service.events().delete(
                calendarId=self._calendar_id, eventId=event_id,
            ).execute()
        except Exception as exc:
            if _http_status(exc) in (404, 410):
                return  # уже удалено/отменено — идемпотентный успех
            raise _classify(exc, f"delete_event {event_id}") from exc

    # ---- pull -------------------------------------------------------------------

    def list_changes(self, cursor: Optional[str]) -> RemoteChangeBatch:
        """Изменения после курсора (nextSyncToken) + новый курсор.

        HTTP 410 (протухший syncToken) детерминированно превращается в
        один полный пересбор без токена в этом же вызове.
        """
        try:
            return self._list_all_pages(cursor)
        except _SyncTokenExpired:
            logger.info("syncToken протух (410) — полный пересбор календаря")
            return self._list_all_pages(None)

    def _list_all_pages(self, sync_token: Optional[str]) -> RemoteChangeBatch:
        events: List[CalendarEvent] = []
        page_token: Optional[str] = None
        next_sync_token = ""

        while True:
            params: Dict[str, Any] = {
                "calendarId": self._calendar_id,
                "maxResults": LIST_PAGE_SIZE,
                "showDeleted": True,     # отмены (в т.ч. с телефона) обязаны приходить
                "singleEvents": False,   # серии мастером, экземпляры — отдельно
            }
            if sync_token:
                params["syncToken"] = sync_token
            if page_token:
                params["pageToken"] = page_token

            try:
                page = self._service.events().list(**params).execute()
            except Exception as exc:
                if sync_token and _http_status(exc) == 410:
                    raise _SyncTokenExpired() from exc
                raise _classify(exc, "list_changes") from exc

            for item in page.get("items", []):
                events.append(payload_to_event(item))

            page_token = page.get("nextPageToken")
            if not page_token:
                next_sync_token = page.get("nextSyncToken", "") or ""
                break

        # Курсор без nextSyncToken (аномалия API) — сохраняем старый,
        # чтобы не потерять инкрементальность.
        return RemoteChangeBatch(
            events=events,
            next_cursor=next_sync_token or (sync_token or ""),
        )


__all__ = [
    "GoogleCalendarGateway",
    "DEFAULT_CALENDAR_ID",
    "event_to_insert_body",
    "patch_to_body",
    "payload_to_event",
    "times_to_body",
]
