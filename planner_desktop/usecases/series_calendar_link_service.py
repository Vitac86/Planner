"""Explicit, local-only use cases for linking TaskSeries to Google masters."""
from __future__ import annotations

from copy import deepcopy
from datetime import date
from typing import Callable, Optional

from planner_desktop.domain.google_recurrence import recurrence_round_trip_support
from planner_desktop.domain.recurrence import TaskSeries, is_valid_timezone
from planner_desktop.domain.series_calendar_link import (
    DEFAULT_GOOGLE_CALENDAR_ID,
    GOOGLE_PROVIDER,
    SeriesCalendarLink,
    SeriesConnectValidationIssue,
    SeriesConnectValidationResult,
    SeriesLinkActionResult,
    SeriesLinkStatus,
    deterministic_remote_event_id,
)
from planner_desktop.sync.calendar_series_mapper import (
    master_event_to_owned_payload,
    master_payload_hash,
    series_to_master_event,
)


SERIES_NOT_FOUND = "Серия не найдена или уже удалена."
SERIES_ALREADY_LINKED = "Серия уже связана с Google Calendar."
SERIES_DELETE_REQUIRES_INTENT = (
    "Для связанной серии выберите явно: отключить связь, удалить только "
    "серию Google или удалить локальную и Google-серию."
)


def _slot_date(task) -> Optional[date]:
    key = getattr(task, "occurrence_key", None)
    if not key:
        return None
    try:
        return date.fromisoformat(str(key)[:10])
    except ValueError:
        return None


def finalize_local_series_delete(series_repository, task_repository, series_uid: str) -> bool:
    """Tombstone definition after remote delete; keep completed/history rows."""
    series = series_repository.get_by_uid(series_uid)
    if series is None or series.is_deleted:
        return True
    removed = []
    try:
        for task in task_repository.list_by_series(series_uid):
            if task.is_deleted or task.completed or task.is_series_exception:
                continue
            snapshot = deepcopy(task)
            if task_repository.hard_delete_by_uid(task.uid):
                removed.append(snapshot)
        if not series_repository.delete(series_uid):
            raise RuntimeError("Локальная серия уже удалена.")
    except Exception:
        for task in removed:
            try:
                current = task_repository.get_by_uid(task.uid)
                if current is None:
                    task_repository.add(task)
            except Exception:
                pass
        raise
    return True


class SeriesCalendarLinkService:
    """No network calls: mutations end at schema-v8 link/queue rows."""

    def __init__(
        self,
        series_repository,
        task_repository,
        store,
        *,
        provider: str = GOOGLE_PROVIDER,
        calendar_id: str = DEFAULT_GOOGLE_CALENDAR_ID,
        today_provider: Callable[[], date] = date.today,
    ) -> None:
        self.series_repository = series_repository
        self.task_repository = task_repository
        self.store = store
        self.provider = provider
        self.calendar_id = calendar_id
        self._today = today_provider

    def validate_connection(self, series_uid: str) -> SeriesConnectValidationResult:
        issues: list[SeriesConnectValidationIssue] = []
        series = self.series_repository.get_by_uid(series_uid)
        if series is None or series.is_deleted or not series.active:
            issues.append(SeriesConnectValidationIssue("series_inactive", SERIES_NOT_FOUND))
            return SeriesConnectValidationResult(series_uid, tuple(issues))

        existing = self.store.get_link(series_uid)
        if existing is not None:
            code = (
                "link_conflict"
                if existing.link_status is SeriesLinkStatus.CONFLICT
                else "already_linked"
            )
            message = (
                "У серии есть неразрешённый конфликт Google Calendar."
                if code == "link_conflict"
                else SERIES_ALREADY_LINKED
            )
            issues.append(SeriesConnectValidationIssue(code, message))

        schedule = series.schedule
        if not is_valid_timezone(schedule.timezone_name):
            issues.append(SeriesConnectValidationIssue(
                "invalid_timezone",
                "Часовой пояс серии должен быть действительным IANA timezone.",
            ))
        if not schedule.all_day:
            if schedule.local_time is None:
                issues.append(SeriesConnectValidationIssue(
                    "missing_time", "У серии со временем не задано время начала."
                ))
            if schedule.duration_minutes is None or schedule.duration_minutes <= 0:
                issues.append(SeriesConnectValidationIssue(
                    "invalid_duration", "Длительность серии должна быть больше нуля."
                ))

        try:
            round_trip = recurrence_round_trip_support(
                series.rule, schedule=series.schedule
            )
            if not round_trip.supported or round_trip.planner_rule != series.rule:
                issues.append(SeriesConnectValidationIssue(
                    "unsupported_recurrence",
                    round_trip.readable_reason
                    or "Правило нельзя без потерь представить в Google Calendar.",
                ))
        except (TypeError, ValueError) as exc:
            issues.append(SeriesConnectValidationIssue(
                "unsupported_recurrence",
                f"Правило нельзя без потерь представить в Google Calendar: {exc}",
            ))

        today = self._today()
        for task in self.task_repository.list_by_series(series_uid):
            if any((
                task.google_calendar_event_id,
                task.google_calendar_recurring_event_id,
                task.google_calendar_original_start,
            )):
                issues.append(SeriesConnectValidationIssue(
                    "occurrence_has_google_id",
                    "У локального экземпляра уже есть Google-идентификатор.",
                ))
                break

        for task in self.task_repository.list_by_series(series_uid):
            if task.completed:
                continue  # completion/history remains local and is safe
            slot = _slot_date(task)
            if slot is None or slot < today:
                continue
            if task.is_deleted:
                issues.append(SeriesConnectValidationIssue(
                    "future_tombstone",
                    "В серии есть удалённый будущий слот; EXDATE появится в Phase 3.2B3.",
                ))
                break
            if task.is_series_exception:
                issues.append(SeriesConnectValidationIssue(
                    "future_exception",
                    "В серии есть будущие изменения отдельных экземпляров; "
                    "их синхронизация появится в Phase 3.2B3.",
                ))
                break

        # Stable ordering and no duplicate code/message pairs make UI output
        # deterministic even if more than one occurrence exposes the same issue.
        unique = tuple(dict.fromkeys(issues))
        return SeriesConnectValidationResult(series_uid, unique)

    def connect_to_google(self, series_uid: str) -> SeriesLinkActionResult:
        validation = self.validate_connection(series_uid)
        if not validation.ok:
            # Duplicate rapid calls are an idempotent success from an API
            # perspective but remain clearly "already linked" in preflight.
            link = self.store.get_link(series_uid)
            return SeriesLinkActionResult(
                ok=False,
                link=link,
                validation=validation,
                error="\n".join(validation.errors),
            )
        series = self.series_repository.get_by_uid(series_uid)
        if series is None:  # defensive race after validation
            return SeriesLinkActionResult(ok=False, error=SERIES_NOT_FOUND)
        event = series_to_master_event(series)
        payload = master_event_to_owned_payload(event)
        payload_hash = master_payload_hash(event)
        link = SeriesCalendarLink(
            series_uid=series.uid,
            provider=self.provider,
            calendar_id=self.calendar_id,
            remote_event_id=deterministic_remote_event_id(series.uid),
        )
        try:
            stored = self.store.create_pending_link(
                link,
                desired_revision=series.revision,
                desired_payload_hash=payload_hash,
                payload=payload,
            )
        except Exception as exc:
            return SeriesLinkActionResult(
                ok=False, validation=validation, error=f"Не удалось создать связь: {exc}"
            )
        return SeriesLinkActionResult(
            ok=True, link=stored, validation=validation, changed=True
        )

    def on_series_updated(self, before: TaskSeries, after: TaskSeries) -> bool:
        """Queue one latest UPDATE only for remote-owned master fields."""
        if self.store.get_link(after.uid) is None:
            return False
        before_event = series_to_master_event(before)
        after_event = series_to_master_event(after)
        before_hash = master_payload_hash(before_event)
        after_hash = master_payload_hash(after_event)
        if before_hash == after_hash:
            return False
        return self.store.enqueue_update(
            after.uid,
            desired_revision=after.revision,
            desired_payload_hash=after_hash,
            payload=master_event_to_owned_payload(after_event),
        )

    def get_link(self, series_uid: str) -> Optional[SeriesCalendarLink]:
        return self.store.get_link(series_uid)

    def list_links(self, *, include_detached: bool = True):
        return self.store.list_links(include_detached=include_detached)

    def is_linked(self, series_uid: Optional[str]) -> bool:
        return bool(series_uid and self.store.get_link(series_uid) is not None)

    def disconnect_keep_remote(self, series_uid: str) -> SeriesLinkActionResult:
        changed = self.store.disconnect_keep_remote(series_uid)
        return SeriesLinkActionResult(
            ok=changed,
            link=self.store.get_link(series_uid, include_detached=True),
            changed=changed,
            error="" if changed else "Активная связь серии не найдена.",
        )

    def request_remote_delete_keep_local(
        self, series_uid: str
    ) -> SeriesLinkActionResult:
        outcome = self.store.enqueue_delete(series_uid)
        ok = outcome not in ("missing",)
        return SeriesLinkActionResult(
            ok=ok,
            link=self.store.get_link(series_uid, include_detached=True),
            changed=outcome in ("queued", "cancelled_create"),
            error="" if ok else "Активная связь серии не найдена.",
        )

    def request_delete_local_and_remote(
        self, series_uid: str
    ) -> SeriesLinkActionResult:
        if self.series_repository.get_by_uid(series_uid) is None:
            return SeriesLinkActionResult(ok=False, error=SERIES_NOT_FOUND)
        outcome = self.store.enqueue_delete(
            series_uid, delete_local_after_remote=True
        )
        if outcome == "missing":
            return SeriesLinkActionResult(ok=False, error="Активная связь серии не найдена.")
        if outcome == "cancelled_create":
            try:
                finalize_local_series_delete(
                    self.series_repository, self.task_repository, series_uid
                )
            except Exception as exc:
                return SeriesLinkActionResult(
                    ok=False,
                    link=self.store.get_link(series_uid, include_detached=True),
                    error=f"Не удалось удалить локальную серию: {exc}",
                )
        return SeriesLinkActionResult(
            ok=True,
            link=self.store.get_link(series_uid, include_detached=True),
            changed=outcome in ("queued", "cancelled_create"),
        )

    def cancel_unpushed_delete(self, series_uid: str) -> SeriesLinkActionResult:
        changed = self.store.cancel_unpushed_delete(series_uid)
        return SeriesLinkActionResult(
            ok=changed,
            link=self.store.get_link(series_uid),
            changed=changed,
            error="" if changed else "Удаление уже отправлялось или не ожидает отправки.",
        )

    def retry_terminal_operation(self, op_id: int) -> bool:
        return self.store.retry_terminal_operation(op_id)


__all__ = [
    "SERIES_ALREADY_LINKED",
    "SERIES_DELETE_REQUIRES_INTENT",
    "SERIES_NOT_FOUND",
    "SeriesCalendarLinkService",
    "finalize_local_series_delete",
]
