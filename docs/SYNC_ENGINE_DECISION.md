# Sync Engine Decision — Unscheduled Tasks

Status: **Decided** (Step 1 of the rebuild plan, see `docs/ARCHITECTURE_AUDIT.md` §6 Step 1).
Scope: which engine owns synchronization of **unscheduled** (`start is None`) tasks with Google Tasks, for future desktop **and** mobile MyPlanner development.
Hard constraint: the app remains a client-side planner using **Google APIs only** — no backend server, no REST API, no PostgreSQL/Firebase/cloud functions, no non-Google sync server. Every option below already satisfies this; it is restated because it rules out the "just add a sync server" answer to multi-device conflicts.

This document decides architecture only. No code is changed by this step.

---

## 1. Current live sync path

Wired in `ui/app_shell.py:44-61`. This is the only path that actually runs today.

**Construction chain:**

```
GoogleAuth
 ├── GoogleCalendar(auth, calendar_id="primary")
 ├── GoogleTasks(auth)                          # tasklist "Planner Inbox"
 └── GoogleTasks(auth, dailies_tasklist_name)   # tasklist "Planner Dailies"

SyncService(gcal, gtasks, TaskService(), SyncTokenStorage(), PendingOpsQueue())
DailyTasksSync(gtasks_dailies, DailyTaskService())
```

**Event wiring.** `TaskService` exposes a class-level in-process pub/sub; `AppShell` subscribes `SyncService.on_task_created / on_task_updated / on_task_deleted` to `after_create / after_update / after_delete`. Every local mutation therefore immediately routes into `SyncService`, which classifies the task via `_is_scheduled` (`start` and `duration_minutes` both set):

- **Scheduled** → Calendar lane: enqueue `gcal_create`/`gcal_update`, and enqueue `gtasks_delete` if the task previously lived in Google Tasks.
- **Unscheduled** → Tasks lane: enqueue `gtasks_create`/`gtasks_update`, and enqueue `gcal_delete` if it previously lived on the calendar.

This cross-lane cleanup is how the live path handles the scheduled↔unscheduled **transition** — an important behavior any replacement must preserve.

**Outbound (push).** `PendingOpsQueue` (`services/pending_ops_queue.py`) is a durable retry queue backed by the `pendingop` SQLite table (op type, task_id, JSON payload, attempts, last_error, next_try_at; exponential backoff capped at 30 s). `SyncService.push_queue_worker()` drains due entries. Known defect (audit §3): retryable vs. non-retryable classification is computed and then ignored — both branches requeue identically, forever, with no max-attempts/dead-letter path.

**Inbound (pull).** `SyncService.pull_all()`:

- Calendar: incremental pull via the Calendar API `syncToken`; on HTTP 410 the token is cleared and one full resync runs with a 90-day `timeMin` backfill. Robust.
- Tasks: incremental pull via an `updatedMin` high-water mark. There is no gap-detection equivalent to the calendar sync token — if the stored cursor is lost or corrupted, older remote changes are silently never seen again.

Cursors live in `SyncTokenStorage` (`services/sync_token_storage.py`), a flat JSON file at `storage/gcal_sync_token.json` (`{calendar:{syncToken,lastPullAt}, tasks:{updatedMin,lastPullAt}, lastPushAt}`).

**Driver.** `AppShell._start_auto_refresh` runs an asyncio loop per active page: pull → re-render → drain push queue, every `GOOGLE_SYNC.auto_pull_interval_sec` (default 60 s), skipping cycles while any overlay/dialog is open. All Google calls inside are synchronous/blocking.

**State model.** Google linkage is inline on the `Task` row (`gcal_event_id`/`gcal_etag`/`gcal_updated`, `gtasks_id`/`gtasks_updated`). No mapping table.

**Conflict resolution.** Whole-record last-write-wins: remote `updated` vs. local `updated_at`, second resolution, no device identity, no field-level merge. If local is newer, remote is discarded and local is re-queued for push.

**What the live path cannot sync.** The Google Tasks API has no priority field, and this path attaches no metadata anywhere else, so a task's `priority` (0–3, the primary sort key across all pages) simply does not travel between devices. `status` only round-trips as done/not-done via the API's `completed` state.

**DailyTasksSync** (`services/daily_tasks_sync.py`) is a separate, architecturally independent lane: synchronous inline pushes (no queue, best-effort, failures printed), full-list pull, correlation via a JSON blob in the Google Task `notes` field, against the separate "Planner Dailies" list. It is out of scope for this decision and is unaffected by it.

---

## 2. Alternative undated sync path (built, tested, never wired)

`UndatedTasksSync` (`services/undated_tasks_sync.py`) + `GoogleTasksBridge` (`services/tasks_bridge.py`) + `AppDataClient` (`services/appdata.py`) + `SyncMapUndated` (`models/sync_map_undated.py`). No `AppShell` code constructs any of it.

**UndatedTasksSync** is the engine: `sync()` = `pull()` then `push_dirty()`.

- `pull()` fetches the full remote list, reconciles each Google Task against the local DB via the mapping table (falling back to the shared index's `task_id`), creates local tasks for unknown remote ones, applies remote content when the local copy is not dirty, and applies index metadata (priority/status) with timestamp arbitration.
- `push_dirty()` scans all local undated tasks (`Task.start == None`), creates a mapping (dirty) for any unmapped one, and upserts every dirty task through the bridge, then updates the shared index entry.
- `mark_dirty(task_id)` is the intended event hook (the analogue of `SyncService.on_task_updated`): sets the mapping's `dirty_flag` and eagerly refreshes the index entry. The dirty flag is durable in SQLite, so a failed push is retried on the next sync cycle — a different but comparably durable retry mechanism to `PendingOpsQueue`.
- `remove_mapping(task_id, delete_remote=...)` exists for deletions/transitions but currently has **no caller**.

**GoogleTasksBridge** is the Tasks API wrapper: retry/backoff (5 attempts, 1→32 s exponential, on 429/5xx), `ensure_tasklist()` for "Planner Inbox", `fetch_all()` (full list, `showCompleted=True`), `upsert_task()`, 404-tolerant `delete_task()`. `fetch_all` also detects a legacy JSON metadata blob at the start of a task's `notes`, strips it, and **patches the remote task** to clean the notes — a write-on-read migration away from notes-embedded metadata.

**AppDataClient** is what makes this path structurally different. It stores two JSON files in the user's Google Drive **`appDataFolder`** — a per-app, per-user hidden space that requires only the already-requested `drive.appdata` scope and no server:

- `planner_config.json` — `{version, tasklist_id, last_full_sync}`: which tasklist the engine owns, shared across devices.
- `gtasks_index.json` — `{version, tasklist_id, tasks: {gtask_id → {task_id, priority, status, updated_at, device_id}}}`: a per-task **metadata sidecar** carrying exactly the fields Google Tasks cannot (priority, tri-state status, device attribution).

Writes use `If-Match`/ETag optimistic concurrency: on HTTP 412 the client re-reads the remote file, calls an `on_conflict` merge callback, and retries (up to 5 times).

**Conflict resolution** (`_resolve_meta_entry` + `_merge_index_payload`): per-entry, newer `updated_at` wins; on equal/absent timestamps, `device_id` string comparison is a deterministic tiebreak. `device_id` comes from `storage/device.py` — a uuid4 hex persisted once per installation in `device_id.txt`. This gives the path real multi-device semantics: two devices editing concurrently converge deterministically on metadata instead of last-writer-by-wall-clock.

**Tests** (`tests/test_undated_tasks_sync.py`, collected and passing): an in-memory `FakeAppData` + `FakeBridge` + SQLite-in-memory harness covering (1) `push_dirty` creating mapping + index entry + tasklist config, (2) notes metadata splitting (`_split_notes`), (3) completed-status round-trip on pull (`_status_payload` + pull applying `done`). This is the only sync engine in the repo with any automated coverage at all.

### Verified defects in the alternative path (must be fixed before wiring)

These were confirmed by reading the code during this decision, not speculated:

1. **Cross-device local-ID collision (correctness, serious).** The shared index stores the *local autoincrement* `task_id`. On a second device, `pull()` falls back to `entry["task_id"]` when no mapping exists (`undated_tasks_sync.py:181-182`) and will merge remote content into whatever unrelated local task happens to have that row id. The `Task` model already has a globally unique `uid` (uuid4) — the index must use it.
2. **Dead dedupe → remote duplicates.** `GoogleTasksBridge.find_task_by_local_id` matches on `item["metadata"]`, but `fetch_all` always sets `"metadata": {}` and puts parsed metadata in `"detected_meta"` (`tasks_bridge.py:196-217`). The pre-insert duplicate check can never match, so a lost mapping leads to duplicate Google Tasks.
3. **No remote-deletion propagation.** `fetch_all` skips `deleted` items and `pull()` never removes local tasks whose remote counterpart vanished. Deleting a task on the phone would resurrect nothing locally — it would simply never be noticed. (The live path handles this via `showDeleted`/`_apply_task_entry`.)
4. **No scheduled↔unscheduled transition wiring.** `remove_mapping(delete_remote=True)` exists but nothing calls it; the engine has no equivalent of `SyncService`'s cross-lane cleanup when a task gains or loses a date.
5. **Full-list pull every cycle.** No `updatedMin` cursor; every 60 s cycle fetches the entire list, and an unmapped push triggers a *second* full fetch via the (broken) dedupe. Fine at personal-planner scale, wasteful on mobile.
6. **Silent failure swallowing.** `push_dirty` does `except Exception: continue` and `_ensure_tasklist_id` returns `None` on any exception — no logging at all, unlike `SyncService`'s rotating `logs/sync.log`.
7. **Shaky Drive ETag extraction.** `AppDataClient._upload_json` reads `request.resp` for an ETag after execution and falls back to file `version`/`modifiedTime`; Drive v3 media updates do not reliably honor `If-Match` the way the code assumes. The 412-merge path is well-designed but may rarely trigger in practice — concurrency remains *eventually* correct via the merge-on-write callback, but the optimistic-locking guarantee is weaker than the tests (which stub it) suggest.
8. **Mixed naive/aware datetimes.** `pull()`/`_apply_remote_payload` use naive `datetime.utcnow()` while the mapping and index use tz-aware timestamps.

### Other implementations, for completeness

- `services/sync.py` (`GoogleSync`/`JsonTokenStore`) — legacy marker-in-description calendar engine, zero callers. Not a candidate.
- `storage/store.py` (`MetadataStore`) — cannot even be imported (references the nonexistent `core.settings.STORE_DB_PATH`), zero callers. Not a candidate.
- `models/task_sync.py` + `services/task_sync_store.py`, `services/appdata_store.py` — zero callers. Not candidates.

All four are deletion candidates in a later cleanup step (audit §6 Step 2); they play no role in this decision.

---

## 3. Comparison

| Criterion | Live path (`SyncService` Tasks lane) | Alternative (`UndatedTasksSync` stack) |
|---|---|---|
| **Correctness (today)** | Works in production daily. Known bugs: requeue-forever; silent cursor-loss gap. | Not wired; three correctness defects (§2: ID collision, dead dedupe, no remote-deletion) that would bite immediately in real multi-device use. |
| **Multi-device readiness** | Poor by design: second-resolution whole-record LWW, no device identity. Two offline devices editing within one pull interval silently clobber each other. | Designed for it: `device_id`, per-entry timestamp arbitration, deterministic tiebreak, shared merged index with conflict-merge-on-write. Needs the `uid` fix to actually be safe. |
| **Mobile MyPlanner readiness** | Would ship the clobbering problem to every user the day a phone client exists. No way to sync priority. | The appDataFolder sidecar is the *only* Google-APIs-only mechanism for cross-device metadata under the no-backend constraint. Engine is UI-free and session-factory-injected — directly reusable from a mobile shell. |
| **Risk of Google Tasks data corruption** | Low: writes only API-native fields (title/notes/status/due). | Moderate today: dead dedupe can duplicate tasks; `fetch_all`'s write-on-read notes cleanup patches remote tasks; a second legacy writer on the same list would fight it. Low after fixes + single-writer rule. |
| **Implementation complexity** | Lower: one engine, one queue, inline linkage columns. | Higher: mapping table + two Drive JSON files + merge logic. The complexity buys the multi-device semantics; it is not incidental. |
| **Migration effort** | Zero (status quo). | Moderate: fix §2 defects, wire events, one-time mapping backfill from `Task.gtasks_id`, feature flag, pilot. Bounded — the engine, bridge, client, model, and tests already exist. |
| **Test coverage** | None. Zero automated tests reference `SyncService`. | The only tested sync engine in the repo (3 tests + helpers, offline fakes for both Google surfaces). |
| **Compatibility with scheduled Calendar tasks** | Built in: the same engine owns both lanes and the transition between them. | None yet: must be given an explicit seam (transition hook) and must coexist with `SyncService`, which keeps the Calendar lane. |
| **Offline behavior** | Good: durable `pendingop` queue with backoff; local SQLite is source of truth. | Good: durable `dirty_flag` retried each cycle; local SQLite is source of truth. Index writes are also retried (flag stays dirty until persisted). |
| **Failure handling** | Rotating `logs/sync.log`, Settings-page status, 410 auto-recovery; but requeue-forever bug and UI-boundary `print()` swallowing. | Retry/backoff in both bridge and client is solid; but engine-level failures are swallowed with no logging at all (§2.6). Both paths need the same "surface errors to the user" work. |

The decisive rows are **multi-device** and **mobile**: those are the stated future, and the live path's deficiencies there are structural (Google Tasks has no priority field; LWW-without-identity cannot be patched into safety without building exactly the sidecar the alternative already has). The alternative's deficiencies (§2) are, by contrast, ordinary bugs with obvious fixes and an existing test harness to pin them.

---

## 4. Recommendation

**Migrate unscheduled tasks to the `UndatedTasksSync` stack.** It becomes the single owner of the "Planner Inbox" Google Tasks list. `SyncService` remains — unchanged in role — as the engine for scheduled tasks on Google Calendar and as the place where the scheduled↔unscheduled transition is detected. `DailyTasksSync` is untouched.

This is not "wire it in as-is": the §2 defects (at minimum items 1–4 and 6) are **blocking preconditions**, each with a regression test, before the engine touches a real account.

Why this and not the other two options:

- **Not "keep the live path":** a mobile MyPlanner client makes ≥2 devices the normal case, and the live path then silently loses edits (LWW without identity) and silently loses priority (no metadata channel). Fixing that inside `SyncService` means building an appDataFolder sidecar, a device identity, a mapping keyed by a stable ID, and merge logic — i.e., re-implementing `UndatedTasksSync` from scratch, minus its tests.
- **Not a "hybrid" as a permanent architecture:** two engines writing the same Google Tasks list is precisely the corruption scenario the audit flagged. The end state must have exactly one writer per Google surface: `UndatedTasksSync` → Planner Inbox, `SyncService` → Calendar, `DailyTasksSync` → Planner Dailies. (During migration there is a transitional flag, but never two simultaneous writers — see §5.)

One consequence to accept explicitly: scheduled (Calendar) tasks keep the weaker LWW conflict model for now. That is acceptable because the calendar lane has a real sync token, calendar events change less often concurrently, and extending the appDataFolder index to calendar-linked tasks later is a natural follow-up on top of this decision, not a conflict with it.

---

## 5. Migration plan

Incremental; each phase is a separate reviewable change, gated on the whole suite passing plus a manual round-trip against a **test Google account** (never the developer's real one).

**Phase 0 — safety rails (before any engine code changes).**
*Status: flag, tripwire and DB migration implemented (July 2026); the pre-cutover on-demand backup call is still pending and remains a hard gate before any production migration.* As built:
- **Feature flag.** `GOOGLE_SYNC.undated_engine = "legacy" | "undated"`, default `"legacy"`, set only via the `PLANNER_UNDATED_ENGINE` environment variable (`core/settings.py::resolve_undated_engine`); unknown values fall back to `"legacy"`, so a typo can never activate the new engine. Nothing in the live `AppShell` reads the flag yet — live behavior is byte-for-byte unchanged regardless of its value until Phase 2 wiring.
- **Engine gate.** `UndatedTasksSync` is inert unless the flag is `"undated"`: `sync`/`pull`/`push_dirty` return `False` with the deterministic skip reason `SKIP_REASON_ENGINE_NOT_SELECTED`, and `mark_dirty`/`remove_mapping`/the transition hooks no-op. This holds even if the object is constructed by mistake.
- **Ownership marker / two-writer tripwire.** The shared `planner_config.json` in appDataFolder carries an `"engine"` marker (`null` = unclaimed). When active, the engine claims a vacant marker with `"undated"` through the etag-guarded conflict-merging write; if the marker names any other engine — including one that appears concurrently on the 412 conflict path — every write path raises `EngineOwnershipError` and nothing is written. The legacy lane does not check the marker yet; teaching `SyncService` to refuse while the marker is foreign is part of Phase 2.
- **DB migration.** `storage/migrations.py::ensure_sync_map_undated_columns` (wired into `run_all`) idempotently adds `syncmapundated.task_uid` to databases created before the column existed, backfills it from `task.uid`, and creates the index; it is a no-op when the table is missing (fresh DBs get the full schema from SQLModel metadata) or already current.
- Pre-migration backup: `storage/backup.py` already snapshots `app.db` daily; the explicit on-demand backup call and the remote "Planner Inbox" export now exist as Phase 3 tooling (see below). **No production data migration happens without both being taken first.**

**Phase 1 — harden `UndatedTasksSync` (still unwired; pure engine work + tests).**
*Status: implemented (July 2026); the engine remains unwired from `AppShell`.* As built: the appData index and `SyncMapUndated` carry `task_uid` (`Task.uid`) as the cross-device identity, local autoincrement ids never leave the device; `GoogleTasksBridge.fetch_all` returns planner metadata consistently and includes deleted/hidden items, dedupe goes through `find_task_by_uid`; deletions are index tombstones `{"deleted": true, "reason": "deleted" | "scheduled"}` with edit-wins resurrection and idempotent replay (full semantics in the `services/undated_tasks_sync.py` module docstring); transition seams are `on_task_unscheduled` / `on_task_scheduled` / `on_task_deleted`; malformed remote items are skipped deterministically and reported via `SyncReport.skipped`.
- Replace local `task_id` with `Task.uid` in the appData index and mapping lookups (§2.1). Test: two session factories simulating two devices with colliding autoincrement IDs must not cross-contaminate.
- Fix `find_task_by_local_id` to read `detected_meta`… or better, drop notes-based discovery entirely and rely on the index (§2.2). Test: push with lost mapping does not duplicate the remote task.
- Add remote-deletion propagation: fetch with `showDeleted=True`, treat mapped-but-missing/deleted remote tasks as deletions with a local tombstone check (§2.3). Test: remote delete → local delete; local dirty edit vs. remote delete → deterministic outcome, documented.
- Add the transition hook: an explicit `on_task_scheduled(task_id)` (calls `remove_mapping(delete_remote=True)`) and `on_task_unscheduled(task_id)` (calls `mark_dirty`) surface for `SyncService` to invoke (§2.4).
- Replace `except Exception: continue/pass` with logging into the existing `planner.sync` logger (§2.6); normalize tz-aware datetimes (§2.8).

**Phase 2 — wire behind the flag.**
*Status: implemented (July 2026).* As built:
- When the flag is `"undated"`: `AppShell` constructs `UndatedTasksSync` (sharing one `AppDataClient` with `SyncService`) and subscribes its `on_task_created`/`on_task_updated`/`on_task_deleted` to the `TaskService` events; the created/updated hooks route statelessly (undated task → `on_task_unscheduled`/`mark_dirty`, dated task → `on_task_scheduled`, vanished task → `on_task_deleted`). `UndatedTasksSync.sync()` joins the auto-refresh pull cycle and `push_dirty()` joins the push step. In `"legacy"` mode the engine object is never constructed.
- `SyncService.tasks_lane_blocked_reason()` gates the whole Google Tasks lane: with the local flag `"undated"` it skips `_pull_tasks`, refuses to enqueue `gtasks_*` ops, and `push_queue_worker` refuses (requeues with the reason, never executes) any stale `gtasks_*` op — so no duplicate pending ops and never two writers. The Calendar lane is untouched.
- Legacy-side tripwire: even with the local flag `"legacy"`, `SyncService` reads the shared `planner_config.json` `engine` marker (TTL-cached, 5 min; unreadable marker ⇒ legacy behavior preserved) and refuses the Google Tasks lane while the marker names another engine, logging a clear reason. This protects against two *installations* at different app versions writing the same list.
- No production migration/backfill happens in this phase; existing `gtasks_id` values are untouched (Phase 3).

**Phase 3 — one-time mapping backfill.**
*Status: tooling implemented and tested (July 2026, `tests/test_undated_migration.py`); not executed against any real account yet.* Nothing in this phase runs automatically: there is no call site in `main.py`/`AppShell`, no import-time side effect, and no dependency on the engine flag — every entry point is invoked manually (operator script/REPL), and the default engine remains `"legacy"` throughout.

- **Backup/export requirements (hard gate before apply).**
  - Local: `storage/backup.py::create_precutover_backup(db_path, backup_dir)` copies `app.db` to `app_precutover_<YYYY-MM-DD_HHMMSS>.db` and returns the path. It raises when the database is missing, never overwrites an existing backup (colliding names get a numeric suffix), and its naming is exempt from the daily-backup rotation, so pre-cutover snapshots are never rotated away.
  - Remote: `services/undated_migration.py::export_planner_inbox_snapshot(bridge=…, appdata=…, path=…)` writes a JSON snapshot containing the tasklist id/title, every Google Task from `bridge.fetch_all` (hidden and deleted included, with parsed planner metadata), the current `planner_config.json`, the current `gtasks_index.json`, and the export timestamp. It refuses to overwrite an existing file and performs no remote writes by default (it will not even create the tasklist unless `allow_ensure_tasklist=True` is passed).
- **Dry-run/apply behavior.** `services/undated_migration.py::backfill_planner_mappings(appdata=…)` selects every local `Task` with `start IS NULL`, a non-empty `gtasks_id`, and no `SyncMapUndated` row yet, and plans per task: a clean mapping row (`task_id`, `task_uid`, `gtask_id`, resolved Planner Inbox `tasklist_id`, `dirty_flag=0`) plus a `gtasks_index.json` entry keyed by `gtask_id` (`task_uid`, `status`, `priority`, `updated_at`, `device_id` — exactly the shape `UndatedTasksSync` writes). Dry-run is the default and writes nothing anywhere; `apply=True` performs the writes but is refused (`ValueError`) unless a `local_backup_path` **and** a `remote_export` (path or payload) are provided — or `confirm_without_backup=True` waives the gate explicitly. Conflicting items are skipped with a per-item reason, never overwritten: already-mapped tasks, a `gtask_id` already mapped to (or duplicated on) another local task, index tombstones, and index entries carrying a different `task_uid`.
- **Ownership marker contract.** Expected before backfill: the `engine` marker in `planner_config.json` is vacant (`null`) or already `"undated"`. A foreign marker blocks the run — `apply` raises `EngineOwnershipError`, dry-run reports `blocked_reason` and plans nothing. After backfill the marker is exactly what it was before: the backfill never writes `planner_config.json`, so it cannot hand the list to a second writer. Claiming `"undated"` remains the engine's own first write once `PLANNER_UNDATED_ENGINE=undated` is set; until then the undated engine is inert and `SyncService` keeps the lane, so legacy and undated never write simultaneously.
- **Exact data touched by apply — and nothing else.** `syncmapundated`: inserts only (existing rows never updated or deleted). `gtasks_index.json`: one etag-guarded merge write adding the planned entries and filling the file's `tasklist_id` if vacant (the conflict callback re-checks tombstones/foreign uids against the remote payload). `Task` rows are read, never written — `gtasks_id` is **kept populated** (do not drop it) until Phase 5, so rollback to `"legacy"` remains possible throughout the pilot. Google Tasks is never called: the backfill takes no bridge, so no remote insert/update/delete can occur, and **nothing remote is ever deleted**.
- **Rollback guarantee.** Unset `PLANNER_UNDATED_ENGINE` (or set it to `legacy`) and the Tasks lane returns to `SyncService` with all legacy state (`Task.gtasks_id`, pending ops) intact; the backfilled mapping rows and index entries are inert under the legacy engine and may simply remain. The pre-cutover `app.db` copy and the JSON snapshot cover the destructive-failure case.
- **Still required before a test-account pilot (Phase 4):** a small operator script wrapping the sequence (close the app → `create_precutover_backup` → `export_planner_inbox_snapshot` → dry-run → review the plan → `apply` → set `PLANNER_UNDATED_ENGINE=undated` → restart), run on a **test Google account only**; keeping the window between backfill and flag-flip minimal (no app running in between, so index metadata cannot go stale); and the manual two-device checklist of Phase 4.

**Phase 4 — pilot on a test Google account.**
Manual checklist, observed not assumed: create/edit/complete/delete in both directions; priority change on device A visible on device B (two data dirs with distinct `device_id.txt` simulate two devices); schedule an undated task → Google Task disappears, Calendar event appears; unschedule → reverse; offline edits queue and drain; kill the app mid-sync and verify convergence on restart; confirm `gtasks_index.json` contents via a small inspection script. Then flip the default flag to `"undated"`.

**Phase 5 — cleanup (a later, separate task; nothing deleted before the pilot passes).**
- Remove the Tasks lane from `SyncService` (`_pull_tasks`, `_apply_task_entry`, `gtasks_*` ops) and from `PendingOpsQueue.VALID_OPS`; `PendingOpsQueue` stays for the Calendar lane.
- Delete the confirmed-dead implementations: `services/sync.py`, `storage/store.py`, `services/task_sync_store.py` + `models/task_sync.py`, `services/appdata_store.py`.
- Drop the legacy flag once no installation needs rollback.

**Tests to add along the way:** the Phase 1 regression tests above; a two-device index-merge test exercising `_merge_index_payload` on a real 412 path (fake client that actually raises it); a transition test (scheduled↔unscheduled with both engines' hooks); a backfill idempotency test; and a "legacy writer refused while ownership marker present" test.

---

## 6. Non-goals

Explicitly out of scope for this decision and for the migration it plans:

- **No backend server** of any kind — no REST API, no PostgreSQL server, no Firebase backend, no cloud functions, no non-Google sync relay. Google Calendar, Google Tasks, and the Google Drive `appDataFolder` remain the only remote surfaces.
- **No UI rewrite in this step.** No Flet layout changes, no drag-and-drop changes; the UI keeps consuming `TaskService` exactly as today.
- **No OAuth/token rewrite in this step.** `GoogleAuth`, scopes, and `token.json` handling stay as they are (the `drive.appdata` scope is already requested by the live auth flow).
- **No production data migration without a backup/export** taken first, per Phase 0 — and no remote deletions at any point during migration phases 0–4.
