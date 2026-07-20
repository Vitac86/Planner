"""Фейковый in-memory шлюз календаря для тестов и разработки.

Реализует контракт CalendarGateway без сети, без OAuth и без импорта
Google-клиентов. Симулирует ровно то поведение Calendar API, на которое
опирается движок синхронизации:

- create/update/delete с etag-ами и updated_at;
- журнал изменений с курсором (аналог syncToken): list_changes(cursor)
  отдаёт события, изменившиеся после курсора, включая правки,
  «сделанные на телефоне» (в тестах — прямые вызовы insert/patch/delete);
- delete = status "cancelled" (событие остаётся в журнале, как у Google);
- all-day события (start/end — date) и события со временем (datetime);
- метаданные экземпляра повторяющегося события (recurring_event_id,
  original_start) и фирменный отказ Google: слепой патч start/end
  такого экземпляра поднимает TerminalGatewayError (аналог 400);
- инъекция ошибок для тестов ретраев/dead-letter: fail_next(error).

Часы детерминированные: base_time + секунда за каждое изменение,
поэтому updated_at событий строго монотонен и сравним между тестами.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Mapping, Optional

from zoneinfo import ZoneInfo

from planner_desktop.domain.task import utc_now
from planner_desktop.domain.series_calendar_link import (
    PLANNER_PAYLOAD_HASH_PROPERTY,
    PLANNER_SERIES_UID_PROPERTY,
    canonical_master_payload_fingerprint,
)
from planner_desktop.sync.calendar_series_mapper import (
    master_event_to_owned_payload,
)
from planner_desktop.sync.sync_types import (
    EVENT_STATUS_CANCELLED,
    CalendarEvent,
    RemoteMasterConflictError,
    RemoteOccurrenceConflictError,
    RemoteChangeBatch,
    TerminalGatewayError,
)

_TIME_FIELDS = ("start", "end", "is_all_day")
_PATCHABLE_FIELDS = ("summary", "description", "start", "end", "is_all_day")


class FakeCalendarGateway:
    """Календарь в памяти процесса. Никакой сети, никаких Google-импортов."""

    def __init__(self, base_time: Optional[datetime] = None,
                 calendar_id: str = "primary") -> None:
        self._events: Dict[str, CalendarEvent] = {}
        self._instance_resources: Dict[str, Dict[str, Any]] = {}
        # Unrelated provider fields of a master resource (attendees, location
        # and similar) survive full-resource split writes verbatim.
        self._master_extras: Dict[str, Dict[str, Any]] = {}
        self._change_log: List[str] = []  # id событий в порядке изменений
        self._next_id = 1
        self._base_time = base_time or utc_now()
        self._ticks = 0
        self._pending_errors: List[Exception] = []
        self._calendar_id = calendar_id
        self.list_call_count = 0
        self.write_call_count = 0

    @property
    def calendar_id(self) -> str:
        return self._calendar_id

    def reset_call_counts(self) -> None:
        self.list_call_count = 0
        self.write_call_count = 0

    # ---- управление фейком из тестов ------------------------------------------

    def fail_next(self, error: Exception) -> None:
        """Следующий мутирующий вызов (insert/patch/delete) поднимет error."""
        self._pending_errors.append(error)

    def get_event(self, event_id: str) -> Optional[CalendarEvent]:
        """Прямой доступ к «удалённому» состоянию для ассертов в тестах."""
        event = self._events.get(event_id)
        return replace(event) if event is not None else None

    def get_recurring_master(self, remote_event_id: str) -> Optional[CalendarEvent]:
        event = self._events.get(remote_event_id)
        if event is None or event.is_cancelled:
            return None
        return replace(
            event,
            private_extended_properties=dict(event.private_extended_properties),
        )

    @property
    def events(self) -> List[CalendarEvent]:
        return [replace(e) for e in self._events.values()]

    # ---- внутреннее -------------------------------------------------------------

    def _tick(self) -> datetime:
        self._ticks += 1
        return self._base_time + timedelta(seconds=self._ticks)

    def _maybe_fail(self) -> None:
        if self._pending_errors:
            raise self._pending_errors.pop(0)

    def _bump_etag(self, event: CalendarEvent) -> None:
        revision = int((event.etag or '"0"').strip('"')) + 1
        event.etag = f'"{revision}"'

    def _record_change(self, event: CalendarEvent) -> None:
        self._change_log.append(event.id)

    # ---- контракт CalendarGateway ------------------------------------------------

    def insert_event(self, event: CalendarEvent) -> CalendarEvent:
        self.write_call_count += 1
        self._maybe_fail()
        stored = replace(event)
        stored.id = f"evt-{self._next_id}"
        self._next_id += 1
        stored.etag = '"1"'
        stored.updated_at = self._tick()
        self._events[stored.id] = stored
        if stored.is_recurring_instance:
            resource = self._event_to_resource(stored)
            stored.raw_payload = deepcopy(resource)
            self._instance_resources[stored.id] = resource
        self._record_change(stored)
        return replace(stored)

    def patch_event(self, event_id: str, patch: Mapping[str, Any]) -> CalendarEvent:
        self.write_call_count += 1
        self._maybe_fail()
        event = self._events.get(event_id)
        if event is None:
            raise TerminalGatewayError(f"Событие {event_id} не существует (404).")
        if event.is_cancelled:
            raise TerminalGatewayError(f"Событие {event_id} отменено (410).")
        unknown = set(patch) - set(_PATCHABLE_FIELDS)
        if unknown:
            raise TerminalGatewayError(f"Непатчабельные поля: {sorted(unknown)} (400).")
        if event.is_recurring_instance and any(f in patch for f in _TIME_FIELDS):
            # Так отвечает Google на слепой перенос экземпляра серии.
            raise TerminalGatewayError(
                "Слепой патч start/end экземпляра повторяющегося события "
                "запрещён (400): используйте recurringEventId + originalStartTime."
            )
        for name, value in patch.items():
            setattr(event, name, value)
        self._bump_etag(event)
        event.updated_at = self._tick()
        self._record_change(event)
        return replace(event)

    def delete_event(self, event_id: str) -> None:
        self.write_call_count += 1
        self._maybe_fail()
        event = self._events.get(event_id)
        if event is None or event.is_cancelled:
            return  # идемпотентность: уже отсутствует/отменено — не ошибка
        event.status = EVENT_STATUS_CANCELLED
        self._bump_etag(event)
        event.updated_at = self._tick()
        self._record_change(event)

    # ---- explicit recurring-master contract ---------------------------------

    @staticmethod
    def _verify_master_owner(
        current: CalendarEvent, desired: CalendarEvent, remote_event_id: str
    ) -> None:
        actual_uid = current.private_extended_properties.get(
            PLANNER_SERIES_UID_PROPERTY
        )
        desired_uid = desired.private_extended_properties.get(
            PLANNER_SERIES_UID_PROPERTY
        )
        if not actual_uid or actual_uid != desired_uid:
            raise TerminalGatewayError(
                f"Коллизия Google event id {remote_event_id}: чужой мастер."
            )

    def insert_recurring_master(
        self, remote_event_id: str, master_payload: CalendarEvent
    ) -> CalendarEvent:
        self.write_call_count += 1
        self._maybe_fail()
        if not master_payload.is_recurring_master:
            raise TerminalGatewayError("Recurring master payload has no recurrence.")
        existing = self._events.get(remote_event_id)
        if existing is not None and not existing.is_cancelled:
            self._verify_master_owner(existing, master_payload, remote_event_id)
            desired_hash = master_payload.private_extended_properties.get(
                PLANNER_PAYLOAD_HASH_PROPERTY
            )
            actual_hash = existing.private_extended_properties.get(
                PLANNER_PAYLOAD_HASH_PROPERTY
            )
            if desired_hash and desired_hash == actual_hash:
                return self.get_recurring_master(remote_event_id)
            raise RemoteMasterConflictError(
                "Существующий мастер этой серии отличается.",
                self.get_recurring_master(remote_event_id),
            )
        stored = replace(
            master_payload,
            id=remote_event_id,
            etag='"1"',
            status="confirmed",
            updated_at=self._tick(),
            private_extended_properties=dict(
                master_payload.private_extended_properties
            ),
        )
        self._events[remote_event_id] = stored
        self._record_change(stored)
        return self.get_recurring_master(remote_event_id)

    def patch_recurring_master(
        self,
        remote_event_id: str,
        master_payload: CalendarEvent,
        *,
        expected_etag: Optional[str] = None,
    ) -> CalendarEvent:
        self.write_call_count += 1
        self._maybe_fail()
        current = self._events.get(remote_event_id)
        if current is None or current.is_cancelled:
            raise RemoteMasterConflictError("Связанный мастер удалён.")
        self._verify_master_owner(current, master_payload, remote_event_id)
        desired_hash = master_payload.private_extended_properties.get(
            PLANNER_PAYLOAD_HASH_PROPERTY
        )
        current_hash = current.private_extended_properties.get(
            PLANNER_PAYLOAD_HASH_PROPERTY
        )
        # Идемпотентный повтор идентичной записи не меняет etag.  Одних
        # маркеров недостаточно: чужая правка (например, summary с телефона)
        # НЕ обновляет приватные маркеры, и «свежий» маркер не должен
        # блокировать явную перезапись Keep-Planner (Phase 3.2B3A).
        same_content = (
            current.summary == master_payload.summary
            and current.description == master_payload.description
            and current.start == master_payload.start
            and current.end == master_payload.end
            and current.is_all_day == master_payload.is_all_day
            and tuple(current.recurrence_lines)
                == tuple(master_payload.recurrence_lines)
        )
        if desired_hash and desired_hash == current_hash and same_content:
            return self.get_recurring_master(remote_event_id)
        if expected_etag and current.etag != expected_etag:
            raise RemoteMasterConflictError(
                "Мастер изменён вне Planner.",
                self.get_recurring_master(remote_event_id),
            )
        private = dict(current.private_extended_properties)
        private.update(master_payload.private_extended_properties)
        updated = replace(
            master_payload,
            id=remote_event_id,
            etag=current.etag,
            status="confirmed",
            private_extended_properties=private,
        )
        self._bump_etag(updated)
        updated.updated_at = self._tick()
        self._events[remote_event_id] = updated
        self._record_change(updated)
        return self.get_recurring_master(remote_event_id)

    def delete_recurring_master(self, remote_event_id: str) -> None:
        self.delete_event(remote_event_id)

    # ---- full-resource master split contract (Phase 3.2B3C1) ---------------

    _OWNED_RESOURCE_KEYS = (
        "id", "etag", "status", "updated", "summary", "description",
        "start", "end", "recurrence", "extendedProperties",
    )

    def _master_event_to_resource(self, event: CalendarEvent) -> Dict[str, Any]:
        body = master_event_to_owned_payload(event)
        body["id"] = event.id
        body["etag"] = event.etag
        body["status"] = event.status
        body["updated"] = (
            event.updated_at.isoformat() if event.updated_at is not None else None
        )
        body["extendedProperties"] = {
            "private": dict(event.private_extended_properties)
        }
        for key, value in (self._master_extras.get(event.id or "") or {}).items():
            body.setdefault(key, deepcopy(value))
        return body

    def _master_resource_to_event(self, raw: Mapping[str, Any]) -> CalendarEvent:
        start_raw = raw.get("start") or {}
        end_raw = raw.get("end") or {}
        is_all_day = bool(start_raw.get("date"))
        if is_all_day:
            start: Any = date.fromisoformat(str(start_raw["date"]))
            end: Any = (
                date.fromisoformat(str(end_raw["date"]))
                if end_raw.get("date") else start + timedelta(days=1)
            )
        else:
            timezone_name = str(start_raw.get("timeZone") or "UTC")
            zone = ZoneInfo(timezone_name)
            start = datetime.fromisoformat(
                str(start_raw["dateTime"]).replace("Z", "+00:00")
            ).astimezone(zone).replace(tzinfo=None)
            end = datetime.fromisoformat(
                str(end_raw["dateTime"]).replace("Z", "+00:00")
            ).astimezone(zone).replace(tzinfo=None)
        return CalendarEvent(
            id=str(raw.get("id") or ""),
            etag=raw.get("etag"),
            summary=str(raw.get("summary") or ""),
            description=str(raw.get("description") or ""),
            start=start,
            end=end,
            is_all_day=is_all_day,
            status=str(raw.get("status") or "confirmed"),
            recurrence_lines=tuple(
                str(line) for line in (raw.get("recurrence") or ())
            ),
            start_timezone=start_raw.get("timeZone"),
            end_timezone=end_raw.get("timeZone"),
            recurrence_start=start,
            private_extended_properties={
                str(key): str(value) for key, value in (
                    ((raw.get("extendedProperties") or {}).get("private") or {})
                ).items()
            },
        )

    def _store_master_extras(
        self, remote_event_id: str, payload: Mapping[str, Any]
    ) -> None:
        extras = {
            key: deepcopy(value)
            for key, value in payload.items()
            if key not in self._OWNED_RESOURCE_KEYS
        }
        if extras:
            self._master_extras[remote_event_id] = extras

    def get_recurring_master_resource(
        self, remote_event_id: str
    ) -> Optional[Dict[str, Any]]:
        event = self._events.get(remote_event_id)
        if event is None or event.is_cancelled:
            return None
        return self._master_event_to_resource(event)

    def update_recurring_master_full(
        self,
        remote_event_id: str,
        complete_master_payload: Mapping[str, Any],
        expected_etag: Optional[str],
    ) -> Dict[str, Any]:
        self.write_call_count += 1
        self._maybe_fail()
        current = self._events.get(remote_event_id)
        if current is None or current.is_cancelled:
            raise RemoteMasterConflictError("Связанный мастер удалён.")
        desired_private = (
            (complete_master_payload.get("extendedProperties") or {})
            .get("private") or {}
        )
        desired_uid = str(desired_private.get(PLANNER_SERIES_UID_PROPERTY) or "")
        actual_uid = current.private_extended_properties.get(
            PLANNER_SERIES_UID_PROPERTY
        )
        if not actual_uid or actual_uid != desired_uid:
            raise TerminalGatewayError(
                f"Коллизия Google event id {remote_event_id}: чужой мастер."
            )
        if expected_etag and current.etag != expected_etag:
            raise RemoteMasterConflictError(
                "Мастер изменён вне Planner.",
                self.get_recurring_master(remote_event_id),
            )
        updated = self._master_resource_to_event(complete_master_payload)
        updated.id = remote_event_id
        updated.etag = current.etag
        updated.status = "confirmed"
        self._bump_etag(updated)
        updated.updated_at = self._tick()
        self._events[remote_event_id] = updated
        self._store_master_extras(remote_event_id, complete_master_payload)
        self._record_change(updated)
        return self._master_event_to_resource(updated)

    def insert_split_successor_master(
        self,
        remote_event_id: str,
        complete_master_payload: Mapping[str, Any],
    ) -> Dict[str, Any]:
        self.write_call_count += 1
        self._maybe_fail()
        existing = self._events.get(remote_event_id)
        if existing is not None and not existing.is_cancelled:
            desired_private = (
                (complete_master_payload.get("extendedProperties") or {})
                .get("private") or {}
            )
            desired_uid = str(
                desired_private.get(PLANNER_SERIES_UID_PROPERTY) or ""
            )
            actual_uid = existing.private_extended_properties.get(
                PLANNER_SERIES_UID_PROPERTY
            )
            if not actual_uid or actual_uid != desired_uid:
                raise TerminalGatewayError(
                    f"Коллизия Google event id {remote_event_id}: чужое событие."
                )
            resource = self._master_event_to_resource(existing)
            if canonical_master_payload_fingerprint(resource) == (
                canonical_master_payload_fingerprint(complete_master_payload)
            ):
                return resource
            raise RemoteMasterConflictError(
                "Мастер-преемник уже существует, но отличается.",
                self.get_recurring_master(remote_event_id),
            )
        stored = self._master_resource_to_event(complete_master_payload)
        stored.id = remote_event_id
        stored.etag = '"1"'
        stored.status = "confirmed"
        stored.updated_at = self._tick()
        self._events[remote_event_id] = stored
        self._store_master_extras(remote_event_id, complete_master_payload)
        self._record_change(stored)
        return self._master_event_to_resource(stored)

    # ---- explicit recurring-instance contract ----------------------------

    @staticmethod
    def _time_resource(value: Any, timezone_name: Optional[str]) -> dict:
        if isinstance(value, datetime):
            result = {"dateTime": value.isoformat()}
            if timezone_name:
                result["timeZone"] = timezone_name
            return result
        if isinstance(value, date):
            return {"date": value.isoformat()}
        return {}

    def _event_to_resource(self, event: CalendarEvent) -> Dict[str, Any]:
        original = self._time_resource(
            event.original_start,
            event.original_start_timezone or event.start_timezone,
        )
        return {
            "id": event.id,
            "etag": event.etag,
            "summary": event.summary,
            "description": event.description,
            "start": self._time_resource(event.start, event.start_timezone),
            "end": self._time_resource(event.end, event.end_timezone),
            "status": event.status,
            "updated": event.updated_at.isoformat() if event.updated_at else None,
            "recurringEventId": event.recurring_event_id,
            "originalStartTime": original,
            "extendedProperties": {
                "private": dict(event.private_extended_properties)
            },
        }

    @staticmethod
    def _resource_time(raw: Mapping[str, Any]) -> Any:
        if raw.get("date"):
            return date.fromisoformat(str(raw["date"]))
        if raw.get("dateTime"):
            return datetime.fromisoformat(
                str(raw["dateTime"]).replace("Z", "+00:00")
            )
        return None

    def _resource_to_event(self, raw: Mapping[str, Any]) -> CalendarEvent:
        start_raw = raw.get("start") or {}
        end_raw = raw.get("end") or {}
        original_raw = raw.get("originalStartTime") or {}
        start = self._resource_time(start_raw)
        end = self._resource_time(end_raw)
        original = self._resource_time(original_raw)
        return CalendarEvent(
            id=str(raw.get("id") or ""),
            etag=raw.get("etag"),
            summary=str(raw.get("summary") or ""),
            description=str(raw.get("description") or ""),
            start=start,
            end=end,
            is_all_day=isinstance(start, date) and not isinstance(start, datetime),
            status=str(raw.get("status") or "confirmed"),
            updated_at=(
                datetime.fromisoformat(str(raw["updated"]).replace("Z", "+00:00"))
                if raw.get("updated") else None
            ),
            recurring_event_id=str(raw.get("recurringEventId") or "") or None,
            original_start=original,
            original_start_timezone=original_raw.get("timeZone"),
            start_timezone=start_raw.get("timeZone"),
            end_timezone=end_raw.get("timeZone"),
            private_extended_properties={
                str(key): str(value) for key, value in (
                    ((raw.get("extendedProperties") or {}).get("private") or {})
                ).items()
            },
            raw_payload=deepcopy(dict(raw)),
        )

    def seed_recurring_instance(self, payload: Mapping[str, Any]) -> Dict[str, Any]:
        """Install a complete remote instance without counting a Planner write."""
        resource = deepcopy(dict(payload))
        event_id = str(resource.get("id") or f"evt-{self._next_id}")
        if not resource.get("id"):
            self._next_id += 1
        resource["id"] = event_id
        resource.setdefault("etag", '"1"')
        resource.setdefault("status", "confirmed")
        resource.setdefault("updated", self._tick().isoformat())
        event = self._resource_to_event(resource)
        self._events[event_id] = event
        self._instance_resources[event_id] = resource
        self._record_change(event)
        return deepcopy(resource)

    def list_recurring_instances(
        self,
        master_event_id: str,
        original_start: Optional[Mapping[str, Any]] = None,
        show_deleted: bool = True,
    ) -> List[Dict[str, Any]]:
        self.list_call_count += 1
        result: List[Dict[str, Any]] = []
        for resource in self._instance_resources.values():
            if str(resource.get("recurringEventId") or "") != master_event_id:
                continue
            if not show_deleted and resource.get("status") == "cancelled":
                continue
            if original_start is not None and (
                resource.get("originalStartTime") or {}
            ) != dict(original_start):
                continue
            result.append(deepcopy(resource))
        return result

    def get_recurring_instance(
        self, instance_event_id: str
    ) -> Optional[Dict[str, Any]]:
        resource = self._instance_resources.get(instance_event_id)
        return deepcopy(resource) if resource is not None else None

    def _write_recurring_instance(
        self,
        instance_event_id: str,
        complete_instance_payload: Mapping[str, Any],
        expected_etag: Optional[str],
        *,
        cancel: bool,
    ) -> Dict[str, Any]:
        self.write_call_count += 1
        self._maybe_fail()
        current = self._instance_resources.get(instance_event_id)
        if current is None:
            raise TerminalGatewayError(
                f"Recurring instance {instance_event_id} does not exist (404)."
            )
        if str(current.get("recurringEventId") or "") != str(
            complete_instance_payload.get("recurringEventId") or ""
        ):
            raise TerminalGatewayError("Recurring instance has the wrong parent.")
        if (current.get("originalStartTime") or {}) != (
            complete_instance_payload.get("originalStartTime") or {}
        ):
            raise TerminalGatewayError(
                "Recurring instance has the wrong originalStartTime."
            )
        if expected_etag and current.get("etag") != expected_etag:
            raise RemoteOccurrenceConflictError(
                "Recurring instance changed after acknowledgement.",
                deepcopy(current),
            )
        if cancel and current.get("status") == "cancelled":
            return deepcopy(current)
        updated = deepcopy(dict(complete_instance_payload))
        updated.pop("recurrence", None)
        updated["id"] = instance_event_id
        revision = int(str(current.get("etag") or '"0"').strip('"')) + 1
        updated["etag"] = f'"{revision}"'
        updated["updated"] = self._tick().isoformat()
        if cancel:
            updated["status"] = "cancelled"
        event = self._resource_to_event(updated)
        self._instance_resources[instance_event_id] = updated
        self._events[instance_event_id] = event
        self._record_change(event)
        return deepcopy(updated)

    def update_recurring_instance(
        self,
        instance_event_id: str,
        complete_instance_payload: Mapping[str, Any],
        expected_etag: Optional[str],
    ) -> Dict[str, Any]:
        return self._write_recurring_instance(
            instance_event_id, complete_instance_payload, expected_etag,
            cancel=False,
        )

    def cancel_recurring_instance(
        self,
        instance_event_id: str,
        complete_instance_payload: Mapping[str, Any],
        expected_etag: Optional[str],
    ) -> Dict[str, Any]:
        return self._write_recurring_instance(
            instance_event_id, complete_instance_payload, expected_etag,
            cancel=True,
        )

    def list_changes(self, cursor: Optional[str]) -> RemoteChangeBatch:
        self.list_call_count += 1
        start_index = int(cursor) if cursor else 0
        changed_ids: List[str] = []
        for event_id in self._change_log[start_index:]:
            if event_id in changed_ids:
                changed_ids.remove(event_id)
            changed_ids.append(event_id)  # каждое событие один раз, свежим
        return RemoteChangeBatch(
            events=[replace(self._events[eid]) for eid in changed_ids],
            next_cursor=str(len(self._change_log)),
        )
