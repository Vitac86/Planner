"""Profile isolation, atomic saves and preferences applied to daily workflows."""
from datetime import datetime
import json

import pytest

from planner_desktop.repositories.fake_task_repository import FakeTaskRepository
from planner_desktop.storage.preferences_store import Preferences, PreferencesError, PreferencesStore
from planner_desktop.viewmodels.calendar_viewmodel import CalendarViewModel
from planner_desktop.viewmodels.preferences_viewmodel import PreferencesViewModel
from planner_desktop.viewmodels.today_viewmodel import TodayViewModel


def test_preferences_survive_restart_without_restoring_an_old_calendar_date(tmp_path):
    path = tmp_path / "isolated-profile" / "preferences.json"
    assert not path.parent.exists()
    vm = PreferencesViewModel(PreferencesStore(path))
    assert not path.parent.exists()  # simply opening preferences never creates files
    assert vm.setShowCompletionStats(False)
    assert vm.setStartupPage("last")
    assert vm.rememberPage(1)
    assert vm.rememberWindowSize(1380, 920)
    assert vm.setWorkHours(8, 20)
    assert vm.setDefaultDurationMinutes(45)
    calendar = CalendarViewModel(FakeTaskRepository(seed=False), preferences=vm,
                                 now_provider=lambda: datetime(2026, 7, 15, 9))
    calendar.setDisplayMode("day")
    calendar.setFilter("active")
    calendar.nextPeriod()
    assert calendar.selectedDateText == "2026-07-16"

    reopened = PreferencesViewModel(PreferencesStore(path))
    assert reopened.showCompletionStats is False
    assert reopened.startupPageIndex == 1
    assert (reopened.windowWidth, reopened.windowHeight) == (1380, 920)
    assert (reopened.workStartHour, reopened.workEndHour) == (8, 20)
    assert reopened.defaultDurationMinutes == 45
    next_day = CalendarViewModel(FakeTaskRepository(seed=False), preferences=reopened,
                                 now_provider=lambda: datetime(2026, 7, 18, 9))
    assert next_day.displayMode == "day"
    assert next_day.filterMode == "active"
    assert next_day.selectedDateText == "2026-07-18"
    assert (next_day.visibleStartHour, next_day.visibleEndHour) == (8, 20)


def test_memory_preferences_never_write_to_the_real_profile(tmp_path, monkeypatch):
    monkeypatch.setenv("PLANNER_DESKTOP_DATA_DIR", str(tmp_path / "real-profile"))
    memory = PreferencesStore()
    memory.update(default_duration_minutes=30)
    assert memory.settings.default_duration_minutes == 30
    assert PreferencesStore().settings.default_duration_minutes == 60
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("changes", [
    {"show_completion_stats": 1}, {"startup_page": "settings"},
    {"last_page_index": 4}, {"calendar_mode": "month"},
    {"calendar_filter": "bad"}, {"work_start_hour": -1},
    {"work_end_hour": 25}, {"work_start_hour": 20, "work_end_hour": 20},
    {"default_duration_minutes": True}, {"default_duration_minutes": 4},
    {"default_duration_minutes": 1441}, {"window_width": 50},
    {"window_height": 10000}, {"window_width": 1240.5}, {"unknown": 1},
])
def test_invalid_preferences_rejected_without_changing_file_or_memory(tmp_path, changes):
    path = tmp_path / "preferences.json"
    store = PreferencesStore(path)
    store.update(default_duration_minutes=30)
    before = path.read_bytes()
    settings = store.settings
    with pytest.raises(PreferencesError):
        store.update(**changes)
    assert store.settings == settings
    assert path.read_bytes() == before


def test_replace_failure_preserves_previous_preferences_and_reports_error(tmp_path, monkeypatch):
    path = tmp_path / "preferences.json"
    vm = PreferencesViewModel(PreferencesStore(path))
    vm.setDefaultDurationMinutes(30)
    before = path.read_bytes()
    changed, errors = [], []
    vm.settingsChanged.connect(lambda: changed.append(True))
    vm.toastError.connect(errors.append)

    def fail(*_):
        raise OSError("disk unavailable")

    monkeypatch.setattr("planner_desktop.storage.preferences_store.os.replace", fail)
    assert vm.setDefaultDurationMinutes(90) is False
    assert vm.defaultDurationMinutes == 30
    assert path.read_bytes() == before
    assert changed == []
    assert "disk unavailable" in vm.errorMessage and errors
    assert list(tmp_path.iterdir()) == [path]


def test_corrupt_file_is_preserved_and_valid_settings_are_recovered(tmp_path):
    path = tmp_path / "preferences.json"
    path.write_text("{broken", encoding="utf-8")
    store = PreferencesStore(path)
    assert store.settings == Preferences()
    assert store.load_error
    assert path.read_text() == "{broken"

    path.write_text(json.dumps({
        "version": 1, "default_duration_minutes": 45,
        "window_width": -10, "show_completion_stats": "false",
        "work_start_hour": 22, "work_end_hour": 8,
    }), encoding="utf-8")
    recovered = PreferencesStore(path)
    assert recovered.settings.default_duration_minutes == 45
    assert recovered.settings.window_width == 1240
    assert recovered.settings.show_completion_stats is True
    assert (recovered.settings.work_start_hour, recovered.settings.work_end_hour) == (6, 23)
    assert recovered.load_error


def test_startup_page_is_explicit_and_last_can_remember_other_pages():
    vm = PreferencesViewModel()
    vm.rememberPage(3)
    assert vm.startupPageIndex == 0
    vm.setStartupPage("calendar")
    assert vm.startupPageIndex == 1
    vm.setStartupPage("last")
    assert vm.startupPageIndex == 3


def test_hours_update_grid_without_navigation_and_bad_save_keeps_selection():
    prefs = PreferencesViewModel()
    calendar = CalendarViewModel(FakeTaskRepository(seed=False), preferences=prefs,
                                 now_provider=lambda: datetime(2026, 7, 15, 9))
    calendar.nextWeek()
    selected = calendar.selectedDateText
    updates = []
    calendar.gridConfigChanged.connect(lambda: updates.append(True))
    assert prefs.setWorkHours(9, 18)
    assert (calendar.visibleStartHour, calendar.visibleEndHour) == (9, 18)
    assert calendar.selectedDateText == selected
    assert updates == [True]
    assert not prefs.setWorkHours(18, 9)
    assert calendar.selectedDateText == selected
    assert (calendar.visibleStartHour, calendar.visibleEndHour) == (9, 18)


def test_restored_work_week_selects_a_visible_weekday():
    prefs = PreferencesViewModel()
    prefs.setCalendarMode("work_week")
    calendar = CalendarViewModel(FakeTaskRepository(seed=False), preferences=prefs,
                                 now_provider=lambda: datetime(2026, 7, 19, 9))
    assert calendar.selectedDateText == "2026-07-17"


def test_quick_add_uses_current_default_but_preserves_explicit_duration_and_all_day():
    prefs = PreferencesViewModel()
    prefs.setDefaultDurationMinutes(30)
    tasks = FakeTaskRepository(seed=False)
    today = TodayViewModel(tasks,
                          default_duration_provider=lambda: prefs.defaultDurationMinutes,
                          now_provider=lambda: datetime(2026, 7, 15, 9))
    assert today.addQuick("Первый отчёт 10:00", 0)
    assert tasks.list_all()[-1].duration_minutes == 30
    prefs.setDefaultDurationMinutes(45)
    assert today.addQuick("Второй отчёт 11:00", 0)
    assert tasks.list_all()[-1].duration_minutes == 45
    assert today.addTaskDetailed("Встреча", "", 0, True, False,
                                 "2026-07-16", "14:00", "90")
    assert tasks.list_all()[-1].duration_minutes == 90
    assert today.addQuick("Отпуск завтра", 0)
    assert tasks.list_all()[-1].is_all_day
    assert tasks.list_all()[-1].duration_minutes is None
    assert today.addQuick("Купить книгу", 0)
    assert tasks.list_all()[-1].start is None
