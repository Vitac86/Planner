"""Выполнение долгих операций вне GUI-потока (OAuth, ручной синк).

QtBackgroundExecutor запускает функцию в QThreadPool и возвращает
результат В GUI-ПОТОК через сигнал (авто-queued connection, т.к. эмит
идёт из потока пула, а приёмник живёт в GUI-потоке) — QML не замирает,
а колбэк безопасно трогает свойства ViewModel-ей.

Исключение работы не пробрасывается, а ПЕРЕДАЁТСЯ колбэку как результат:
колбэк один, ветвится по isinstance(outcome, Exception) — так ошибка
гарантированно возвращает кнопкам рабочее состояние.

В тестах вместо него подставляется синхронный/ручной executor с тем же
интерфейсом ``submit(fn, callback)`` — потоков и Qt-цикла не требуется.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Set

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

logger = logging.getLogger(__name__)


class _JobSignals(QObject):
    """Живёт в потоке создателя (GUI): finished доедет queued-соединением."""

    finished = Signal(object)


class _Job(QRunnable):
    def __init__(self, fn: Callable[[], Any], signals: _JobSignals) -> None:
        super().__init__()
        self._fn = fn
        self._signals = signals

    def run(self) -> None:  # исполняется в потоке пула
        try:
            outcome: Any = self._fn()
        except Exception as exc:  # ошибка — тоже результат, не тихая смерть
            logger.exception("Фоновая операция упала")
            outcome = exc
        self._signals.finished.emit(outcome)


class QtBackgroundExecutor(QObject):
    """submit(fn, callback): fn — в пуле потоков, callback — в GUI-потоке."""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._pool = QThreadPool.globalInstance()
        # Держим сигнальные объекты живыми до доставки finished:
        # иначе GC уберёт их раньше queued-эмита.
        self._active: Set[_JobSignals] = set()

    def submit(self, fn: Callable[[], Any], callback: Callable[[Any], None]) -> None:
        signals = _JobSignals(self)
        self._active.add(signals)

        def _deliver(outcome: Any) -> None:
            self._active.discard(signals)
            signals.deleteLater()
            callback(outcome)

        signals.finished.connect(_deliver)
        self._pool.start(_Job(fn, signals))


__all__ = ["QtBackgroundExecutor"]
