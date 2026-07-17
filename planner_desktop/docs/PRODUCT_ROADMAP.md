# Продуктовый роадмап Planner Desktop (PySide6 + Qt Quick/QML)

Документ фиксирует оставшийся путь нового десктопа от «работающей
технической базы» до законченного настольного приложения, которое
полностью заменяет старое Flet-приложение.

Точка отсчёта (что уже есть и работает):

- PySide6 + Qt Quick/QML, дизайн-система (`qml/theme` + `qml/components`);
- изолированное SQLite-хранилище `<PlannerDesktop>/app_desktop.db`
  (старый `Planner/app.db` не читается и не пишется);
- страницы «Сегодня», «Календарь», «История», «Настройки»;
- создание/правка/выполнение/удаление задач, ежедневные задачи;
- фильтры календаря, диагностика в настройках;
- реальный `GoogleCalendarGateway`, изолированный OAuth
  (`<PlannerDesktop>/token.json`), явный двусторонний ручной синк;
- живой пилот ручного синка на тестовом Google-аккаунте пройден.

Неизменные ограничения и границы фаз:

- никакого бэкенда: ни сервера, ни REST API, ни Firebase, ни sidecar;
- автоматический/фоновый Google-синк всегда выключен по умолчанию;
  в фазах 1–3 синхронизация запускается только явным действием пользователя,
  а фаза 4 может добавить исключительно opt-in режим с сохранением ручной
  кнопки и безопасного default-off поведения;
- старое Flet-приложение (`main.py`, `ui/`, `services/`, `models/`), старый
  sync-движок, UndatedTasksSync и старые инструменты миграции/пилота/
  dead-letter не изменяются ни в одной фазе; старый `Planner/token.json`
  никогда не читается и не копируется;
- старый `Planner/app.db` не читается и не пишется в фазах 1–4; единственное
  исключение — отдельный, явно запущенный read-only импорт фазы 5, который
  никогда не изменяет исходную БД;
- все данные нового приложения — только в изолированном профиле
  PlannerDesktop;
- протестированное ядро Calendar-синка (mapper, engine, store, gateway)
  не переписывается — расширяется только аддитивно.

Статусы: `план` — не начато; `в работе`; `готово`.

---

## Фаза 1 — UX ежедневного использования (готово)

### Цель для пользователя

Планировать и переносить задачи быстро и без «инженерных» текстовых
полей: нормальные пикеры даты/времени, пресеты переноса, снуз, клавиатура
и аккуратный интерфейс, который не разваливается при изменении размера
окна.

### Фичи

- нативные контролы даты/времени вместо сырого текста:
  `DatePickerField` (месячная сетка, русские подписи дней/месяцев),
  `TimePickerField` (клавиатурный ввод + выбор из списка),
  `DurationPicker` (пресеты 15/30/45/60/90/120 минут + своя длительность);
- редизайн `TaskEditorDialog`: название — главное поле, заметки, режим
  планирования сегментами («Без даты» / «Весь день» / «Со временем»),
  приоритет, «выполнено» для существующих, визуально отделённое
  удаление, инлайн-ошибки; все переходы режимов (без даты ↔ дата,
  весь день ↔ со временем) безопасны;
- пресеты планирования в редакторе и инспекторе: «Сегодня», «Завтра»,
  «Следующий понедельник», «Без даты», «+1 час», «На вечер»
  (детерминированная семантика — см. `domain/scheduling.py`);
- снуз/перенос на карточке задачи и в инспекторе: «Позже сегодня»,
  «Завтра», «Следующая неделя», «Выбрать дату и время», «Без даты»;
- единые действия на всех поверхностях (Сегодня / агенда Календаря /
  История / инспектор): выполнить/вернуть, изменить, удалить
  с подтверждением, вернуть из истории;
- клавиатурный контур: Ctrl+N, Ctrl+Shift+N, Enter, Space, Delete, Esc,
  ←/→ в календаре, Ctrl+S, Ctrl+R; `Ctrl+F` активирован в Phase 3.1 —
  сокращения не срабатывают при конфликтующем наборе текста;
- отзывчивые раскладки: компактная / обычная / широкая; инспектор —
  боковая колонка на широком окне и выезжающая панель на обычном;
- визуальная консистентность: единая иконографика (линейные AppIcon
  вместо эмодзи), состояния hover/selected/focus/pressed, бейджи синка,
  пустые состояния, тосты успеха/ошибки, защита от двойных кликов
  (busy-guard).

### Архитектурная работа

- `domain/scheduling.py` — чистые бизнес-расчёты дат для пресетов и снуза
  (без Qt/QML); QML отвечает только за навигацию внутри визуальной сетки
  пикера и не определяет семантику планирования;
- `usecases/task_service.py` — `postpone_task` поверх существующих
  `schedule_task`/`unschedule_task`/`update_task`; правила Calendar-очереди
  не дублируются, а переиспользуются (`record_local_*`);
- общая база ViewModel-действий (`viewmodels/task_actions.py`):
  занятость (busy), пресеты, снуз, тосты ошибок — один контракт для
  Today/Calendar/History;
- `domain/layout.py` + `viewmodels/ui_state.py` — пороги раскладки
  (compact/normal/wide) в Python, QML только применяет режим;
- `domain/keyboard.py` — политика маршрутизации сокращений
  (что разрешено при фокусе в текстовом поле) в чистом Python;
- схема БД НЕ меняется: пресеты и состояние UI не персистятся.

### Тесты

- `tests/test_desktop_scheduling.py` — следующий понедельник, сегодня/
  завтра/вечер/+1 час/позже сегодня, пресеты длительности, переходы
  timed ↔ all-day;
- `tests/test_desktop_task_editor_viewmodel.py` — редактор через
  ViewModel: создание/правка всех режимов, невалидный ввод, пресеты формы,
  сигналы обновления;
- `tests/test_desktop_task_postpone.py` — снуз ставит правильные операции
  в Calendar-очередь (update для привязанных, create для новых, delete при
  unschedule привязанной), запреты для экземпляров повторяющихся;
- `tests/test_desktop_keyboard_actions.py` — маршрутизация сокращений
  не конфликтует с текстовым вводом;
- `tests/test_desktop_responsive_state.py` — пороги compact/normal/wide;
- все существующие desktop-тесты (включая синк) остаются зелёными.

### Риски

- Qt Quick пикеры легко сделать «мигающими»/теряющими фокус — попапы
  обязаны закрываться по Esc и не воровать фокус у полей;
- двусмысленная семантика пресетов («Завтра» от сегодня или от даты
  задачи?) — снимается фиксацией правил в `domain/scheduling.py` и тестах;
- перенос экземпляров повторяющихся событий опасен (уроки dead-letter
  старого приложения) — снуз обязан отказывать с человекочитаемой ошибкой;
- рост дублирования слотов в трёх ViewModel — общая база `task_actions`.

### Критерии приёмки

- в редакторе нет ни одного сырого текстового поля даты/времени;
  невалидная строка не доходит до пользователя, Python-валидация остаётся
  последним рубежом;
- режим «Весь день» корректно скрывает время и длительность; неуспешное
  сохранение оставляет диалог открытым и редактируемым, успешное — обновляет
  Today/Calendar/History;
- все шесть пресетов и все пять пунктов снуза работают детерминированно:
  вычисляемые действия покрыты pure-Python тестами, а «Выбрать дату и время»
  подтверждено визуальным smoke как переход в редактор;
- каждый переход расписания (без даты ↔ дата, весь день ↔ время)
  сохраняется, обновляет Today/Calendar/History и ставит корректную
  операцию в Calendar-очередь;
- перенос связанной задачи ставит `update`, планирование недатированной —
  `create`, unschedule использует существующее правило; UI не вызывает
  Google API напрямую, а экземпляры повторяющихся событий сохраняют
  существующие ограничения безопасности;
- Today, Calendar, History и TaskInspector дают согласованные действия:
  выполнить/вернуть, изменить, удалить с подтверждением и восстановить;
  busy-guard блокирует быстрый повтор, кнопки выключены во время операции,
  успех/ошибка видимы в toast, после закрытия возвращается разумный фокус;
- сокращения из [`SHORTCUTS.md`](SHORTCUTS.md) работают и не мешают набору
  текста; `Ctrl+F` открывает глобальный поиск, `Ctrl+R` обновляет только локальные
  модели;
- окно остаётся работоспособным от минимального размера до широкого,
  без наложений и обрезанных русских подписей; tab/focus-состояния и
  доступные подписи контролов проверены;
- QML обращается только к ViewModel/use-case контрактам; Phase 1 не добавляет
  схему БД, сервер, sidecar или скрытое состояние в колонках;
- изолированный визуальный smoke подтверждает все режимы редактора, пресеты,
  snooze, complete/restore/delete, compact/normal/wide, перезапуск и
  сохранность данных;
- ручной Google-синк после изменений подтверждён отдельно; автоматического
  синка при старте/по таймеру нет;
- выполнены `python -m compileall . -q`, `pytest --collect-only -q`, пять
  focused Phase 1 файлов, все desktop-тесты и `pytest -q`; единственный
  допустимый платформенный провал полного набора —
  `tests/test_settings_paths.py::test_macos_data_dir` на Windows.

### Сознательно отложено (НЕ в фазе 1)

- почасовая сетка дня/недели, drag-and-drop, изменение длительности
  мышью — фаза 2;
- глобальный поиск, теги, дубликация и массовые действия — Phase 3.1;
  повторяющиеся серии и шаблоны — Phase 3.2;
- фоновый синк, конфликтный UI, управление dead-letter — фаза 4;
- импорт старой БД, упаковка, откат — фаза 5.

### Статус проверки Phase 1

Фаза **закрыта 14 июля 2026 года**: функциональный, регрессионный и
визуальный прогоны выполнены на Windows. Live-вызов Google API в финальном
прогоне сознательно не выполнялся: scratch-профиль не содержит OAuth-секрета,
а проверка не должна менять внешний календарь. Ручной путь подтверждён
отдельным sync-набором и сохраняет ранее пройденный live pilot как базовую
интеграционную проверку.

| Проверка | Статус | Результат |
|---|---|---|
| Пять focused Phase 1 файлов | PASS | `127 passed` |
| Все desktop-тесты | PASS | `491 passed`; отдельный sync-регрессионный срез — `193 passed` |
| Compile + collection + полный pytest | PASS с известным исключением | compileall — без ошибок; `612 tests collected`; полный прогон — `611 passed`, единственный сбой `test_macos_data_dir` на Windows |
| Изолированный UI smoke + persistence после restart | PASS | 3 режима создания, 4 перехода расписания, 6 пресетов, 5 snooze-путей, complete/restore, delete confirmation, обе цели shortcuts и 3 layout-режима; после перезапуска обе оставленные задачи перечитаны из `D:\planner-desktop-ui-smoke` |
| Ручной Google-синк после Phase 1 | PASS без live-вызова | 193 теста покрывают очередь, mapper, engine, gateway, `ManualSyncService`, Settings и CLI; ручные контролы присутствуют на обновлённом Settings screenshot |
| No auto-sync + legacy scope | PASS | startup/page-open/timer sync отсутствует; изменения ограничены `planner_desktop`, desktop-тестами и документацией, старый Flet/DB/token/tooling не затронут |

Подготовленные обязательные скриншоты:

- [Today — wide](screenshots/today_wide_phase1.png)
- [Today — compact](screenshots/today_compact_phase1.png)
- [Редактор — timed](screenshots/task_editor_timed_phase1.png)
- [Редактор — all-day](screenshots/task_editor_allday_phase1.png)
- [Calendar — normal](screenshots/calendar_normal_phase1.png)
- [History](screenshots/history_phase1.png)
- [Settings](screenshots/settings_phase1.png)

Дополнительные подтверждения деталей:

- [Date picker](screenshots/date_picker_phase1.png)
- [Time picker](screenshots/time_picker_phase1.png)
- [Snooze menu](screenshots/snooze_menu_phase1.png)
- [Inspector drawer](screenshots/inspector_drawer_phase1.png)
- [Минимальная ширина Today](screenshots/today_min_phase1.png)

---

## Фаза 2 — Полноценный календарный UI (частично)

### Фаза 2.1 — Почасовая основа (готово)

#### Результат для пользователя

Calendar показывает реальные события в почасовой сетке в трёх режимах:
«День», «Рабочая неделя» (Пн–Пт) и «Неделя» (Пн–Вс). Существующие агенда,
фильтры, ежедневный чек-лист, редактор, сводка дня и инспектор сохранены.

#### Реализовано

- видимый диапазон 06:00–23:00, фиксированный time ruler, часовые и
  получасовые линии, вертикальные разделители дней и прокрутка;
- отдельная all-day lane с однодневными/многодневными событиями,
  детерминированным порядком и «ещё N» при переполнении;
- нормализованная event geometry: clipping до видимых часов и границ дня,
  minimum visual duration для нулевой/битой длительности, разбиение
  cross-midnight события на дневные блоки;
- overlap layout: half-open интервалы (касание концом не overlap),
  стабильная сортировка start/duration/uid, две/три side-by-side колонки,
  chained overlap groups с переиспользованием освободившейся колонки;
- selected/today/focus/hover/completed/priority/pending/dead-letter состояния,
  доступные подписи событий и информация не только цветом;
- current-time line только на сегодняшнем дне и внутри диапазона; initial
  auto-scroll рядом с текущим временем, иначе к 08:00; минутное обновление
  линии не двигает scroll;
- мышь: один клик выбирает событие и открывает/reuses inspector, двойной —
  общий TaskEditorDialog; клик пустого слота лишь визуально выбирает время;
- клавиатура: ←/→ день, PageUp/PageDown период, Home сегодня, ↑/↓ видимое
  событие, Enter edit, Space complete/uncomplete, Esc clear; политика
  `domain/keyboard.py` не перехватывает TextInput/TextEdit и диалоги;
- responsive: wide grid + inspector rail, normal grid + drawer, compact
  принудительно начинает с читаемого Day mode; multi-day grid при нехватке
  ширины прокручивается горизонтально;
- схема БД и sync-ядро не менялись; режим отображения не персистится.

#### Архитектура

- `domain/calendar_layout.py` — Qt-free `CalendarGridConfig`,
  `CalendarEventBlock`, `CalendarDayColumn`, `OverlapGroup` и чистый interval
  coloring; QML не считает overlap;
- `CalendarViewModel` отдаёт visible dates, all-day/timed rows, normalized
  ratios, current-time data и period/event navigation;
- reusable QML: `CalendarTimeGrid`, `CalendarDayColumn`,
  `CalendarEventBlock`, `CalendarAllDayLane`, `CalendarTimeRuler`,
  `CurrentTimeIndicator`, `CalendarViewModeSwitch`;
- старая agenda-first область стала сворачиваемой нижней секцией и продолжает
  использовать общие TaskCard/TaskInspector/TaskEditorDialog/use-case слоты.

#### Тесты и visual smoke

- `test_desktop_calendar_layout.py` — top/height, clipping, midnight,
  minimum duration, all-day exclusion/multi-day placement;
- `test_desktop_calendar_overlap.py` — touching, 2-way, 3-way, chained,
  input-order stability;
- `test_desktop_calendar_grid_viewmodel.py` — modes/dates, periods/today,
  current time, geometry rows, selection refresh, compact behavior;
- `test_desktop_calendar_grid_keyboard.py` — plain/text/dialog routing;
- профиль `D:\planner-desktop-calendar-smoke`: 14 synthetic tasks,
  pending + dead-letter, scroll/current-time, click/double-click,
  agenda/daily, три display modes, compact и reopen persistence `14/14`.

Скриншоты:

- [Day grid](screenshots/calendar_day_grid_phase2.png)
- [Work week grid](screenshots/calendar_workweek_grid_phase2.png)
- [Week grid](screenshots/calendar_week_grid_phase2.png)
- [Overlap + inspector](screenshots/calendar_overlap_phase2.png)
- [All-day overflow](screenshots/calendar_allday_phase2.png)
- [Compact day](screenshots/calendar_compact_phase2.png)

#### Статус проверки Phase 2.1

Фаза **2.1 закрыта 14 июля 2026 года** на Windows. Live Google API не
вызывался: smoke-профиль не содержит реальных OAuth-данных, а сетевой путь
проверен существующим regression-набором через fake/injected gateways.

| Проверка | Результат |
|---|---|
| Focused Phase 2.2 interactions | `51 passed` |
| Phase 2.1 Calendar layout/grid regression | `64 passed` |
| Явный sync regression slice | `184 passed` |
| Collection | `731 tests collected` |
| Полный pytest | `730 passed`; единственный failure — известный Windows `test_macos_data_dir` |
| Compileall | PASS |
| Visual + interaction smoke | PASS: move same/cross-day, undated → timed/all-day, timed ↔ all-day, resize, recurring refusal, Escape, keyboard, auto-scroll, responsive modes, persistence и queue state |
| Sync safety | Manual sync controls/service/engine/gateway regression green; startup/page-open/minute timer не вызывают Google, automatic sync остаётся disabled |
| Scope safety | Только `planner_desktop/`, desktop tests/docs/screenshots; old Flet, `main.py`, old sync/Undated/migration tooling, old DB и old token не изменены |

### Фаза 2.2 — Интерактивное перемещение (готово)

Реализовано поверх Phase 2.1 без изменения layout engine:

- drag-and-drop timed-событий между слотами и днями с детерминированным
  округлением к 15 минутам (`Shift` — временно к 5 минутам) и сохранением
  длительности;
- resize за нижний handle, минимум 15 минут и ограничение видимыми границами;
- timed ↔ all-day, включая 60-минутную длительность по умолчанию при переносе
  all-day в сетку и сохранение span многодневного all-day события;
- responsive-панель активных задач «Без даты»: постоянная в wide, drawer в
  normal и bottom-sheet drawer в compact; поддержаны schedule и unschedule;
- чистый `domain/calendar_interactions.py`, optimistic preview без мутации
  layout-модели, commit только через `DesktopTaskService` и компенсирующий
  rollback repository + Calendar queue при ошибке;
- bounded auto-scroll у верхнего/нижнего края, который останавливается при
  drop/cancel и потере фокуса окна;
- keyboard alternatives и доступные имена/статус для drag/resize;
- перенос/resize linked recurring instance отклоняется до repository/queue
  mutation с сообщением «Перенос экземпляров повторяющихся событий пока не
  поддерживается».

Изолированный smoke-профиль подтвердил same/cross-day move, undated → timed и
all-day, timed ↔ all-day, resize, Escape/cancel, keyboard actions, recurring
refusal, restart persistence и ожидаемую очередь. Автоматический Google sync
остаётся выключенным; ручной путь не изменён.

Отложены: редактирование recurring series/всех будущих экземпляров,
автоматический sync, сложный resize многодневных timed-событий, touch/mobile
gestures и горизонтальная auto-scroll недели.

---

## Фаза 3.1 — Поиск, теги, дублирование и массовые действия (готово)

### Цель для пользователя

Быстро найти, организовать и безопасно изменить большой локальный набор задач,
не открывая каждую задачу по отдельности.

### Фичи

- глобальная command-palette поверхность по `Ctrl+F`: кириллица, слова и
  простые quoted phrase, title/notes/tag fields, фильтры status/schedule/
  priority/tags, result count и sync/completion state без данных аккаунта;
- локальные теги в редакторе, карточках, инспекторе и Settings; создание,
  переименование и удаление с task counts; максимум 32 символа, 10 на задачу;
- дублирование из общего service-level пути: свежий uid, active state,
  скопированные title/notes/priority/schedule/tags и очищенная Google/
  recurrence linkage;
- общий неперсистентный `TaskSelection` для Today, Calendar agenda/undated,
  Search и безопасных one-off строк History; Ctrl/Shift и visible-only select all;
- contextual bulk toolbar: complete/restore/priority/add-remove tag/tomorrow/
  unschedule/delete, один busy guard, подтверждение удаления, итог
  affected/skipped/failed;
- доступная клавиатура: Up/Down, Enter, Esc, Ctrl+A, Ctrl+D, Delete; фокус,
  русские подписи и compact/normal/wide не зависят только от цвета.

### Архитектурная работа

- схема v5 аддитивно и идемпотентно добавляет `tags` и `task_tags` с FK,
  cascade только для association rows и индексами; старые task rows совместимы;
- `domain/task_search.py` выполняет NFKC+casefold в Python, не зависит от
  ASCII-only SQLite `lower()` и не вводит FTS5;
- `TagService`, `SearchService`, service-level duplicate и `BulkTaskService`
  отделяют QML от repositories; общий `tasksMutated` обновляет все страницы;
- массовая операция атомарна на уровне каждого task item: repository+queue
  mutation компенсируется при ошибке. Batch продолжает независимые элементы
  и явно возвращает partial failure; молчаливого half-mutated item нет;
- tag-only mutations не вызывают `record_local_*`, duplicate scheduled вызывает
  один create, linked postpone — update, linked unschedule — remote delete + detach,
  delete сохраняет прежнюю tombstone/queue семантику.

### Тесты и критерии приёмки

- поиск по кириллице/заметкам/тегам, фильтры, стабильный выбор и ranking — PASS;
- local tags, persistence/reopen, rename/delete associations и no queue — PASS;
- duplicate scheduled/undated/recurring-copy/tombstone semantics — PASS;
- visible-only selection и все required bulk actions, busy/partial/rollback — PASS;
- Python search по 1000 задачам: median 19.573 ms, worst 21.455 ms — PASS <100 ms;
- responsive QML и шесть visual smoke screenshots — PASS;
- Calendar Phase 2 и manual sync regression — PASS; automatic sync disabled.

### Статус проверки Phase 3.1

Фаза закрыта 14 июля 2026 года на Windows. `compileall` прошёл,
`pytest --collect-only` собрал 775 тестов, focused/Calendar/sync срезы зелёные.
Полный набор: `774 passed`, единственный failure — заранее известный и не
относящийся к задаче `tests/test_settings_paths.py::test_macos_data_dir`.
Изолированный QML smoke: 26 задач, 5 тегов, 0 QML warnings, 6 screenshots;
никаких автоматических Google-вызовов не выполнялось.

### Сознательно отложено

- recurring rules/series editor, templates и «все будущие» — Phase 3.2;
- сложные reminder rules, вложенные задачи/проекты, уведомления ОС, вложения.

---

## Фаза 3.2A — Локальные повторяющиеся задачи и шаблоны (готово)

Phase 3.2A реализует отдельную локальную модель `TaskSeries`, не смешивая её с
`DailyTask` и импортированными экземплярами Google-серий. Схема v6 аддитивно
добавляет определения серий, их теги, шаблоны и неизменяемую идентичность
экземпляра `(series_uid, occurrence_key)`.

Реализовано:

- daily/weekly/monthly/yearly правила, interval, окончание по дате/числу,
  детерминированные month-end и DST политики;
- ограниченная идемпотентная материализация обычных `Task`-строк для Today и
  видимого Calendar-диапазона; History никогда не генерирует будущее;
- явные области «только этот экземпляр» и «этот и все будущие» с exception и
  транзакционным split/rollback;
- tombstone одного слота, остановка/удаление серии с сохранением выполненной
  истории;
- ordinary и recurring шаблоны, управление ими в Settings, меню новой задачи,
  действие «Из шаблона» и `Ctrl+Alt+N`;
- локальные бейджи/сводки в Today, Calendar, Search, History и Inspector;
- нулевая дельта Calendar-очереди для всех операций локальной серии.

Дублирование экземпляра по-прежнему создаёт независимую ordinary task без
series/Google linkage. Автоматический sync остаётся выключенным.

Статус приёмки на 15 июля 2026 года:

| Проверка | Статус | Результат |
|---|---|---|
| Focused Phase 3.2A | PASS | `82 passed`: rules/generation/DST, schema/repositories, materialization, scopes/rollback, sync isolation, templates, ViewModel/QML contracts |
| Phase 3.1 + Calendar Phase 2 + desktop sync | PASS | `365 passed` |
| Compile + collection + полный pytest | PASS с известным исключением | compileall — без ошибок; `859 tests collected`; `858 passed`, единственный сбой — `test_macos_data_dir` на Windows |
| Изолированный smoke + restart | PASS | 7 видов серий, exception, tombstone, split, история, 2 шаблона; `qml_warnings=0`, tombstone сохранён после restart |
| Google safety | PASS | local series queue delta = 0; ordinary drag создаёт ожидающую Calendar-операцию; local drag отклонён; ручные sync-контролы доступны; автоматических Google-вызовов нет |

Скриншоты приёмки:

- [редактор серии](screenshots/recurrence_editor_phase3_2a.png);
- [явный выбор области](screenshots/recurrence_scope_dialog_phase3_2a.png);
- [локальные серии в Calendar](screenshots/calendar_local_series_phase3_2a.png);
- [exception-экземпляр](screenshots/recurrence_exception_phase3_2a.png);
- [выбор шаблона](screenshots/template_picker_phase3_2a.png);
- [шаблоны в Settings](screenshots/settings_templates_phase3_2a.png);
- [compact-редактор](screenshots/recurrence_compact_phase3_2a.png).

## Фаза 3.2B1 — Google recurrence transport и read-only discovery (готово)

Реализован безопасный фундамент без удалённых записей:

- чистый Google RRULE parser/serializer с lossless mapping подмножества
  daily/weekly/monthly/yearly, interval, weekly BYDAY, monthly BYMONTHDAY,
  yearly BYMONTH+BYMONTHDAY, COUNT/UNTIL и безопасного WKST;
- exact raw preservation, structured diagnostics, EXDATE/RDATE/TZID transport;
  BYSETPOS/ordinal BYDAY/multiple RRULE/EXRULE и сложные комбинации не
  упрощаются;
- recurrence-aware `CalendarEvent` и взаимоисключающая классификация
  ordinary/master/instance; pagination, `singleEvents=False`, syncToken и
  HTTP 410 rebuild сохранены;
- schema v7: отдельный `external_calendar_series` без FK к Task/TaskSeries,
  SQLite + in-memory repositories и local-only query service;
- master-aware pull: master никогда не становится Task, instance остаётся на
  прежнем пути, cancelled master тумбстоунит только каталог, catalog failure
  не продвигает cursor;
- консервативный `possible legacy master import` diagnostic без удаления;
- read-only Settings каталог и расширенный ManualSyncResult reporting;
- нулевая очередь для master pull/catalog; local TaskSeries по-прежнему
  local-only; production write paths recurrence не отправляют.

Изолированный smoke на synthetic FakeCalendarGateway подтвердил 9 мастеров
(8 active, 1 unsupported, 1 cancelled), ordinary timed/all-day events,
changed/cancelled instances, второй change sync, идемпотентный следующий sync,
restart persistence, `queue delta = 0`, Settings page-open Google calls = 0 и
`qml_warnings=0`.

Статус приёмки на 15 июля 2026 года:

| Проверка | Статус | Результат |
|---|---|---|
| Focused Phase 3.2B1 | PASS | `63 passed`: RRULE/round-trip, schema/repository, gateway/master pull, diagnostics/ViewModel/isolation |
| Focused Phase 3.2A | PASS | `82 passed` |
| Phase 3.1 search/tag/bulk | PASS | `70 passed` |
| Все Calendar Phase 2 файлы | PASS | `197 passed` |
| Ordinary desktop sync regression | PASS | `163 passed` |
| Все desktop tests | PASS | `803 passed` |
| Compile + collection + полный pytest | PASS с известным исключением | compileall без ошибок; `924 tests collected`; `923 passed`, единственный сбой `test_macos_data_dir` на Windows |
| Visual smoke + restart | PASS | 6 screenshots, `qml_warnings=0`, 9 masters persisted, idempotent follow-up, Settings Google calls = 0, queue delta = 0 |

Скриншоты приёмки:

- [каталог Google-серий](screenshots/google_series_catalog_phase3_2b1.png);
- [поддерживаемое правило](screenshots/google_series_supported_phase3_2b1.png);
- [неподдерживаемое правило + raw RRULE](screenshots/google_series_unsupported_phase3_2b1.png);
- [отменённый master](screenshots/google_series_cancelled_phase3_2b1.png);
- [compact layout](screenshots/google_series_compact_phase3_2b1.png);
- [диагностические счётчики](screenshots/google_series_diagnostics_phase3_2b1.png).

## Фаза 3.2B2 — явная связь с новым Google master (готово)

Поддерживаемая чистая локальная `TaskSeries` теперь по явному действию
связывается с **одним новым** Google recurring master. Подключение не делает
сетевой вызов: schema v8 транзакционно создаёт link и отдельную series-операцию
CREATE, а ручной sync отправляет master до ordinary Task-очереди. Stable
base32hex event id и private Planner markers делают повтор после удалённого
успеха/локального сбоя идемпотентным без второго master.

Реализованы:

- lossless preflight (RRULE, IANA timezone, timed/all-day schedule, отсутствие
  Google id у materialized occurrences и неподдерживаемых future exceptions);
- coalescing `CREATE+UPDATE`, `CREATE+DELETE`, `UPDATE+UPDATE` и
  `UPDATE+DELETE`, retry/backoff/dead-letter отдельно от ordinary queue;
- отдельные recurring-master insert/get/patch/delete gateway methods;
- explicit disconnect/keep remote, delete remote/keep local и recoverable
  delete local+remote — неоднозначного удаления нет;
- linked-master echo/conflict/remote-deleted pull policy и карантин изменённых
  linked instances без создания ordinary Task;
- локальные completion/tags/history; series-level title/notes/rule/schedule
  правки ставят один UPDATE, materialized occurrences никогда не загружаются
  как отдельные события;
- локальная Settings-диагностика link/queue/conflict/remote-deleted/terminal и
  quarantine; page-open, startup и timer сети не вызывают.

Fake/injected smoke в `D:\planner-desktop-google-series-write-smoke`
подтвердил create → update того же id → delete, insert reconciliation,
disconnect/reconnect, конфликт, remote cancellation, instance quarantine,
ordinary sync, restart persistence, `occurrence_event_flood=0`,
`settings_google_calls=0` и `qml_warnings=0`.

Статус проверки на 15 июля 2026 года:

| Проверка | Статус | Результат |
|---|---|---|
| Focused Phase 3.2B2 | PASS | `25 passed` |
| Phase 3.2B2 + Phase 3.2B1 | PASS | `88 passed` |
| Phase 3.2A + Phase 3.1 + Calendar Phase 2 + ordinary desktop sync | PASS | `350 passed` |
| Compile + collection | PASS | compileall без ошибок; `949 tests collected` |
| Полный pytest | PASS с известным Windows-исключением | `948 passed`, единственный сбой `tests/test_settings_paths.py::test_macos_data_dir`; не относится к B2 и не исправлялся |
| QML/fake visual smoke | PASS | 7 screenshots, compact/normal/wide, restart, `qml_warnings=0`, network calls on page-open = 0 |
| Live-пилот recurring master (реальный Calendar API) | PASS | 15 июля 2026: create → idempotent re-sync → title update → local-only mutations → delete remote/keep local; после пилота повторно `9 passed` focused series-набор, `128 passed` ordinary sync slice, compileall без ошибок; код не менялся |

Live-пилот **пройден 15 июля 2026 года** на изолированном профиле
`D:\planner-desktop-google-series-live-pilot` (собственные client_secret,
token.json и app_desktop.db; старые `Planner/app.db` и `Planner/token.json`
не читались и не копировались). OAuth выполнен только явным флоу, Google-аккаунт
явно подтверждён пользователем перед первой записью (идентичность аккаунта в
документации не фиксируется). Timed-серия
`TEST Planner Phase 3.2B2 — recurring master pilot`
(Europe/Moscow, 2026-07-20 09:00–09:15, `RRULE:FREQ=DAILY;INTERVAL=1;COUNT=3`)
создала ровно один master с детерминированным id
`plrf214mq1637lfd06cu13gpt77i9t402sflk51jh1it7mq7nr5ss5g` и private
Planner-маркерами; повторный manual sync без локальных изменений не выполнил
ни одной записи (etag не изменился, только один pull-list); title-правка
поставила один UPDATE и обновила тот же master (etag сменился, private
revision/hash совпали с новым локальным состоянием, recurrence/timezone не
изменились); completion/tag/priority не поставили ни одной series-операции и
sync после них не выполнил master-мутаций; явное «удалить в Google, оставить
локально» отменило master (status `cancelled`, повторное удаление
идемпотентно), external catalog tombstone, link `detached`, локальная серия и
завершённая история сохранены. Финальная проверка: 0 событий `TEST Planner`
в календаре (masters и instances), обе очереди и dead-letter пусты,
`occurrence_event_flood=0` — materialized occurrences ни разу не получили
Google id. Каждый write-цикл стоил ровно один master-вызов плюс
предварительный get (create: get+insert, update: get+patch, delete:
get+delete; каждый цикл — один pull list); автоматических/фоновых вызовов
Google — ноль, все вызовы происходили из явных ручных действий.

Скриншоты fake-приёмки:

- [подключение](screenshots/series_connect_google_phase3_2b2.png);
- [ожидает ручного sync](screenshots/series_pending_sync_phase3_2b2.png);
- [связана](screenshots/series_linked_phase3_2b2.png);
- [конфликт](screenshots/series_sync_conflict_phase3_2b2.png);
- [master удалён в Google](screenshots/series_remote_deleted_phase3_2b2.png);
- [Settings diagnostics](screenshots/series_link_settings_phase3_2b2.png);
- [compact](screenshots/series_link_compact_phase3_2b2.png).

Подробный контракт: [`GOOGLE_SERIES_SYNC_ARCHITECTURE.md`](GOOGLE_SERIES_SYNC_ARCHITECTURE.md).

## Фаза 3.2B3A — явное разрешение конфликтов и восстановление после удаления (готово, live-пилот PASS)

Для связанного Planner-owned master пользователь теперь получает явные и
безопасные выборы; ни одна сторона никогда не перезаписывается молча.

Реализованы:

- schema v9 (аддитивно/идемпотентно): durable conflict base на link
  (etag/hash/полный JSON-снимок remote master), `link_generation`,
  resolution-метаданные на строках очереди и audit-таблица
  `series_conflict_resolutions` (pending/completed/failed/superseded);
  v8-links мигрируют с `link_generation = 0`;
- **«Оставить версию Planner»** — явное подтверждение, audit-строка с
  зафиксированным (acknowledged) etag конфликтной версии, ровно одна
  conflict-resolution UPDATE-операция; при ручном sync master
  перезаписывается ТОЛЬКО если текущий remote etag всё ещё равен
  зафиксированному; новая внешняя правка обновляет снимок, помечает решение
  superseded и требует нового решения; чужой/неподтверждённый master не
  перезаписывается никогда; конфликт снимается только после успеха remote
  update и локальной персистентности; retry после remote-успеха/локального
  сбоя не делает второй patch;
- **«Использовать версию Google»** — только для lossless-снимка
  (поддерживаемое правило, валидные DTSTART/end/форма/IANA timezone, без
  EXDATE/RDATE); одна SQLite-транзакция обновляет серию, заменяет только
  будущие невыполненные не-exception occurrences, чистит очередь и завершает
  audit; выполненная история, прошлые exceptions, тумбстоуны и локальные теги
  сохраняются; Google-запись не выполняется; следующий pull — echo; при сбое
  все строки откатываются (compensation для in-memory);
- **«Отключить и сохранить обе»** — link detach + отмена pending операций;
  обе версии и catalog/history нетронуты;
- remote_deleted: **«Оставить локальной»**, **«Создать серию в Google
  заново»** (link generation N+1 с детерминированным id
  `plr+base32hex(SHA-256(uid + separator + generation))`, ровно один CREATE,
  повторные нажатия не создают поколений, старая строка сохраняется как
  история) и **«Удалить локальную серию»** (без Google-операции, история
  сохраняется); внезапно «воскресший» master по старому id НЕ переподключается
  автоматически — только диагностика;
- pull при неразрешённом конфликте лишь освежает снимок/etag/hash, локальная
  серия не перезаписывается, автоматический UPDATE не ставится, устаревшее
  решение становится superseded; курсор не продвигается до персистентности;
- QML: SeriesConflictDialog + SeriesConflictComparison (Planner ↔ Google,
  поддержка/владение текстом, raw RRULE при неподдерживаемом правиле),
  RemoteDeletedRecoveryDialog, ConflictResolutionHistory в Settings;
  деструктивные действия без выбора по умолчанию, подтверждение обязательно,
  Esc отменяет, Enter с начального фокуса не подтверждает; compact/normal/wide;
- `ManualSyncResult` аддитивно: conflicts_resolved_keep_planner /
  use_google / disconnected, remote_deleted_recreated,
  resolution_attempts_superseded, resolution_failures (use_google/disconnect —
  локальные действия, попадают в следующую сводку).

Fake/injected smoke в `D:\Users\v.pyatakov\backup\planner-desktop-series-conflict-smoke`
подтвердил: create+link, конфликт заголовка, Keep Planner success, гонку
второй правки (stale решение НЕ перезаписало master), Use Google success,
неподдерживаемое правило (действие заблокировано, raw RRULE виден),
disconnect, remote deletion → keep local / recreate generation 1 (другой
стабильный id) / delete local с сохранением истории, restart persistence,
ordinary task sync, `occurrence_event_flood=0`, `settings_gateway_calls=0`,
`qml_warnings=0`, реальных вызовов Google — ноль.

Статус проверки на 17 июля 2026 года:

| Проверка | Статус | Результат |
|---|---|---|
| Focused Phase 3.2B3A (12 файлов + real-gateway regression) | PASS | `95 passed` |
| Focused Phase 3.2B2 | PASS | `27 passed` |
| Ordinary/manual-sync regression | PASS | `199 passed` |
| Compile | PASS | compileall без ошибок |
| Полный pytest | PASS с известным Windows-исключением | `1040 passed`, единственный сбой `tests/test_settings_paths.py::test_macos_data_dir`; не относится к B3A и не исправлялся |
| QML/fake visual smoke | PASS | 7 screenshots, compact/normal/wide, restart, `qml_warnings=0`, network calls on page-open = 0 |
| Live-пилот B3A (реальный Calendar API) | **PASS** | conflict → Keep Planner → second-edit race → Use Google → remote_deleted → generation 1 → explicit cleanup; `occurrence_event_flood=0` |

Live-пилот **пройден 17 июля 2026 года** в изолированном профиле
`D:\planner-desktop-google-series-live-pilot` (собственные `client_secret.json`,
`token.json` и `app_desktop.db`). Токен принадлежит личному Google-аккаунту,
а не выделенному тестовому; этот факт и Calendar selector `primary` были явно
сообщены пользователю, и перед финальной Google-записью получено отдельное
явное подтверждение. Идентичность аккаунта, содержимое OAuth-файлов и полный
Calendar payload в документацию не записывались. Старые Flet `app.db` /
`token.json` не читались и не изменялись.

Пилот использовал локальную серию `b3a-live-abf24e6bcb`: timed
09:00–09:15, `Europe/Moscow`, 20 июля 2026 года,
`RRULE:FREQ=DAILY;INTERVAL=1;COUNT=3`, без гостей и чувствительных заметок.
Generation 0 получил id
`plr00kqaul5iqtu843ahjihbf3gmlfjjkpcj3fdup1j6oap9pc9j6vg`, generation 1 —
`plrfnoq93370s646bvha6gmvqbupfskfco6rfath7h427o66tdla9b0`; random fallback
не использовался. После remote deletion generation 0 сохранился исторической
строкой (`remote_deleted` → `detached`, `resolution_kind=recreate`), повторное
нажатие recreate не создало generation 2, один CREATE создал generation 1,
а следующий ручной sync выполнил 0 create / 0 patch.

Реальный conflict/resolve сценарий подтвердил:

- первый conflict имел etag `"3568551433515006"`. Он обнаружил дефект:
  private payload marker после внешней правки оставался прежним, поэтому
  gateway ошибочно считал Keep Planner уже применённым и пропускал PATCH;
- минимальное исправление требует совпадения не только marker, но и
  канонического фактического содержимого мастера. Focused fake-тест
  воспроизводит stale-marker случай и сохраняет идемпотентный retry при
  действительно одинаковом содержимом;
- после исправления повторный Keep Planner с acknowledged etag
  `"3568552575396638"` выполнил реальный PATCH того же id; итоговый etag
  `"3568552585835230"`, recurrence/timezone не изменились, audit завершён;
- в second-edit race решение с acknowledged etag `"3568552683058430"`
  стало `superseded`, потому что вторая внешняя правка уже имела etag
  `"3568552688963390"`. Planner её не перезаписал и потребовал новое решение;
- «Использовать версию Google» выполнилось целиком локально с 0 Google writes:
  title стал `TEST Planner Phase 3.2B3A — REMOTE RACE TWO`, завершённая история
  сохранилась, будущие replaceable occurrences регенерированы, следующий
  ручной pull распознал echo без PATCH;
- прямое удаление generation 0 перевело связь в `remote_deleted`, не
  воскресило master автоматически и не удалило локальную серию/историю;
- явное «Удалить серию в Google, оставить локальной» для generation 1
  выполнило ровно `events.get + events.delete + events.list`. Оба id после
  этого имеют remote status `cancelled`, catalog содержит оба tombstone,
  обе связи исторические/detached, обе очереди и оба terminal/dead-letter
  счётчика равны 0, unresolved quarantine равен 0;
- финальный lookup (`events.get` для обоих id и отдельные master/expanded
  list) дал 0 активных masters, 0 активных instances и 0 ordinary events с
  префиксом `TEST Planner Phase 3.2B3A`. Ни один из трёх materialized
  occurrences не получил Google event id: `occurrence_event_flood=0`.

Google-write accounting пилота: master insert — 2 (generation 0/1), master
patch — 4 (три явные внешние title-правки и один успешный Keep Planner),
master delete — 2 (generation 0/1); Use Google — 0 writes. Каждый Planner
push/pull выполнялся только отдельным ручным действием; automatic/background/
page-open/timer calls — 0, Settings/dialog-opening calls — 0. В финальной
инструментированной части зафиксированы один OAuth refresh, один
`calendarList.get`, пять `events.get`, один `events.delete` и пять
`events.list` (один pull + четыре verification reads), без insert/patch.
Более ранние verification-only reads не сохранялись в durable ledger, поэтому
для них не заявляется выдуманный точный итог; write-счётчики подтверждаются
фактическими поколениями, audit/link-состоянием и remote tombstones.

Скриншоты fake-приёмки:

- [сравнение версий](screenshots/series_conflict_compare_phase3_2b3a.png);
- [решение «Оставить версию Planner» ожидает sync](screenshots/series_conflict_keep_planner_phase3_2b3a.png);
- [подтверждение «Использовать версию Google»](screenshots/series_conflict_use_google_phase3_2b3a.png);
- [неподдерживаемое правило](screenshots/series_conflict_unsupported_phase3_2b3a.png);
- [восстановление после удаления в Google](screenshots/series_remote_deleted_recovery_phase3_2b3a.png);
- [Settings: диагностика и история решений](screenshots/series_conflict_settings_phase3_2b3a.png);
- [compact](screenshots/series_conflict_compact_phase3_2b3a.png).

### Фаза 3.2B3B — явно отложено

- изменение/отмена одного Google occurrence;
- синхронизация локальных exceptions/EXDATE;
- разрешение карантина изменённых linked instances (он остаётся видимым).

### Фаза 3.2B3C — явно отложено

- remote split «этот и все будущие»;
- adoption несвязанных внешних Google masters.

---

## Фаза 4 — Продуктизация синхронизации (план)

### Цель для пользователя

Синхронизация, которую не страшно включить: понятно, что ушло и что
пришло, конфликты видны и решаются кликом, ошибки не копятся молча.

### Фичи

- opt-in фоновый синк: выключен по умолчанию, включается явным
  переключателем в настройках (интервал, «только при активном окне»),
  выключается одним кликом; ручной режим остаётся;
- понятный конфликтный UI: список конфликтов «локальная ↔ удалённая»
  с выбором стороны (текущая политика движка остаётся дефолтом);
- управление dead-letter: просмотр застрявших операций, повтор,
  отбрасывание с подтверждением;
- бейджи синка на задачах и в сайдбаре (ожидает push / конфликт /
  ошибка), сводка последнего синка;
- безопасный старт: при запуске НИКАКОГО автосинка, только чтение
  локального состояния; «протухший» токен показывается статусом,
  а не диалогом в лицо.

### Архитектурная работа

- планировщик поверх существующего `ManualSyncService` (QTimer в
  GUI-потоке, работа — в фоновом Qt-потоке; те же соединения-на-цикл);
- взаимное исключение ручного и фонового запуска (уже есть замок
  «синк выполняется» — распространить);
- persist настроек синка в `desktop_sync_state` (аддитивно);
- конфликт-лог: аддитивная таблица или переиспользование dead-letter
  с типом «conflict»;
- ядро движка (политика, mapper, gateway) НЕ переписывается.

### Тесты

- планировщик не стартует без opt-in; выключение останавливает цикл;
  одновременный ручной+фоновый запуск невозможен; dead-letter
  retry/discard; конфликтные сценарии на FakeCalendarGateway;
  «протухший» токен → статус, не крэш.

### Риски

- фоновый синк = главный источник «пропали данные» — только opt-in,
  видимый индикатор, лог последних циклов;
- SQLite из двух потоков — строго существующий паттерн
  «соединения на время цикла»;
- конфликтный UI легко перегрузить — начать со списка и двух кнопок.

### Критерии приёмки

- по умолчанию поведение НЕ меняется (нет фонового синка);
- включённый фоновый синк переживает сон/пробуждение и сетевые ошибки
  без зависших замков;
- каждая dead-letter-операция видна и разруливается из UI;
- пилот на тестовом аккаунте: неделя фонового синка без потери данных.

### Сознательно отложено

- realtime push-уведомления Google (watch-каналы требуют публичный
  endpoint — запрещено ограничением «без бэкенда»); мультиаккаунт;
  выбор произвольного календаря (не primary).

---

## Фаза 5 — Миграция и релиз (план)

### Цель для пользователя

Один день перехода: старые данные импортированы, приложение
устанавливается и запускается как обычная программа, старое приложение
уходит на пенсию без потери информации.

### Фичи

- импорт старого `Planner/app.db`: задачи, ежедневные, история
  выполнения — в изолированный профиль PlannerDesktop (старый файл
  открывается строго read-only и только в этой фазе);
- dry-run импорта: отчёт «что будет импортировано/пропущено/сконвертировано»
  до записи хоть одной строки;
- резервная копия профиля PlannerDesktop перед импортом + кнопка отката;
- упаковка/сборка: установочный артефакт для Windows (PyInstaller или
  аналог), иконка, версия, ярлык;
- критерии выключения старого Flet-приложения и план отката на него.

### Архитектурная работа

- `migration/import_legacy.py` в planner_desktop (отдельный модуль,
  НЕ трогающий старые скрипты миграции): маппинг схем старая → новая,
  идемпотентность (повторный импорт не дублирует), отчёт;
- политика привязок Google: импортированные задачи приходят БЕЗ
  event_id (иначе два клиента будут драться за одни события) — связь
  восстанавливается вручную или отдельным осознанным шагом;
- сборочный скрипт + smoke-тест собранного артефакта;
- версионирование схемы БД зафиксировано для релиза.

### Тесты

- импорт на копии реальной структуры старой БД (фикстура), дважды —
  без дублей; dry-run ничего не пишет; бэкап/восстановление байт-в-байт;
  собранный артефакт стартует и проходит smoke-сценарий.

### Риски

- старая БД содержит неожиданные состояния (NULL-ы, осиротевшие
  привязки) — dry-run обязателен, импорт консервативен;
- двойная запись в один Google-календарь из двух приложений — на время
  перехода синк включён только в одном;
- PyInstaller + PySide6/QML капризен к путям QML — smoke-тест артефакта
  в CI-подобном прогоне.

### Критерии приёмки

- импорт реального профиля проходит без ошибок, счётчики совпадают
  с отчётом dry-run;
- откат возвращает профиль к состоянию до импорта;
- установленное приложение проходит полный ручной smoke;
- документирован чек-лист вывода старого приложения.

### Сознательно отложено

- автообновления; подпись кода; магазины приложений; портативная сборка.
