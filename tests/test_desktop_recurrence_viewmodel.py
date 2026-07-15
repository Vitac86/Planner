from datetime import date, datetime

from planner_desktop.domain.recurrence import RecurrenceFrequency, RecurrenceRule
from planner_desktop.domain.templates import (
    SCHEDULE_MODE_ALL_DAY,
    TEMPLATE_KIND_RECURRING,
    TaskTemplate,
)
from planner_desktop.repositories.fake_task_repository import FakeTaskRepository
from planner_desktop.repositories.series_repository import InMemorySeriesRepository
from planner_desktop.repositories.template_repository import InMemoryTemplateRepository
from planner_desktop.usecases.occurrence_materializer import OccurrenceMaterializer
from planner_desktop.usecases.recurrence_service import RecurrenceService
from planner_desktop.usecases.task_service import DesktopTaskService
from planner_desktop.usecases.template_service import TemplateService
from planner_desktop.viewmodels.today_viewmodel import TodayViewModel


TODAY = datetime(2026, 7, 15, 8)


def payload(**changes):
    data = {
        "title": "Daily review",
        "notes": "local series",
        "priority": 2,
        "scheduled": True,
        "isAllDay": False,
        "dateText": "2026-07-15",
        "timeText": "09:00",
        "durationText": "30",
        "completed": False,
        "tagIds": [],
        "rule": {
            "frequency": "daily",
            "interval": 1,
            "weekdays": [],
            "monthDay": 0,
            "yearlyMonth": 0,
            "yearlyDay": 0,
            "endMode": "never",
            "untilDate": "",
            "occurrenceCount": 0,
        },
    }
    data.update(changes)
    return data


def make_vm():
    tasks = FakeTaskRepository(seed=False)
    service = DesktopTaskService(tasks)
    recurrence = RecurrenceService(InMemorySeriesRepository(), tasks)
    templates = TemplateService(InMemoryTemplateRepository())
    materializer = OccurrenceMaterializer(recurrence)
    service.recurrence_service = recurrence
    service.template_service = templates
    service.materializer = materializer
    vm = TodayViewModel(service=service, now_provider=lambda: TODAY)
    return vm, service, recurrence, templates, tasks


def test_create_series_materializes_today_and_exposes_editor_identity():
    vm, _service, recurrence, _templates, tasks = make_vm()
    assert vm.saveEditorAsSeries(payload())

    series = recurrence.list_series()[0]
    occurrences = tasks.list_by_series(series.uid)
    assert occurrences
    today_row = next(row for row in vm.todayTasks if row["seriesUid"] == series.uid)
    assert today_row["isSeriesOccurrence"]
    data = vm.editorDataFor(today_row["uid"])
    assert data["seriesUid"] == series.uid
    assert data["seriesSummary"]
    assert data["rule"]["frequency"] == "daily"


def test_scoped_occurrence_edit_and_split_route_through_viewmodel():
    vm, _service, recurrence, _templates, tasks = make_vm()
    assert vm.saveEditorAsSeries(payload())
    original = recurrence.list_series()[0]
    rows = tasks.list_by_series(original.uid)

    only_this = payload(title="One-off review", timeText="10:00")
    assert vm.saveOccurrenceScoped(rows[0].uid, "this_occurrence", only_this)
    assert tasks.get_by_uid(rows[0].uid).is_series_exception

    future = next(row for row in tasks.list_by_series(original.uid)
                  if row.occurrence_key.startswith("2026-07-16"))
    split_payload = payload(
        title="Weekly review",
        dateText="2026-07-16",
        rule={**payload()["rule"], "frequency": "weekly", "weekdays": [3]},
    )
    assert vm.saveOccurrenceScoped(future.uid, "this_and_future", split_payload)
    assert len(recurrence.list_series()) == 2
    assert tasks.get_by_uid(future.uid).series_uid != original.uid


def test_duplicate_occurrence_becomes_independent_ordinary_task():
    vm, service, recurrence, _templates, tasks = make_vm()
    assert vm.saveEditorAsSeries(payload())
    occurrence = tasks.list_by_series(recurrence.list_series()[0].uid)[0]

    assert vm.duplicateTask(occurrence.uid)
    duplicate = service.get_task(vm.selectedUid)
    assert duplicate.uid != occurrence.uid
    assert duplicate.series_uid is None
    assert duplicate.occurrence_key is None
    assert duplicate.google_calendar_event_id is None


def test_template_prefill_is_non_persisting_and_change_signal_refreshes_list():
    vm, _service, _recurrence, templates, tasks = make_vm()
    changed = []
    vm.templatesChanged.connect(lambda: changed.append(True))
    result = templates.create_template(TaskTemplate(
        name="Morning series",
        kind=TEMPLATE_KIND_RECURRING,
        title="Morning focus",
        schedule_mode=SCHEDULE_MODE_ALL_DAY,
        rule=RecurrenceRule(RecurrenceFrequency.DAILY),
    ))
    assert result.ok and changed
    assert vm.taskTemplates[0]["isRecurring"]

    before = len(tasks.list_all())
    prefill = vm.templatePrefill(result.template.uid)
    assert prefill["uid"] == ""
    assert prefill["recurring"] is True
    assert prefill["dateText"] == date(2026, 7, 15).isoformat()
    assert len(tasks.list_all()) == before
