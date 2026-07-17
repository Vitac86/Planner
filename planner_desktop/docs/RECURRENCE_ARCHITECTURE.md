# Архитектура повторяющихся задач Planner Desktop (Phase 3.2A–3.2B3A)

Документ фиксирует продуктовые и технические решения локальных
повторяющихся серий (`TaskSeries`), материализации экземпляров и шаблонов
задач. Phase 3.2A остаётся основой локальной семантики, Phase 3.2B1 добавляет
lossless RRULE/read-only discovery, а Phase 3.2B2 — только явную связь
поддерживаемой чистой серии с одним **новым** Google recurring master.

---

## 1. DailyTask и TaskSeries — разные концепции

| | DailyTask (существует с Phase 1) | TaskSeries (новое в 3.2A) |
|---|---|---|
| Смысл | лёгкий локальный чек-лист/привычка «по дням недели» | полноценная повторяющаяся задача с расписанием |
| Строки Task | НЕ создаёт | создаёт обычные Task-строки (occurrence) |
| Выполнение | отметка за день (`desktop_daily_completions`) | галочка на конкретном Task-экземпляре |
| Календарь | не появляется в сетке, не синхронизируется | экземпляры видны в сетке Calendar |
| Правила | только маска дней недели | daily/weekly/monthly/yearly, interval, end mode |
| Синк | никогда | materialized rows — никогда; в B2 только definition-level master op по явной связи |

DailyTask не изменяется этой фазой: его домен, репозиторий, сервис,
диалог управления и отметки выполнения остаются как были.

## 2. Локальная серия и импортированные Google recurring instances

Существуют два независимых вида «повторяемости»:

- **Локальная TaskSeries** — создана пользователем в Planner Desktop.
  У серии и её экземпляров нет `google_calendar_event_id`,
  нет `google_calendar_recurring_event_id`, нет etag. Экземпляры несут
  `series_uid` + `occurrence_key`. Бейдж в UI: «Локальная серия».
- **Импортированный экземпляр Google-серии** — задача, пришедшая pull-ом
  с заполненным `google_calendar_recurring_event_id` /
  `google_calendar_original_start`. Правила Phase 1–2 сохраняются:
  расписание менять нельзя (unschedule/postpone/drag/resize отклоняются
  с человекочитаемой ошибкой), текст/приоритет/выполнено — можно.
  Бейдж в UI: «Серия Google».

Перекрёстные переходы в 3.2A запрещены:

- задача, привязанная к Google-событию, **не** конвертируется в локальную
  серию (редактор объясняет причину);
- Google recurring instance **не** «усыновляется» локальной серией —
  ни автоматически, ни вручную;
- миграция схемы **не** строит TaskSeries из Google-метаданных.

## 3. Идентичность экземпляра (occurrence key)

`occurrence_key` — детерминированная неизменяемая идентичность слота серии,
вычисляемая из **исходного** расписания серии, а не из текущего
(возможно отредактированного) времени задачи:

- all-day: `YYYY-MM-DD` (локальная дата слота);
- timed: `YYYY-MM-DDTHH:MM@<IANA-zone>`, например
  `2026-07-20T09:00@Europe/Moscow` — исходные локальные дата/время слота
  плюс идентичность таймзоны серии.

Правка одного экземпляра (в т.ч. перенос его времени через
«только этот») НЕ меняет его occurrence_key: ключ продолжает указывать
на слот серии, из которого экземпляр родился. Уникальность пары
`(series_uid, occurrence_key)` закреплена частичным уникальным индексом,
поэтому повторная материализация не может создать дубль.

## 4. Горизонт материализации

Материализация выполняется только по явному запросу поверхности
(конструктор ViewModel, refresh, навигация Calendar) через единый
`OccurrenceMaterializer`:

- запрошенный видимый диапазон расширяется буфером
  `MATERIALIZATION_BUFFER_DAYS = 14` дней в обе стороны;
- жёсткий предел одного вызова — `MAX_OCCURRENCES_PER_CALL = 366`
  экземпляров на серию (дополнительно `MAX_GENERATION_STEPS` итераций
  внутри генератора) — бесконечная серия физически не может
  материализоваться дальше запрошенного окна;
- повторный вызов idempotent: уже существующие ключи (живые, exception,
  выполненные и тумбстоуны) не пересоздаются;
- «История» материализацию не запускает никогда (прошлое строится только
  из уже существующих выполненных строк);
- фоновых таймеров нет; сеть не участвует.

## 5. Семантика исключений (exception)

Экземпляр, отредактированный в области «только этот», помечается
`is_series_exception = 1` и сохраняет `series_uid` + `occurrence_key`.

- Регенерация НИКОГДА не перезаписывает exception: ключ уже занят.
- Exception может отличаться заголовком, заметками, приоритетом, тегами,
  выполнением и расписанием.
- Обновление правила серии не трогает exception-строки (и не трогает
  выполненные строки) — заменяются только будущие невыполненные
  не-exception экземпляры.

## 6. «Только этот» (this_occurrence)

- Меняется ровно одна Task-строка; серия и остальные экземпляры не
  затрагиваются.
- Строка помечается exception (см. выше), ключ и series_uid сохраняются.
- Calendar-очередь не получает ни одной операции.
- Выполнение (галочка) ВСЕГДА действует только на экземпляр и не требует
  диалога области.

## 7. «Этот и все будущие» (this_and_future) — split

Правка области «этот и все будущие» реализована расщеплением серии по
исходному occurrence-слоту выбранного экземпляра:

1. исходная серия завершается непосредственно ПЕРЕД выбранным слотом
   (`end_mode = until`, `until_date = слот − 1 день`);
2. создаётся новая независимая серия, начинающаяся с выбранного слота,
   с новым расписанием/правилом/текстом;
3. прошлые экземпляры (включая выполненные и exception) остаются
   привязанными к старой серии — история не разрушается;
4. будущие невыполненные не-exception материализованные экземпляры старой
   серии физически удаляются и заменяются экземплярами новой серии;
5. выбранный экземпляр переходит к новой серии (новый ключ от нового
   расписания);
6. в SQLite операция выполняется одной транзакцией `BEGIN IMMEDIATE`, которая
   охватывает старую и новую серии, выбранный экземпляр, заменяемые будущие
   строки и связи тегов; любая ошибка делает `ROLLBACK`. In-memory/test
   адаптер сохраняет эквивалентную семантику через компенсацию. Наружу всегда
   возвращается структурированный `SeriesSplitResult`;
7. Calendar-очередь не получает ни одной операции.

Никакого неявного «обновить всё»: изменение правила/расписания без
явно выбранной области невозможно — диалог области обязателен.

## 8. Удаление и сохранение истории

- **Удалить один экземпляр** — обычный тумбстоун Task (`deleted_at`);
  строка сохраняет `series_uid`/`occurrence_key` и работает как tombstone:
  регенерация этот слот больше не создаёт.
- **Остановить «этот и все будущие»** — серия завершается перед выбранным
  слотом; будущие невыполненные не-exception экземпляры удаляются;
  прошлое (выполненные, exception) не трогается.
- **Удалить серию целиком** — серия получает тумбстоун
  (`task_series.deleted_at`), будущие невыполненные не-exception
  экземпляры удаляются, а выполненные исторические строки остаются
  навсегда (страница «История» продолжает их показывать).
- Внешние ключи не каскадируют на исторические Task-строки.

## 9. Часовые пояса и DST

- Таймзона серии хранится явно (IANA-имя, например `Europe/Moscow`),
  по умолчанию — локальная зона машины на момент создания серии.
- Timed-экземпляры сохраняют **локальное wall-clock время**: серия
  «каждый день 09:00» остаётся 09:00 и после перехода на летнее/зимнее
  время (семантика повторений Google/RFC 5545 для локальных зон).
- Неоднозначное локальное время (осенний повтор часа): используется
  **первое** прохождение (`fold=0`).
- Несуществующее локальное время (весенний пропуск часа): время слота
  сдвигается **вперёд на величину разрыва** (02:30 → 03:30 при разрыве
  в час). Обе политики детерминированы и покрыты тестами
  (`resolve_wall_clock`).
- Task-строки хранят naive-локальные datetime — как весь существующий
  Planner Desktop; идентичность зоны серии входит в occurrence_key.

## 10. Месячные правила 29/30/31 и 29 февраля

Поведение RRULE-подобное (RFC 5545: невалидные даты пропускаются):

- monthly day=31 → появляются только месяцы, где 31 существует
  (янв, мар, май, …); февраль/апрель и т.п. пропускаются, interval
  отсчитывается по календарным месяцам, а не по «найденным» экземплярам;
- monthly day=30 → пропускается только февраль;
- yearly 29 февраля → только високосные годы.

Пропуск не сдвигает последующие экземпляры и не переносит дату на
последний день месяца.

## 11. Почему Phase 3.2A не синхронизирует серии с Google

- Перевод локальных правил в RRULE и обратно, а также согласование
  split/exception семантики с Google recurring events — отдельная большая
  задача с собственными рисками (уроки dead-letter старого приложения).
- Отправка каждого материализованного экземпляра отдельным событием
  создала бы лавину одиночных событий, которые Google не считает серией,
  и сделала бы будущую честную интеграцию невозможной без крупной чистки.
- Поэтому в 3.2A действует инвариант: **любая операция локальной серии
  даёт нулевую дельту Calendar-очереди** (закреплено тестами
  `test_desktop_series_sync_isolation.py`). `DesktopTaskService`
  пропускает запись `record_local_*` для задач с `series_uid`.
- Ручной синк обычных одиночных задач продолжает работать как раньше.

## 12. Phase 3.2B1: transport и read-only каталог Google-мастеров

### Чистый RRULE codec

`domain/google_recurrence.py` не импортирует Qt, SQLite, Google client или
сеть. Входной массив Calendar `recurrence` сохраняется дословно и в исходном
порядке. Результат содержит разобранный RRULE, EXDATE/RDATE, структурированные
причины неподдерживаемости, канонический RRULE и `RecurrenceRule` только когда
переход действительно lossless.

Поддерживаемое подмножество:

- `FREQ=DAILY|WEEKLY|MONTHLY|YEARLY`;
- `INTERVAL >= 1` в пределах локальной модели;
- `BYDAY` только для weekly и только обычные `MO..SU` без ordinal;
- один положительный `BYMONTHDAY` для monthly;
- по одному `BYMONTH` + `BYMONTHDAY` для yearly;
- ровно один вариант окончания: `COUNT >= 1` либо `UNTIL`;
- `WKST`, если он не меняет Monday-anchored семантику Planner
  (для многонедельного interval безопасен только `MO`);
- корректные EXDATE/RDATE типа date или date-time, включая сохранение TZID;
  эти даты пока только показываются в диагностике и не применяются к
  локальной TaskSeries.

Ключи и значения weekday читаются без учёта регистра. Каноническая запись
стабильна: `FREQ`, `INTERVAL`, `BYDAY`, `BYMONTHDAY`, `BYMONTH`, `COUNT`/
`UNTIL`, `WKST`. Дубликаты свойств/значений и невалидные целые отклоняются;
`COUNT + UNTIL` отклоняется.

`UNTIL` различает `YYYYMMDD`, UTC `YYYYMMDDTHHMMSSZ` и небезопасный floating
date-time. Date-only lossless для all-day. UTC date-time преобразуется в
инклюзивную `RecurrenceRule.until_date` только при наличии DTSTART wall-clock
и IANA timezone и только если UTC-момент точно совпадает со стартом последнего
локального экземпляра (включая DST). Иначе исходная строка остаётся в каталоге,
а правило получает статус unsupported — дата не округляется и не угадывается.

Явно не поддерживаются `BYSETPOS`, `BYWEEKNO`, `BYYEARDAY`, `BYHOUR`,
`BYMINUTE`, `BYSECOND`, ordinal `BYDAY` (`2MO`, `-1FR`), несколько RRULE,
EXRULE и сложные комбинации, которых нет в Phase 3.2A. Ничего из этого не
отбрасывается и не упрощается до похожего правила.

### Транспортная классификация

`CalendarEvent` аддитивно несёт `recurrence_lines`, timezone start/end и
provider wall-clock DTSTART. Классы взаимоисключающие:

1. `is_ordinary_event`: нет recurrence и `recurring_event_id`;
2. `is_recurring_instance`: есть `recurring_event_id` (даже если provider
   прислал лишнюю recurrence metadata);
3. `is_recurring_master`: есть recurrence, но нет `recurring_event_id`.

`payload_to_event` сохраняет `recurrence`, `start.timeZone`, `end.timeZone`,
`recurringEventId` и `originalStartTime`; усечённый cancelled master
распознаётся движком по уже существующей строке каталога. Пагинация,
`singleEvents=False`, syncToken и HTTP 410 rebuild не менялись.

### Схема v7 и каталог

`external_calendar_series` — отдельная read-only таблица с уникальностью
`(provider, calendar_id, remote_event_id)`. Она хранит remote id/etag/status,
заголовок/описание, timed/all-day start/end и timezone, точный JSON recurrence
lines, lossless parsed rule или unsupported reason, first/last seen, remote
updated и cancellation tombstone. Индексы покрывают remote id, статусы и
last_seen. FK к `tasks`/`task_series` нет: отмена мастера не каскадирует историю.

`ExternalSeriesRepository` имеет SQLite и in-memory реализации;
`ExternalSeriesService` только читает локальный каталог. Количество экземпляров
вычисляется из Task по `google_calendar_recurring_event_id`, не хранится счётчиком.

### Master-aware pull и cursor safety

Обработчик мастера стоит перед ordinary Task mapping:

- active master upsert-ит каталог и никогда не создаёт Task/TaskSeries;
- unsupported master сохраняет все raw lines и readable reason;
- recurring instance остаётся на прежнем Task import/update пути;
- cancelled instance остаётся прежним Task tombstone;
- cancelled master помечает только каталог и не удаляет completed/history
  instance rows;
- отсутствие optional catalog dependency всё равно не превращает master в Task;
- исключение catalog persistence выходит из pull до `set_sync_cursor`, поэтому
  курсор не продвигается и master не падает в ordinary fallback.

Pull мастеров и запись каталога создают **нулевую дельту Calendar-очереди**.
Production `insert_event`/`patch_event` отклоняют recurrence input, а
`delete_event` принимает только event id; recurrence они не пишут;
Phase 3.2B2 использует отдельный recurring-master gateway contract и отдельную
series queue, не ослабляя ordinary path.

### Legacy diagnostic, UI и reporting

Строка Task, чей `google_calendar_event_id` совпал с обнаруженным master id,
но не имеет `google_calendar_recurring_event_id`, считается только
`possible legacy master import`. Показываются count и внутренние uid; строка
не удаляется и не изменяется автоматически. Будущая B3 adoption
должна потребовать явную идентификацию и пользовательское решение.

Settings читает только локальный каталог: active/unsupported/cancelled/legacy
counts, last refresh, title, русскую сводку, timed/all-day, timezone,
поддержку текстом и цветом, instance count, remote update и selectable raw
RRULE. В каталоге чужих master нет adopt/repair/materialize controls; B2
connect доступен только из редактора локальной TaskSeries и создаёт новый master.
Открытие страницы не строит gateway и не делает Google call. ManualSyncResult
аддитивно сообщает ordinary events, masters, instances, unsupported/cancelled,
а также B2 create/update/delete/conflict/terminal/quarantine и B3A resolved/recreated/superseded/failure counts. Автоматического
sync по-прежнему нет.

## 13. Phase 3.2B2: явная связь с новым master

`task_series_calendar_links` хранит lifecycle отдельно от `TaskSeries`, а
`pending_calendar_series_ops` — независимую coalescing queue. Connection
preflight принимает только active/lossless/IANA-valid серию без Google ids на
materialized rows и без future exception/tombstone, требующих EXDATE или
instance write. Completed rows и теги не блокируют связь.

Связь сразу получает deterministic Google-valid id и один CREATE. Ручной sync
создаёт/патчит/удаляет master через отдельный gateway contract; ordinary Task
path не получает recurrence. Title/notes/schedule/rule — owned master fields,
tags/priority/completion/history остаются локальными. Поэтому серия по-прежнему
материализуется для локального UX, но её строки никогда не выгружаются отдельно.

Pull совпавшего master обновляет metadata как echo. Неожиданная remote-правка
ставит `conflict` без overwrite/auto-push, отмена — `remote_deleted` без удаления
локальной истории. Изменённый linked instance записывается в
`external_series_occurrence_changes`, не становится ordinary Task и ждёт B3.

Disconnect/delete намерение всегда явное: detach+keep remote, delete remote+
keep local либо recoverable delete local+remote. Полный lifecycle, coalescing,
non-atomic recovery и markers описаны в
[`GOOGLE_SERIES_SYNC_ARCHITECTURE.md`](GOOGLE_SERIES_SYNC_ARCHITECTURE.md).

## 14. Phase 3.2B3A: явное разрешение конфликтов и восстановление

Schema v9 добавляет durable conflict base на link (etag/hash/полный JSON-
снимок remote master), audit-таблицу `series_conflict_resolutions` и link
generations. Разрешение — только явные действия пользователя:

- **Оставить версию Planner** — audit + ровно одна conflict-resolution
  UPDATE; ручной sync перезаписывает master ТОЛЬКО при равенстве текущего
  remote etag зафиксированному; новая внешняя правка supersede-ит решение и
  освежает снимок; чужой master не перезаписывается никогда;
- **Использовать версию Google** — только lossless-снимок (без EXDATE/
  RDATE); одна SQLite-транзакция (`accept_remote_master_atomic`) обновляет
  серию (revision +1), заменяет только будущие невыполненные не-exception
  строки и завершает audit; история/exceptions/тумбстоуны/теги сохраняются;
  Google-записи нет, следующий pull — echo;
- **Отключить и сохранить обе** — detach + отмена очереди; обе версии и
  catalog/history нетронуты;
- remote_deleted: keep local / recreate (link generation N+1 с
  детерминированным id от `series_uid + separator + generation`, ровно один
  CREATE, повторные нажатия идемпотентны, старая строка — история) /
  delete local без Google-операции; «воскресший» master по старому id не
  переподключается автоматически.

Материализация, occurrence identity, история и правила «только этот / этот
и будущие» не изменились; conflict никогда не перезаписывает локальные
Task-строки автоматически.

## 14a. Граница Phase 3.2B3B / 3.2B3C (явно отложено)

Phase 3.2B3B: запись локальных exceptions/EXDATE, перенос/отмена одного
Google occurrence, разрешение linked-instance quarantine.
Phase 3.2B3C: remote split «этот и все будущие», adoption существующего
внешнего master.

## 15. Шаблоны задач

- Шаблон — локальная заготовка (`task_templates`): имя, заголовок,
  заметки, приоритет, теги, дефолты планирования; recurring-шаблон
  дополнительно несёт дефолты правила повторения.
- Применение шаблона только ПРЕДЗАПОЛНЯЕТ общий редактор — ничего не
  сохраняется до явного «Создать».
- Ordinary-шаблон создаёт независимую обычную задачу; recurring-шаблон —
  новую независимую TaskSeries.
- Google-идентификаторы/etag/recurrence-метаданные в шаблон не попадают
  и из шаблона не копируются.
- Правка шаблона не мутирует ранее созданные задачи/серии; удаление
  шаблона ничего не удаляет, кроме самого шаблона.
- Лимиты детерминированы: имя ≤ 60 символов, максимум 100 активных
  шаблонов, уникальность нормализованного имени среди активных.
