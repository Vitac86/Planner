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
from zoneinfo import ZoneInfo

from planner_desktop.domain.google_recurrence import (
    recurrence_to_google_lines as _pure_recurrence_to_google_lines,
)
from planner_desktop.domain.recurrence import RecurrenceRule, SeriesSchedule
from planner_desktop.domain.series_calendar_link import (
    PLANNER_PAYLOAD_HASH_PROPERTY,
    PLANNER_SERIES_UID_PROPERTY,
)
from planner_desktop.sync.calendar_series_mapper import (
    master_event_to_owned_payload,
    master_payload_hash,
)
from planner_desktop.sync.sync_types import (
    EVENT_STATUS_CONFIRMED,
    CalendarEvent,
    RemoteChangeBatch,
    RemoteMasterConflictError,
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


def recurrence_to_google_lines(
    rule: RecurrenceRule,
    *,
    schedule: Optional[SeriesSchedule] = None,
    extra_lines: Tuple[str, ...] = (),
) -> Tuple[str, ...]:
    """Pure future-write helper. Production insert/patch does not call it in B1."""
    return _pure_recurrence_to_google_lines(
        rule, schedule=schedule, extra_lines=extra_lines
    )


def recurring_master_to_insert_body(event: CalendarEvent) -> Dict[str, Any]:
    """Build a future recurring-master insert body without performing IO.

    Deliberately separate from :func:`event_to_insert_body`: real B1 inserts
    remain ordinary-only even when a caller constructs recurrence metadata.
    """
    if not event.is_recurring_master:
        raise ValueError("CalendarEvent is not a recurring master.")
    return master_event_to_owned_payload(event)


def recurring_master_patch_to_body(event: CalendarEvent) -> Dict[str, Any]:
    """Pure future master patch body; unused by real patch_event in B1."""
    if not event.is_recurring_master:
        raise ValueError("CalendarEvent is not a recurring master.")
    return master_event_to_owned_payload(event)


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
    recurrence_start: Any = None
    if is_all_day:
        start = date.fromisoformat(start_raw["date"])
        recurrence_start = start
        end = (date.fromisoformat(end_raw["date"])
               if end_raw.get("date") else start + timedelta(days=1))
    elif start_raw.get("dateTime"):
        start = _parse_timed(start_raw["dateTime"])
        # Keep the provider's wall-clock DTSTART separately.  The existing
        # ``start`` field remains local-naive for Task compatibility, while
        # recurrence UTC UNTIL must be compared in start.timeZone semantics.
        provider_start = _parse_rfc3339(start_raw["dateTime"])
        if start_raw.get("timeZone"):
            try:
                provider_start = provider_start.astimezone(
                    ZoneInfo(start_raw["timeZone"])
                )
            except Exception:
                # Unknown provider zone remains transport metadata; using the
                # explicit RFC3339 offset is safer than guessing another zone.
                pass
        recurrence_start = provider_start.replace(tzinfo=None)
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
        recurrence_lines=tuple(str(line) for line in (item.get("recurrence") or ())),
        start_timezone=start_raw.get("timeZone"),
        end_timezone=end_raw.get("timeZone"),
        recurrence_start=recurrence_start,
        private_extended_properties={
            str(key): str(value)
            for key, value in (
                ((item.get("extendedProperties") or {}).get("private") or {})
            ).items()
        },
    )


def _master_content_matches(current: CalendarEvent, desired_hash: str) -> bool:
    """True только когда фактическое Planner-owned содержимое мастера равно
    каноническому желаемому payload. Любой сбой канонизации (в т.ч. чужая
    нормализация Google) считается несовпадением и безопасно деградирует к
    etag-проверке вызывающего кода — молчаливого «успеха» без записи нет."""
    try:
        return master_payload_hash(current) == desired_hash
    except (TypeError, ValueError):
        return False


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

    @property
    def calendar_id(self) -> str:
        return self._calendar_id

    # ---- push -------------------------------------------------------------------

    def insert_event(self, event: CalendarEvent) -> CalendarEvent:
        if event.recurrence_lines:
            raise TerminalGatewayError(
                "Запись повторяющегося мастера отложена до Phase 3.2B2."
            )
        body = event_to_insert_body(event)
        try:
            created = self._service.events().insert(
                calendarId=self._calendar_id, body=body,
            ).execute()
        except Exception as exc:  # классифицируем в ошибки шлюза
            raise _classify(exc, "insert_event") from exc
        return payload_to_event(created)

    def patch_event(self, event_id: str, patch: Mapping[str, Any]) -> CalendarEvent:
        if "recurrence" in patch or "recurrence_lines" in patch:
            raise TerminalGatewayError(
                "Изменение повторяющегося мастера отложено до Phase 3.2B2."
            )
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

    # ---- explicit recurring-master writes (Phase 3.2B2) ---------------------

    @staticmethod
    def _verify_master_owner(
        remote: CalendarEvent, desired: CalendarEvent, remote_event_id: str
    ) -> None:
        expected_uid = desired.private_extended_properties.get(
            PLANNER_SERIES_UID_PROPERTY
        )
        actual_uid = remote.private_extended_properties.get(
            PLANNER_SERIES_UID_PROPERTY
        )
        if not actual_uid or actual_uid != expected_uid:
            raise TerminalGatewayError(
                "Коллизия Google event id: существующий мастер "
                f"{remote_event_id} принадлежит другой серии."
            )

    def get_recurring_master(
        self, remote_event_id: str
    ) -> Optional[CalendarEvent]:
        try:
            item = self._service.events().get(
                calendarId=self._calendar_id, eventId=remote_event_id,
            ).execute()
        except Exception as exc:
            if _http_status(exc) in (404, 410):
                return None
            raise _classify(exc, f"get_recurring_master {remote_event_id}") from exc
        event = payload_to_event(item)
        if event.is_cancelled:
            return None
        return event

    def insert_recurring_master(
        self, remote_event_id: str, master_payload: CalendarEvent
    ) -> CalendarEvent:
        body = recurring_master_to_insert_body(master_payload)
        body["id"] = remote_event_id
        try:
            item = self._service.events().insert(
                calendarId=self._calendar_id, body=body,
            ).execute()
            return payload_to_event(item)
        except Exception as exc:
            if _http_status(exc) != 409:
                raise _classify(
                    exc, f"insert_recurring_master {remote_event_id}"
                ) from exc

        # Deterministic ID retry: reconcile the one existing resource.  Never
        # fall back to a random second master.
        remote = self.get_recurring_master(remote_event_id)
        if remote is None:
            raise TerminalGatewayError(
                f"Google сообщил коллизию id {remote_event_id}, но мастер не найден."
            )
        self._verify_master_owner(remote, master_payload, remote_event_id)
        desired_hash = master_payload.private_extended_properties.get(
            PLANNER_PAYLOAD_HASH_PROPERTY
        )
        remote_hash = remote.private_extended_properties.get(
            PLANNER_PAYLOAD_HASH_PROPERTY
        )
        if desired_hash and remote_hash == desired_hash:
            return remote
        raise RemoteMasterConflictError(
            "Мастер с детерминированным id уже принадлежит этой серии, "
            "но его содержимое отличается.",
            remote,
        )

    def patch_recurring_master(
        self,
        remote_event_id: str,
        master_payload: CalendarEvent,
        *,
        expected_etag: Optional[str] = None,
    ) -> CalendarEvent:
        current = self.get_recurring_master(remote_event_id)
        if current is None:
            raise RemoteMasterConflictError(
                "Связанный мастер Google был удалён.", None
            )
        self._verify_master_owner(current, master_payload, remote_event_id)
        desired_hash = master_payload.private_extended_properties.get(
            PLANNER_PAYLOAD_HASH_PROPERTY
        )
        current_hash = current.private_extended_properties.get(
            PLANNER_PAYLOAD_HASH_PROPERTY
        )
        # Повтор после remote-успеха/локального сбоя: одних маркеров
        # недостаточно — чужая правка (например, summary с телефона) НЕ
        # обновляет приватные маркеры, и устаревший маркер не должен выдавать
        # неотправленный PATCH за применённый (перезапись Keep Planner обязана
        # реально перезаписать мастер). Требуем совпадения и маркеров, и
        # фактического содержимого.
        if (
            desired_hash
            and current_hash == desired_hash
            and _master_content_matches(current, desired_hash)
        ):
            return current
        if expected_etag and current.etag != expected_etag:
            raise RemoteMasterConflictError(
                "Мастер Google изменён вне Planner; автоматическая перезапись запрещена.",
                current,
            )

        body = recurring_master_patch_to_body(master_payload)
        private = dict(current.private_extended_properties)
        private.update(master_payload.private_extended_properties)
        body["extendedProperties"] = {"private": private}
        try:
            request = self._service.events().patch(
                calendarId=self._calendar_id,
                eventId=remote_event_id,
                body=body,
            )
            headers = getattr(request, "headers", None)
            if expected_etag and isinstance(headers, dict):
                headers["If-Match"] = expected_etag
            item = request.execute()
        except Exception as exc:
            if _http_status(exc) in (409, 412):
                raise RemoteMasterConflictError(
                    "Google отклонил условное обновление: мастер изменён.",
                    self.get_recurring_master(remote_event_id),
                ) from exc
            raise _classify(
                exc, f"patch_recurring_master {remote_event_id}"
            ) from exc
        return payload_to_event(item)

    def delete_recurring_master(self, remote_event_id: str) -> None:
        # Same idempotent HTTP semantics as ordinary delete, but a separate
        # method keeps user intent and the series queue explicit.
        try:
            self._service.events().delete(
                calendarId=self._calendar_id, eventId=remote_event_id,
            ).execute()
        except Exception as exc:
            if _http_status(exc) in (404, 410):
                return
            raise _classify(
                exc, f"delete_recurring_master {remote_event_id}"
            ) from exc

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
    "recurrence_to_google_lines",
    "recurring_master_patch_to_body",
    "recurring_master_to_insert_body",
    "times_to_body",
]
