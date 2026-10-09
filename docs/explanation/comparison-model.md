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

The shared rule in `src/lion/state/model.py` uses the list identities in
`src/lion/state/comparison.py` for both equality and diffing:

- `packages.manual`, `packages.auto` and `packages.held` ignore order only when
  they contain unique, nonempty strings.
- `hardware.gpu` ignores order only when every entry has a unique, nonempty
  `pci_id`. Entries at the same PCI slot are compared field by field.
- All other lists, duplicate entries and missing/invalid identities compare as
  complete ordered values. No entries are silently deduplicated or overwritten.

`MemTotal` can wobble by a few KiB for purely technical reasons. Only
`hardware.memory_total_bytes` has a tolerance (1 MiB); GPU memory and all other
values compare exactly, including scalar types. A valid reading never equals
`0`. Timestamps do not participate, but each collector's `status` and `error` do.

## Rendering a difference

`status` groups the difference per collector with `+` (added), `-` (removed) and
`~` (changed) lines. If nothing changed it says so; if no state exists it tells
you to run `lion scan`.

## Structural versus value changes

A change is **structural** when something was added or removed: a whole
collector, a key, or a list entry. It is a **value change** when only an existing
value differs. Structural changes are the more consequential kind — a collector
or field appearing means the comparison baseline shifted — so `status` and
`diff` flag them separately: the JSON carries `struktur_geaendert` and the text
output leads with `Struktur geändert.`.

GPUs are matched by PCI slot, not physical card identity. A change of slot
appears as removed + added; replacing a card in the same slot appears as changed
fields. Known package selections are compared per value. Lists without unique
identities stay a single atomic value. Both history decisions and displayed
differences apply these same rules.

`scan` needs no extra rule: a structural change is already a difference, so it
appends a new entry on its own. Old snapshots stay valid because new collectors
and fields are additive; the first scan after such an upgrade shows the new
fields once as a structural addition.

## Who uses it

- `status` compares the freshly collected state with the latest stored one and
  never writes.
- `scan` compares the freshly collected state with the latest stored one to pick
  the event (`created`/`confirmed`/`appended`).
- `diff` compares two *stored* states without collecting and reuses the same
  rule, including the RAM tolerance.

The exact JSON categories are documented in
[Data structures](../reference/data-structures.md).
