"""GUI-thread scheduling; all network work stays in the existing executor.

Construction does not start timers. MainWindow explicitly starts scheduling
after loading QML. A single timer covers debounce, periodic pull and backoff.
"""
from __future__ import annotations

from PySide6.QtCore import QObject, QTimer, Signal


class AutoSyncScheduler(QObject):
    syncRequested = Signal()
    stateChanged = Signal()

    CHANGE_DELAY_MS = 1000
    POLL_INTERVAL_MS = 60000
    RETRY_INITIAL_MS = 5000
    RETRY_MAX_MS = 300000

    def __init__(self, parent=None, *, timer=None):
        super().__init__(parent)
        self._timer = timer if timer is not None else QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._timeout)
        self.started = False
        self.enabled = True
        self.busy = False
        self.dirty = False
        self.failures = 0

    def start(self, enabled=True):
        self.started = True
        self.set_enabled(enabled)

    def stop(self):
        self.started = False
        self._timer.stop()

    def set_enabled(self, enabled):
        self.enabled = bool(enabled)
        self._timer.stop()
        if self.started and self.enabled and not self.busy:
            self._timer.start(self.CHANGE_DELAY_MS)
        self.stateChanged.emit()

    def request(self):
        if not self.started or not self.enabled:
            return
        self.dirty = True
        # Edits during a failed connection must not bypass the retry delay.
        if not self.busy and not self.failures:
            self._timer.start(self.CHANGE_DELAY_MS)
        self.stateChanged.emit()

    def begin(self):
        # Shared by manual sync and OAuth: never overlap background requests.
        self.busy = True
        self.dirty = False
        self._timer.stop()

    def finish(self, success):
        self.busy = False
        self.failures = 0 if success else min(self.failures + 1, 7)
        if self.started and self.enabled:
            delay = (self.CHANGE_DELAY_MS if self.dirty else self.POLL_INTERVAL_MS)
            if self.failures:
                delay = min(self.RETRY_INITIAL_MS * 2 ** (self.failures - 1),
                            self.RETRY_MAX_MS)
            self._timer.start(delay)
        self.stateChanged.emit()

    def _timeout(self):
        if not self.started or not self.enabled or self.busy:
            return
        self.syncRequested.emit()
        # Disconnected/demo profiles do no network work, but can be connected
        # later. The receiver calls begin() when a job actually starts.
        if not self.busy and not self._timer.isActive():
            self._timer.start(self.POLL_INTERVAL_MS)


__all__ = ["AutoSyncScheduler"]
