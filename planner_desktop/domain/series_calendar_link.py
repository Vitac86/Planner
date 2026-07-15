"""Pure domain types for explicit TaskSeries <-> Calendar master links.

No repository, Google client, Qt object, or network access belongs here.  The
stable event id and payload fingerprint helpers are intentionally deterministic
so a retry after a process or database failure addresses the same remote master.
"""
from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import Enum
from typing import Any, Mapping, Optional, Tuple

from planner_desktop.domain.task import utc_now


GOOGLE_PROVIDER = "google"
DEFAULT_GOOGLE_CALENDAR_ID = "primary"
PLANNER_LINK_SCHEMA_VERSION = "1"

PLANNER_SERIES_UID_PROPERTY = "planner_series_uid"
PLANNER_LINK_VERSION_PROPERTY = "planner_link_version"
PLANNER_SERIES_REVISION_PROPERTY = "planner_series_revision"
PLANNER_PAYLOAD_HASH_PROPERTY = "planner_payload_hash"

LINKED_OCCURRENCE_CHANGE_ERROR = (
    "Изменение отдельных экземпляров серии Google будет добавлено "
    "на следующем этапе."
)


class SeriesLinkStatus(str, Enum):
    PENDING_CREATE = "pending_create"
    SYNCED = "synced"
    PENDING_UPDATE = "pending_update"
    PENDING_DELETE = "pending_delete"
    CONFLICT = "conflict"
    REMOTE_DELETED = "remote_deleted"
    DETACHED = "detached"
    TERMINAL_ERROR = "terminal_error"


class SeriesSyncOpKind(str, Enum):
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"


class SeriesSyncOpStatus(str, Enum):
    PENDING = "pending"
    TERMINAL = "terminal"


_STATUS_TEXT_RU = {
    SeriesLinkStatus.PENDING_CREATE: "Ожидает создания в Google",
    SeriesLinkStatus.SYNCED: "Связана с Google",
    SeriesLinkStatus.PENDING_UPDATE: "Есть локальные изменения",
    SeriesLinkStatus.PENDING_DELETE: "Ожидает удаления из Google",
    SeriesLinkStatus.CONFLICT: "Конфликт изменений",
    SeriesLinkStatus.REMOTE_DELETED: "Серия удалена в Google",
    SeriesLinkStatus.DETACHED: "Отключена",
    SeriesLinkStatus.TERMINAL_ERROR: "Ошибка синхронизации",
}


def readable_series_link_status(status: SeriesLinkStatus | str | None) -> str:
    if status is None:
        return "Локальная серия"
    try:
        key = status if isinstance(status, SeriesLinkStatus) else SeriesLinkStatus(status)
    except ValueError:
        return "Неизвестное состояние связи"
    return _STATUS_TEXT_RU[key]


def deterministic_remote_event_id(series_uid: str) -> str:
    """Return a stable Google-valid base32hex id for one local series.

    Google accepts only lowercase base32hex characters (0-9, a-v).  The fixed
    ``plr`` prefix is in that alphabet and the SHA-256 digest makes accidental
    reuse across different series impractical.  Private ownership properties
    still verify identity before reconciliation.
    """
    normalized = str(series_uid).strip()
    if not normalized:
        raise ValueError("series_uid is required")
    digest = hashlib.sha256(normalized.encode("utf-8")).digest()
    encoded = base64.b32hexencode(digest).decode("ascii").rstrip("=").lower()
    return f"plr{encoded}"


def canonical_master_payload_data(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Copy only Planner-owned master fields into canonical hash input."""
    start = payload.get("start") or {}
    end = payload.get("end") or {}
    return {
        "summary": str(payload.get("summary") or ""),
        "description": str(payload.get("description") or ""),
        "start": dict(start),
        "end": dict(end),
        "recurrence": [str(item) for item in payload.get("recurrence") or ()],
    }


def canonical_master_payload_fingerprint(payload: Mapping[str, Any]) -> str:
    canonical = json.dumps(
        canonical_master_payload_data(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def planner_private_properties(
    series_uid: str, revision: int, payload_hash: str
) -> dict[str, str]:
    return {
        PLANNER_SERIES_UID_PROPERTY: str(series_uid),
        PLANNER_LINK_VERSION_PROPERTY: PLANNER_LINK_SCHEMA_VERSION,
        PLANNER_SERIES_REVISION_PROPERTY: str(int(revision)),
        PLANNER_PAYLOAD_HASH_PROPERTY: str(payload_hash),
    }


@dataclass
class SeriesCalendarLink:
    series_uid: str
    provider: str = GOOGLE_PROVIDER
    calendar_id: str = DEFAULT_GOOGLE_CALENDAR_ID
    remote_event_id: str = ""
    link_status: SeriesLinkStatus = SeriesLinkStatus.PENDING_CREATE
    remote_etag: Optional[str] = None
    remote_updated_at: Optional[datetime] = None
    last_synced_series_revision: Optional[int] = None
    last_synced_payload_hash: Optional[str] = None
    linked_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    detached_at: Optional[datetime] = None
    last_error: Optional[str] = None
    id: Optional[int] = None

    @property
    def is_active(self) -> bool:
        return self.link_status is not SeriesLinkStatus.DETACHED

    @property
    def status_text(self) -> str:
        return readable_series_link_status(self.link_status)

    def clone(self) -> "SeriesCalendarLink":
        return replace(self)


@dataclass
class PendingSeriesSyncOp:
    id: int
    series_uid: str
    op: SeriesSyncOpKind
    remote_event_id: Optional[str] = None
    desired_revision: Optional[int] = None
    desired_payload_hash: Optional[str] = None
    payload_json: Optional[str] = None
    attempts: int = 0
    last_error: Optional[str] = None
    status: SeriesSyncOpStatus = SeriesSyncOpStatus.PENDING
    created_at: Optional[datetime] = None
    next_try_at: Optional[datetime] = None

    @property
    def payload(self) -> dict[str, Any]:
        if not self.payload_json:
            return {}
        value = json.loads(self.payload_json)
        return value if isinstance(value, dict) else {}


@dataclass(frozen=True)
class SeriesConnectValidationIssue:
    code: str
    message: str


@dataclass(frozen=True)
class SeriesConnectValidationResult:
    series_uid: str
    issues: Tuple[SeriesConnectValidationIssue, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.issues

    @property
    def errors(self) -> Tuple[str, ...]:
        return tuple(item.message for item in self.issues)


@dataclass
class SeriesSyncItemResult:
    series_uid: str
    op: SeriesSyncOpKind
    ok: bool
    reconciled: bool = False
    conflict: bool = False
    terminal: bool = False
    error: str = ""


@dataclass
class SeriesSyncResult:
    created: int = 0
    updated: int = 0
    deleted: int = 0
    conflicts: int = 0
    terminal: int = 0
    items: list[SeriesSyncItemResult] = field(default_factory=list)

    @property
    def pushed(self) -> int:
        return self.created + self.updated + self.deleted


@dataclass
class SeriesLinkActionResult:
    ok: bool
    link: Optional[SeriesCalendarLink] = None
    validation: Optional[SeriesConnectValidationResult] = None
    changed: bool = False
    error: str = ""


@dataclass
class RemoteOccurrenceChange:
    provider: str
    calendar_id: str
    remote_master_event_id: str
    remote_instance_event_id: str
    original_start_value: str
    status: str
    payload_json: Optional[str] = None
    remote_etag: Optional[str] = None
    remote_updated_at: Optional[datetime] = None
    first_seen_at: datetime = field(default_factory=utc_now)
    last_seen_at: datetime = field(default_factory=utc_now)
    resolved_at: Optional[datetime] = None
    id: Optional[int] = None


@dataclass(frozen=True)
class RemoteMasterConflict:
    series_uid: str
    remote_event_id: str
    local_etag: Optional[str]
    remote_etag: Optional[str]
    local_payload_hash: Optional[str]
    remote_payload_hash: Optional[str]
    message: str


def transition_link(
    link: SeriesCalendarLink,
    status: SeriesLinkStatus,
    *,
    when: Optional[datetime] = None,
    error: Optional[str] = None,
) -> SeriesCalendarLink:
    stamp = when or utc_now()
    detached_at = stamp if status is SeriesLinkStatus.DETACHED else None
    return replace(
        link,
        link_status=status,
        updated_at=stamp,
        detached_at=detached_at,
        last_error=error,
    )


__all__ = [
    "DEFAULT_GOOGLE_CALENDAR_ID",
    "GOOGLE_PROVIDER",
    "LINKED_OCCURRENCE_CHANGE_ERROR",
    "PLANNER_LINK_SCHEMA_VERSION",
    "PLANNER_LINK_VERSION_PROPERTY",
    "PLANNER_PAYLOAD_HASH_PROPERTY",
    "PLANNER_SERIES_REVISION_PROPERTY",
    "PLANNER_SERIES_UID_PROPERTY",
    "PendingSeriesSyncOp",
    "RemoteMasterConflict",
    "RemoteOccurrenceChange",
    "SeriesCalendarLink",
    "SeriesConnectValidationIssue",
    "SeriesConnectValidationResult",
    "SeriesLinkStatus",
    "SeriesLinkActionResult",
    "SeriesSyncItemResult",
    "SeriesSyncOpKind",
    "SeriesSyncOpStatus",
    "SeriesSyncResult",
    "canonical_master_payload_data",
    "canonical_master_payload_fingerprint",
    "deterministic_remote_event_id",
    "planner_private_properties",
    "readable_series_link_status",
    "transition_link",
]
