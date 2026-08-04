# Planner

Десктопный планировщик задач на **PySide6 + Qt Quick/QML** с ручной
двусторонней синхронизацией с Google Calendar.

Основной пакет — [`planner_desktop/`](planner_desktop/). Единственная точка
входа — [`run_desktop.py`](run_desktop.py).

> Прежняя реализация на Flet (`main.py`, `core/`, `storage/`, `ui/`,
> `models/`, `services/`) **удалена** из репозитория вместе со своим
> sync-контуром, тестами и документацией. Её профиль данных
> (`<user data dir>/Planner`) приложение никогда не читает и не изменяет.

## Запуск

### Windows

Дважды кликните `start_planner.bat` в Проводнике.

Лаунчер сам выбирает интерпретатор (`.venv\Scripts\python.exe` → `py -3` →
`python`), проверяет, что установлен PySide6, и запускает `run_desktop.py`.
Окно остаётся открытым только если запуск завершился ошибкой.

### Любая ОС

```bash
python run_desktop.py
```

## Установка

```bash
python -m venv .venv
# Windows
.venv\Scripts\python.exe -m pip install -r requirements.txt
# Linux / macOS
.venv/bin/python -m pip install -r requirements.txt
```

Нужен Python 3.11+. Зависимости — [`requirements.txt`](requirements.txt)
(runtime) и [`requirements-dev.txt`](requirements-dev.txt) (тесты).

## Тесты

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q                      # весь набор
python -m pytest tests/test_desktop_recurrence_service.py -q   # один файл
```

Тесты не ходят в сеть, не читают реальный профиль и не вызывают Google API:
календарные сценарии работают через `FakeCalendarGateway`, хранилище — через
временные каталоги.

## Где лежат данные

Приложение хранит данные **вне репозитория**, в отдельном профиле
пользователя:

| Что | Где |
|---|---|
| Каталог профиля | `<user data dir>/PlannerDesktop` (Windows — `%APPDATA%\PlannerDesktop`) |
| База данных | `<профиль>/app_desktop.db` |
| OAuth-токен | `<профиль>/token.json` |
| OAuth client secret | `<профиль>/secrets/client_secret.json` |

Путь профиля переопределяется переменной окружения
`PLANNER_DESKTOP_DATA_DIR` — так запускаются изолированные тестовые и
smoke-профили.

## Безопасность

**Не коммитьте секреты и локальные данные.** OAuth-токены, `client_secret*.json`,
`*.db`/`*.sqlite`, `.env`, логи и профили закрыты правилами в
[`.gitignore`](.gitignore) — не добавляйте их через `git add -f`.

Держите client secret и токены только в каталоге профиля. Для первого
подключения используйте **тестовый** Google-аккаунт.

## Синхронизация с Google Calendar

Синхронизация выполняется **только вручную** и только по явному действию:

* кнопка «Синхронизировать сейчас» в настройках, либо
* `python -m scripts.desktop_calendar_sync_once --real-google`

Автоматического и фонового синка нет — ни при старте, ни по таймеру, ни при
открытии страницы. Один цикл — это push локальной очереди операций, затем
инкрементальный pull по `nextSyncToken`. Задачи без даты остаются локальными;
отметка «выполнено» — локальная и события в календаре не трогает.

Настройка и правила безопасности маппинга —
[`GOOGLE_SYNC_SETUP.md`](planner_desktop/docs/GOOGLE_SYNC_SETUP.md).

## Документация

| Документ | О чём |
|---|---|
| [ARCHITECTURE.md](planner_desktop/docs/ARCHITECTURE.md) | слои Domain → Repository → Service → ViewModel → QML, схема БД, миграции |
| [PRODUCT_ROADMAP.md](planner_desktop/docs/PRODUCT_ROADMAP.md) | фазы, принятые решения и результаты приёмок |
| [FEATURE_PARITY.md](planner_desktop/docs/FEATURE_PARITY.md) | перечень функциональности со статусами |
| [GOOGLE_SYNC_SETUP.md](planner_desktop/docs/GOOGLE_SYNC_SETUP.md) | подключение Google Calendar, правила безопасности, операционные скрипты |
| [RECURRENCE_ARCHITECTURE.md](planner_desktop/docs/RECURRENCE_ARCHITECTURE.md) | локальные повторяющиеся задачи, исключения, шаблоны |
| [GOOGLE_SERIES_SYNC_ARCHITECTURE.md](planner_desktop/docs/GOOGLE_SERIES_SYNC_ARCHITECTURE.md) | связь серий с Google master, конфликты, восстановление |
| [GOOGLE_OCCURRENCE_SYNC_ARCHITECTURE.md](planner_desktop/docs/GOOGLE_OCCURRENCE_SYNC_ARCHITECTURE.md) | отдельные экземпляры, исключения, карантин |
| [GOOGLE_SERIES_REMOTE_SPLIT_ARCHITECTURE.md](planner_desktop/docs/GOOGLE_SERIES_REMOTE_SPLIT_ARCHITECTURE.md) | «этот и будущие»: split мастера и откат |
| [SHORTCUTS.md](planner_desktop/docs/SHORTCUTS.md) | клавиатурные сокращения |
