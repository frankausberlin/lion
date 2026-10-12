# 0012 — Recording layout and shell-segment liveness

- **Status:** accepted (E2 implementation complete; see the implementation notes below)
- **Date:** 2026-10-12

## Context

A recording spans a `watch start` → `watch stop` interval that can outlive a
single shell process: the user may `exec zsh`, the terminal may receive SIGHUP,
the process may crash, or the machine may reboot between the two ends. The E1
prototype showed which exit paths are observable (`ignore/watch-prototype/`,
Q4 of the report):

- a normal `exit` runs `zshexit`;
- `exec zsh` **does not** run `zshexit` and **keeps the PID**;
- SIGHUP **does** run `zshexit` (contrary to the earlier planning assumption);
- SIGKILL runs nothing.

PID alone therefore cannot identify a shell segment: it is reused across time
and preserved across `exec`. A recording also must not disturb the scan
history, which stores validated machine snapshots under
`$XDG_DATA_HOME/lion/history`.

## Decision

Recordings live in their own data space, separate from the scan history.

- Layout: `$XDG_DATA_HOME/lion/recordings/<ref>/` with `meta.toml`,
  `state-start.toml`, `state-end.toml` and an `events/` directory. The
  `Snapshot` model is reused for the two states; the scan history is left
  untouched.
- A **shell segment** is stamped with its PID, the kernel `boot_id` and a start
  time. Segment ends are derived when reading, from liveness of that stamp —
  never from an exit event.
- A missing end is never treated as success. `exec`, SIGHUP and crashes all
  leave an incomplete segment, which is reported as such.
- Exactly one recording is active per user and machine. Start and stop enforce
  the same clock guard as `save_state` (a stop whose clock precedes the latest
  confirmation fails loudly).
- `stop` is not required before the shell ends or before a reboot: liveness
  provides the end.

## Consequences

- Liveness must check PID + `boot_id` + start time together; PID reuse and
  `exec` are handled correctly.
- Relying on `zshexit` is unnecessary — and, because SIGHUP and normal exit do
  run it, it may be used as a *fast-path* hint, but correctness never depends on
  it. This keeps the design honest about `exec` and crashes.
- A recording can be read while still active; the reader shows segments whose
  liveness is still true as open. An older segment whose process was taken
  over (`exec zsh`, a reloaded integration) can receive no further events and
  is reported as ended (`superseded`) instead.
- Parallel shells interleave chronologically on read; the segment name and the
  file line break ties, so the order stays deterministic and the text view can
  mark the segment when the event stream switches.

## E2 implementation notes

- Implemented in `src/lion/program/recording.py`. Layout:
  `$XDG_DATA_HOME/lion/recordings/<ref>/` with `meta.toml` (schema version 1:
  `ref`, `status` `open`/`stopped`, `started_at`, `stopped_at`, `note`,
  `integration_version`), `state-start.toml`, `state-end.toml` and `events/`;
  the reused `Snapshot` model stores both states. Record directories use mode
  `700`, files `600`, and same-instant collisions get a `~NNNN` suffix.
- The `active` pointer is published with `os.link` (never overwritten) and
  removed last during `stop`; a stale pointer is loud on `start`/`stop` and a
  `warn` in `doctor`. `state-end.toml` is published before `meta.toml` is
  updated; both are never overwritten.
- Segment files are named `<startticks>-<pid>-<random>.log`. Liveness checks
  the kernel `boot_id`, the PID and `/proc/<pid>/stat` field 22 (start ticks):
  mismatches yield `reboot`, `gone`, `reused` or `unknown` (unreadable boot
  id), and an alive segment is shown as open. Segments that share one stamp
  are a takeover chain (`exec zsh`, a reloaded integration): only the newest
  of the chain keeps its liveness verdict, every older one ends as
  `superseded` — unconditionally, so the takeover date stays stable after the
  process ends.
- Readers validate strictly and order events chronologically, with the segment
  name and file line as tie-breakers; the text view prints a continuation line
  when interleaved events switch the segment. `stop` guards against the last
  event of its own record, and both mutations use the shared clock guard over
  meta, states and segment starts.
- `read_events` decodes the documented event grammar and fails loudly with the
  segment path and line number on damage; no read function writes.

## E3 addendum — the ADR overlay

- An optional `adr.toml` (schema version 1: `edited_at` plus a `[fields]`
  table) may live inside a record directory. It is the second documented
  in-place write next to `meta.toml` and is written only by `lion records
  edit`: atomically via `os.replace`, mode `600`, and only for stopped
  recordings.
- `start`, `stop` and `watch` never read or write it. Readers fail loudly with
  the path on a damaged overlay (never skipping it), and `doctor` reports it
  as an error finding (`watch.adr.<ref>`).
- Overlay fields replace the event-derived field set completely; the raw
  events are never modified (see ADR 0013).

## Alternatives

- **Store recordings in the scan history / one ledger.** Deferred, not chosen:
  the two record types have different lifetimes and validation, and mixing them
  would touch the proven history invariants (see
  `docs/explanation/architecture.md`). A
  shared ledger remains a later, explicit decision.
- **Rely on `zshexit` for segment ends.** Rejected: `exec zsh` and SIGKILL skip
  it, so a recording could appear successfully closed while it was not.
- **Identify a segment by PID.** Rejected: PID reuse and `exec` (same PID)
  make it ambiguous.
