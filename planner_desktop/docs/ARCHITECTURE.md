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
- schema v5 аддитивно и идемпотентно добавляет локальные `tags` и
  `task_tags`: FK `task_uid`/`tag_id`, cascade удаляет только связи,
  индексы ускоряют обе стороны association; задачи не удаляются вместе с тегом;
- `tags.normalized_name` уникален и вычисляется Python NFKC+casefold;
  отображаемый регистр сохраняется, имя trim-ится и ограничено 32 символами,
  на задачу разрешено не более 10 тегов;
- schema v6 добавляет локальные TaskSeries/templates и occurrence identity;
  schema v7 аддитивно добавляет независимый read-only
  `external_calendar_series` без FK/cascade к Task или TaskSeries;
- scheduling presets, Calendar layout и временное selection state остаются
  в domain/ViewModel, а не в БД.

## Слои

```
Domain (planner_desktop/domain)
   ↓  чистые dataclass-ы и правила валидации; scheduling.py считает
      пресеты/snooze, layout.py определяет compact/normal/wide,
      keyboard.py задаёт контекстную политику shortcuts;
      tags.py задаёт Unicode validation/limits, task_search.py — чистые
      query/filter/ranking rules;
      recurrence.py — локальная Phase 3.2A семантика;
      google_recurrence.py — чистый lossless Google RRULE transport;
      external_series.py — read-only модель удалённого мастера;
      calendar_layout.py режет события по дням/видимому диапазону и
      детерминированно раскладывает overlap-колонки;
      calendar_interactions.py рассчитывает snap/target/move/resize/conversion
      proposals и structured rejection; без Qt/Flet/SQLModel
Repository (planner_desktop/repositories + planner_desktop/storage)
   ↓  SQLiteTaskRepository — по умолчанию (изолированный app_desktop.db);
      FakeTaskRepository — для тестов и демо-режима; общий контракт —
      Protocol TaskRepository; TagRepository/SQLiteTagRepository — локальные
      теги и associations; CalendarSyncStore — очередь Calendar-операций
      и состояние синка в той же БД; ExternalSeriesRepository имеет SQLite
      и in-memory реализации, не связывая каталог с локальной TaskSeries
Use cases (planner_desktop/usecases)
   ↓  DesktopTaskService: CRUD + schedule/unschedule/postpone/restore задач,
      explicit move/resize/timed↔all-day operations и компенсирующий rollback
      repository + queue при ошибке
      с постановкой Calendar-операций в очередь; postpone переиспользует
      существующие schedule/unschedule правила, не вызывает Google API;
      TagService: local-only CRUD/assignment без Calendar queue;
      SearchService: Python casefold search поверх repository results;
      BulkTaskService: детерминированная пачка и structured item results;
      duplicate_task: независимая копия без Google/recurrence metadata;
      DailyTaskService: ежедневные задачи (локально);
      HistoryService: журнал выполненного (разовые по Task.completed_at +
      отметки ежедневных), группировка по датам, фильтр диапазона
ViewModels (planner_desktop/viewmodels)
   ↓  QObject-обёртки: свойства, сигналы, слоты для QML; CRUD — через сервис.
      TodayViewModel, CalendarViewModel и HistoryViewModel наследуют общий
      TaskActionsViewModel: selected task, editor data/presets/tags, snooze,
      duplicate, multi-selection/bulk, complete/delete/restore, busy-guard,
      toast и tasksMutated; поэтому
      диалог редактирования и поведение действий едины на всех страницах.
      CalendarViewModel добавляет режимы day/work_week/week, visible dates,
      all-day/timed geometry, current-time data, period/event navigation,
      drag/resize proposal state и responsive undated-panel data;
      overlap-математика в ViewModel/QML не дублируется.
      UiStateViewModel экспортирует layout mode, minimum window, human-date,
      time options и проверку shortcuts; task_rows.py —
      общие чистые преобразования Task -> словари для QML;
      SearchViewModel — overlay state, filters, result navigation и selection;
      SettingsViewModel — статус локального состояния (без сети), управление
      tags/counts, разбивка очереди, последнее локальное изменение, диагностика.
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
      SegmentedControl, SchedulePresetBar, SnoozeMenu, Toast, GlobalSearch,
      SearchFilterBar/SearchResultRow, TagChip/TagPicker, BulkActionToolbar,
      CalendarTimeGrid/DayColumn/EventBlock/AllDayLane/TimeRuler/
      CurrentTimeIndicator/ViewModeSwitch/CalendarDropPreview/
      CalendarResizeHandle/UndatedTaskPanel/UndatedTaskDragCard) — без
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

## Phase 3.1: поиск, теги, duplicate и bulk consistency

Поиск полностью выполняется в Python: NFKC+casefold для query/title/notes/tags,
substring words и простые quoted phrases. Фильтры status (`active`, `completed`,
`all`), scope (`today`, `this_week`, `scheduled`, `undated`, `all_day`), priority
и tags применяются до ranking. Порядок: exact title, title prefix, all terms in
title, title+tags, notes; затем completion, scheduled time, newest `updated_at`,
uid. Empty query с фильтрами валиден. SQLite `lower()` и FTS5 не используются.

Теги — только Planner Desktop metadata. Они не попадают в Calendar description,
не меняют Calendar mapper и не ставят create/update/delete. Rename сохраняет
association rows; delete каскадно удаляет лишь `task_tags`. Любая mutation идёт
через `TagService`/ViewModel и общий `tasksMutated`, поэтому Today, Calendar,
History, Search и Settings counts обновляются без QML page-to-page coupling.

Duplicate создаётся сервисом, а не QML-копированием. У копии новый uid, active
state и отсутствуют event id, etag, recurring id/original start и sync metadata.
Undated-копия локальна; scheduled-копия проходит обычный create path и получает
ровно одну Calendar create operation. Recurring instance становится независимой
ordinary task; tombstone отклоняется.

`TaskSelection` хранится только в памяти и ограничивается текущими видимыми
rows. Bulk выполняет элементы в стабильном порядке. Внутри одного item mutation
repository+queue компенсируется при исключении; batch не откатывает уже успешные
независимые items, а продолжает и возвращает `BulkActionResult` с
affected/skipped/failed и каждым `BulkActionItemResult`. Unsafe schedule changes
recurring instances пропускаются без mutation. Tag-only bulk не затрагивает
Calendar queue; schedule/unschedule/delete переиспользуют существующую sync
семантику. Один busy guard предотвращает повторный быстрый запуск.

## Ядро Calendar-синхронизации и Google recurring masters

Состав (`planner_desktop/sync/` + `planner_desktop/storage/`):

- `sync_types.py` — `CalendarEvent` (собственная модель события, без
  Google-клиентов) с recurrence/timezone transport и disjoint properties
  ordinary/master/instance, `PendingOp`, `CalendarPullStats`, ошибки шлюза
  (`RetryableGatewayError` / `TerminalGatewayError`);
- `calendar_mapper.py` — чистый маппинг Task ↔ CalendarEvent;
- `calendar_sync_engine.py` — двусторонний движок: push очереди,
  pull изменений, конфликтная политика; optional master handler стоит до
  Task mapping, поэтому master не может стать ordinary Task;
- `fake_calendar_gateway.py` — in-memory календарь для тестов/разработки:
  etag-и, updated_at, журнал изменений с курсором (аналог syncToken),
  all-day/timed, master и changed/cancelled instance события, без expansion
  бесконечной серии, инъекция ошибок;
- `storage/calendar_sync_store.py` — локальная очередь push-операций
  (`desktop_pending_calendar_ops`) и состояние синка
  (`desktop_sync_state`) в том же изолированном `app_desktop.db`.
- `domain/google_recurrence.py` — чистый canonical/lossless RRULE codec;
  unsupported constructs сохраняются raw и никогда не упрощаются;
- `storage/external_series_repository.py` — schema v7 catalog;
  `usecases/external_series_service.py` отдаёт Settings только локальные
  rows/counts и никогда не строит gateway.
- `domain/series_calendar_link.py`, `storage/calendar_series_sync_store.py` и
  `usecases/series_calendar_link_service.py` — schema v8 link/queue/quarantine,
  stable remote id, validation и явные lifecycle actions;
- `calendar_series_mapper.py` и `calendar_series_sync_engine.py` — master body,
  fingerprint, create/update/delete и remote-success/local-failure
  reconciliation. Series queue не смешивается с ordinary Task queue.

Поток данных:

- Quick Add / правки в UI → `DesktopTaskService` → репозиторий +
  постановка операции в очередь (создание события — только задачам
  с датой);
- `CalendarSyncEngine.push_pending()` — отложенные операции уходят в
  шлюз: create возвращает id/etag (записываются в задачу), update идёт
  патчем, тумбстоун — delete-ом; временная ошибка → ретрай с бэкоффом,
  после `MAX_ATTEMPTS` или постоянной ошибки — dead-letter (terminal),
  бесконечных ретраев нет;
- `CalendarSyncEngine.pull_remote_changes()` — ordinary event использует
  прежний create/update/tombstone Task path; recurring instance — тот же path
  с recurring metadata только для unlinked external master; linked instance
  quarantine-ится. Linked master даёт echo, conflict либо remote_deleted;
  ошибка link/catalog/quarantine persistence выходит до записи cursor.
- `ManualSyncService`: series-master push → ordinary Task push → pull → одна
  persisted summary. UI/page-open никогда не строит сетевой запуск.

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
- recurring master хранится отдельно от TaskSeries/Task; schema v8 link
  соединяет только явным действием и не каскадирует completed/history;
- ordinary insert/patch/delete recurrence по-прежнему не принимают. Для master
  есть отдельные insert/get/patch/delete методы с deterministic id, markers,
  etag check и already-absent delete;
- materialized local occurrences всегда дают нулевую дельту ordinary Calendar
  queue. Только series-level owned поля ставят coalesced master op;
- linked occurrence schedule/delete/split/bulk schedule mutations блокируются
  до Phase 3.2B3; completion/tags остаются локальными.

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
| Calendar layout engine | Phase 2.1: `domain/calendar_layout.py`, normalized top/height, clipping видимых часов/границ дня, minimum visual duration, all-day spans и deterministic interval coloring; Qt-free |
| Calendar interaction engine | Phase 2.2: `domain/calendar_interactions.py`, deterministic 15/5-minute snapping, clamping, timed/all-day/undated conversion, resize proposals и structured rejection; Qt-free |
| CalendarPage | Phase 2.2: возможности Phase 2.1 плюс safe DnD/resize, translucent valid/invalid preview, timed ↔ all-day, responsive undated panel, bounded focus-aware vertical auto-scroll и keyboard alternatives; authoritative geometry обновляется только после service commit |
| SettingsPage | режим, БД, очередь/курсор/диагностика, подключение и только ручной sync; B1 read-only catalog плюс B2 local-only linked/pending/conflict/remote-deleted/terminal/quarantine diagnostics; page-open Google call отсутствует |
| HistoryPage | журнал выполненного по датам, фильтр 7/30/всё, restore/edit/delete через общий контракт действий; полностью локально |
| HistoryService + Task.completed_at | готовы: миграция схемы v3 → v4 аддитивно добавляет tasks.completed_at и заполняет для уже выполненных задач их updated_at |
| DailyTaskService / ежедневные задачи | готовы (локально, в Calendar не уходят); отметки хранят момент выполнения — «История» показывает их по датам |
| TaskSeries / RecurrenceService | local authority: идемпотентная материализация и history semantics Phase 3.2A сохранены; B2 series-level owned edit coalesce-ит один master UPDATE, tag/completion не ставят op |
| GoogleRecurrence codec | Phase 3.2B1 готов: canonical daily/weekly/monthly/yearly subset, interval/BYDAY/BYMONTHDAY/BYMONTH/COUNT/UNTIL/safe WKST, EXDATE/RDATE/TZID transport, structured unsupported reasons и timezone-safe inclusive UNTIL |
| External series catalog | B1 schema v7 сохранена; B2 добавляет Planner ownership/link metadata, но чужие masters не усыновляются |
| Series link/queue/quarantine | Phase 3.2B2 schema v8: separate historical links, independent coalescing queue/dead-letter и changed-instance quarantine; additive/idempotent/reopen-safe |
| CalendarSeriesSyncEngine | готов: deterministic create, etag-safe update, explicit delete, catalog/link persistence и idempotent reconciliation после non-atomic failure |
| OccurrenceMaterializer | готов: Today запрашивает сегодня, Calendar — видимый диапазон, буфер 14 дней, предел 366 экземпляров на серию за вызов; History генерацию не запускает |
| TemplateService | готов: локальные ordinary/recurring шаблоны, NFKC+casefold уникальность имени, Settings CRUD/duplicate и неперсистентный editor prefill |
| TaskEditorDialog (создание/правка) | готов: режимы «Без даты»/«Весь день»/«Со временем», native date/time/duration controls, scheduling presets, приоритет/completed, inline validation, busy guard и отдельное delete-действие |
| DesktopTaskService (usecases/) | готов: create/update/delete/restore, schedule/unschedule/postpone и Phase 2.2 move/resize/conversion; linked change enqueue update, undated schedule enqueue create, linked unschedule enqueue delete; recurring instance rejected before mutation; прямого Google-вызова из UI нет |
| Unschedule (запланирована -> без даты) | реализован для непушенных и привязанных одиночных задач; для экземпляров повторяющихся серий — запрещён с ошибкой |
| Очередь Calendar-операций (calendar_sync_store.py) | готова, с ретраями, dead-letter и счётчиками для UI |
| Маппер Task ↔ CalendarEvent | готов, покрыт тестами |
| CalendarSyncEngine (двусторонний) | ordinary/unlinked-instance behavior сохранён; linked master echo/conflict/remote_deleted и linked-instance quarantine выполняются до Task mapping; persistence failure не продвигает cursor |
| FakeCalendarGateway | готов: deterministic change journal/cursor, etag, masters + changed/cancelled instances, без expansion, инъекция ошибок |
| GoogleCalendarGateway (реальный) | ordinary behavior неизменён; отдельные master insert/get/patch/delete используют supplied id, recurrence/timezone/private markers, etag validation и retryable/terminal classification |
| Изолированный OAuth десктопа (sync/google_auth.py) | token.json и secrets/client_secret.json в профиле PlannerDesktop (учитывает PLANNER_DESKTOP_DATA_DIR); старый профиль не читается; вход только явным действием, рекомендуется тестовый аккаунт |
| Ручной синк (usecases/manual_sync_service.py + scripts/desktop_calendar_sync_once.py + кнопка в настройках) | series push → ordinary push → pull, concurrency guard и persisted summary; B2 result добавляет created/updated/deleted/conflict/terminal/quarantine counts |
| Автоматический/фоновый синк | НЕ реализован сознательно: ни при старте, ни по таймеру — только явные действия пользователя |

Подробная инвентаризация фич относительно старого приложения —
в `FEATURE_PARITY.md` (этот же каталог).

## Тесты

Чистая Python-логика тестируется без видимого окна. Каноническая
верификация Phase 3.2B2:

```
python -m compileall . -q
python -m pytest --collect-only -q
python -m pytest -q tests/test_desktop_series_calendar_link_schema.py tests/test_desktop_series_calendar_link_repository.py tests/test_desktop_series_connect_validation.py tests/test_desktop_series_sync_queue.py tests/test_desktop_series_master_mapper.py tests/test_desktop_google_master_write_gateway.py tests/test_desktop_calendar_series_sync_engine.py tests/test_desktop_series_sync_reconciliation.py tests/test_desktop_linked_master_pull.py tests/test_desktop_linked_instance_quarantine.py tests/test_desktop_series_link_viewmodel.py tests/test_desktop_series_link_sync_isolation.py
python -m pytest -q tests/test_desktop_google_rrule.py tests/test_desktop_google_recurrence_roundtrip.py tests/test_desktop_external_series_schema.py tests/test_desktop_external_series_repository.py tests/test_desktop_google_master_gateway.py tests/test_desktop_google_master_pull.py tests/test_desktop_google_master_diagnostics.py tests/test_desktop_google_recurrence_viewmodel.py tests/test_desktop_google_recurrence_sync_isolation.py
python -m pytest -q tests/test_desktop_recurrence_rules.py tests/test_desktop_recurrence_generation.py tests/test_desktop_series_repository.py tests/test_desktop_recurrence_service.py tests/test_desktop_series_edit_scope.py tests/test_desktop_occurrence_materializer.py tests/test_desktop_series_sync_isolation.py tests/test_desktop_templates.py tests/test_desktop_template_service.py tests/test_desktop_recurrence_viewmodel.py tests/test_desktop_recurrence_keyboard.py
python -m pytest -q
```

Focused Phase 3.2B2 покрывает schema v8/reopen, validation, coalescing,
master mapping/gateway/engine/reconciliation, linked pull/quarantine,
ViewModel и ordinary/series isolation. B1 RRULE/catalog regression остаётся.
Focused Phase 3.2A тесты покрывают rules/DST/month-end,
schema v6/reopen/idempotence, occurrence identity/materialization, exception/tombstone,
transactional split/rollback, templates, ViewModel/QML и нулевую Calendar-queue delta.
Все focused Phase 3.1, Calendar Phase 2 и desktop
sync regression файлы дополнительно запускаются отдельными срезами; существующие
Phase 1/2/sync тесты не удаляются и входят в полный прогон. На Windows известен отдельный
платформенный провал `tests/test_settings_paths.py::test_macos_data_dir`; он не
исправляется в Phase 3.2B2. Фактический статус финального прогона фиксируется в
[`PRODUCT_ROADMAP.md`](PRODUCT_ROADMAP.md), а не объявляется архитектурной
гарантией заранее.
