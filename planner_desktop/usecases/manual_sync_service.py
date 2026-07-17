"""Ручной запуск одного цикла Calendar-синка (use-case-слой десктопа).

Единственная точка, откуда выполняется реальный синк: её делят кнопка
«Синхронизировать сейчас» в настройках и CLI
``python -m scripts.desktop_calendar_sync_once --real-google`` — логика
не дублируется.

Гарантии:

- ровно один цикл push+pull за вызов, САМ ПО СЕБЕ сервис никогда не
  запускается (ни таймеров, ни фоновых потоков здесь нет);
- два одновременных запуска исключены: неблокирующий lock, второй вызов
  честно возвращает ошибку «уже выполняется»;
- результат структурный (ManualSyncResult) — сколько ушло/пришло,
  очередь до/после, dead-letter, обновился ли курсор, человекочитаемая
  ошибка; исключения наружу не летят;
- шлюз строится лениво через инъецированный ``gateway_provider`` —
  импорт модуля и создание сервиса сети не делают; в тестах провайдер
  отдаёт FakeCalendarGateway;
- сводка последнего синка сохраняется в desktop_sync_state
  (ключи LAST_SYNC_*_KEY) — «Настройки» показывают её после перезапуска.

QML/Qt здесь нет — модуль чистый Python и тестируется без окна.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Optional, Union

from planner_desktop.domain.task import utc_now
from planner_desktop.sync.calendar_sync_engine import CalendarSyncEngine

if TYPE_CHECKING:
    from planner_desktop.repositories import TaskRepository
    from planner_desktop.storage.calendar_sync_store import CalendarSyncStore

logger = logging.getLogger(__name__)

SYNC_ALREADY_RUNNING_ERROR = "Синхронизация уже выполняется — дождитесь завершения."

LAST_SYNC_AT_KEY = "last_sync_at"
LAST_SYNC_SUMMARY_KEY = "last_sync_summary"
LAST_SYNC_ERROR_KEY = "last_sync_error"


@dataclass
class ManualSyncResult:
    """Итог одного ручного цикла синка (для UI и CLI)."""

    ok: bool
    pushed: int = 0
    pulled: int = 0
    ordinary_events_pulled: int = 0
    recurring_masters_discovered: int = 0
    recurring_instances_pulled: int = 0
    unsupported_masters: int = 0
    cancelled_masters: int = 0
    series_masters_created: int = 0
    series_masters_updated: int = 0
    series_masters_deleted: int = 0
    series_master_conflicts: int = 0
    series_ops_terminal: int = 0
    linked_instance_changes_quarantined: int = 0
    # Phase 3.2B3A additive resolution counters.  Keep Planner, superseded
    # attempts, failures and recreation execute inside this push cycle.
    # Use Google and disconnect are LOCAL actions performed outside sync;
    # their counters report resolutions completed since the previous manual
    # sync, i.e. they surface in the NEXT summary after the local action.
    conflicts_resolved_keep_planner: int = 0
    conflicts_resolved_use_google: int = 0
    conflicts_disconnected: int = 0
    remote_deleted_recreated: int = 0
    resolution_attempts_superseded: int = 0
    resolution_failures: int = 0
    pending_before: int = 0
    pending_after: int = 0
    terminal_ops: int = 0
    cursor_updated: bool = False
    error: str = ""
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None

    @property
    def summary(self) -> str:
        """Короткая человекочитаемая сводка для настроек/CLI."""
        if not self.ok:
            return self.error or "Синхронизация не выполнена."
        parts = [f"отправлено {self.pushed}", f"получено {self.pulled}"]
        parts.extend([
            f"обычных событий {self.ordinary_events_pulled}",
            f"мастеров серий {self.recurring_masters_discovered}",
            f"экземпляров серий {self.recurring_instances_pulled}",
            f"неподдерживаемых мастеров {self.unsupported_masters}",
            f"отменённых мастеров {self.cancelled_masters}",
            f"мастеров создано {self.series_masters_created}",
            f"мастеров обновлено {self.series_masters_updated}",
            f"мастеров удалено {self.series_masters_deleted}",
            f"конфликтов мастеров {self.series_master_conflicts}",
            (
                "изменений экземпляров в карантине "
                f"{self.linked_instance_changes_quarantined}"
            ),
        ])
        if self.conflicts_resolved_keep_planner:
            parts.append(
                f"конфликтов решено (Planner) {self.conflicts_resolved_keep_planner}"
            )
        if self.conflicts_resolved_use_google:
            parts.append(
                f"конфликтов решено (Google) {self.conflicts_resolved_use_google}"
            )
        if self.conflicts_disconnected:
            parts.append(f"связей отключено {self.conflicts_disconnected}")
        if self.remote_deleted_recreated:
            parts.append(f"серий пересоздано {self.remote_deleted_recreated}")
        if self.resolution_attempts_superseded:
            parts.append(
                f"решений устарело {self.resolution_attempts_superseded}"
            )
        if self.resolution_failures:
            parts.append(f"ошибок разрешения {self.resolution_failures}")
        if self.pending_after:
            parts.append(f"в очереди осталось {self.pending_after}")
        if self.terminal_ops:
            parts.append(f"dead-letter: {self.terminal_ops}")
        return "Синхронизировано: " + ", ".join(parts) + "."


class ManualSyncService:
    """Один цикл push+pull по требованию пользователя. Без автозапуска.

    Два режима владения соединениями:

    - прямая инъекция ``(repository, store)`` — для тестов и однопоточных
      сценариев: соединения живут снаружи, сервис их не закрывает;
    - ``ManualSyncService.for_db_path(...)`` — для GUI: run_once() выполняется
      в фоновом потоке, а SQLite-соединения нельзя переносить между потоками,
      поэтому сервис открывает СВОИ соединения в потоке выполнения на время
      одного цикла и закрывает их в finally.
    """

    def __init__(
        self,
        repository: Optional["TaskRepository"],
        store: Optional["CalendarSyncStore"],
        gateway_provider: Callable[[], object],
        *,
        clock: Callable[[], datetime] = utc_now,
        external_series_repository=None,
    ) -> None:
        self._repository = repository
        self._store = store
        self._gateway_provider = gateway_provider
        self._clock = clock
        self._external_series_repository = external_series_repository
        self._db_path: Optional[Path] = None
        self._lock = threading.Lock()

    @classmethod
    def for_db_path(
        cls,
        db_path: Union[Path, str],
        gateway_provider: Callable[[], object],
        *,
        clock: Callable[[], datetime] = utc_now,
    ) -> "ManualSyncService":
        """Сервис, открывающий соединения per-run в потоке выполнения
        (безопасно для запуска из фонового Qt-потока)."""
        service = cls(None, None, gateway_provider, clock=clock)
        service._db_path = Path(db_path)
        return service

    @property
    def is_running(self) -> bool:
        return self._lock.locked()

    def run_once(self) -> ManualSyncResult:
        """Выполнить ровно один цикл синка; никогда не бросает исключений."""
        if not self._lock.acquire(blocking=False):
            return ManualSyncResult(ok=False, error=SYNC_ALREADY_RUNNING_ERROR)
        try:
            if self._db_path is not None:
                return self._run_with_own_connections()
            return self._run_cycle(self._repository, self._store)
        finally:
            self._lock.release()

    # ---- внутреннее -------------------------------------------------------------

    def _run_with_own_connections(self) -> ManualSyncResult:
        """Свежие соединения в ТЕКУЩЕМ потоке (sqlite3 не переносится
        между потоками); закрываются всегда, даже при ошибке."""
        from planner_desktop.storage.calendar_sync_store import CalendarSyncStore
        from planner_desktop.storage.sqlite_task_repository import (
            SQLiteTaskRepository,
        )
        from planner_desktop.storage.external_series_repository import (
            SQLiteExternalSeriesRepository,
        )
        from planner_desktop.storage.calendar_series_sync_store import (
            CalendarSeriesSyncStore,
        )
        from planner_desktop.storage.series_repository import SQLiteSeriesRepository

        repository = SQLiteTaskRepository(self._db_path)
        try:
            store = CalendarSyncStore(self._db_path, clock=self._clock)
            try:
                external_series = SQLiteExternalSeriesRepository(self._db_path)
                try:
                    series_store = CalendarSeriesSyncStore(
                        self._db_path, clock=self._clock
                    )
                    try:
                        series_repository = SQLiteSeriesRepository(self._db_path)
                        try:
                            return self._run_cycle(
                                repository,
                                store,
                                external_series,
                                series_store=series_store,
                                series_repository=series_repository,
                            )
                        finally:
                            series_repository.close()
                    finally:
                        series_store.close()
                finally:
                    external_series.close()
            finally:
                store.close()
        finally:
            repository.close()

    def _run_cycle(
        self,
        repository: "TaskRepository",
        store: "CalendarSyncStore",
        external_series_repository=None,
        *,
        series_store=None,
        series_repository=None,
    ) -> ManualSyncResult:
        started = self._clock()
        pending_before = store.count_pending_ops()
        cursor_before = store.get_sync_cursor()
        previous_sync_at = self._parse_stamp(store.get_state(LAST_SYNC_AT_KEY))

        try:
            gateway = self._gateway_provider()
        except Exception as exc:  # нет токена/секрета и т.п. — честно в UI
            return self._finish(store, ManualSyncResult(
                ok=False, pending_before=pending_before,
                pending_after=pending_before,
                terminal_ops=store.count_terminal_ops(),
                error=str(exc), started_at=started,
            ))

        catalog = (external_series_repository
                   if external_series_repository is not None
                   else self._external_series_repository)
        engine = CalendarSyncEngine(
            repository,
            store,
            gateway,
            catalog,
            series_link_store=series_store,
        )
        series_result = None
        try:
            if series_store is not None and series_repository is not None:
                from planner_desktop.sync.calendar_series_sync_engine import (
                    CalendarSeriesSyncEngine,
                )

                series_engine = CalendarSeriesSyncEngine(
                    series_repository,
                    repository,
                    series_store,
                    catalog,
                    gateway,
                )
                series_result = series_engine.push_pending()
            pushed = engine.push_pending()
            pulled = engine.pull_remote_changes()
        except Exception as exc:
            # Ошибки отдельных операций push гасятся очередью (requeue/
            # dead-letter); сюда попадает падение pull-а или неожиданное.
            logger.exception("Ручной синк упал")
            return self._finish(store, ManualSyncResult(
                ok=False, pending_before=pending_before,
                pending_after=store.count_pending_ops(),
                terminal_ops=store.count_terminal_ops(),
                error=f"Синхронизация прервана: {exc}",
                started_at=started,
            ))

        cursor_after = store.get_sync_cursor()
        local_use_google = 0
        local_disconnected = 0
        if series_store is not None:
            counter = getattr(
                series_store, "count_resolutions_completed_after", None
            )
            if callable(counter):
                # Local actions (Use Google / disconnect / keep-local) finished
                # outside sync; the next summary reports them.
                local_use_google = counter(previous_sync_at, ("use_google",))
                local_disconnected = counter(
                    previous_sync_at, ("disconnect", "keep_local")
                )
        return self._finish(store, ManualSyncResult(
            ok=True,
            pushed=pushed + (series_result.pushed if series_result else 0),
            pulled=pulled,
            ordinary_events_pulled=engine.last_pull_stats.ordinary_events,
            recurring_masters_discovered=engine.last_pull_stats.recurring_masters,
            recurring_instances_pulled=engine.last_pull_stats.recurring_instances,
            unsupported_masters=engine.last_pull_stats.unsupported_masters,
            cancelled_masters=engine.last_pull_stats.cancelled_masters,
            series_masters_created=(series_result.created if series_result else 0),
            series_masters_updated=(series_result.updated if series_result else 0),
            series_masters_deleted=(series_result.deleted if series_result else 0),
            series_master_conflicts=(series_result.conflicts if series_result else 0),
            series_ops_terminal=(
                series_store.count_terminal_ops() if series_store is not None else 0
            ),
            linked_instance_changes_quarantined=(
                engine.last_pull_stats.linked_instance_changes_quarantined
            ),
            conflicts_resolved_keep_planner=(
                series_result.resolved_keep_planner if series_result else 0
            ),
            conflicts_resolved_use_google=local_use_google,
            conflicts_disconnected=local_disconnected,
            remote_deleted_recreated=(
                series_result.remote_deleted_recreated if series_result else 0
            ),
            resolution_attempts_superseded=(
                series_result.resolution_superseded if series_result else 0
            ),
            resolution_failures=(
                series_result.resolution_failed if series_result else 0
            ),
            pending_before=pending_before,
            pending_after=store.count_pending_ops(),
            terminal_ops=store.count_terminal_ops(),
            cursor_updated=cursor_after != cursor_before,
            started_at=started,
        ))

    @staticmethod
    def _parse_stamp(raw: Optional[str]) -> Optional[datetime]:
        if not raw:
            return None
        try:
            return datetime.fromisoformat(raw)
        except ValueError:
            return None

    def _finish(self, store: "CalendarSyncStore",
                result: ManualSyncResult) -> ManualSyncResult:
        result.finished_at = self._clock()
        try:
            if result.ok:
                store.set_state(LAST_SYNC_AT_KEY,
                                result.finished_at.isoformat())
                store.set_state(LAST_SYNC_SUMMARY_KEY, result.summary)
                store.set_state(LAST_SYNC_ERROR_KEY, None)
            else:
                store.set_state(LAST_SYNC_ERROR_KEY, result.error)
        except Exception:  # сводка — не повод уронить результат
            logger.exception("Не удалось сохранить сводку синка")
        return result


__all__ = [
    "ManualSyncResult",
    "ManualSyncService",
    "SYNC_ALREADY_RUNNING_ERROR",
    "LAST_SYNC_AT_KEY",
    "LAST_SYNC_SUMMARY_KEY",
    "LAST_SYNC_ERROR_KEY",
]
