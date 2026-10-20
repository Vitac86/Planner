"""QML preferences contract, backed by an explicitly selected local profile."""
from __future__ import annotations

from PySide6.QtCore import QObject, Property, Signal, Slot

from planner_desktop.storage.preferences_store import PreferencesError, PreferencesStore


class PreferencesViewModel(QObject):
    settingsChanged = Signal()
    errorChanged = Signal()
    toastError = Signal(str)

    def __init__(self, store: PreferencesStore | None = None, parent=None) -> None:
        super().__init__(parent)
        self._store = store if store is not None else PreferencesStore()
        self._error = self._store.load_error

    @Property(bool, notify=settingsChanged)
    def showCompletionStats(self) -> bool:
        return self._store.settings.show_completion_stats

    @Property(str, notify=settingsChanged)
    def startupPage(self) -> str:
        return self._store.settings.startup_page

    @Property(int, notify=settingsChanged)
    def startupPageIndex(self) -> int:
        if self.startupPage == "last":
            return self.lastPageIndex
        return 1 if self.startupPage == "calendar" else 0

    @Property(int, notify=settingsChanged)
    def lastPageIndex(self) -> int:
        return self._store.settings.last_page_index

    @Property(str, notify=settingsChanged)
    def calendarMode(self) -> str:
        return self._store.settings.calendar_mode

    @Property(str, notify=settingsChanged)
    def calendarFilter(self) -> str:
        return self._store.settings.calendar_filter

    @Property(int, notify=settingsChanged)
    def workStartHour(self) -> int:
        return self._store.settings.work_start_hour

    @Property(int, notify=settingsChanged)
    def workEndHour(self) -> int:
        return self._store.settings.work_end_hour

    @Property(int, notify=settingsChanged)
    def defaultDurationMinutes(self) -> int:
        return self._store.settings.default_duration_minutes

    @Property(int, notify=settingsChanged)
    def windowWidth(self) -> int:
        return self._store.settings.window_width

    @Property(int, notify=settingsChanged)
    def windowHeight(self) -> int:
        return self._store.settings.window_height

    @Property(str, notify=errorChanged)
    def errorMessage(self) -> str:
        return self._error

    def _set_error(self, message: str) -> None:
        if self._error != message:
            self._error = message
            self.errorChanged.emit()

    def _save(self, **changes) -> bool:
        try:
            changed = self._store.update(**changes)
        except PreferencesError as exc:
            self._set_error(str(exc))
            self.toastError.emit(str(exc))
            return False
        self._set_error("")
        if changed:
            self.settingsChanged.emit()
        return True

    @Slot(bool, result=bool)
    def setShowCompletionStats(self, enabled: bool) -> bool:
        return self._save(show_completion_stats=enabled)

    @Slot(str, result=bool)
    def setStartupPage(self, page: str) -> bool:
        return self._save(startup_page=page)

    @Slot(int, result=bool)
    def rememberPage(self, index: int) -> bool:
        return self._save(last_page_index=index)

    @Slot(str, result=bool)
    def setCalendarMode(self, mode: str) -> bool:
        return self._save(calendar_mode=mode)

    @Slot(str, result=bool)
    def setCalendarFilter(self, mode: str) -> bool:
        return self._save(calendar_filter=mode)

    @Slot(int, int, result=bool)
    def setWorkHours(self, start: int, end: int) -> bool:
        return self._save(work_start_hour=start, work_end_hour=end)

    @Slot(int, result=bool)
    def setDefaultDurationMinutes(self, minutes: int) -> bool:
        return self._save(default_duration_minutes=minutes)

    @Slot(int, int, result=bool)
    def rememberWindowSize(self, width: int, height: int) -> bool:
        return self._save(window_width=width, window_height=height)

    @Slot()
    def clearError(self) -> None:
        self._set_error("")
