# 0004 — A single tolerance for `MemTotal`

- **Status:** accepted
- **Date:** 2026-10-07

## Context

`/proc/meminfo` `MemTotal` can change by a few KiB between scans for purely
technical reasons (firmware reservations, driver allocations). Without a
tolerance, these harmless wobbles would append a new history entry on nearly
every scan and drown the real changes.

## Decision

Two states compare equal when their collector sections match exactly, or when
the only difference is `hardware.memory_total_bytes` within
`MEMORY_TOTAL_TOLERANCE_BYTES` (1 MiB). This is the only tolerated field.

## Consequences

- A small, constant drift in `MemTotal` is treated as "unchanged", so the
  history reflects real changes.
- Every other value — including GPU memory — compares exactly, and a valid
  reading never equals `0`, so a reading switching to or from `0` stays visible.
- The rule lives in one place (`src/lion/state/model.py`) and is shared by
  `status`, `scan` and `diff`, so they cannot disagree.

## Alternatives

- A per-field or percentage tolerance was rejected: it adds rules without a
  concrete need and weakens exact comparisons elsewhere.
- No tolerance was rejected: it makes ordinary scans look like changes.
