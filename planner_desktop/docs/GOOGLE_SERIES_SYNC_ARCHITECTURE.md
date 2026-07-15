# Google recurring-master sync — Phase 3.2B2

## Scope and authority

Phase 3.2B2 adds one explicit write path: a supported local `TaskSeries` may be
linked to one newly created Google Calendar recurring master. The local
`TaskSeries` remains authoritative. Materialized `Task` occurrences are a local
view/history cache and never become separate Calendar events.

The phase does not adopt an existing external master, edit/cancel one Google
occurrence, push local exceptions or EXDATE, perform a remote "this and future"
split, resolve conflicts automatically, or restore a remotely deleted master.
Those operations are the Phase 3.2B3 boundary.

## Official Calendar API audit

The implementation follows the primary Google Calendar API documentation:

- [`events.insert`](https://developers.google.com/workspace/calendar/api/v3/reference/events/insert)
  permits a client-supplied event `id`. Its alphabet is lowercase base32hex
  (`0-9`, `a-v`), length is 5–1024, and the id must be unique in the calendar.
  A recurring event is created by including the RFC 5545 `recurrence` array.
- [Recurring events](https://developers.google.com/workspace/calendar/api/guides/recurringevents)
  identifies an instance by `recurringEventId` and `originalStartTime`. Updating
  one instance creates an exception; changing "this and future" requires a
  deliberate two-master split. B2 therefore quarantines linked instance changes
  and defers both mutations.
- [Extended properties](https://developers.google.com/workspace/calendar/api/guides/extended-properties)
  provides calendar-copy-private key/value metadata. B2 stores only Planner
  ownership/version/revision/hash markers there.
- [`events.patch`](https://developers.google.com/workspace/calendar/api/v3/reference/events/patch)
  has partial-update semantics. B2 patches only Planner-owned master fields and
  merges private markers with unrelated existing private properties.
- [Calendar API errors](https://developers.google.com/workspace/calendar/api/guides/errors)
  documents retryable quota/server failures and that deleting an already deleted
  event can return 410. Master delete treats 404/410 as idempotent success.

## One local series ↔ one Google master

`task_series_calendar_links` is separate from `task_series`. An active partial
unique index permits one active link per local series; another partial unique
index prevents two active local links from claiming the same
provider/calendar/event tuple. Detached rows remain as diagnostics/history.
Deleting or detaching a link never cascades into `TaskSeries` or historical
occurrences.

The event id is available before any network call:

```
"plr" + lowercase_base32hex(SHA-256(series_uid))
```

It is stable across retries/restarts, contains only Google-valid characters,
does not use Python `hash()`, randomness, or time, and is never used for another
series. Remote private markers provide a second ownership check:

- `planner_series_uid`;
- `planner_link_version`;
- `planner_series_revision`;
- `planner_payload_hash`.

A foreign/missing ownership marker at the deterministic id is a terminal id
collision. There is no random-id fallback and therefore no duplicate master.

## Canonical master payload

Planner owns and fingerprints only:

- title (`summary`);
- notes (`description`);
- first occurrence start/end;
- timed recurrence IANA timezone;
- canonical recurrence lines.

The fingerprint is SHA-256 of deterministic UTF-8 JSON with sorted keys and
compact separators. Tags, completion, priority display metadata, template data,
occurrence rows, and task history are absent. All-day `end.date` is exclusive.
Timed start/end carry the series IANA timezone used to expand recurrence.

## Link lifecycle

Statuses are `pending_create`, `synced`, `pending_update`, `pending_delete`,
`conflict`, `remote_deleted`, `detached`, and `terminal_error`.

`connect_to_google()` performs validation and one SQLite transaction that adds
the pending link and one CREATE row. It never builds a gateway and never calls
Google. A successful manual sync records the remote id/etag/update time and the
synced local revision/hash, then marks the link `synced`.

A series-level title, notes, schedule, or rule edit recomputes the canonical
hash and coalesces one UPDATE. Tag/priority/completion/history edits do not touch
the master queue. A conflict or remotely deleted link is never overwritten by a
new automatic UPDATE.

## Separate series queue and coalescing

`pending_calendar_series_ops` is independent from
`desktop_pending_calendar_ops`; it uses the same bounded retry/backoff constants
but has its own due/dead-letter rows and diagnostics.

| Existing + request | Result |
|---|---|
| CREATE + UPDATE | one CREATE carrying the latest revision/hash/payload |
| CREATE + DELETE before push | remove the op and detach; zero Google calls |
| UPDATE + UPDATE | one UPDATE carrying the latest state |
| UPDATE + DELETE | one DELETE |
| DELETE + reconnect | connection rejected while delete is active |
| no-op or tag-only edit | no queue mutation |
| terminal op | visible dead-letter; no automatic retry |

An unattempted DELETE can be explicitly cancelled. Terminal retry is explicit
and allowed only when no competing pending row exists and the link is still in
the matching terminal state.

## Master create, update, and delete

The gateway exposes separate methods:

- `insert_recurring_master(id, payload)`;
- `get_recurring_master(id)`;
- `patch_recurring_master(id, payload, expected_etag=...)`;
- `delete_recurring_master(id)`.

Ordinary `insert_event`/`patch_event`/`delete_event` semantics remain unchanged.

CREATE first fetches the deterministic id during retries. An exact
series-uid/payload-hash match is reconciliation success. A duplicate insert is
handled the same way after a 409. A different owner is terminal; a same-owner
but different payload is a conflict.

UPDATE fetches the master, checks ownership and the last recorded etag, and
does not overwrite an unexpected remote edit. Only Planner-owned master fields
are patched; unrelated Google fields and unrelated private properties are
preserved.

DELETE is queued only by an explicit user action. An already absent master is
success. Retryable versus terminal errors use the existing gateway classifier.

## Local/remote non-atomic boundary and recovery

Google and SQLite cannot participate in one transaction. The durable queue row
is therefore removed last. If Google succeeds and local link/catalog persistence
fails, the next manual sync fetches the deterministic id and compares private
series uid, revision, and payload hash. An exact desired state completes local
persistence without another mutation. DELETE retries treat absence as success.
The manual-sync concurrency guard is released in `finally`, including failures.

## Conflict state

Pull or pre-write validation treats a changed etag/hash/owner as an unexpected
remote modification. The latest remote snapshot is persisted in
`external_calendar_series`, the link becomes `conflict`, any pending automatic
overwrite is removed, and the local `TaskSeries` is unchanged. A cancelled or
absent linked master becomes `remote_deleted`; local definition and history are
kept and B2 does not recreate it. Conflict resolution and restoration are B3.

## Remote occurrence-change quarantine

When pull sees an instance whose `recurringEventId` belongs to an active local
link, it upserts `external_series_occurrence_changes` with the instance id,
original start, status, etag/update time, and a normalized payload snapshot. It
creates zero ordinary Tasks and zero queue operations. Repeated pulls update
`last_seen_at`. Settings exposes the unresolved count with the B3 message.

Instances of unlinked external masters keep the Phase 3.2B1 ordinary-instance
behavior. Catalog, link, or quarantine persistence failure occurs before the
pull cursor is stored, so the change is replayed safely.

## Explicit disconnect/delete semantics

There is no ambiguous one-click series delete:

1. **Disconnect and keep Google** cancels pending local master ops and detaches
   the link. The remote master is untouched.
2. **Delete Google, keep local** queues DELETE; success tombstones the catalog
   row and detaches the link. The local series becomes local-only.
3. **Delete local and Google** queues DELETE with durable local-delete intent.
   Only after remote absence is confirmed does Planner tombstone the local
   definition and remove replaceable unfinished rows. Completed history and
   exceptions remain. A pre-push CREATE+DELETE needs no remote call and can
   finalize locally immediately.

Direct `RecurrenceService.delete_series()` rejects an active link and asks the
user to choose one of these actions.

## Linked local occurrence restrictions

Completion/restore remains local. Duplicate creates an independent ordinary
Task. Tags and priority display metadata remain local. B2 blocks drag/resize,
postpone/unschedule/delete, "this occurrence" schedule edits, bulk schedule
mutations, and "this and future" split for linked occurrences with:

> Изменение отдельных экземпляров серии Google будет добавлено на следующем этапе.

Whole-series title/notes/schedule/rule edits remain allowed and coalesce one
master UPDATE.

## Manual-only orchestration

The one explicit manual cycle is:

1. push pending series-master operations;
2. push pending ordinary Task operations;
3. pull remote changes with linked-master/instance handling;
4. persist the combined summary.

Opening Settings, Calendar, Today, an inspector, or a series editor only reads
local SQLite data. No startup, page-open, timer, or background Google sync is
introduced.

## Phase 3.2B3 boundary

Deferred explicitly:

- adoption of an existing external master;
- editing/cancelling one Google occurrence;
- synchronization of local exceptions/EXDATE/instance writes;
- remote "this and future" split;
- conflict-resolution UI and automatic merge policy;
- restoring a remotely deleted master.
