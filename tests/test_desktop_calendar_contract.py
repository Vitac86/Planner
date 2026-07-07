"""Контракт будущей синхронизации с Google Calendar (planner_desktop).

Реализации нет — проверяем сам контракт: доменная модель несёт все поля
для синка, интерфейсы объявляют нужные методы, а правила маппинга
(timed vs all-day, эксклюзивный конец, повторяющиеся экземпляры)
зафиксированы в документации модулей.
"""
import dataclasses

from planner_desktop.domain.task import Task
from planner_desktop.sync import calendar_contract
from planner_desktop.sync.calendar_contract import (
    CalendarEventMapper,
    CalendarSyncGateway,
    RemoteEventChange,
)


# ---- поля синхронизации в доменной модели -----------------------------------

def test_task_has_calendar_sync_fields():
    field_names = {f.name for f in dataclasses.fields(Task)}
    required = {
        "id",
        "uid",
        "title",
        "notes",
        "start",
        "end",
        "duration_minutes",
        "is_all_day",
        "priority",
        "completed",
        "google_calendar_event_id",
        "google_calendar_etag",
        "google_calendar_recurring_event_id",
        "google_calendar_original_start",
        "updated_at",
        "deleted_at",
    }
    missing = required - field_names
    assert not missing, f"У Task нет полей: {missing}"


def test_new_task_is_not_linked_to_google():
    task = Task(title="X")
    assert task.google_calendar_event_id is None
    assert task.google_calendar_etag is None
    assert task.google_calendar_recurring_event_id is None
    assert task.google_calendar_original_start is None


def test_task_delete_is_tombstone_not_removal():
    task = Task(title="X")
    assert task.is_deleted is False
    task.mark_deleted()
    assert task.is_deleted is True
    assert task.deleted_at is not None  # запись сохраняется для push delete


# ---- интерфейс шлюза ---------------------------------------------------------

def test_gateway_declares_pull_and_push_methods():
    for method in ("pull_changed_events", "push_task_create",
                   "push_task_update", "push_task_delete"):
        assert hasattr(CalendarSyncGateway, method), method


def test_mapper_declares_both_directions():
    assert hasattr(CalendarEventMapper, "task_to_google_event_payload")
    assert hasattr(CalendarEventMapper, "google_event_to_task")


def test_remote_event_change_carries_cancellation_and_etag():
    change = RemoteEventChange(event_id="abc", status="cancelled",
                               etag='"e1"')
    assert change.status == "cancelled"
    assert change.etag == '"e1"'
    assert change.payload == {}


def test_fake_gateway_can_satisfy_protocol():
    """Контракт реализуем фейком без сети — так его будет гонять движок."""

    class FakeGateway:
        def pull_changed_events(self):
            return [RemoteEventChange(event_id="evt1")]

        def push_task_create(self, task):
            return RemoteEventChange(event_id="evt-new", etag='"1"')

        def push_task_update(self, task):
            return RemoteEventChange(event_id="evt-new", etag='"2"')

        def push_task_delete(self, task):
            return None

    gateway: CalendarSyncGateway = FakeGateway()
    assert isinstance(gateway, CalendarSyncGateway)
    assert gateway.pull_changed_events()[0].event_id == "evt1"


# ---- правила маппинга зафиксированы в документации ---------------------------

def test_module_documents_timed_vs_all_day_mapping():
    doc = calendar_contract.__doc__
    assert "dateTime" in doc            # timed -> dateTime/dateTime
    assert "date" in doc                # all-day -> date/date
    assert "ЭКСКЛЮЗИВ" in doc.upper()   # конец all-day — эксклюзивный


def test_mapper_docstring_documents_payload_shapes():
    doc = CalendarEventMapper.task_to_google_event_payload.__doc__
    assert "dateTime" in doc
    assert '"date"' in doc
    assert "эксклюзив" in doc.lower()


def test_recurring_instances_must_not_be_blindly_patched():
    doc = calendar_contract.__doc__
    assert "recurringEventId" in doc
    assert "originalStartTime" in doc
    # правило: экземпляр повторяющегося события не патчится по start/end вслепую
    assert "вслепую" in doc or "слепо" in doc


def test_remote_vs_local_change_semantics_documented():
    assert "телефон" in calendar_contract.__doc__  # правки с телефона = remote
    doc_pull = CalendarSyncGateway.pull_changed_events.__doc__
    assert "телефон" in doc_pull
