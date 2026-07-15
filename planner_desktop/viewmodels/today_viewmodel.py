"""ViewModel страницы «Сегодня».

Правила валидации живут в domain/commands.py; здесь только адаптация
под QML: свойства-списки словарей, сигналы об изменениях и слоты.
QObject можно создавать без QApplication, поэтому тесты гоняют этот
класс без какого-либо окна.

Общие действия над задачами (редактор, удаление, снуз, выбор задачи,
busy-защита, тосты) — в базе TaskActionsViewModel; здесь остаются только
специфичные для «Сегодня» вещи: Quick Add, ежедневный чек-лист и
статистика шапки.

Сигналы:

- tasksChanged — данные списков/статистики устарели, QML перечитает свойства;
- tasksMutated (база) — эта ViewModel сама изменила задачи; MainWindow
  подписывает на него refresh() других ViewModel-ей, чтобы страницы
  не расходились. refresh() эмитит только tasksChanged, поэтому петля
  исключена;
- toastMessage/toastError (база) — всплывашки успеха/ошибки.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Dict, List

from PySide6.QtCore import Property, Signal, Slot

from planner_desktop.domain.commands import (
    QuickAddCommand,
    execute_quick_add,
    normalize_priority,
)
from planner_desktop.domain.quick_parse import parse_natural
from planner_desktop.repositories import TaskRepository
from planner_desktop.repositories.daily_task_repository import (
    InMemoryDailyTaskRepository,
)
from planner_desktop.repositories.fake_task_repository import FakeTaskRepository
from planner_desktop.usecases.daily_task_service import DailyTaskService
from planner_desktop.usecases.task_service import DesktopTaskService
from planner_desktop.viewmodels.task_actions import TaskActionsViewModel
from planner_desktop.viewmodels.task_rows import task_to_row

_MONTHS_GENITIVE = [
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]
_WEEKDAYS = [
    "понедельник", "вторник", "среда", "четверг",
    "пятница", "суббота", "воскресенье",
]


def _header_date_text(day: date) -> str:
    return (
        f"{_WEEKDAYS[day.weekday()]}, {day.day} "
        f"{_MONTHS_GENITIVE[day.month - 1]} {day.year}"
    )


class TodayViewModel(TaskActionsViewModel):
    tasksChanged = Signal()
    dailyChanged = Signal()
    dailyMutated = Signal()
    errorChanged = Signal()

    def __init__(self, repository: TaskRepository | None = None,
                 parent=None,
                 service: DesktopTaskService | None = None,
                 daily_service: DailyTaskService | None = None,
                 **kwargs) -> None:
        """CRUD идёт через DesktopTaskService: он же ставит Calendar-операции
        в очередь, когда сервис создан с CalendarSyncStore (см. main_window).
        Старый вызов TodayViewModel(repository) работает как раньше —
        сервис без очереди собирается автоматически.

        Ежедневные задачи — через DailyTaskService (общий с DailyTasksViewModel
        в MainWindow). Без явного daily_service поднимается in-memory сервис,
        поэтому старые вызовы TodayViewModel(repository) не ломаются."""
        if service is None:
            service = DesktopTaskService(repository or FakeTaskRepository())
        super().__init__(service, parent, **kwargs)
        self._repository = self._service.repository
        self._daily = daily_service or DailyTaskService(InMemoryDailyTaskRepository())
        self._error = ""
        self._ensure_today(notify=False)

    def _emit_data_changed(self) -> None:
        self.tasksChanged.emit()

    def _ensure_today(self, *, notify: bool = True) -> None:
        """Материализовать сегодняшние экземпляры серий перед показом.

        Идемпотентно и локально (см. usecases/occurrence_materializer.py);
        mutated эмитится только при реальном создании строк, поэтому петли
        обновлений нет."""
        materializer = getattr(self._service, "materializer", None)
        if materializer is None:
            return
        today = self._now().date()
        result = materializer.ensure_range(today, today)
        if notify and result.created:
            self.tasksMutated.emit()

    @Slot()
    def refresh(self) -> None:
        self._ensure_today()
        super().refresh()

    def _visible_task_uids(self) -> List[str]:
        return list(dict.fromkeys(
            [task.uid for task in self._repository.list_today()]
            + [task.uid for task in self._repository.list_undated()]
        ))

    # ---- свойства для QML -------------------------------------------------

    @Property(str, notify=tasksChanged)
    def headerDateText(self) -> str:
        return _header_date_text(date.today())

    @Property("QVariantList", notify=tasksChanged)
    def todayTasks(self) -> List[Dict[str, Any]]:
        pending = self._service.pending_task_uids()
        return [task_to_row(t, pending) for t in self._repository.list_today()]

    @Property("QVariantList", notify=tasksChanged)
    def undatedTasks(self) -> List[Dict[str, Any]]:
        pending = self._service.pending_task_uids()
        return [task_to_row(t, pending) for t in self._repository.list_undated()]

    @Property("QVariantList", notify=dailyChanged)
    def dailyTasks(self) -> List[Dict[str, Any]]:
        """Пункты ежедневного чек-листа на сегодня с отметкой выполнения."""
        return [
            {
                "uid": occ.task.uid,
                "title": occ.task.title,
                "timeLabel": occ.task.preferred_time,
                "notes": occ.task.notes,
                "done": occ.done,
            }
            for occ in self._daily.occurrences_for(date.today())
        ]

    @Property(int, notify=dailyChanged)
    def dailyTotalCount(self) -> int:
        return len(self._daily.occurrences_for(date.today()))

    @Property(int, notify=dailyChanged)
    def dailyDoneCount(self) -> int:
        return sum(1 for occ in self._daily.occurrences_for(date.today()) if occ.done)

    # ---- статистика шапки ---------------------------------------------------

    @Property(int, notify=tasksChanged)
    def todayCount(self) -> int:
        return len(self._repository.list_today())

    @Property(int, notify=tasksChanged)
    def undatedCount(self) -> int:
        return len(self._repository.list_undated())

    @Property(int, notify=tasksChanged)
    def completedTodayCount(self) -> int:
        return sum(1 for t in self._repository.list_today() if t.completed)

    @Property(int, notify=tasksChanged)
    def pendingSyncCount(self) -> int:
        return self._service.count_pending_ops()

    @Property(bool, constant=True)
    def hasSyncQueue(self) -> bool:
        return self._service.has_sync_queue

    @Property(str, notify=errorChanged)
    def errorMessage(self) -> str:
        return self._error

    # ---- слоты Quick Add ----------------------------------------------------

    @Slot(str, str, bool, bool, str, str, str, result=bool)
    def addTask(self, title: str, notes: str, add_to_calendar: bool,
                is_all_day: bool, date_text: str, time_text: str,
                duration_text: str) -> bool:
        """Quick Add с явными полями (обратная совместимость). Любой
        невалидный ввод даёт False + errorMessage, исключения наружу не
        выпускаются — UI не зависает."""
        return self._create_from_command(QuickAddCommand(
            title=title,
            notes=notes,
            add_to_calendar=add_to_calendar,
            is_all_day=is_all_day,
            date_text=date_text,
            time_text=time_text,
            duration_text=duration_text,
        ))

    @Slot(str, int, result=bool)
    def addQuick(self, text: str, priority: int) -> bool:
        """Компактный Quick Add с лёгким разбором ввода: «Отчет 15:00»,
        «Позвонить Ивану завтра». Разбор консервативный (см. quick_parse),
        приоритет приходит из компактной строки."""
        parsed = parse_natural(text)
        return self._create_from_command(parsed.to_command(), priority=priority)

    @Slot(str, str, int, bool, bool, str, str, str, result=bool)
    def addTaskDetailed(self, title: str, notes: str, priority: int,
                        add_to_calendar: bool, is_all_day: bool,
                        date_text: str, time_text: str,
                        duration_text: str) -> bool:
        """Развёрнутый Quick Add: явные поля расписания + приоритет."""
        return self._create_from_command(
            QuickAddCommand(
                title=title,
                notes=notes,
                add_to_calendar=add_to_calendar,
                is_all_day=is_all_day,
                date_text=date_text,
                time_text=time_text,
                duration_text=duration_text,
            ),
            priority=priority,
        )

    def _create_from_command(self, command: QuickAddCommand,
                             priority: int | None = None) -> bool:
        dedupe_key = f"{command!r}\x1f{priority!r}"
        if not self._begin("quickAdd", dedupe_key, dedupe=True):
            return False
        try:
            result = execute_quick_add(command)
            if not result.ok:
                self._set_error(" ".join(result.errors))
                return False
            if priority is not None:
                result.task.priority = normalize_priority(priority)
            self._service.create_task(result.task)
        except Exception as exc:  # в том числе repository/Calendar queue
            message = f"Не удалось добавить задачу: {exc}"
            self._set_error(message)
            self.toastError.emit(message)
            return False
        finally:
            self._end()

        self._set_error("")
        self._notify_mutation("Задача добавлена")
        return True

    # ---- ежедневные -----------------------------------------------------------

    @Slot(str, result=bool)
    def toggleDaily(self, uid: str) -> bool:
        """Отметить/снять выполнение ежедневной задачи на сегодня."""
        result = self._daily.toggle_completed(uid, date.today())
        if result is None:
            return False
        self.dailyChanged.emit()
        self.dailyMutated.emit()
        return True

    @Slot()
    def refreshDaily(self) -> None:
        """Перечитать ежедневные (вызывается после мутаций DailyTasksViewModel)."""
        self.dailyChanged.emit()

    @Slot()
    def clearError(self) -> None:
        self._set_error("")

    # ---- внутреннее -------------------------------------------------------

    def _set_error(self, message: str) -> None:
        if self._error != message:
            self._error = message
            self.errorChanged.emit()

    @property
    def repository(self) -> TaskRepository:
        return self._repository
