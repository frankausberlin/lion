# 0012 — Recording layout and shell-segment liveness

- **Status:** proposed (E1 draft; to be finalized with the E2 implementation)
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
  liveness is still true as open.
- Parallel shells are ordered by segment start time and the shared `boot_id`, so
  their events interleave deterministically on read.

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
