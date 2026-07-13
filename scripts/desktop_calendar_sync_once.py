"""Однократный ручной запуск Calendar-синка НОВОГО десктопа (CLI).

    python -m scripts.desktop_calendar_sync_once --real-google

Правила безопасности:

- без явного флага ``--real-google`` скрипт НИЧЕГО не делает (выход с
  кодом 2 и подсказкой): случайный запуск не тронет ни сеть, ни данные;
- работает ТОЛЬКО с изолированным профилем нового десктопа
  (``PlannerDesktop/app_desktop.db`` + ``PlannerDesktop/token.json``;
  каталог переопределяется ``--data-dir`` или переменной окружения
  ``PLANNER_DESKTOP_DATA_DIR``); старый ``Planner/app.db`` и старый
  ``token.json`` не читаются и не пишутся;
- используется ТОТ ЖЕ ManualSyncService, что и кнопка «Синхронизировать
  сейчас» в настройках, — логика синка не дублируется;
- ровно один цикл push+pull за запуск; никакого фонового режима;
- рекомендуется ТЕСТОВЫЙ Google-аккаунт (см. docs/GOOGLE_SYNC_SETUP.md).

Импорт модуля не запускает ни OAuth, ни Google API, ни QML.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Callable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from planner_desktop.storage.paths import DATA_DIR_ENV_VAR  # noqa: E402

REFUSED_EXIT_CODE = 2
REFUSED_MESSAGE = (
    "Ничего не сделано. Реальный синк запускается только с явным флагом "
    "--real-google (используйте тестовый Google-аккаунт; токен должен "
    "лежать в изолированном профиле PlannerDesktop)."
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.desktop_calendar_sync_once",
        description="Один ручной цикл Calendar-синка нового десктопа "
                    "(изолированный профиль PlannerDesktop).",
    )
    parser.add_argument(
        "--real-google", action="store_true",
        help="выполнить настоящий цикл push+pull через Google Calendar API "
             "(без флага скрипт только печатает подсказку и выходит)",
    )
    parser.add_argument(
        "--data-dir", default=None,
        help="каталог изолированного профиля (иначе PLANNER_DESKTOP_DATA_DIR "
             "или платформенный PlannerDesktop)",
    )
    return parser


def _build_default_service():
    """Реальные зависимости: изолированная БД + шлюз из token.json профиля.

    for_db_path: соединения открываются на время цикла и закрываются —
    скрипт не оставляет открытых файлов БД.
    """
    from planner_desktop.storage.paths import (
        ensure_desktop_data_dir,
        get_desktop_db_path,
    )
    from planner_desktop.sync.google_auth import build_real_gateway
    from planner_desktop.usecases.manual_sync_service import ManualSyncService

    ensure_desktop_data_dir()
    return ManualSyncService.for_db_path(
        get_desktop_db_path(), gateway_provider=build_real_gateway)


def main(
    argv: Optional[list] = None,
    *,
    service_factory: Optional[Callable[[], object]] = None,
) -> int:
    """Точка входа. ``service_factory`` подменяется в тестах фейком."""
    args = build_parser().parse_args(argv)

    if not args.real_google:
        print(REFUSED_MESSAGE)
        return REFUSED_EXIT_CODE

    if args.data_dir:
        os.environ[DATA_DIR_ENV_VAR] = args.data_dir

    factory = service_factory or _build_default_service
    service = factory()
    result = service.run_once()

    print("Ручной синк Calendar (новый десктоп)")
    print(f"  статус:            {'OK' if result.ok else 'ОШИБКА'}")
    print(f"  отправлено (push): {result.pushed}")
    print(f"  получено (pull):   {result.pulled}")
    print(f"  очередь до/после:  {result.pending_before} -> {result.pending_after}")
    print(f"  dead-letter:       {result.terminal_ops}")
    print(f"  курсор обновлён:   {'да' if result.cursor_updated else 'нет'}")
    if result.error:
        print(f"  ошибка:            {result.error}")
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
