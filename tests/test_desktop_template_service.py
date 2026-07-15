from planner_desktop.domain.recurrence import RecurrenceFrequency, RecurrenceRule
from planner_desktop.domain.templates import (
    BAD_TEMPLATE_TIME_ERROR,
    RECURRING_RULE_REQUIRED_ERROR,
    RECURRING_SCHEDULE_REQUIRED_ERROR,
    TaskTemplate,
)
from planner_desktop.repositories.template_repository import InMemoryTemplateRepository
from planner_desktop.usecases.template_service import TemplateService


def test_recurring_template_requires_schedule_rule_and_valid_time():
    service = TemplateService(InMemoryTemplateRepository())
    missing = service.create_template(TaskTemplate(
        name="Broken", kind="recurring", title="Task", schedule_mode="none"
    ))
    assert RECURRING_RULE_REQUIRED_ERROR in missing.errors
    assert RECURRING_SCHEDULE_REQUIRED_ERROR in missing.errors

    bad_time = service.create_template(TaskTemplate(
        name="Bad time",
        kind="recurring",
        title="Task",
        schedule_mode="timed",
        time_text="25:90",
        rule=RecurrenceRule(RecurrenceFrequency.DAILY),
    ))
    assert BAD_TEMPLATE_TIME_ERROR in bad_time.errors


def test_prefill_is_non_persisting_and_contains_no_google_metadata():
    repository = InMemoryTemplateRepository()
    service = TemplateService(repository)
    created = service.create_template(TaskTemplate(
        name="Ordinary",
        title="Independent task",
        notes="notes",
        priority=3,
        schedule_mode="timed",
        time_text="10:30",
        duration_minutes=45,
    )).template
    before = repository.count_active()
    payload = service.editor_prefill(created.uid)
    assert repository.count_active() == before
    assert payload["uid"] == ""
    assert payload["title"] == "Independent task"
    assert not any("google" in key.lower() or "etag" in key.lower() for key in payload)


def test_duplicate_edit_and_delete_do_not_mutate_prior_prefill_snapshot():
    repository = InMemoryTemplateRepository()
    service = TemplateService(repository)
    source = service.create_template(TaskTemplate(
        name="Recurring",
        kind="recurring",
        title="Before",
        schedule_mode="allday",
        rule=RecurrenceRule(RecurrenceFrequency.DAILY),
    )).template
    snapshot = dict(service.editor_prefill(source.uid))
    duplicate = service.duplicate_template(source.uid)
    assert duplicate.ok and duplicate.template.uid != source.uid

    updated = service.update_template(source.uid, TaskTemplate(
        name="Renamed",
        kind="recurring",
        title="After",
        schedule_mode="allday",
        rule=RecurrenceRule(RecurrenceFrequency.WEEKLY, weekdays=(1,)),
    ))
    assert updated.ok
    assert snapshot["title"] == "Before"
    assert service.delete_template(source.uid)
    assert service.get_template(source.uid) is None
    assert service.get_template(duplicate.template.uid) is not None


def test_change_listener_fires_once_per_successful_mutation():
    service = TemplateService(InMemoryTemplateRepository())
    calls = []
    service.add_change_listener(lambda: calls.append("changed"))
    created = service.create_template(TaskTemplate(name="One", title="Task"))
    service.update_template(created.template.uid, TaskTemplate(
        name="Two", title="Task"
    ))
    service.delete_template(created.template.uid)
    assert calls == ["changed", "changed", "changed"]

