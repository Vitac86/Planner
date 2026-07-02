# Planner — Architecture Audit

Scope: read-only audit of the current codebase at `D:\planner`, branch `rollback-broken-gui-fix`. No behavior was changed to produce this report. A handful of read-only commands (`ast.parse`, `pytest`) were run against the existing code to verify claims instead of guessing; those commands and their output are quoted inline where relevant.

## 0. Current repository state (read this first)

The working tree is **not currently importable**, independent of anything discussed below. This is orthogonal to the architecture problems in §4 but blocks verifying *any* of them by running the app, so it has to be fixed (or reverted to) before the rest of this report can be validated interactively.

Verified directly:

```
$ python -c "import ast; ast.parse(open('ui/app_shell.py', encoding='utf-8').read())"
SyntaxError: invalid syntax (ui/app_shell.py, line 109)   # "<<<<<<< HEAD"

$ python -c "import ast; ast.parse(open('ui/dialogs.py', encoding='utf-8').read())"
IndentationError: expected an indented block after function definition on line 71

$ python -m pytest --collect-only -q
...
ERROR collecting tests/test_undated_tasks_sync.py
ImportError: cannot import name 'SyncMapUndated' from 'models'
10 tests collected, 1 error in 1.26s
```

Root cause, from `git status` / `git log`:

- Current branch `rollback-broken-gui-fix` has staged changes (`models/__init__.py`, `ui/app_shell.py`, `ui/dialogs.py`, `ui/pages/calendar.py` modified; `ui/overlay.py` added) on top of merge commit `d636789` ("Merge pull request #40 from Vitac86/fix/gui-overlay-dnd-stability"), which itself merged `aa491c3` ("Stabilize GUI: resolve merge conflicts, consolidate overlays, fix calendar DnD"). That is the "previous GUI stabilization attempt" referenced in the task brief — it left literal, unresolved `<<<<<<< HEAD` / `=======` / `>>>>>>> bc0bd30c...` conflict markers committed into `ui/app_shell.py` (three separate conflict blocks) and `ui/dialogs.py` (one block), and the in-progress rollback on top of it additionally dropped the `SyncMapUndated` export from `models/__init__.py` (`git diff --cached -- models/__init__.py` shows the two lines removed).
- Net effect: `main.py` cannot import `ui.app_shell`, so the app cannot start at all right now, and `tests/test_undated_tasks_sync.py` cannot even be collected.
- This report does not fix any of this (out of scope per the task), but every recommendation in §6 assumes it gets fixed or reverted first, since nothing downstream can be verified against a running app otherwise.

One pre-existing, unrelated test failure was also confirmed (matches prior project memory):

```
$ python -m pytest tests/test_datetime_utils.py tests/test_settings_paths.py -q
1 failed, 9 passed
FAILED tests/test_settings_paths.py::test_macos_data_dir
AssertionError: assert WindowsPath('D:/Users/.../AppData/Roaming/Planner') == WindowsPath('/Users/test/Library/Application Support/Planner')
```

Cause (`core/settings.py:21`): `environ = dict(env or os.environ)` — when a test passes `env={}` to simulate "no environment overrides", the empty dict is falsy, so the function silently falls back to the *real* `os.environ` instead of the intentionally-empty one. On this Windows machine the real `APPDATA` var leaks into the simulated-macOS code path. Doesn't affect real runtime (production calls never pass a meaningfully-empty `env`), but it's a genuine bug in the function, not just a flaky test.

---

## 1. Current product functionality

Everything below was verified by reading the actual page/service code, not inferred from naming.

**Tasks (ordinary / dated / undated).** A single `Task` entity (`models/task.py`) covers all three: a task with `start` + `duration_minutes` set is a *scheduled* task (rendered on the Calendar grid and on Today's "Сегодня" list if `start` is today); a task with `start is None` is *unscheduled* (rendered in the "Без даты" panel on both Today and Calendar). There is no separate "type" field — scheduling state is purely derived from whether `start`/`duration_minutes` are populated (`services/sync_service.py:_is_scheduled`).

- Fields exposed in the UI: title, notes, date, time, duration (minutes), priority (0–3). `status` (`todo`/`doing`/`done`) is set via the checkbox (`todo`↔`done`) on Today/Calendar; `doing` exists in the model and is filterable in History but nothing in the UI ever sets a task to `doing`.
- Create: "Быстрый ввод" (quick add) form on Today (`ui/pages/today.py:88-112`) and click-empty-cell / "Быстрый блок" dialog on Calendar (`ui/pages/calendar.py:630-689`).
- Edit: full dialog (title, date, time, duration, priority, notes) opened from Today's list rows or from clicking a Calendar chip; Today and Calendar each have their **own**, independently-implemented copy of this dialog (see §4).
- Complete/delete: checkbox toggles `done`; delete has a confirmation dialog on Today, **no confirmation** on Calendar's right-click delete.
- Calendar-only actions via right-click chip menu: edit, "Перенести…" (re-schedule to the clicked hour), "Snooze +30 мин", "Сегодня вечером" (evening), "Завтра 10:00" (tomorrow morning) — `ui/pages/calendar.py:494-530`.
- Drag & drop: unscheduled-list chips and calendar chips are both `ft.Draggable`; dropping onto an hour cell (`ft.DragTarget`) schedules/reschedules the task for that day+hour via a dialog asking for duration/priority (`ui/pages/calendar.py:532-629`).
- The "Сразу в календарь" (add straight to calendar) checkbox on Today's quick-add form (`ui/pages/today.py:82-85`) is built and displayed but its value is **never read** in `on_add` (`ui/pages/today.py:334-370`) — it currently has no effect; whether a task is scheduled is determined solely by whether the date/time fields are filled in.

**Today screen** (`ui/pages/today.py`): quick-add form, "Сегодня" list (today's non-done tasks), "Без даты" list (unscheduled), and the Daily Tasks panel embedded at the bottom. Pulls from Google on activation and on an auto-refresh timer.

**Calendar screen** (`ui/pages/calendar.py`): a Monday–Sunday week grid, one column per day, one row per configured hour (`DAY_START`..`DAY_END`, default 0–23), with a side panel listing unscheduled tasks as drag sources. Header row and body scroll horizontally in sync; the grid auto-scrolls to "now" on load. Week navigation via prev/home/next.

**History screen** (`ui/pages/history.py`): filter by free-text search (title+notes, with Cyrillic/Latin transliteration-aware matching implemented in `TaskService._match_query`), date range, status, priority; read-only result cards showing full metadata (created/updated/status/priority/dates).

**Settings screen** (`ui/pages/settings.py`): Google Calendar/Tasks connection status, last pull/push timestamps, "Переподключить Google" (reconnect), "Сбросить syncToken" (reset calendar sync token), "Полная ресинхронизация" (force full resync), and a viewer for the tail of `logs/sync.log`.

**Daily Tasks** (`ui/daily_tasks.py` + `services/daily_tasks.py`): a separate recurring-task entity (`DailyTask`) with a weekday bitmask, rendered as its own panel on the Today screen. Checkbox marks "done today"; can only be *unmarked* on the same day it was marked (`services/daily_tasks.py:178-202` raises if you try to uncheck on a later day). Status (`active` / `done_today` / `inactive`) is recalculated per-task on load and via a midnight rollover timer (`_rollover_loop`, `ui/daily_tasks.py:328-345`) that runs as an `asyncio` task owned by the panel itself.

**Priorities** (`core/priorities.py`): four fixed levels (0 none, 1 low, 2 medium, 3 high), each with a label/short-label/color/bgcolor. Used consistently for badges and as a primary sort key across Today, Calendar and History.

**Tags**: `Tag`/`TaskTag` models and a fully implemented `TagService` (`services/tags.py`, CRUD + validation) exist, but **nothing in `ui/` references `TagService`, `Tag`, or `TaskTag`** — this is a complete backend feature with zero UI surface today.

**Google Calendar sync**: two-way. Scheduled tasks ↔ events on the `primary` calendar. Incremental pull via Calendar API `syncToken`; automatic full resync (90-day backfill window) on token expiry (HTTP 410).

**Google Tasks sync**: two-way, two independent lists — regular unscheduled tasks sync to a list named by `GOOGLE_SYNC.tasks_tasklist_name` ("Planner Inbox"), Daily Tasks sync to a separate list named by `GOOGLE_SYNC.dailies_tasklist_name` ("Planner Dailies"), using a JSON blob embedded in the Google Task's `notes` field as correlation metadata (`services/daily_tasks_sync.py:_notes_payload`).

**Offline / local storage**: local SQLite (`app.db`) via SQLModel is the source of truth; the app is fully usable offline. Sync is opportunistic — pull-then-render on page activation and on a timer, push via a durable retry queue (see §3).

**Backup**: `storage/backup.py` copies `app.db` to a dated file once per day on `init_db()`, rotating out backups older than `BACKUP.keep_days` (default 7).

**Token/settings persistence**: `client_secret.json` and `token.json` live under a per-OS app-data directory (`core/settings.get_default_data_dir`); `GoogleAuth` (`services/google_auth.py`) handles the OAuth consent flow, validates that the cached token covers all required scopes and deletes+re-authenticates if not, and refreshes expired tokens via the refresh token.

---

## 2. Current data model

### `Task` (`models/task.py`, table `task`) — the live, actually-used entity
```
id, uid(uuid4), title, notes, start, due(*), duration_minutes, priority(int 0-3),
status("todo"/"doing"/"done"), gcal_event_id, gcal_etag, gcal_updated,
gtasks_id, gtasks_updated, created_at, updated_at
```
(*) `due` is declared but never read or written anywhere in `services/tasks.py` or the UI — appears vestigial.

Scheduling state is implicit: `start is not None and duration_minutes` ⇒ scheduled/calendar; `start is None` ⇒ unscheduled/Tasks. Google linkage is inline on the row (`gcal_event_id`/`gtasks_id` + matching `*_etag`/`*_updated` fields used for last-write-wins comparisons) — no separate mapping table is needed for the live sync path because of this.

### `DailyTask` (`models/daily_task.py`, table `dailytask`)
```
id(uuid4 str, PK), title, weekdays(int bitmask), status_today("active"/"done_today"/"inactive"),
last_done_at(ISO date string), last_status_calc_at(ISO date string),
created_at/updated_at(ISO strings, NOT datetime columns), timezone, gtasks_id, gtasks_updated(ISO string)
```
Note the inconsistency with `Task`: dates are stored as plain ISO strings rather than real `datetime` columns, and status is a derived/cached field recomputed on read (`DailyTaskService._recalculate_for_task`) rather than computed on demand.

### Tags
`Tag` (id, name, color_hex, timestamps) and `TaskTag` (task_id, tag_id composite PK) — `models/tag.py`. Fully backed by `TagService`; unused by the UI (see §1).

### Sync-support tables actually wired into the running app
- `PendingOp` (`models/pending_op.py`, table `pendingop`) — the outbound retry queue for Calendar/Tasks pushes (op type, task_id, JSON payload, attempts, last_error, next_try_at). This is the one durable sync-state table that's live.
- Calendar/Tasks sync cursors are **not** a DB table at all — `SyncTokenStorage` (`services/sync_token_storage.py`) persists `{calendar:{syncToken,lastPullAt}, tasks:{updatedMin,lastPullAt}, lastPushAt}` as a flat JSON file (`storage/gcal_sync_token.json`).

### Sync-support tables that exist but are **not** wired into the running app
These were the most surprising finding of this audit — the repo contains three additional, fully-built persistence layers for sync bookkeeping that `ui/app_shell.py` never instantiates:
- `TaskSyncMapping` / `TaskSyncMeta` (`models/task_sync.py`) + `TaskSyncStore` (`services/task_sync_store.py`) — a per-task Google-Tasks mapping table with its own metadata row. Zero callers anywhere in the repo.
- `SyncMapUndated` (`models/sync_map_undated.py`) — mapping table for the alternate, more sophisticated `UndatedTasksSync` engine (see §3). Has real test coverage (`tests/test_undated_tasks_sync.py`) but is not constructed by `AppShell`, and is currently unreachable anyway because `models/__init__.py` no longer re-exports it (§0).
- `ListRecord` / `TaskMetadata` / `SyncState` (`storage/store.py`, `MetadataStore`) — a *fourth* generic sync-metadata schema. Currently **cannot even be imported**: it references `core.settings.STORE_DB_PATH`, which does not exist in `core/settings.py` (verified: `from core.settings import STORE_DB_PATH` → `ImportError`). Zero callers anywhere in the repo.

### Local database
SQLite at `<AppData>/Planner/storage/app.db` (`core/settings.DB_PATH`), opened via SQLModel/SQLAlchemy (`storage/db.py`). Schema evolution is hand-rolled: `storage/migrations.py.run_all()` runs a fixed sequence of idempotent `ALTER TABLE ... ADD COLUMN` / `CREATE TABLE IF NOT EXISTS` statements on every `init_db()` call — there is no migration-version table and no down-migrations; ordering/idempotency is entirely by convention in `run_all()`.

---

## 3. Current sync architecture

### OAuth
`GoogleAuth` (`services/google_auth.py`): desktop `InstalledAppFlow`, local-server redirect. Requests 4 scopes: `calendar`, `calendar.events`, `tasks`, `drive.appdata`. Validates that a cached token's scopes are a superset of the required set on every `ensure_credentials()` call; if not, deletes `token.json` and re-runs the consent flow. Refreshes via `creds.refresh(Request())` when expired and a refresh token is present.

Note: `core/settings.GOOGLE_SYNC.scopes` independently defines a **different**, shorter scope list (`calendar`, `tasks` — 2 entries, no `drive.appdata`), used only as a fallback default inside `google_calendar.py`/`google_tasks.py`/`tasks_bridge.py`'s `_find_creds_in_auth` helpers if they ever have to construct credentials directly from `token.json` rather than via `GoogleAuth`. In the live wiring this fallback path is never exercised (auth always exposes `get_credentials()`), but it's a second, driftable source of truth for "what scopes does this app need" and should be unified.

### Calendar integration
`GoogleCalendar` (`services/google_calendar.py`) is a thin wrapper over `googleapiclient` `calendar v3`. `SyncService._pull_calendar` (`services/sync_service.py:198-233`) does incremental pull via `syncToken`; on `HttpError 410` (token expired/invalid) it clears the token and does one full resync with a 90-day `timeMin` backfill window (`services/sync_service.py:127-135`). Push (create/update/delete of the linked event) happens through the pending-ops queue, not synchronously.

### Tasks integration
`GoogleTasks` (`services/google_tasks.py`), thin wrapper over `tasks v1`, auto-creates its named tasklist if missing (`ensure_tasklist`). `SyncService._pull_tasks` does incremental pull via `updatedMin` high-water mark (no true sync-token/gap-detection mechanism the way Calendar has — if the stored `updatedMin` were ever lost or corrupted, older-than-last-pull remote changes would simply never be seen again, silently).

### Pending operations queue (the actual outbound path for `Task`)
`PendingOpsQueue` (`services/pending_ops_queue.py`), backed by the `pendingop` table. `TaskService` fires `after_create`/`after_update`/`after_delete` events (simple in-process pub/sub, `services/tasks.py:_listeners`); `SyncService` subscribes to these in `AppShell.__init__` and enqueues one of `gcal_create/update/delete` or `gtasks_create/update/delete` depending on whether the task is currently scheduled. `push_queue_worker()` drains due entries with exponential backoff (`min(30, 2**attempts)` seconds).

**Bug found**: in `SyncService.push_queue_worker` (`services/sync_service.py:148-169`), failures are classified into `RETRYABLE_STATUS` vs. not, but both branches call the exact same `self.queue.requeue(entry.id, str(exc))` — the classification is computed and then not acted on. A permanently-invalid operation (e.g. a 400 the API will never accept) is requeued forever at the same capped ≤30s backoff as a genuinely transient 503, with no max-attempts cutoff or dead-letter path, and nothing surfaces this to the user beyond a log line.

### Daily Tasks sync
`DailyTasksSync` (`services/daily_tasks_sync.py`) is architecturally separate from the queue above: it pushes **synchronously**, inline, the moment `DailyTaskService` fires its own `after_create/update/delete` events — no retry queue, no backoff, failures are caught and printed (`print("Daily tasks push error:", exc)`) and otherwise silently dropped. Pull is a full list-diff against `notes`-embedded JSON metadata (`{"planner_kind":"daily","local_id":...,"weekdays":...}`) rather than an incremental cursor.

### Auto pull/push driver
`AppShell._start_auto_refresh` (`ui/app_shell.py:257-293`) runs an `asyncio` task per active page: on activation it immediately pulls (`SyncService.pull_all()` + `DailyTasksSync.pull()`), re-renders, and pushes (`SyncService.push_queue_worker()`); then loops on a timer (`GOOGLE_SYNC.auto_pull_interval_sec`, default 60s). All of the actual Google API calls inside this loop are **synchronous/blocking** — there is no real `async`/`await` I/O, just blocking calls scheduled inside an `asyncio` task via `page.run_task`. The loop skips a whole cycle (pull *and* push) if any dialog/overlay is currently open (`_has_open_overlay()`), which avoids yanking the UI out from under an in-progress edit but also means push (of unrelated, already-saved changes) is delayed for as long as any modal — including an unrelated one — stays open.

### Conflict resolution
Whole-record, last-write-wins by comparing the remote item's `updated` timestamp against the local `updated_at` column (second resolution, no device identity) — see `SyncService._apply_calendar_event` / `_apply_task_entry`. If local is newer, the remote change is discarded and the local state is re-queued for push; there is no field-level merge.

### Failure handling summary
- Calendar 410 → automatic full resync (handled well).
- Tasks pull errors, and any non-410 Calendar pull error → logged and **re-raised** up through `AppShell._pull_from_google`'s bare `except Exception as e: print(...)` (i.e., swallowed at the UI boundary with no user-facing indication beyond whatever `refresh_status()` shows next time Settings is opened).
- Push errors → see the requeue-forever bug above.

### What is safe to preserve
- `GoogleAuth`, `GoogleCalendar`, `GoogleTasks` — thin, focused wrappers; keep.
- The `PendingOpsQueue` durable-retry pattern for outbound writes — sound design, just needs the retryable/non-retryable bug fixed and a max-attempts/dead-letter path added.
- `SyncTokenStorage`'s JSON-file cursor model — simple and works; fine to keep as-is or fold into a slightly richer store later.
- The overall shape — local SQLite is the source of truth, Google Calendar/Tasks are the only sync targets, no custom backend — matches the hard product constraint and should not change.

### What needs a decision, not blind preservation
Two independent, incompatible engines currently target **the same Google Tasks list** ("Planner Inbox", from `GOOGLE_SYNC.tasks_tasklist_name`):
1. The live path: plain `GoogleTasks` + `SyncService`, whole-record last-write-wins, no per-device metadata.
2. The unwired-but-tested path: `UndatedTasksSync` + `GoogleTasksBridge` + `AppDataClient` (`services/undated_tasks_sync.py`, `services/tasks_bridge.py`, `services/appdata.py`) — embeds richer JSON metadata (`task_id`, `priority`, `status`, `updated_at`, `device_id`) in each Google Task's notes, keeps a separate `SyncMapUndated` mapping table, and stores/merges a shared index+config JSON in the user's Google Drive `appDataFolder` with ETag-based optimistic concurrency and explicit multi-device conflict resolution (`_resolve_meta_entry`: newer timestamp wins, device-id tiebreak). This is materially better-designed for multi-device use (which matters once a mobile client exists) and has unit tests; it was simply never connected to `AppShell`.

If both were ever run against the same list simultaneously they would corrupt each other's data (different metadata formats, no shared locking). §6 recommends resolving this *before* any UI rebuild work touches unscheduled-task rendering, since the answer changes what the repository/use-case layer needs to expose.

Also unwired and safe to delete outright (no tests, no references anywhere): `services/sync.py` (`GoogleSync`/`JsonTokenStore` — an older, marker-in-description-based single-calendar sync engine, entirely superseded by `SyncService`), `storage/store.py` (`MetadataStore`, currently broken import besides being unused).

---

## 4. Current UI architecture problems

These are structural, not a list of individual bugs (though a few concrete bugs are called out where they illustrate the structural problem).

**1. The tree doesn't currently run.** Covered in §0 — included here because it's the most acute symptom of "patch UI issues in place without a clear ownership model": the last stabilization attempt's merge produced unresolved conflict markers that were committed, and the rollback on top of it is itself incomplete.

**2. Two competing overlay/dialog ownership models exist simultaneously.**
- The one actually used: `ui/dialogs.py`'s `open_alert_dialog`/`close_alert_dialog` stash the current dialog on a dynamically-created `page._planner_active_dialog` attribute (because `page.dialog` is not a reliable/supported property in the installed Flet version — see project memory), *also* set `page.dialog` "for compatibility", and call `page.open(dlg)`/`page.close(dlg)`. Meanwhile `AppShell._on_key`/`cleanup_overlays`/`_has_open_overlay` independently inspect `page.dialog` and `page.overlay` directly. Pages additionally append `DatePicker`/`TimePicker` controls straight onto `self.app.page.overlay` themselves (`ui/pages/today.py:56-58`, and duplicated in `calendar.py`, `history.py`, `daily_tasks.py`). That's three different places that all have to agree, by convention, on what "currently open" means, with no single owner.
- The one built during the last stabilization attempt but never wired in: `ui/overlay.py`'s `OverlayManager` class is a clean, single-owner stack-based implementation (push/pop dialogs and overlays, one Esc handler) — but it is never instantiated anywhere (`grep -rn "OverlayManager("` → no matches) and it also uses the removed `ft.colors` (lowercase) API, so it would fail immediately if wired in as-is.

**3. Dead code from the abandoned refactor is mixed in with live code**, making it hard to tell which implementation is "the real one" without tracing call sites (as this audit had to):
- `ui/overlay.py` (`OverlayManager`) — unused, and broken (`ft.colors`).
- `ui/dialogs.py`'s `open_overlay`/`close_overlay` functions — unused.
- `TodayPage._close_alert_dialog` — dead, its own docstring says "not used after overlay migration" (`ui/pages/today.py:745-750`).
- `services/sync.py` (`GoogleSync`, `JsonTokenStore`) — unused legacy sync engine.
- `services/task_repository.py` (`TaskRepository`) — parallel to `TaskService`, zero callers.
- `storage/store.py` (`MetadataStore`) — unused, and currently broken (missing settings constant).
- `helpers/snooze.py` — unused, **and** currently broken: it references `UI.snooze.evening_hour`/`evening_minute`/`tomorrow_hour`/`tomorrow_minute` and `UI.calendar.min_block_duration_minutes`/`grid_step_minutes`, none of which exist on the current `core/settings.py` dataclasses (confirmed: `hasattr(UI, "snooze")` → `False`). If anything ever called it, it would crash. Calendar's own `_snooze_minutes`/`_snooze_evening`/`_snooze_tomorrow` (`ui/pages/calendar.py:865-878`) reimplement the same three presets inline instead, with the evening/tomorrow hour hardcoded (`19:00`, `10:00`) rather than config-driven.

**4. `today.py` and `calendar.py` are large, near-duplicate monoliths (750 and 1068 lines).** Both independently implement: `_new_time_picker`, `_open_time_picker`, `_time_picker_on_change`, `_time_picker_on_dismiss`, `_set_tf_date`, `_set_tf_time`, `_parse_date_tf`, `_parse_time_tf`, `_combine_dt` — copy-pasted almost verbatim (compare `ui/pages/today.py:294-331` to `ui/pages/calendar.py:1006-1037`) — instead of using the already-written, already-unit-tested `helpers/datetime_utils.py` (`parse_date_input`, `parse_time_input`, `build_start_datetime`, `smart_defaults`, `snap_minutes`), which is currently only consumed by `helpers/snooze.py` (itself dead, per above). Each page also builds its own independent "edit task" dialog (title/date/time/duration/priority/notes + save/cancel) rather than sharing one.

**5. Pages are simultaneously view, controller, dialog host, and part of the domain layer.** Every `*Page` class directly: constructs and holds Flet controls as `self.` attributes; instantiates `TaskService()`/`DailyTaskService()` and calls it inline from click handlers; calls `self.app.sync_service.pull()` / `self.app.push_tasks_to_google()` synchronously from inside UI handlers; builds and opens `ft.AlertDialog`s inline; and calls `self.app.page.update()` / `self.app.cleanup_overlays()` itself. There is no use-case/view-model layer between "checkbox toggled" and the SQL `UPDATE` running — see `TodayPage.on_toggle_done` (`ui/pages/today.py:372-376`) doing exactly that in four lines with no intermediate abstraction. Validation logic (date/time regex parsing, "duration must be a number") also lives inline in the page rather than in the domain layer, duplicated per-page as noted in point 4.

**6. The calendar grid is a hand-rolled, tightly-coupled layout.** `_build_week_grid` (`ui/pages/calendar.py:305-399`) computes per-hour row heights from the max number of concurrent tasks in that hour across all 7 days, using constants from `core.settings.CalendarUISettings` (`chip_estimated_height`, `cell_vertical_padding`, `chips_spacing`) that all have to agree with each other and with the actual rendered chip size by convention, not by measurement. Horizontal scroll is synchronized between a separate header `Row` and body `Row` via two `ft.Ref`s and `on_scroll` handlers with a manual `_syncing_hscroll` reentrancy guard (`ui/pages/calendar.py:401-422`) to prevent the two handlers from ping-ponging each other. None of this geometry logic is unit-testable in its current form (it's interleaved with Flet control construction).

**7. Drag-and-drop payload resolution needs three fallback strategies**, which is a sign the payload hasn't reliably carried the dragged task id in the past: `_on_drop_accept` (`ui/pages/calendar.py:533-549`) first checks an instance variable set on `on_drag_start` (`self.current_drag_task_id`), then tries `e.data` as a bare digit string, then tries parsing `e.data` as JSON. This does not match the `DragTargetEvent.src_id` + `page.get_control(e.src_id).data` pattern noted as the established convention from the last stabilization attempt — i.e., `calendar.py`'s DnD was not actually migrated to that fix.

**8. Same action, inconsistent UX across pages.** Delete has a confirmation dialog on Today (`on_delete`) and Daily Tasks (`_confirm_delete`), but Calendar's right-click "Удалить" deletes immediately with no confirmation (`_open_chip_menu` → `act_delete` → `_delete_task`). The "Сразу в календарь" checkbox on Today (§1) is inert.

**9. A module-level side effect that silently degrades on this platform.** `ui/daily_tasks.py:20-24` calls `locale.setlocale(locale.LC_COLLATE, "ru_RU.UTF-8")` at import time inside a bare `except locale.Error: pass`. That locale name is glibc-style and doesn't exist on stock Windows, so `locale.strxfrm`-based sorting of daily-task titles (`_sorted_tasks`) silently falls back to whatever the default collation is, rather than the intended Cyrillic-aware collation — with no indication anywhere that this happened.

**10. No seam for testing or reuse.** Because pages instantiate concrete `TaskService()`/`DailyTaskService()` directly rather than receiving a repository/interface, there's no way to unit-test a page's logic or swap in a fake without monkeypatching module-level classes — and, relevant to the brief, no way for a future mobile client to reuse this layer as-is; it would have to be re-written against Flet-mobile from scratch, re-duplicating today's problems.

---

## 5. Recommended target architecture

Goal: a layered structure where the only thing that changes between "rebuilt desktop app" and "future MyPlanner mobile app" is the outermost UI layer, and where each inner layer is independently testable without Flet. No backend server anywhere — Google Calendar/Tasks remain the only sync/storage integration, matching the hard product constraint.

```
┌─────────────────────────────────────────────────────────┐
│ Flet UI (desktop today; mobile later — different package)│
│  pages/components, navigation shell, dialog/overlay svc  │
├─────────────────────────────────────────────────────────┤
│ UI state / view-model layer                              │
│  TodayViewModel, CalendarViewModel, ... — Flet-agnostic   │
├─────────────────────────────────────────────────────────┤
│ Application / use-case layer                             │
│  AddTask, RescheduleTask, ToggleDailyTask, SnoozeTask...  │
├───────────────────────────┬───────────────────────────────┤
│ Domain layer               │ Google sync adapters          │
│  Task, DailyTask, Priority │  GoogleAuth/Calendar/Tasks     │
│  pure, framework-free      │  PendingOpsQueue, SyncTokens   │
├───────────────────────────┴───────────────────────────────┤
│ Repository / storage layer (interfaces + SQLite impl)      │
└─────────────────────────────────────────────────────────┘
```

- **Domain layer** — plain Python dataclasses for `Task`, `DailyTask`, `Priority`, with no `SQLModel`/`table=True` and no Flet imports. Validation rules that currently live inline in page code (title required, duration is a positive int, at least one weekday selected, etc.) move here as the single source of truth.
- **Repository/storage layer** — a `TaskRepository`/`DailyTaskRepository` *protocol* (interface), with the current `services/tasks.py`/`services/daily_tasks.py` SQLModel-backed logic as the concrete SQLite implementation behind it. Event emission (`after_create`/`after_update`/`after_delete`) stays here or moves to the use-case layer — either is fine, but it should be one or the other, not implicit class-level listener sets as today.
- **Google sync adapters** — keep `GoogleAuth`, `GoogleCalendar`, `GoogleTasks` largely as-is (they're already appropriately thin). Consolidate the "undated tasks" sync question (§3) into one adapter before building UI against it. `PendingOpsQueue`/`SyncTokenStorage` stay, with the requeue bug (§3) fixed and a max-attempts/dead-letter path added.
- **Application/use-case layer (new)** — this is where `TodayPage.on_add`, `CalendarPage._schedule_task`, `DailyTasksPanel._on_toggle`, and the three snooze presets should actually live, as small callable use-cases that take a repository + sync adapter and return a result/error. This is the layer a page's click handler calls, and the layer unit tests target without touching Flet.
- **UI state/view-model layer (new)** — one view-model per page (or per major component, e.g. the calendar grid), holding presentation state (current week, filter values, "is saving" flags) and exposing commands the view binds to. Keeps `ft.Page.update()` calls and control construction entirely out of business logic. This layer is still Python and still shareable in principle, but it's the natural place where desktop and mobile *could* start to diverge (different navigation patterns) even if the use-case layer underneath stays identical.
- **Flet UI components** — replace the current copy-pasted inline builders with focused, reusable components:
  - `TaskEditForm` / `DateTimePickerField` — one implementation, backed by `helpers/datetime_utils.py`'s already-tested parsing, used by both Today and Calendar instead of two divergent copies.
  - `TaskCard` — one row/card renderer shared by Today's lists and History's results (currently three separate near-identical implementations: `TodayPage._row_for_task`, `CalendarPage._build_chip`, `HistoryPage._task_card`).
  - `PriorityBadge` — one widget wrapping `core/priorities.py`, replacing `_priority_marker`/`_priority_badge` duplicated per page.
  - `WeekGrid` / `DayColumn` / `HourSlot` — calendar grid decomposed into components, with the row-height and scroll-offset math extracted into plain functions that take (tasks-by-slot, config) and return numbers, unit-testable independent of Flet (see §6 step 6).
  - `DailyTasksPanel` — mostly fine structurally; move the rollover scheduling and dialog logic to the use-case/view-model layers rather than owning an `asyncio` task itself.
- **Navigation shell** — `AppShell` shrinks to: page registration/routing, wiring concrete implementations into view-models at startup, and delegating overlay/dialog concerns to the one dialog/overlay service below. No business logic, no ad-hoc overlay bookkeeping.
- **Dialog/overlay service** — exactly one implementation, constructor-injected into pages/view-models rather than imported ad hoc. Either repair `ui/overlay.py`'s `OverlayManager` (fix the `ft.colors`→`ft.Colors` bug, wire it into `AppShell`, delete the parallel mechanisms in `ui/dialogs.py`/`AppShell`) or replace it — but pick one and delete the other two/three.

**Mobile portability.** The domain, repository-interface, use-case, and Google-sync-adapter layers should live in a package with zero Flet imports (e.g. `planner_core/`), so a future MyPlanner mobile app — whether Flet-mobile or something else — depends on that package and supplies its own UI layer and its own concrete storage backend (SQLite is fine on mobile too via the same repository interface) plus the same Google OAuth/Calendar/Tasks adapters (the OAuth flow differs on mobile — no local-server redirect — but that's a new adapter behind the same interface, not a new domain model). This is the concrete mechanism for "support a future mobile app without a backend server": share everything below the UI/view-model line, per the layering diagram above.

---

## 6. Migration plan

**Step 0 — unblock, don't rebuild.** Resolve the merge-conflict markers in `ui/app_shell.py`/`ui/dialogs.py` and restore the `SyncMapUndated` export (or explicitly decide to drop that subsystem, in which case delete its callers too) so the app imports and `pytest` collects cleanly again. This is prerequisite housekeeping, not part of the rebuild, and should be its own small, reviewable change before anything in §5 starts. Verify with the same commands used in §0 (`ast.parse` on the two files, `pytest --collect-only`).

**Step 1 — decide the undated-tasks sync engine (§3) before touching UI.** Choose between the live simple path and the unwired `UndatedTasksSync` device-aware path, since the UI's "unscheduled list" data shape and the repository interface both depend on the answer. If keeping `UndatedTasksSync`, wire it into `AppShell` in place of the plain `GoogleTasks` push/pull for unscheduled tasks and delete the now-redundant code paths in `SyncService`; if dropping it, delete `services/undated_tasks_sync.py`, `services/tasks_bridge.py`, `services/appdata.py`, `services/appdata_store.py`, `models/sync_map_undated.py`, `migrate_descriptions.py`, `tests/test_undated_tasks_sync.py`. Verify by running the existing sync tests plus a manual pull/push round-trip against a test Google account.

**Step 2 — delete confirmed-dead code.** Cheap and low-risk since each item was verified in this audit to have zero references (`grep -rn` for the class/function name across the repo) and, where applicable, no test coverage: `ui/overlay.py`, `ui/dialogs.py`'s `open_overlay`/`close_overlay`, `services/sync.py`, `storage/store.py`, `services/task_repository.py`, and either fix-and-adopt or delete `helpers/snooze.py` (recommend adopting it — it's the only implementation with the right shape, just needs the missing `UI.snooze` settings block added and to become the thing `calendar.py`'s snooze menu items call instead of their inline reimplementation). Verify by re-running the whole `ast.parse` sweep and `pytest` after each deletion.

**Step 3 — extract the domain + use-case layer** from `services/tasks.py`, `services/daily_tasks.py`, and the inline handlers in `ui/pages/*.py`, without changing observable behavior. This is a pure refactor: move logic, don't rewrite it, so the existing manual test plan (create/edit/delete/complete/snooze/drag-drop, for both dated and undated tasks, with Google sync on) is the acceptance check at the end of this step, plus new unit tests for each extracted use-case.

**Step 4 — introduce the one dialog/overlay service** and migrate pages onto it one at a time, easiest first: Settings (no dialogs) → History (no dialogs) → Today → Calendar (most complex, most dialogs, owns DnD). Each page's migration is verified independently before moving to the next.

**Step 5 — extract `TaskEditForm`/`DateTimePickerField`** from the duplicated Today/Calendar code, backed by `helpers/datetime_utils.py` instead of the inline regex parsers. Verify both pages' create/edit flows manually plus unit tests on the shared component's date/time parsing (already partially covered by `tests/test_datetime_utils.py`).

**Step 6 — rebuild the calendar grid as its own component**, extracting row-height and scroll-offset calculation into plain, unit-tested functions before touching any rendering code. This directly targets the fragility called out in §4 point 6 and is the highest-risk area to get right before any visual changes.

**Step 7 — cosmetic/layout work, explicitly last**, once the layers above exist and are independently testable. This directly follows the task brief's instruction not to patch layout issues yet: doing it last, on top of a stable structure, is what prevents another round of "stabilization attempt made it worse."

Each step should be gated on: (a) the app still imports and starts, (b) the full test suite passes, (c) a manual smoke test of the touched page's create/edit/delete/complete + Google pull/push round-trip, run and actually observed — not assumed.

---

## 7. Risk list

- **Requeue-forever bug (confirmed, §3).** `SyncService.push_queue_worker` requeues non-retryable failures identically to retryable ones, with no max-attempts/dead-letter path. A single permanently-rejected operation loops silently forever. Should be fixed early (it's small and isolated) regardless of the broader rebuild timeline.
- **Two sync engines targeting one Google Tasks list.** If `UndatedTasksSync` were ever wired in without first disabling the live `GoogleTasks`-based path for unscheduled tasks (or vice versa), both would write incompatible metadata into the same "Planner Inbox" list and corrupt each other's state. §6 Step 1 exists specifically to force this decision before any further sync work.
- **Google API quotas/limits.** Calendar and Tasks APIs are both subject to per-user rate limits; the current design polls on a fixed interval per active page (default 60s) plus on every navigation — reasonable for a single-user desktop app, but worth re-checking if a mobile client adds a second concurrently-polling device against the same account.
- **OAuth/token handling.** One `token.json` covers Calendar + both Tasks lists + the (currently unused by the live path) Drive `appDataFolder` scope. Revocation or scope changes invalidate everything at once; there's no partial-scope degradation, only "re-run the whole consent flow." Per task instructions this audit does not touch credential/token logic — flagging only as a design risk for the rebuild to be aware of.
- **Sync conflicts.** The live path's last-write-wins-by-timestamp has no device identity and second-resolution timestamps — two offline devices editing the same task within one pull interval can silently clobber each other with no conflict surfaced to the user. The unwired `UndatedTasksSync` engine already solves this better for undated tasks (device_id + ETags); Calendar-linked (scheduled) tasks have no equivalent story today, which matters more once a mobile client exists.
- **Flet DnD reliability.** Calendar's three-strategy fallback for recovering the dragged task id (§4 point 7) suggests the `Draggable`/`DragTarget` payload has not been reliably delivered in this Flet version historically. A rebuild should not assume `e.data` is trustworthy; either adopt the `src_id`/`page.get_control` pattern explicitly and test it, or lean more on the already-present non-DnD alternative (right-click "Перенести…") if DnD keeps regressing — a product call, flagged here rather than decided.
- **Local DB migration strategy.** `storage/migrations.py`'s hand-rolled idempotent-ALTER-TABLE approach has no version table and no rollback; fine at the current schema size, but every future rebuild step that changes the domain model needs a corresponding manual migration function added to `run_all()`'s call chain, and there's nothing enforcing that ordering stays correct as it grows.
- **Desktop/mobile divergence.** Nothing today prevents it, because there's no package boundary between Flet UI and everything else — see §5. If the domain/use-case/repository/sync-adapter layers aren't physically extracted into a Flet-free package as part of this rebuild, a future mobile effort will most likely re-fork the logic rather than reuse it, reproducing today's duplication problem at a larger scale.
- **Windows-specific edges found in passing.** The `ru_RU.UTF-8` locale name (§4 point 9) and the `get_default_data_dir` empty-`env` bug (§0) are both small, cheap to fix, and worth fixing early since they're isolated and well-understood, unlike the larger structural items above.
