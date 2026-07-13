"""Поведение GoogleCalendarGateway на фейковом сервисе Calendar API:
insert/patch/delete, пагинация pull-а, syncToken, HTTP 410, классификация
ошибок. Без сети, без OAuth; сервис — инъецированный фейк той же формы,
что и googleapiclient (`service.events().insert(...).execute()`).

Здесь же — изоляция путей google_auth: token.json строго в профиле
PlannerDesktop, старый профиль Planner не участвует.
"""
from datetime import date, datetime

import pytest

from planner_desktop.storage.paths import DATA_DIR_ENV_VAR
from planner_desktop.sync import google_auth
from planner_desktop.sync.google_calendar_gateway import GoogleCalendarGateway
from planner_desktop.sync.sync_types import (
    CalendarEvent,
    RetryableGatewayError,
    TerminalGatewayError,
)


# ---- фейковый сервис Calendar API ---------------------------------------------------

class FakeHttpError(Exception):
    """Форма ошибки googleapiclient: .resp.status (duck-typing)."""

    def __init__(self, status, reason="error"):
        super().__init__(f"HTTP {status}: {reason}")

        class _Resp:
            pass

        self.resp = _Resp()
        self.resp.status = status


class _FakeRequest:
    def __init__(self, fn):
        self._fn = fn

    def execute(self):
        return self._fn()


class FakeEventsResource:
    def __init__(self):
        self.insert_calls = []
        self.patch_calls = []
        self.delete_calls = []
        self.list_calls = []
        self.insert_response = {"id": "evt-1", "etag": '"1"', "status": "confirmed"}
        self.patch_response = {"id": "evt-1", "etag": '"2"', "status": "confirmed"}
        self.list_responses = []          # очередь ответов постранично
        self.next_error = None            # исключение на следующий вызов

    def _maybe_fail(self):
        if self.next_error is not None:
            error, self.next_error = self.next_error, None
            raise error

    def insert(self, calendarId, body):
        self.insert_calls.append({"calendarId": calendarId, "body": body})
        return _FakeRequest(lambda: (self._maybe_fail(), self.insert_response)[1])

    def patch(self, calendarId, eventId, body):
        self.patch_calls.append(
            {"calendarId": calendarId, "eventId": eventId, "body": body})
        return _FakeRequest(lambda: (self._maybe_fail(), self.patch_response)[1])

    def delete(self, calendarId, eventId):
        self.delete_calls.append({"calendarId": calendarId, "eventId": eventId})
        return _FakeRequest(lambda: (self._maybe_fail(), {})[1])

    def list(self, **params):
        self.list_calls.append(params)

        def _run():
            self._maybe_fail()
            if not self.list_responses:
                return {"items": [], "nextSyncToken": "tok-final"}
            return self.list_responses.pop(0)

        return _FakeRequest(_run)


class FakeGoogleService:
    def __init__(self):
        self.events_resource = FakeEventsResource()

    def events(self):
        return self.events_resource


@pytest.fixture()
def service():
    return FakeGoogleService()


@pytest.fixture()
def gateway(service):
    return GoogleCalendarGateway(service)


# ---- insert / patch / delete ---------------------------------------------------------

def test_insert_sends_body_and_returns_linked_event(gateway, service):
    created = gateway.insert_event(CalendarEvent(
        summary="Встреча", start=datetime(2026, 7, 14, 10, 0),
        end=datetime(2026, 7, 14, 11, 0), is_all_day=False,
    ))
    call = service.events_resource.insert_calls[0]
    assert call["calendarId"] == "primary"
    assert "dateTime" in call["body"]["start"]
    assert created.id == "evt-1"
    assert created.etag == '"1"'


def test_patch_sends_event_id_and_returns_new_etag(gateway, service):
    updated = gateway.patch_event("evt-1", {"summary": "Новое"})
    call = service.events_resource.patch_calls[0]
    assert call["eventId"] == "evt-1"
    assert call["body"] == {"summary": "Новое"}
    assert updated.etag == '"2"'


def test_delete_is_idempotent_on_404_and_410(gateway, service):
    for status in (404, 410):
        service.events_resource.next_error = FakeHttpError(status)
        gateway.delete_event("evt-gone")  # не бросает
    assert len(service.events_resource.delete_calls) == 2


def test_delete_other_errors_are_classified(gateway, service):
    service.events_resource.next_error = FakeHttpError(500)
    with pytest.raises(RetryableGatewayError):
        gateway.delete_event("evt-1")


# ---- классификация ошибок --------------------------------------------------------------

@pytest.mark.parametrize("status", [408, 429, 500, 502, 503, 504])
def test_retryable_statuses(gateway, service, status):
    service.events_resource.next_error = FakeHttpError(status)
    with pytest.raises(RetryableGatewayError):
        gateway.insert_event(CalendarEvent(
            summary="х", start=datetime(2026, 7, 14, 10, 0),
            end=datetime(2026, 7, 14, 11, 0)))


@pytest.mark.parametrize("status", [400, 403, 404, 409])
def test_terminal_statuses(gateway, service, status):
    service.events_resource.next_error = FakeHttpError(status)
    with pytest.raises(TerminalGatewayError):
        gateway.patch_event("evt-1", {"summary": "х"})


def test_rate_limit_403_is_retryable(gateway, service):
    service.events_resource.next_error = FakeHttpError(403, "rateLimitExceeded")
    with pytest.raises(RetryableGatewayError):
        gateway.patch_event("evt-1", {"summary": "х"})


def test_network_error_without_status_is_retryable(gateway, service):
    service.events_resource.next_error = ConnectionError("сеть недоступна")
    with pytest.raises(RetryableGatewayError):
        gateway.insert_event(CalendarEvent(
            summary="х", start=datetime(2026, 7, 14, 10, 0),
            end=datetime(2026, 7, 14, 11, 0)))


# ---- pull: list_changes ------------------------------------------------------------------

def test_list_changes_paginates_and_returns_sync_token(gateway, service):
    service.events_resource.list_responses = [
        {
            "items": [{"id": "a", "status": "confirmed",
                       "start": {"date": "2026-07-14"},
                       "end": {"date": "2026-07-15"}}],
            "nextPageToken": "page-2",
        },
        {
            "items": [{"id": "b", "status": "cancelled"}],
            "nextSyncToken": "tok-1",
        },
    ]
    batch = gateway.list_changes(None)

    assert [e.id for e in batch.events] == ["a", "b"]
    assert batch.events[1].is_cancelled is True  # отменённые приходят тоже
    assert batch.next_cursor == "tok-1"

    calls = service.events_resource.list_calls
    assert len(calls) == 2
    assert calls[0]["showDeleted"] is True
    assert calls[0]["singleEvents"] is False
    assert "syncToken" not in calls[0]          # первый pull — без токена
    assert calls[1]["pageToken"] == "page-2"


def test_list_changes_passes_cursor_as_sync_token(gateway, service):
    service.events_resource.list_responses = [
        {"items": [], "nextSyncToken": "tok-2"},
    ]
    batch = gateway.list_changes("tok-1")
    assert service.events_resource.list_calls[0]["syncToken"] == "tok-1"
    assert batch.next_cursor == "tok-2"


def test_expired_sync_token_410_triggers_full_resync(gateway, service):
    """Протухший syncToken (HTTP 410) детерминированно превращается в один
    полный пересбор без токена в том же вызове."""
    service.events_resource.next_error = FakeHttpError(410, "Sync token is no longer valid")
    service.events_resource.list_responses = [
        {"items": [{"id": "c", "status": "confirmed",
                    "start": {"dateTime": "2026-07-14T10:00:00Z"},
                    "end": {"dateTime": "2026-07-14T11:00:00Z"}}],
         "nextSyncToken": "tok-fresh"},
    ]
    batch = gateway.list_changes("tok-expired")

    calls = service.events_resource.list_calls
    assert calls[0].get("syncToken") == "tok-expired"   # первая попытка
    assert "syncToken" not in calls[1]                   # пересбор без токена
    assert [e.id for e in batch.events] == ["c"]
    assert batch.next_cursor == "tok-fresh"


def test_410_without_sync_token_is_ordinary_error(gateway, service):
    service.events_resource.next_error = FakeHttpError(410)
    with pytest.raises(TerminalGatewayError):
        gateway.list_changes(None)


def test_missing_next_sync_token_keeps_old_cursor(gateway, service):
    service.events_resource.list_responses = [{"items": []}]
    batch = gateway.list_changes("tok-old")
    assert batch.next_cursor == "tok-old"


# ---- изоляция путей google_auth ------------------------------------------------------------

def test_token_path_is_inside_isolated_profile(monkeypatch, tmp_path):
    monkeypatch.setenv(DATA_DIR_ENV_VAR, str(tmp_path))
    token_path = google_auth.get_desktop_token_path()
    secret_path = google_auth.get_desktop_client_secret_path()
    assert token_path == tmp_path / "token.json"
    assert secret_path == tmp_path / "secrets" / "client_secret.json"


def test_default_token_path_is_planner_desktop_not_old_planner(monkeypatch):
    monkeypatch.delenv(DATA_DIR_ENV_VAR, raising=False)
    token_path = google_auth.get_desktop_token_path()
    assert "PlannerDesktop" in str(token_path)
    # это НЕ старый профиль <...>/Planner/token.json
    assert token_path.parent.name != "Planner"


def test_connection_status_reads_only_isolated_files(monkeypatch, tmp_path):
    monkeypatch.setenv(DATA_DIR_ENV_VAR, str(tmp_path))
    status = google_auth.get_connection_status()
    assert status.connected is False
    assert status.has_client_secret is False

    (tmp_path / "secrets").mkdir()
    (tmp_path / "secrets" / "client_secret.json").write_text("{}", encoding="utf-8")
    (tmp_path / "token.json").write_text("{}", encoding="utf-8")
    status = google_auth.get_connection_status()
    assert status.connected is True
    assert status.has_client_secret is True


def test_build_real_gateway_without_token_raises_friendly_error(monkeypatch, tmp_path):
    monkeypatch.setenv(DATA_DIR_ENV_VAR, str(tmp_path))
    with pytest.raises(RuntimeError) as excinfo:
        google_auth.build_real_gateway()
    assert "не подключён" in str(excinfo.value)
