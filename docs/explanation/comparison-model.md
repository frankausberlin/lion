# Comparison model

`scan` writes and `status` only reads. Both use the same rule to decide whether
two collected states are the same, so they never disagree.

## Events

A state has two timestamps: `erstscan` (first observation) and
`zuletzt_bestaetigt` (last unchanged confirmation).

- No stored state → a new entry is created (`created`).
- Identical collector data → the latest entry's `zuletzt_bestaetigt` is
  refreshed in place (`confirmed`); no new file is written.
- Any difference → a new entry is appended (`appended`). The previous entry is
  kept, so the history records every distinct state.

Returning states are not reactivated: a state that reappears after a change is
stored as a new entry, not matched to an old one.

## The equality rule

Two states are equal using the shared rule in `src/lion/state/model.py`: an exact
canonical match of the `collectors` section, or a difference confined to
`hardware.memory_total_bytes` within `MEMORY_TOTAL_TOLERANCE_BYTES` (1 MiB).

`MemTotal` can wobble by a few KiB for purely technical reasons, which is not a
hardware change. Only that one field has a tolerance; GPU memory and every other
value compare exactly, and a valid reading never equals `0`. The timestamps do
not participate, but each collector's `status` and `error` do.

## Rendering a difference

`status` groups the difference per collector with `+` (added), `-` (removed) and
`~` (changed) lines. If nothing changed it says so; if no state exists it tells
you to run `lion scan`.

## Who uses it

- `status` compares the freshly collected state with the latest stored one and
  never writes.
- `scan` compares the freshly collected state with the latest stored one to pick
  the event (`created`/`confirmed`/`appended`).
- `diff` compares two *stored* states without collecting and reuses the same
  rule, including the RAM tolerance.

The exact JSON categories are documented in
[Data structures](../reference/data-structures.md).
