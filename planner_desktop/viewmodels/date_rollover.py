"""Local date changes, including midnight while the application was asleep."""
from __future__ import annotations

from datetime import date
from typing import Callable

from PySide6.QtCore import QObject, QTimer, Qt, Signal, Slot
from PySide6.QtGui import QGuiApplication


class DateRolloverMonitor(QObject):
    """Notify date-dependent views without navigating or starting a sync.

    Checking the wall clock each minute also catches a resumed machine whose
    application remained active. Activation handles returning to the app before
    the next timer tick. A date is notified once, even after several days asleep.
    """

    dayChanged = Signal()

    def __init__(
        self,
        parent: QObject | None = None,
        *,
        today_provider: Callable[[], date] = date.today,
    ) -> None:
        super().__init__(parent)
        self._today = today_provider
        self._last_day = self._today()
        self._timer = QTimer(self)
        self._timer.setInterval(60_000)
        self._timer.timeout.connect(self.checkDate)
        self._timer.start()
        application = QGuiApplication.instance()
        if isinstance(application, QGuiApplication):
            application.applicationStateChanged.connect(
                self._on_application_state_changed
            )

    @Slot()
    def checkDate(self) -> None:
        current_day = self._today()
        if current_day != self._last_day:
            self._last_day = current_day
            self.dayChanged.emit()

    def _on_application_state_changed(self, state: Qt.ApplicationState) -> None:
        if state == Qt.ApplicationState.ApplicationActive:
            self.checkDate()
