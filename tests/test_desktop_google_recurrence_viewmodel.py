"""Read-only Settings view-model for discovered Google recurring masters."""
from datetime import datetime, timezone
from types import SimpleNamespace

from planner_desktop.domain.external_series import ExternalCalendarSeries
from planner_desktop.repositories.external_series_repository import (
    InMemoryExternalSeriesRepository,
)
from planner_desktop.repositories.fake_task_repository import FakeTaskRepository
from planner_desktop.sync.fake_calendar_gateway import FakeCalendarGateway
from planner_desktop.usecases.external_series_service import ExternalSeriesService
from planner_desktop.usecases.manual_sync_service import ManualSyncService
from planner_desktop.usecases.task_service import DesktopTaskService
from planner_desktop.viewmodels.settings_viewmodel import SettingsViewModel


NOW = datetime(2026, 7, 15, 8, tzinfo=timezone.utc)


def _connection_status():
    return SimpleNamespace(
        connected=True,
        has_client_secret=True,
        token_path="isolated/token.json",
        client_secret_path="isolated/client_secret.json",
    )


def _series(remote_id, *, supported=True, cancelled=False):
    return ExternalCalendarSeries(
        provider="google", calendar_id="primary", remote_event_id=remote_id,
        title=("Поддерживаемая еженедельная серия" if supported
               else "Сложное правило"),
        description="Private description must not be exposed in the UI row",
        start_kind="all_day", start_value="2026-07-15", end_value="2026-07-16",
        timezone_name="Europe/Moscow",
        recurrence_lines=(("RRULE:FREQ=WEEKLY;BYDAY=MO,WE" if supported else
                           "RRULE:FREQ=MONTHLY;BYDAY=MO;BYSETPOS=1"),),
        support_status="supported" if supported else "unsupported",
        unsupported_reason=None if supported else "BYSETPOS пока не поддерживается.",
        remote_status="cancelled" if cancelled else "confirmed",
        first_seen_at=NOW, last_seen_at=NOW, remote_updated_at=NOW,
        deleted_at=NOW if cancelled else None,
    )


def _vm(*, manual_sync_service=None):
    tasks = FakeTaskRepository(seed=False)
    service = DesktopTaskService(tasks)
    catalog = InMemoryExternalSeriesRepository(tasks)
    catalog.upsert(_series("supported"))
    catalog.upsert(_series("unsupported", supported=False))
    catalog.upsert(_series("cancelled", cancelled=True))
    return SettingsViewModel(
        service,
        manual_sync_service=manual_sync_service,
        connection_checker=_connection_status,
        external_series_service=ExternalSeriesService(catalog),
    )


def test_catalog_properties_are_readable_and_include_textual_state():
    vm = _vm()
    assert vm.externalActiveSeriesCount == 2
    assert vm.externalUnsupportedSeriesCount == 1
    assert vm.externalCancelledSeriesCount == 1
    assert vm.possibleLegacyMasterImportCount == 0
    assert vm.externalSeriesLastRefresh != "—"
    assert "ручной синхронизации" in vm.externalSeriesNote
    rows = vm.externalSeriesRows
    assert len(rows) == 3
    unsupported = next(row for row in rows if row["remoteEventId"] == "unsupported")
    assert unsupported["supportText"] == "Не поддерживается"
    assert "BYSETPOS" in unsupported["unsupportedReason"]
    assert "RRULE:" in unsupported["rawRecurrence"]
    assert "description" not in unsupported
    cancelled = next(row for row in rows if row["cancelled"])
    assert cancelled["stateText"] == "Отменена"


def test_refresh_reads_local_catalog_and_never_builds_or_calls_gateway(tmp_path):
    calls = []

    def gateway_provider():
        calls.append("provider-called")
        return FakeCalendarGateway()

    manual = ManualSyncService(
        FakeTaskRepository(seed=False), None,
        gateway_provider=gateway_provider,
    )
    vm = _vm(manual_sync_service=manual)
    vm.refresh()  # same action Settings performs when the page becomes visible
    _ = vm.externalSeriesRows
    _ = vm.diagnosticsText
    assert calls == []


def test_diagnostics_text_contains_only_catalog_counts_not_private_descriptions():
    vm = _vm()
    text = vm.diagnosticsText
    assert "Google-серий (активных): 2" in text
    assert "Google-серий (неподдерживаемых): 1" in text
    assert "Private description" not in text

