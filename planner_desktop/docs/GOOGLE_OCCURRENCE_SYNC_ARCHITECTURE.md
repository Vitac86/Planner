# Google occurrence sync — Phase 3.2B3B

Phase 3.2B3B synchronizes one changed or cancelled occurrence of a
Planner-owned linked recurring series. `TaskSeries` remains authoritative and
the recurring master is never changed by an occurrence operation. Network
access still happens only during explicit manual sync.

## Identity

Local identity is `(series_uid, occurrence_key)`. `occurrence_key` names the
original recurrence slot:

- all-day: `YYYY-MM-DD`;
- timed: `YYYY-MM-DDTHH:MM@IANA-timezone`.

It is immutable when an exception moves. Google identity is
`(recurringEventId, originalStartTime)`: `recurringEventId` must be the active
linked master and `originalStartTime` must map exactly to the local occurrence
key. The instance's current `start` is mutable and is never used as identity.

`domain/google_occurrence.py` performs the strict, deterministic conversion in
both directions. Timed identities retain the original wall-clock value and
the series IANA zone. All-day identities retain a date. Wrong kind, timezone,
date, DST fold/nonexistent resolution, or a value that is not a real series
slot is rejected. The DST policy is the same deterministic policy used by the
Phase 3.2A recurrence materializer.

Instance IDs are stored only in
`task_series_occurrence_calendar_links`; they are never written to ordinary
Task Google fields.

## Schema v10

The migration is additive, idempotent, and preserves all v9 links, queues,
audits, quarantine rows, Tasks, and history.

`task_series_occurrence_calendar_links` records:

- the local identity, series link id/generation, and remote master;
- the optional remote instance id and exact original-start kind/value/zone;
- remote ETag/update time and last local/remote canonical hashes;
- sync status, remote cancellation flag, conflict reason/snapshot, and
  detach timestamps.

The active unique index is scoped to
`(series_uid, occurrence_key, link_generation)` where `detached_at IS NULL`.
No Task-history row is cascade deleted.

`pending_calendar_series_instance_ops` is a dedicated queue containing only
`update` and `cancel`. It is separate from both the recurring-master and
ordinary Task queues. `external_series_occurrence_changes` now also stores the
matched local identity and durable resolution state.

## Local exceptions and cancellation

For an active Planner-owned linked series, “only this occurrence” may change
title, notes, or a same-kind schedule. Supported schedule changes are timed
move/resize and all-day date move. Timed/all-day conversion and multi-day
timed exceptions are rejected.

The Task remains a local exception with its original `series_uid` and
`occurrence_key`. Completion, tags, priority, and history remain local-only
and do not enqueue instance work. Deletion retains a Task tombstone and
enqueues one instance cancellation; it never changes the RRULE or deletes the
master.

Queue coalescing is:

- update + update: one update with the newest desired state;
- update + cancel: one cancel;
- cancel + update: rejected until the user explicitly restores the tombstone;
- duplicate/no-op update: no new queue row.

Retryable failures use bounded backoff. Terminal operations remain visible and
run again only after an explicit retry.

## Lookup and full-resource writes

The occurrence engine uses a known instance id when available. Otherwise it
calls the active master's instances endpoint with deleted instances included
and accepts exactly one exact `originalStartTime` match. Zero or multiple
matches fail; current moved start is never consulted.

Before either write the gateway retrieves the complete instance and verifies:

1. Planner owns the active parent master;
2. `recurringEventId` equals that master;
3. `originalStartTime` equals the expected local slot;
4. the current ETag equals the acknowledged write base.

Planner then merges only `summary`, `description`, `start`, `end`, confirmed or
cancelled status, and its private occurrence markers into the complete
resource. Unowned Google fields are preserved and `recurrence` is removed.
Cancellation is a full instance update with `status=cancelled`; an already
cancelled matching instance is an idempotent success.

Private markers are:

- `planner_series_uid`;
- `planner_occurrence_key`;
- `planner_link_generation`;
- `planner_occurrence_payload_hash`;
- `planner_occurrence_schema_version`.

The canonical hash includes only Planner-owned remote fields. It excludes
tags, priority, completion, history, template data, attendees, reminders, and
location.

## ETag race protection and reconciliation

“Keep Planner occurrence” records the ETag of the remote snapshot explicitly
acknowledged by the user. The engine fetches again immediately before writing.
If the ETag changed, it performs no write, replaces the quarantine/conflict
snapshot, and requires another explicit decision.

If Google accepted a write but local finalization failed, retry fetches the
same exact instance. It finalizes locally without a second write only when the
actual canonical content, identity, status, and markers agree. Stale private
markers alone never prove success. Queue deletion is the last persistence
step.

Cancelled instance identity is retained after success so restart and later
pulls can reconcile the same tombstone safely.

## Pull and quarantine lifecycle

Manual sync order is:

1. recurring-master queue;
2. occurrence queue;
3. ordinary Task queue;
4. pull remote changes;
5. one persisted combined summary.

A linked instance is matched through the active parent link/generation and
strict `originalStartTime` conversion. It never becomes an ordinary Task.

- Canonical echo: refresh instance id, ETag, and update time; resolve duplicate
  quarantine.
- Unexpected change: preserve the remote snapshot in quarantine, mark the
  occurrence `remote_changed`, and do not overwrite or auto-enqueue.
- Remote cancellation: mark `remote_cancelled`, retain the local Task, and
  wait for an explicit resolution.
- Persistence failure: abort before advancing the pull cursor.

Quarantine history is retained after resolution.

## Explicit resolutions

**Use Google occurrence** performs no Google write. It transactionally applies
the supported same-kind snapshot to one local exception, preserving the local
identity. A remote cancellation becomes or preserves a local tombstone. The
quarantine and occurrence-link metadata are finalized together.

**Keep Planner occurrence** requires confirmation. It records the current ETag
and coalesces one update or cancellation from the local state. Quarantine is
not cleared until a later successful manual sync.

**Keep both as local copy** creates an independent ordinary local Task from the
remote snapshot, strips series/master/instance linkage, creates no automatic
Google event, retains the original local occurrence, and resolves the row as
`duplicated_local_copy`.

**Ignore for now** changes nothing.

Unsupported remote kind conversions or multi-day timed transformations disable
Use Google and expose the exact reason. Keep Planner and Ignore remain
available only while ownership and identity are verified.

## UI and isolation

The editor labels a linked occurrence “Экземпляр серии Google”, shows its
original slot and sync state, and enables only supported single-occurrence
edits. Calendar drag/resize waits for the explicit
“Изменить только этот экземпляр” confirmation; mouse movement never commits.
Settings exposes pending update/cancel, terminal, unresolved, remote-cancelled,
resolved-history, and per-series exception counts plus the four resolution
choices. Esc closes dialogs and controls expose accessible names/descriptions.

Search, History, and UI opening are local reads. Bulk completion/tag/priority
remains local; bulk schedule changes remain blocked. Duplicating an occurrence
creates an independent ordinary Task. Automatic, startup, page-open, and timer
Google sync remain disabled.

## Phase 3.2B3C boundary

Still deferred:

- adoption of unrelated external Google masters;
- remote “this and future” series split;
- automatic conflict merging;
- local/external series conversion;
- automatic/background sync;
- timed/all-day conversion for one linked occurrence;
- attendee, reminder, or location mutation;
- bulk remote occurrence writes.

The controlled real-account pilot is reported separately and must not start
without a new explicit confirmation for the isolated live-pilot profile.
