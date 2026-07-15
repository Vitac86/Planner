# Ручная синхронизация с Google Calendar (новый десктоп)

Реальный шлюз Google Calendar в новом десктопе **есть** и запускается
**только вручную**: кнопкой «Синхронизировать сейчас» в настройках или
CLI-командой. Автоматического/фонового синка нет: ни при старте, ни по
таймеру — и в этой фазе не будет.

## Сначала — тестовый аккаунт

Первое подключение выполняйте **тестовым Google-аккаунтом**, не боевым:
pull ручного синка создаёт локальные задачи из событий календаря, а push
создаёт события в календаре аккаунта. Отладьте цикл на тестовом
аккаунте/профиле, прежде чем думать о боевом.

## Изоляция от старого приложения

Все файлы нового десктопа живут в изолированном профиле
(`%APPDATA%\PlannerDesktop` на Windows; переопределяется переменной
`PLANNER_DESKTOP_DATA_DIR`):

| Файл | Путь |
|---|---|
| База данных | `<PlannerDesktop>/app_desktop.db` |
| OAuth-токен | `<PlannerDesktop>/token.json` |
| OAuth-секрет приложения | `<PlannerDesktop>/secrets/client_secret.json` |

Профиль старого Flet-приложения (`<Planner>/app.db`, `<Planner>/token.json`)
**никогда не читается, не пишется и не копируется автоматически**.
Скоупы нового десктопа — только `calendar` (Google Tasks не используется).

## Первое подключение

1. Возьмите OAuth-клиент типа «Desktop app» в Google Cloud Console
   (можно тот же client_secret, что у старого приложения, — это
   идентификатор ПРИЛОЖЕНИЯ, аккаунт выбирается при входе) и положите
   файл в `<PlannerDesktop>/secrets/client_secret.json`.
2. Запустите десктоп на отдельном тестовом профиле:

   ```powershell
   $env:PLANNER_DESKTOP_DATA_DIR = "D:\planner-desktop-pilot"
   python run_desktop.py
   ```

3. Настройки → «Подключить Google Calendar» → в браузере войдите
   **тестовым** аккаунтом. Токен сохранится только в изолированный
   профиль.

## Запуск синка

- Кнопка **«Синхронизировать сейчас»** в настройках — один цикл
  push+pull, выполняется в фоновом потоке (UI не замирает), результат
  и ошибки показываются на странице.
- CLI (та же логика, тот же ManualSyncService):

  ```powershell
  python -m scripts.desktop_calendar_sync_once --real-google
  ```

  Без флага `--real-google` скрипт ничего не делает (код выхода 2).

Один цикл: сначала push локальной очереди (create/update/delete событий),
затем pull изменений через `nextSyncToken` (включая правки и удаления,
сделанные на телефоне в приложении Google Calendar — телефонная версия
Planner-а это и есть родной Google Calendar).

## Правила безопасности маппинга

- задача со временем ↔ `start.dateTime`/`end.dateTime` (UTC);
- all-day задача ↔ `start.date`/`end.date`, конец **эксклюзивный**;
- формы `date`/`dateTime` не смешиваются; при PATCH противоположная
  форма явно null-ится (урок исторической петли HTTP 400);
- экземпляры повторяющихся событий по start/end вслепую не патчатся
  (обновляются только текстовые поля);
- протухший `syncToken` (HTTP 410) → один детерминированный полный
  пересбор в том же вызове;
- временные ошибки (сеть/429/5xx) → ретраи с бэкоффом; постоянные →
  dead-letter без бесконечных повторов;
- задачи без даты остаются локальными и в календарь не отправляются;
- галочка «выполнено» — локальная, событие в календаре не трогается.

## Повторяющиеся события: Phase 3.2B2

Ручной pull выполняется с `singleEvents=False`. Поэтому Calendar transport
различает три вида данных:

- ordinary event — прежний двусторонний Task path;
- recurring instance (`recurringEventId` + `originalStartTime`) — прежний
  импорт Task с запретом небезопасной смены расписания;
- recurring master (`recurrence` без `recurringEventId`) — **не Task**, а
  read-only строка локального `external_calendar_series`.

Settings показывает обнаруженные мастера, поддержку RRULE, timezone,
количество уже импортированных экземпляров, отмену и exact raw RRULE. Открытие
Settings читает только SQLite: Google API не вызывается. Неизвестные
`BYSETPOS`, ordinal BYDAY, multiple RRULE, EXRULE и другие сложные правила
сохраняются дословно и не упрощаются до локальной серии.

Phase 3.2B2 разрешает только явное создание связи поддерживаемой чистой
`TaskSeries` с **новым** Google master. До первого ручного sync статус остаётся
«Ожидает создания в Google». Один manual cycle сначала отправляет series queue,
затем прежнюю ordinary Task queue и только потом выполняет pull.

В master уходят title, notes, first start/end, timezone, canonical RRULE и
private idempotency markers. Теги, priority, completion и history не уходят;
materialized occurrences никогда не создаются как отдельные events. Обычные
`insert_event`/`patch_event`/`delete_event` не принимают recurrence — master
использует отдельные методы.

Disconnect и delete требуют отдельного выбора: оставить remote, удалить remote
и оставить local либо удалить обе стороны recoverable-последовательностью.
Unexpected remote edit ставит конфликт без overwrite; remote deletion сохраняет
локальную серию; changed linked instance карантинится до Phase 3.2B3.

Adoption существующего master, отдельные occurrence writes, exceptions,
«этот и все будущие», conflict-resolution UI и restore remote-deleted master
не реализованы. Открытие Settings/editor по-прежнему читает только SQLite;
автоматического Google sync нет.
