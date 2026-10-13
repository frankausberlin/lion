# 0017 — Standalone ADRs live beside the recordings

- **Status:** accepted
- **Date:** 2026-10-13

## Context

ADRs arrived with the watch feature ([ADR 0013](0013-watch-adr-annotations.md))
as flat `#-LION:` annotation lines inside a recording: one ADR per record, read
back from the raw events on every read. That covers decisions made while
working in a shell, but not every decision: switching the default browser, for
example, happens outside a shell and is still far-reaching. Such a decision has
nowhere to live, and the planned MCP server (Phase 5) needs a solid layer to
write decisions against before it can expose one.

## Decision

A **standalone ADR** is a TOML document under `$XDG_DATA_HOME/lion/adrs/`,
owned by `src/lion/program/adrs.py` (model, store and doctor check) and exposed
by the `lion adr` command (`src/lion/command/adr.py`). The recording-bound ADRs
and `lion records` are unchanged; there is no migration and no shared data
space.

- **Artifact and id.** One document per decision at `<id>.toml`; the id is
  `adr-` plus the compact UTC timestamp of the recording directories
  (`COMPACT_TIMESTAMP_FORMAT` from `program/storage.py`, the single format
  source). A same-instant collision adds a `~NNNN` suffix, so ordering and
  prefix resolution stay correct. New documents are published with `os.link`
  (never overwriting) as mode `600`; the store directory is created on demand
  with mode `700`.
- **Document (schema 1).** `schema_version = 1`, `id`, `created_at`, optional
  `edited_at` and optional `recording = "<ref>"`, plus a non-empty `[fields]`
  table of non-empty strings. The `id` must match the file name; `created_at`
  and `edited_at` must be timestamps with an offset. Reading is strict: a
  damaged document fails loudly with its path and is never skipped.
- **Shared rules.** The field catalog, the completeness rule (`title` +
  `decision`) and the status rule (`proposed` default, unknown values reported
  as an issue, never fatal) come from `program/annotations.py`, the same source
  the recording overlay uses, so the two cannot drift.
- **Edit, never delete.** `lion adr add` creates; `lion adr edit [REF]` opens a
  TOML template in `$EDITOR` and overwrites and adds fields in place, stamping
  `edited_at` (`os.replace`, mode `600`). There is no delete command and LION
  never removes a field. An empty `[fields]` table is rejected loudly — the
  deliberate difference from the recording overlay, where it means "no ADR".
- **Commands.** `lion adr` is a Typer sub-app whose bare invocation runs
  `status` (like `shlib`/`watch`/`records`): `status`, `add`, `edit [REF]`,
  `list`, `show [REF]`, with `--json` for `status`/`list`/`show`. `uses`/
  `supersedes` resolve against the ADR store first and the recordings second; an
  unresolved token displays as `(unknown)` and is never an error.
- **doctor.** A new read-only topic `adr` (between `watch` and `classify`)
  carries `adr.storage`: a missing store is `ok` with an empty list; a path that
  is not a directory, an unreadable store or an unexpected entry is an `error`; a
  broken document is an `error` naming its path; an orphaned `recording`
  reference is a `warn`.

## Consequences

- Decisions can be recorded without a shell and without a recording, on the
  same field and status vocabulary as the recording-bound ADRs; the reference
  page is [Standalone ADRs](../reference/adrs.md).
- The two sources stay separate in this phase. A combined view and search over
  both arrives with `serve` (Phase 5); no MCP tools are built here.
- `doctor` gains the tenth topic and reports the store read-only; `scan`,
  `status`, `watch` and `records` are untouched.
- The store is a new writer under `$XDG_DATA_HOME/lion/`, consistent with the
  write boundary: no system files, no root.

## Alternatives

- **Reuse a recording for every decision.** Rejected: many decisions happen
  outside a shell, and a synthetic recording would distort the event model.
- **Store standalone ADRs inside the recording space.** Rejected: it couples
  two lifecycles and complicates the liveness model.
- **Allow deleting fields or documents.** Rejected for this phase: "edit, never
  delete" keeps the history of decisions intact and matches the recording-bound
  rule.
- **Build the combined ADR view now.** Rejected: it belongs to the MCP read
  surface (Phase 5), which can then read both sources uniformly.
- **Infer a decision from a collector (drift monitoring).** Rejected here: a
  default-browser collector does not exist; a status-diff/heartbeat view is a
  later phase.
