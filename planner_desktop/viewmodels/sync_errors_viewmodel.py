"""Explain ordinary Calendar failures and request safe, explicit retries.

Only SQLite is accessed here. SettingsViewModel remains the sole owner of
network execution and its shared manual/automatic/OAuth busy state.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Callable

from PySide6.QtCore import QObject, Property, Signal, Slot

from planner_desktop.sync.sync_types import OpKind, OpStatus
from planner_desktop.usecases.task_service import DesktopTaskService

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _Failure:
    reason: str
    hint: str
    retry_allowed: bool = True
    reconnect: bool = False


def _explain_failure(error: str) -> _Failure:
    """Turn provider errors into bounded copy, never expose payloads or tokens."""
    text = (error or "").lower()
    if (re.search(r"\b401\b", text)
            or any(word in text for word in (
                "invalid_grant", "invalid credentials", "unauthorized", "oauth",
                "token has been expired", "token has been revoked", "refresherror",
                "не подключён", "не подключен", "авторизац",
            ))):
        return _Failure(
            "Google не подтвердил доступ к календарю.",
            "Подключите Google заново, затем повторите отправку.",
            reconnect=True,
        )
    if re.search(r"\b(409|412)\b", text) or "conflict" in text or "конфликт" in text:
        return _Failure(
            "Событие в Google изменилось или конфликтует с отправляемой версией.",
            "Откройте задачу и сравните её с Google Calendar. "
            "Повтор без проверки версии заблокирован. Конфликты серий "
            "разбираются в разделе «Связанные серии Google».",
            retry_allowed=False,
        )
    if (re.search(r"\b(429|500|502|503|504|408)\b", text)
            or any(word in text for word in (
                "quota", "квота", "ratelimit", "timed out", "timeout",
                "connection", "network", "сеть", "соединени", "недоступ",
            ))):
        return _Failure(
            "Google временно недоступен или ограничил число запросов.",
            "Проверьте соединение и повторите отправку позже. "
            "Задача сохранена на компьютере.",
        )
    if re.search(r"\b403\b", text) or "forbidden" in text or "доступ writer" in text:
        return _Failure(
            "Google не разрешил изменить событие в этом календаре.",
            "Проверьте аккаунт и права на календарь. После восстановления "
            "доступа можно повторить отправку.",
            reconnect=True,
        )
    if re.search(r"\b(404|410)\b", text):
        return _Failure(
            "Google не нашёл событие или календарь.",
            "Проверьте задачу и календарь Google. Повтор не создаёт "
            "удалённое событие заново.",
            retry_allowed=False,
        )
    if re.search(r"\b400\b", text) or "bad request" in text:
        return _Failure(
            "Google отклонил данные задачи.",
            "Откройте задачу и проверьте дату, время и длительность. "
            "Исправление задачи создаст новую отправку.",
            retry_allowed=False,
        )
    return _Failure(
        "Не удалось отправить изменение в Google Calendar.",
        "Проверьте задачу и подключение Google. Повторная попытка "
        "использует текущее состояние задачи.",
    )


class SyncErrorsViewModel(QObject):
    changed = Signal()
    syncRequested = Signal()
    openTaskRequested = Signal(str)
    toastMessage = Signal(str)

    def __init__(
        self, service: DesktopTaskService, parent: QObject | None = None, *,
        busy_provider: Callable[[], bool] | None = None,
        last_error_provider: Callable[[], str] | None = None,
    ) -> None:
        super().__init__(parent)
        self._service = service
        self._queue = service.calendar_queue if service.has_sync_queue else None
        self._busy_provider = busy_provider or (lambda: False)
        self._last_error_provider = last_error_provider or (lambda: "")
        self._rows: list[dict] = []
        self._error = ""
        self.refresh()

    @Property("QVariantList", notify=changed)
    def rows(self) -> list[dict]:
        return self._rows

    @Property(bool, notify=changed)
    def hasErrors(self) -> bool:
        return bool(self._rows or self._last_error_provider() or self._error)

    @Property(bool, notify=changed)
    def busy(self) -> bool:
        return bool(self._busy_provider())

    @Property(str, notify=changed)
    def errorText(self) -> str:
        return self._error

    @Property(str, notify=changed)
    def cycleErrorReason(self) -> str:
        error = self._last_error_provider()
        if not error:
            return ""
        failure = _explain_failure(error)
        return f"{failure.reason} {failure.hint}"

    @Property(bool, notify=changed)
    def reconnectNeeded(self) -> bool:
        error = self._last_error_provider()
        return bool((error and _explain_failure(error).reconnect)
                    or any(row["reconnect"] for row in self._rows))

    @Slot()
    def refresh(self) -> None:
        self._error = ""
        rows = []
        if self._queue is not None:
            for op in self._queue.list_failed_ops():
                task = self._service.repository.get_by_uid(op.task_uid)
                failure = _explain_failure(op.last_error or "")
                terminal = op.status == OpStatus.TERMINAL.value
                block_reason = (self._queue.terminal_retry_block_reason(op.id)
                                if terminal else "")
                try:
                    payload = json.loads(op.payload_json or "{}")
                except (TypeError, ValueError):
                    payload = {}
                cleanup = isinstance(payload, dict) and bool(payload.get("cleanup"))
                action = {
                    OpKind.CREATE.value: "Создать событие в Google",
                    OpKind.UPDATE.value: "Обновить событие в Google",
                    OpKind.DELETE.value: "Удалить событие в Google",
                }[op.op]
                if cleanup:
                    action = "Удалить прежнее событие в Google"
                if task is None:
                    title = "Задача больше не хранится на компьютере"
                    task_state = "Удаляемое событие сохранено в очереди отдельно."
                elif task.is_deleted:
                    title = task.title or "Задача без названия"
                    task_state = "Задача удалена на компьютере."
                else:
                    title = task.title or "Задача без названия"
                    task_state = ""
                if task is None and op.op != OpKind.DELETE.value:
                    task_state = "Локальная задача не найдена."
                hint = block_reason or failure.hint
                if not terminal:
                    hint = ("Повтор уже запланирован; он выполнится при включённой "
                            "автосинхронизации или следующем ручном синке.")
                rows.append({
                    "operationId": op.id,
                    "taskUid": op.task_uid,
                    "title": title,
                    "taskState": task_state,
                    "action": action,
                    "reason": failure.reason,
                    "hint": hint,
                    "statusText": "Отправка остановлена" if terminal
                                  else "Ожидает повторной попытки",
                    "canRetry": terminal and failure.retry_allowed and not block_reason,
                    "canOpen": task is not None and not task.is_deleted,
                    "reconnect": failure.reconnect,
                })
        self._rows = rows
        self.changed.emit()

    def _fail(self, message: str) -> bool:
        self.refresh()
        self._error = message
        self.changed.emit()
        return False

    @Slot(int, result=bool)
    def retryOperation(self, op_id: int) -> bool:
        if self.busy:
            return self._fail("Дождитесь завершения текущей синхронизации или входа в Google.")
        if self._queue is None:
            return self._fail("В этом режиме синхронизация недоступна.")
        op = next((item for item in self._queue.list_terminal_ops()
                   if item.id == op_id), None)
        if op is None:
            return self._fail("Операция уже изменилась. Список обновлён.")
        failure = _explain_failure(op.last_error or "")
        if not failure.retry_allowed:
            return self._fail(failure.hint)
        try:
            self._queue.retry_terminal_op(op_id, expected_last_error=op.last_error or "")
        except ValueError as exc:
            return self._fail(str(exc))
        except Exception:
            logger.exception("Не удалось подготовить повтор синхронизации")
            return self._fail("Не удалось сохранить повторную попытку. Очередь сохранена.")
        self._error = ""
        self.refresh()
        self.toastMessage.emit("Изменение подготовлено к повторной отправке")
        self.syncRequested.emit()
        return True

    @Slot(str)
    def openTask(self, task_uid: str) -> None:
        task = self._service.repository.get_by_uid(task_uid)
        if task is None or task.is_deleted:
            self._fail("Эта задача уже удалена. Список обновлён.")
            return
        self.openTaskRequested.emit(task_uid)


__all__ = ["SyncErrorsViewModel"]
