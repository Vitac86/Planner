# Undated Sync Pilot Runbook — Phase 4 (test Google account only)

Operator guide for rehearsing the undated ("Planner Inbox") sync migration
described in `docs/SYNC_ENGINE_DECISION.md` §5. The tooling is
`scripts/undated_migration_pilot.py` plus the Phase 3 helpers it wraps
(`storage/backup.py::create_precutover_backup`,
`services/undated_migration.py`).

**Hard rules — read before anything else:**

1. The pilot runs against a **separate test Google account only**. The real
   account is out of bounds for every step in this document.
2. The default engine stays `"legacy"`. `PLANNER_UNDATED_ENGINE=undated` is
   set only per-shell, only for the pilot profile, and is never persisted
   anywhere (no config file, no system environment variable).
3. Nothing runs automatically. Every migration step below is an explicit
   command; the app performs no migration at startup.
4. No remote Google Tasks are ever deleted by the tooling, `Task.gtasks_id`
   is never removed, and the existing Calendar dead-letter rows in the real
   database are left untouched (their tooling is
   `python -m services.dead_letter_recovery` — reference only, not part of
   this pilot).

---

## 1. Isolation: keep the real account untouched

The app derives its whole data directory from the OS user-data location
(`core/settings.py::get_default_data_dir`): on Windows that is
`%APPDATA%\Planner`, on Linux `$XDG_DATA_HOME/Planner`. Everything lives
under it — `app.db`, `token.json` (the Google account binding),
`secrets/client_secret.json`, `backups/`, `storage/`, `device_id.txt`.

So a fully separate pilot profile = a separate `APPDATA` override. Every app
launch **and** every pilot script invocation must run in a shell with the
override set; a shell without it targets the real profile.

```powershell
# PowerShell — one pilot shell, used for everything below
cd D:\planner
$env:APPDATA = "D:\planner-pilot\device-a"
Remove-Item Env:PLANNER_UNDATED_ENGINE -ErrorAction SilentlyContinue  # legacy for now
```

```bash
# bash equivalent
cd /d/planner
export APPDATA="D:\\planner-pilot\\device-a"
unset PLANNER_UNDATED_ENGINE
```

Then place the OAuth client secret into the pilot profile (the client secret
identifies the *application*, not the account — reusing it is safe; the
account is chosen on the Google consent screen):

```powershell
New-Item -ItemType Directory -Force "D:\planner-pilot\device-a\Planner\secrets" | Out-Null
Copy-Item D:\planner\client_secret.json "D:\planner-pilot\device-a\Planner\secrets\client_secret.json"
```

Rules that keep the real account safe:

- **Never copy the real `token.json`** into a pilot profile. The token *is*
  the account. The pilot profile must acquire its own token by signing in
  with the **test** account when the browser consent screen opens.
- On the consent screen, check the account picker: it must show the test
  account. If the browser is signed into the real account, use "Use another
  account".
- Sanity check after first launch: `D:\planner-pilot\device-a\Planner\token.json`
  exists, and the real profile's files (`%APPDATA%\Planner` without the
  override) have unchanged modification times.
- Never run any `scripts/undated_migration_pilot.py` command in a shell
  without the `APPDATA` override.

## 2. Seed legacy state: create test unscheduled tasks

Launch the app in **legacy** mode (no `PLANNER_UNDATED_ENGINE`, or set to
`legacy`) from the pilot shell:

```powershell
.\.venv\Scripts\python.exe main.py
```

- Complete the OAuth flow with the **test** account.
- Create a handful of unscheduled tasks (no start date — they appear in the
  unscheduled list on Today). Vary them: different priorities, one marked
  done, one you plan to delete later, plus at least one *scheduled* task
  (date + duration) for the transition tests.
- Stay on the Today or Calendar page for ~2 minutes so the auto-refresh
  cycle pushes them (Tasks lane pushes every ~90 s; auto-refresh only runs
  on those two pages).
- Confirm at https://tasks.google.com (signed in as the test account) that
  the "Planner Inbox" list contains the unscheduled tasks.

## 3. Close the app before any migration step

Quit the app completely (check no `flet`/`python` process of the pilot
profile survives). Rationale: single writer — the backfill must not race the
sync loop, and the window between backfill and the flag flip should contain
no app writes at all.

## 4. Pre-cutover local backup

```powershell
.\.venv\Scripts\python.exe -m scripts.undated_migration_pilot backup
```

Copies the pilot `app.db` to
`<data-dir>\backups\app_precutover_<YYYY-MM-DD_HHMMSS>.db` and prints the
path. It fails loudly if the DB is missing, never overwrites an existing
backup, and the name is exempt from daily-backup rotation. **Record the
printed path** — `apply` requires it.

## 5. Export the remote Planner Inbox snapshot

```powershell
.\.venv\Scripts\python.exe -m scripts.undated_migration_pilot export --out D:\planner-pilot\exports\inbox_precutover.json
```

Writes a JSON snapshot of the remote state: tasklist id/title, every Google
Task (hidden and deleted included, with parsed planner metadata), the
current `planner_config.json` and `gtasks_index.json`, and the timestamp.
Read-only: it performs no remote writes (it will not even create the
tasklist unless you pass `--allow-ensure-tasklist`). It **refuses to
overwrite** an existing file — pick a new path for each snapshot. Record the
path — `apply` requires it.

## 6. Backfill dry-run and plan review

```powershell
.\.venv\Scripts\python.exe -m scripts.undated_migration_pilot dry-run
```

Writes nothing anywhere. Review the printed plan before going further:

- `tasklist_id` resolved and matching the export from step 5;
- `planned mappings`: exactly the unscheduled tasks you created in step 2,
  each with the expected `gtask` id (cross-check against the export JSON);
- `skipped` entries have understandable reasons (already mapped, tombstone,
  foreign `task_uid`, duplicated gtask id);
- no `BLOCKED:` line. A block means the tasklist id cannot be resolved or a
  foreign engine marker owns the list — stop and investigate.

`verify` at this point is expected to *fail* with "unscheduled task(s) carry
a gtasks_id but have no SyncMapUndated row" — that is precisely what the
backfill will fix:

```powershell
.\.venv\Scripts\python.exe -m scripts.undated_migration_pilot verify
```

## 7. Backfill apply

Only after the dry-run plan looks right, and only with the evidence paths
from steps 4–5:

```powershell
.\.venv\Scripts\python.exe -m scripts.undated_migration_pilot apply --backup "<path from step 4>" --export "D:\planner-pilot\exports\inbox_precutover.json"
```

What it writes — and nothing else: `syncmapundated` insert-only rows
(`dirty_flag=0`) and one etag-guarded merge write to `gtasks_index.json`.
`Task` rows are read, never written; `planner_config.json` is never written;
Google Tasks is never called. The command is idempotent — re-running it
plans nothing and skips everything as "already exists".

Then confirm:

```powershell
.\.venv\Scripts\python.exe -m scripts.undated_migration_pilot verify --backup "<backup path>" --export "<export path>"
```

Expected: `RESULT: OK` — no missing mappings, no uid mismatches, no missing
index entries, no duplicates, `Task.gtasks_id` intact, marker still `None`
(the backfill never claims it), both evidence paths exist.

## 8. Start the app with the undated engine

Immediately after a clean verify (keep the backfill→flip window free of app
runs), in the same pilot shell:

```powershell
$env:PLANNER_UNDATED_ENGINE = "undated"
.\.venv\Scripts\python.exe main.py
```

The flag is per-process: closing the shell or unsetting the variable returns
the default `legacy`. On its first sync the engine claims the `engine`
marker in `planner_config.json` with `"undated"`; from then on the legacy
`SyncService` refuses the Google Tasks lane (Calendar lane unaffected) —
that is the single-writer tripwire working, not a fault.

## 9. Functional test checklist (observe, don't assume)

All on the test account. After each step, wait for a sync cycle (~60–90 s on
Today/Calendar) and check both the app and https://tasks.google.com.

**Create / edit / complete / delete:**

- create an unscheduled task in the app → appears in Planner Inbox remotely;
- create a task at tasks.google.com → appears in the app;
- edit a title in both directions → propagates;
- complete/uncomplete in both directions → status round-trips;
- delete in the app → remote task gone, index entry tombstoned;
- delete remotely → local task gone (when it had no unsynced local edits).

**Scheduled ↔ unscheduled transitions:**

- give an unscheduled task a date + duration → the Google Task disappears
  from Planner Inbox and a Calendar event appears;
- unschedule it again → the Calendar event goes away and the task returns
  to Planner Inbox (new gtask id — that is expected);
- verify no duplicates accumulate in either surface after a few round trips.

**Two-device behavior (two data dirs, two `device_id.txt`):**

Open a second pilot shell as "device B" — same test account, different data
dir (a distinct `device_id.txt` is generated automatically per data dir):

```powershell
cd D:\planner
$env:APPDATA = "D:\planner-pilot\device-b"
# copy client_secret.json into D:\planner-pilot\device-b\Planner\secrets\ as in step 1
$env:PLANNER_UNDATED_ENGINE = "undated"
.\.venv\Scripts\python.exe main.py    # sign in with the SAME test account
```

Device B starts with an empty DB; its first pull creates the local tasks
from the remote list + index (no backfill needed on B). Then:

- change a **priority** on device A → visible on device B after its pull
  (priority travels via the appData index, not Google Tasks);
- edit the same task on both devices between sync cycles → both converge to
  one deterministic winner (newer `updated_at`; `device_id` breaks ties);
- kill the app on A mid-sync (task manager), restart → state converges, no
  duplicates;
- make an edit while offline (disable network), go online → the dirty flag
  drains it on the next cycle.

## 10. Inspect logs and the appData index

- **Sync log:** `logs/sync.log` (rotating), created relative to the
  directory the app was launched from — launch from `D:\planner` so it lands
  in `D:\planner\logs\sync.log`. The undated engine logs under the
  `planner.sync.undated` logger into the same file. Look for "claimed
  ownership", push/pull reports, and any `EngineOwnershipError`.
- **appData index/config:** run the export command with a **new** output
  path — it doubles as the inspection tool and dumps `planner_config.json`
  (check `engine: "undated"` after the flip), `gtasks_index.json` (entries
  keyed by gtask id, each carrying `task_uid`/`priority`/`status`/
  `updated_at`/`device_id`) and the full remote task list:

  ```powershell
  .\.venv\Scripts\python.exe -m scripts.undated_migration_pilot export --out D:\planner-pilot\exports\inbox_during_pilot_01.json
  ```

- **Consistency:** `verify` at any point (app closed for a stable picture).
  Note: tasks created *after* the cutover never had a legacy
  `Task.gtasks_id`, so they appear under "Task.gtasks_id was cleared" — for
  the rollback-safety reading, only pre-cutover tasks matter.

## 11. Rollback to legacy

1. Close the app.
2. Unset the flag: `Remove-Item Env:PLANNER_UNDATED_ENGINE` (PowerShell) /
   `unset PLANNER_UNDATED_ENGINE` (bash) — or set it to `legacy`.
3. Restart the app.

All legacy state is intact: `Task.gtasks_id` was never touched (confirm with
`verify` — "cleared" must be empty for pre-cutover tasks), and the backfilled
mapping rows / index entries are inert under the legacy engine and may
simply remain.

Two caveats, both by design:

- **The ownership marker outlives the rollback.** Once the undated engine
  has claimed `engine: "undated"` in `planner_config.json`, the legacy
  `SyncService` refuses the Google Tasks lane while the marker names another
  engine (Calendar keeps syncing). That is the two-writer tripwire. To hand
  the lane back to legacy **on the test account**, clear the marker with the
  app closed, in the pilot shell:

  ```powershell
  .\.venv\Scripts\python.exe -c "from services.google_auth import GoogleAuth; from services.appdata import AppDataClient; c = AppDataClient(GoogleAuth()); cfg, etag = c.read_config(); assert cfg.get('engine') in (None, 'undated'), cfg; cfg['engine'] = None; c.write_config(cfg, if_match=etag, on_conflict=lambda remote: {**remote, 'engine': None}); print('marker cleared')"
  ```

- **Tasks created during the pilot are new to legacy.** They have no
  `Task.gtasks_id`, so after rollback the legacy engine treats their remote
  copies as unknown and may import them as separate local tasks. Acceptable
  on the test account; one more reason the real cutover only happens after
  the pilot proves out.

## 12. What must NOT be done on the real account yet

- Do **not** set `PLANNER_UNDATED_ENGINE=undated` in any shell/profile that
  targets the real data directory. The default stays `legacy`.
- Do **not** run `dry-run`, `apply`, or the marker-clearing snippet against
  the real profile. (`backup` against the real DB is harmless but is not
  part of this pilot.)
- Do **not** write the real account's `planner_config.json` /
  `gtasks_index.json` in any way.
- Do **not** delete any remote Google Tasks, remove `Task.gtasks_id`, or
  replay/delete the existing Calendar dead-letter rows — they await separate
  manual recovery.
- The real-account cutover is a **separate, later decision**: it happens
  only after this checklist passes end-to-end on the test account, and it
  follows this same runbook (backup → export → dry-run → review → apply →
  flip), never automatically.

---

## Appendix: exact pilot sequence (copy-paste)

```powershell
# 0. pilot shell (EVERY command below runs in it)
cd D:\planner
$env:APPDATA = "D:\planner-pilot\device-a"
Remove-Item Env:PLANNER_UNDATED_ENGINE -ErrorAction SilentlyContinue

# 1. seed: launch app (legacy), sign in with the TEST account,
#    create unscheduled tasks, let them sync, then CLOSE the app
.\.venv\Scripts\python.exe main.py

# 2. local backup (record the printed path)
.\.venv\Scripts\python.exe -m scripts.undated_migration_pilot backup

# 3. remote export (new file, never overwritten)
.\.venv\Scripts\python.exe -m scripts.undated_migration_pilot export --out D:\planner-pilot\exports\inbox_precutover.json

# 4. dry-run and REVIEW the plan
.\.venv\Scripts\python.exe -m scripts.undated_migration_pilot dry-run

# 5. apply with evidence
.\.venv\Scripts\python.exe -m scripts.undated_migration_pilot apply --backup "<step-2 path>" --export "D:\planner-pilot\exports\inbox_precutover.json"

# 6. verify => RESULT: OK
.\.venv\Scripts\python.exe -m scripts.undated_migration_pilot verify --backup "<step-2 path>" --export "D:\planner-pilot\exports\inbox_precutover.json"

# 7. flip the flag for THIS shell only and run the pilot checklist (§9)
$env:PLANNER_UNDATED_ENGINE = "undated"
.\.venv\Scripts\python.exe main.py

# 8. rollback rehearsal (§11): close app, unset flag, restart, re-verify
Remove-Item Env:PLANNER_UNDATED_ENGINE
.\.venv\Scripts\python.exe main.py
```
