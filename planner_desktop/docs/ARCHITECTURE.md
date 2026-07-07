# Planner Desktop (PySide6 + Qt Quick/QML) — архитектура скелета

## Что это

Греенфилд-переписывание десктопного Planner **рядом** со старым
Flet-приложением. Старое приложение (`main.py`, `ui/`, `services/`,
`models/`) остаётся нетронутым и запускается как раньше; движок
синхронизации по умолчанию остаётся `legacy`.

Запуск нового приложения:

```
python run_desktop.py
```

Запуск старого приложения — по-прежнему `python main.py`.

## Жёсткие ограничения

- **Никакого бэкенда**: нет сервера, REST API, Firebase, облачных функций,
  sidecar-процессов. Python-приложение + Google API — и всё.
- Старый код (sync, UndatedTasksSync, миграции/пилот/dead-letter,
  OAuth/токены) не изменяется и из нового пакета не импортируется.
- Реальные данные пользователя не мигрируются.

## «Мобильная версия» = Google Calendar на телефоне

У Planner нет и не будет собственного мобильного приложения.
Мобильная версия — это **родное приложение Google Calendar** на телефоне:

- задачи с датой/временем из десктопа синхронизируются **двусторонне**
  с событиями Google Calendar;
- правки, сделанные на телефоне (перенос, переименование, удаление
  события), забираются pull-ом как удалённые изменения;
- правки в десктопе — локальные изменения, уходят push-ем;
- all-day и повторяющиеся all-day события поддерживаются безопасно
  (см. правила маппинга ниже);
- задачи **без даты** в телефонный календарь в фазе 1 не попадают;
  позже их можно явно замапить на Google Tasks или all-day события.

**Ядро синхронизации реализовано и покрыто тестами, но работает пока
только на фейковом шлюзе** (`sync/fake_calendar_gateway.py`). Реального
Google-шлюза нет: ни одного вызова Google API, ни OAuth, ни сети новый
пакет не делает. Следующей фазой появится `GoogleCalendarGateway`,
реализующий тот же контракт `CalendarGateway` поверх Calendar API.

## Локальное хранилище (экспериментальное)

Фаза 0 (скелет) обходилась `FakeTaskRepository` — данные жили только в
памяти процесса. Теперь по умолчанию приложение использует
`SQLiteTaskRepository` (`planner_desktop/storage/`) — **экспериментальное
изолированное** хранилище:

- файл БД: `<user data dir>/PlannerDesktop/app_desktop.db`
  (на Windows — `%APPDATA%\PlannerDesktop\app_desktop.db`);
- каталог и имя файла намеренно отличаются от профиля старого
  Flet-приложения (`<user data dir>/Planner/app.db`) — старый `app.db`
  **никогда не читается и не пишется**;
- **миграции старых данных нет** — новый десктоп стартует с пустой БД;
- переопределение пути для разработки/тестов — переменная окружения
  `PLANNER_DESKTOP_DATA_DIR` (тесты передают `tmp_path` прямо в
  конструктор репозитория);
- `PLANNER_DESKTOP_DEMO=1` возвращает фейковый репозиторий с
  демо-данными, на диск ничего не пишется;
- удаление задачи — тумбстоун `deleted_at`, строка остаётся в БД;
- схема (`storage/schema.py`) — простой sqlite3, без SQLModel и без
  импорта старых `models/`.

## Слои

```
Domain (planner_desktop/domain)
   ↓  чистые dataclass-ы и правила валидации; без Qt, Flet, SQLModel
Repository (planner_desktop/repositories + planner_desktop/storage)
   ↓  SQLiteTaskRepository — по умолчанию (изолированный app_desktop.db);
      FakeTaskRepository — для тестов и демо-режима; общий контракт —
      Protocol TaskRepository; CalendarSyncStore — очередь Calendar-операций
      и состояние синка в той же БД
Use cases (planner_desktop/usecases)
   ↓  DesktopTaskService: CRUD задач + постановка Calendar-операций в очередь
ViewModels (planner_desktop/viewmodels)
   ↓  QObject-обёртки: свойства, сигналы, слоты для QML; CRUD — через сервис
QML UI (planner_desktop/qml)
   ↓  ApplicationWindow + Sidebar + страницы (Сегодня/Календарь/История/Настройки)
Sync (planner_desktop/sync)
      CalendarSyncEngine + calendar_mapper + FakeCalendarGateway;
      реальный Google-шлюз — позже, по контракту CalendarGateway
```

Правило зависимостей: стрелки только вниз по списку недопустимы —
domain не знает ни про repository, ни про Qt; QML разговаривает только
с viewmodels; движок синхронизации работает с domain + repository +
очередью через шлюзы, не трогая UI.

## Ядро Calendar-синхронизации (фейковый шлюз)

Состав (`planner_desktop/sync/` + `planner_desktop/storage/`):

- `sync_types.py` — `CalendarEvent` (собственная модель события, без
  Google-клиентов), `PendingOp`, ошибки шлюза
  (`RetryableGatewayError` / `TerminalGatewayError`);
- `calendar_mapper.py` — чистый маппинг Task ↔ CalendarEvent;
- `calendar_sync_engine.py` — двусторонний движок: push очереди,
  pull изменений, конфликтная политика;
- `fake_calendar_gateway.py` — in-memory календарь для тестов/разработки:
  etag-и, updated_at, журнал изменений с курсором (аналог syncToken),
  all-day и timed события, метаданные повторяющихся экземпляров,
  инъекция ошибок;
- `storage/calendar_sync_store.py` — локальная очередь push-операций
  (`desktop_pending_calendar_ops`) и состояние синка
  (`desktop_sync_state`) в том же изолированном `app_desktop.db`.

Поток данных:

- Quick Add / правки в UI → `DesktopTaskService` → репозиторий +
  постановка операции в очередь (создание события — только задачам
  с датой);
- `CalendarSyncEngine.push_pending()` — отложенные операции уходят в
  шлюз: create возвращает id/etag (записываются в задачу), update идёт
  патчем, тумбстоун — delete-ом; временная ошибка → ретрай с бэкоффом,
  после `MAX_ATTEMPTS` или постоянной ошибки — dead-letter (terminal),
  бесконечных ретраев нет;
- `CalendarSyncEngine.pull_remote_changes()` — правки «с телефона»:
  новое событие → новая задача, правка → обновление задачи, отмена →
  тумбстоун задачи.

### Конфликтная политика (фаза 1, детерминированная)

1. Есть pending-операция у задачи → remote-правка её НЕ перезаписывает
   (недопушенная локальная правка важнее; задача догонит календарь
   после push-а).
2. Etag события совпадает с сохранённым в задаче → это эхо нашего
   push-а, пропускаем.
3. Иначе побеждает бОльший `updated_at`: remote новее → накатываем
   на задачу; локальная новее → ставим push update в очередь.
4. Ничья (или неизвестный remote updated_at) → локальная версия
   остаётся, ничего не пушится (лог/отладка).

Политика может эволюционировать, но пока она зафиксирована тестами.

### Правила безопасности all-day и повторяющихся событий

- all-day задача ↔ событие с «голыми» датами (`date`/`date`), конец —
  **эксклюзивный**; формы `date` и `dateTime` не смешиваются никогда;
- экземпляр повторяющегося события (`recurring_event_id` заполнен)
  **не патчится по start/end вслепую**: маппер сознательно опускает
  start/end в патче (обновляются только текстовые поля), а фейковый
  шлюз, как и Google, отвечает постоянной ошибкой на слепой перенос;
  осознанный перенос экземпляра — отдельная будущая фича;
- завершённая задача остаётся событием в календаре (галочка локальная,
  в Calendar не уходит);
- unschedule (снятие даты у синхронизированной задачи) в фазе 1 не
  реализован — в UI нет такого действия; операция снимается с очереди,
  событие остаётся;
- локальный тумбстоун «липкий»: после допушенного delete поздние
  remote-правки задачу не воскрешают;
- задачи **без даты** остаются локальными для нового десктопа в этой
  фазе: в календарь (и вообще наружу) они не отправляются.

## Правила маппинга Task ↔ событие Calendar (закреплены заранее)

Уроки старого приложения (историческая петля HTTP 400) учтены в контракте:

1. Задача со временем → `start.dateTime`/`end.dateTime` (+ `timeZone`).
2. All-day задача → `start.date`/`end.date`, конец — **эксклюзивный**
   (событие на один день 2026-06-05 имеет `end.date = 2026-06-06`).
3. Формы `date` и `dateTime` никогда не смешиваются в одном событии.
4. Экземпляр повторяющегося all-day события **нельзя** слепо патчить по
   start/end — это перенос экземпляра; обновление идёт через
   `recurringEventId` + `originalStartTime` (поля уже есть в Task:
   `google_calendar_recurring_event_id`, `google_calendar_original_start`).
5. Удаление задачи — тумбстоун (`deleted_at`), чтобы delete можно было
   допушить в Calendar позже.
6. Разрешение конфликтов — уровнем выше шлюза (в будущем движке
   синхронизации), шлюз только переносит данные и отдаёт etag.

## Статус компонентов

| Компонент | Статус |
|---|---|
| Доменная модель Task с полями Calendar-синка | готова |
| Валидация Quick Add (domain/commands.py) | готова, покрыта тестами |
| FakeTaskRepository | фаза 0; остаётся для тестов и демо-режима |
| SQLiteTaskRepository (storage/) | экспериментальный, изолированный, по умолчанию |
| Миграция старого app.db | НЕ выполняется (и не планируется в этой фазе) |
| QML-оболочка (4 страницы, светлая тема) | готова как скелет |
| CalendarPage | заглушка недельной сетки |
| HistoryPage | заглушка |
| DesktopTaskService (usecases/) | готов; ставит Calendar-операции в очередь |
| Очередь Calendar-операций (calendar_sync_store.py) | готова, с ретраями и dead-letter |
| Маппер Task ↔ CalendarEvent | готов, покрыт тестами |
| CalendarSyncEngine (двусторонний) | готов, работает на FakeCalendarGateway |
| FakeCalendarGateway | готов: журнал изменений, etag-и, инъекция ошибок |
| Реальный Google Calendar sync | НЕ реализован; следующая фаза — GoogleCalendarGateway (сеть/OAuth) |

## Тесты

Чистая Python-логика тестируется без окна:

```
python -m pytest tests/test_desktop_today_viewmodel.py tests/test_desktop_calendar_contract.py tests/test_desktop_storage_paths.py tests/test_desktop_sqlite_repository.py tests/test_desktop_calendar_mapper.py tests/test_desktop_calendar_sync_store.py tests/test_desktop_calendar_sync_engine.py tests/test_desktop_task_service_sync_queue.py -q
```
