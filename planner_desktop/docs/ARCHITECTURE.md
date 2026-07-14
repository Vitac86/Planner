# Planner Desktop (PySide6 + Qt Quick/QML) — актуальная архитектура

## Что это

Греенфилд-переписывание десктопного Planner **рядом** со старым
Flet-приложением. Старое приложение (`main.py`, `ui/`, `services/`,
`models/`) остаётся нетронутым и запускается как раньше со своим legacy
sync-контуром. Новый Planner Desktop использует отдельный
`CalendarSyncEngine`, запускаемый только явным ручным действием.

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

**Ядро синхронизации реализовано и покрыто тестами; реальный шлюз
`GoogleCalendarGateway` (sync/google_calendar_gateway.py) существует**
и реализует тот же контракт `CalendarGateway`, что и фейк. Синк
запускается **только вручную**: кнопкой «Синхронизировать сейчас» в
настройках или CLI `python -m scripts.desktop_calendar_sync_once
--real-google` (общий ManualSyncService, логика не дублируется).
Автоматического/фонового синка нет: ни при старте, ни по таймеру.
OAuth-токен нового десктопа изолирован (`<PlannerDesktop>/token.json`,
секрет — `<PlannerDesktop>/secrets/client_secret.json`); старый
`<Planner>/token.json` не читается и не копируется. Первое подключение —
явное действие пользователя, рекомендуется ТЕСТОВЫЙ аккаунт
(см. GOOGLE_SYNC_SETUP.md).

## Локальное хранилище (изолированное)

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
  импорта старых `models/`;
- Phase 1 не добавляет миграцию или колонку: scheduling presets и временное
  состояние UI остаются в domain/ViewModel, а не в БД.

## Слои

```
Domain (planner_desktop/domain)
   ↓  чистые dataclass-ы и правила валидации; scheduling.py считает
      пресеты/snooze, layout.py определяет compact/normal/wide,
      keyboard.py задаёт контекстную политику shortcuts; без Qt/Flet/SQLModel
Repository (planner_desktop/repositories + planner_desktop/storage)
   ↓  SQLiteTaskRepository — по умолчанию (изолированный app_desktop.db);
      FakeTaskRepository — для тестов и демо-режима; общий контракт —
      Protocol TaskRepository; CalendarSyncStore — очередь Calendar-операций
      и состояние синка в той же БД
Use cases (planner_desktop/usecases)
   ↓  DesktopTaskService: CRUD + schedule/unschedule/postpone/restore задач
      с постановкой Calendar-операций в очередь; postpone переиспользует
      существующие schedule/unschedule правила, не вызывает Google API;
      DailyTaskService: ежедневные задачи (локально);
      HistoryService: журнал выполненного (разовые по Task.completed_at +
      отметки ежедневных), группировка по датам, фильтр диапазона
ViewModels (planner_desktop/viewmodels)
   ↓  QObject-обёртки: свойства, сигналы, слоты для QML; CRUD — через сервис.
      TodayViewModel, CalendarViewModel и HistoryViewModel наследуют общий
      TaskActionsViewModel: selected task, editor data/presets, snooze,
      complete/delete/restore, busy-guard, toast и tasksMutated; поэтому
      диалог редактирования и поведение действий едины на всех страницах.
      UiStateViewModel экспортирует layout mode, minimum window, human-date,
      time options и проверку shortcuts; task_rows.py —
      общие чистые преобразования Task -> словари для QML;
      SettingsViewModel — статус локального состояния (без сети): разбивка
      очереди по типам операций, последнее локальное изменение, диагностика.
      Сигнал tasksMutated («я изменил задачи») соединяется в MainWindow
      с refresh() остальных ViewModel-ей; refresh() эмитит только
      *Changed-сигналы, поэтому петля исключена; для ежедневных задач
      аналогичная пара dailyMutated/refreshDaily.
QML UI (planner_desktop/qml)
   ↓  ApplicationWindow + Sidebar + страницы (Сегодня/Календарь/История/Настройки);
      дизайн-система: qml/theme/Theme.qml (singleton с токенами цветов/
      отступов/типографики и цветами приоритетов из старого приложения) +
      qml/components (Panel, AppButton, IconButton, Badge, PriorityPill,
      SectionHeader, EmptyState, TaskCard, TaskEditorDialog, ConfirmDialog,
      QuickAdd, Sidebar, DatePickerField, TimePickerField, DurationPicker,
      SegmentedControl, SchedulePresetBar, SnoozeMenu, Toast) — без
      картинок-ассетов, единая векторная иконографика AppIcon;
      Main.qml применяет compact/normal/wide и minimum size из UiStateViewModel
Sync (planner_desktop/sync)
      CalendarSyncEngine + calendar_mapper + FakeCalendarGateway +
      GoogleCalendarGateway (реальный, тот же контракт CalendarGateway;
      сервис Calendar API инъецируется — в тестах фейковый объект) +
      google_auth (изолированный OAuth: token.json в профиле
      PlannerDesktop; google-импорты ленивые, при импорте модулей ни
      OAuth, ни сети); ручной запуск — usecases/manual_sync_service.py
```

Правило зависимостей: внутренние слои не зависят от UI — domain не знает
ни про repository, ни про Qt; QML разговаривает только с viewmodels;
движок синхронизации работает с domain + repository + очередью через
шлюзы, не трогая UI.

Пользовательская карта клавиш и контекстные ограничения вынесены в
[`SHORTCUTS.md`](SHORTCUTS.md); `Ctrl+R` там явно отделён от ручного
Google-синка.

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

### Текущая конфликтная политика (детерминированная)

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
- unschedule (снятие даты) реализован на уровне DesktopTaskService:
  у непушенной задачи снимается pending create; у привязанной одиночной
  задачи ставится delete события (event_id — в payload операции), задача
  отвязывается и остаётся локальной; у ЭКЗЕМПЛЯРА повторяющегося события
  снять дату нельзя — сервис возвращает ошибку (уроки dead-letter
  старого приложения);
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
6. Разрешение конфликтов выполняет `CalendarSyncEngine` уровнем выше
   шлюза; gateway только переносит данные и отдаёт etag.

## Статус компонентов

| Компонент | Статус |
|---|---|
| Доменная модель Task с полями Calendar-синка | готова |
| Валидация Quick Add и формы редактора (domain/commands.py) | готова, покрыта тестами |
| FakeTaskRepository | исторический in-memory слой; остаётся для тестов и демо-режима |
| SQLiteTaskRepository (storage/) | экспериментальный, изолированный, по умолчанию |
| Миграция старого app.db | НЕ выполняется в Phase 1; отдельный read-only импорт отложен до roadmap Phase 5 |
| Дизайн-система QML (theme/ + components/) | готова: токены, кнопки, карточки, диалоги |
| Phase 1 domain policy | `scheduling.py`, `layout.py`, `keyboard.py`: детерминированные пресеты/snooze, responsive thresholds и shortcut routing; чистый Python, покрыт focused tests |
| Shared task actions / UI state | `TaskActionsViewModel` объединяет editor/actions/selection/busy/toasts для Today/Calendar/History; `UiStateViewModel` отдаёт QML layout и keyboard policy |
| TodayPage | polished responsive UI: native Quick Add, Today/undated lists, selection, card/inspector actions, snooze, editor/delete confirmation, rail в wide и drawer в normal/compact |
| CalendarPage | недельная полоса, ◀/Сегодня/▶ (+ стрелки ← / → по дням), агенда выбранного дня с фильтрами (все/активные/выполненные/ежедневные), чек-лист ежедневных, общий editor/actions/snooze и responsive inspector; почасовая сетка и DnD — roadmap Phase 2 |
| SettingsPage | режим, путь БД, счётчики очереди с разбивкой по типам (create/update/delete/dead-letter), время последнего локального изменения, курсор pull-а, панель «Диагностика» с копированием; статус подключения Google + кнопки «Подключить Google Calendar» и «Синхронизировать сейчас» (работа в фоновом потоке, прогресс/итог/ошибка на странице, время последнего успешного синка) |
| HistoryPage | журнал выполненного по датам, фильтр 7/30/всё, restore/edit/delete через общий контракт действий; полностью локально |
| HistoryService + Task.completed_at | готовы: миграция схемы v3 → v4 аддитивно добавляет tasks.completed_at и заполняет для уже выполненных задач их updated_at |
| DailyTaskService / ежедневные задачи | готовы (локально, в Calendar не уходят); отметки хранят момент выполнения — «История» показывает их по датам |
| TaskEditorDialog (создание/правка) | готов: режимы «Без даты»/«Весь день»/«Со временем», native date/time/duration controls, scheduling presets, приоритет/completed, inline validation, busy guard и отдельное delete-действие |
| DesktopTaskService (usecases/) | готов: create/update/delete/restore, schedule/unschedule/postpone; Calendar-операции идут только через store, прямого Google-вызова из UI нет |
| Unschedule (запланирована -> без даты) | реализован для непушенных и привязанных одиночных задач; для экземпляров повторяющихся серий — запрещён с ошибкой |
| Очередь Calendar-операций (calendar_sync_store.py) | готова, с ретраями, dead-letter и счётчиками для UI |
| Маппер Task ↔ CalendarEvent | готов, покрыт тестами |
| CalendarSyncEngine (двусторонний) | готов, работает на FakeCalendarGateway |
| FakeCalendarGateway | готов: журнал изменений, etag-и, инъекция ошибок |
| GoogleCalendarGateway (реальный) | реализован (sync/google_calendar_gateway.py): dateTime/dateTime для timed, date/date c эксклюзивным концом для all-day, формы не смешиваются (PATCH явно null-ит противоположную), pull через nextSyncToken c showDeleted, HTTP 410 → детерминированный полный пересбор, ошибки классифицируются retryable/terminal; сервис Calendar API инъецируется, при импорте ни OAuth, ни сети |
| Изолированный OAuth десктопа (sync/google_auth.py) | token.json и secrets/client_secret.json в профиле PlannerDesktop (учитывает PLANNER_DESKTOP_DATA_DIR); старый профиль не читается; вход только явным действием, рекомендуется тестовый аккаунт |
| Ручной синк (usecases/manual_sync_service.py + scripts/desktop_calendar_sync_once.py + кнопка в настройках) | реализован: один цикл push+pull, повторный одновременный запуск отвергается, структурный результат (pushed/pulled/очередь/dead-letter/курсор/ошибка), сводка сохраняется в desktop_sync_state |
| Автоматический/фоновый синк | НЕ реализован сознательно: ни при старте, ни по таймеру — только явные действия пользователя |

Подробная инвентаризация фич относительно старого приложения —
в `FEATURE_PARITY.md` (этот же каталог).

## Тесты

Чистая Python-логика тестируется без видимого окна. Каноническая
верификация перед закрытием Phase 1:

```
python -m compileall . -q
python -m pytest --collect-only -q
python -m pytest -q tests/test_desktop_scheduling.py tests/test_desktop_task_editor_viewmodel.py tests/test_desktop_task_postpone.py tests/test_desktop_keyboard_actions.py tests/test_desktop_responsive_state.py
python -m pytest -q
```

Focused Phase 1 тесты проверяют scheduling/presets, переходы editor,
Calendar-очередь при postpone/unschedule/delete, busy guard, shortcut routing,
responsive state, refresh и repository reopen. Существующие desktop sync-тесты
не удаляются и входят в полный прогон. На Windows известен отдельный
платформенный провал `tests/test_settings_paths.py::test_macos_data_dir`; он не
исправляется в Phase 1. Фактический статус финального прогона фиксируется в
[`PRODUCT_ROADMAP.md`](PRODUCT_ROADMAP.md), а не объявляется архитектурной
гарантией заранее.
