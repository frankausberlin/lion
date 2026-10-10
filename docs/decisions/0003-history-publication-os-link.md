# 0003 — History entries are published with `os.link`

- **Status:** accepted
- **Date:** 2026-10-07

## Context

A new state is written while other commands may read the history. A plain write
to the final file name can expose a half-written entry and can overwrite an
existing one, which would destroy history. The confirmation of an unchanged
state, in contrast, must update the single latest entry in place.

## Decision

New history entries are published with a hard link (`os.link`) from a fully
written and `fsync`ed temporary file, and an existing entry is never
overwritten. Only refreshing `confirmed_at` on the latest entry rewrites a
file, and that happens atomically via `os.replace`.

## Consequences

- Readers never observe a partially written entry, and no stored state is lost.
- Concurrency is safe: scans serialize on an exclusive process lock for the
  complete read/compare/write operation.
- The data filesystem must support hard links; a filesystem without them is not
  supported.
- Same-instant collisions get a `~NNNN` suffix so the name still sorts after the
  base entry and the newest-entry tie-break holds.

## Alternatives

- Writing directly to the final path was rejected: it can truncate an existing
  entry and exposes partial files.
- `os.rename` for new entries was rejected because it silently replaces an
  existing file instead of failing loudly.
