# Compare stored states

`lion history` and `lion diff` work on the stored state history. Both are
read-only: they collect nothing and never write to the history.

## List the history

```bash
uv run lion history
uv run lion history --limit 5
```

`history` lists every stored state from oldest to newest and shows its stable
reference. The reference is the file name without `.toml`, i.e. the `erstscan`
in compact form (`2026-10-05T20-00-00.123456Z`), plus a `~NNNN` suffix for
same-instant collisions. It never changes once published.

```text
  #  REF                           ZULETZT BESTÄTIGT
  1  2026-10-05T18-00-00.123456Z   2026-10-05T20:00:00.123456+00:00
  2  2026-10-05T20-00-00.123456Z   2026-10-05T20:00:00.123456+00:00  aktuell
```

`--limit N` shows only the newest `N` entries (oldest first within that
selection) while keeping the global indices and stable references. The full
history is still validated first, so a damaged older entry is never hidden.

## Diff two states

```bash
uv run lion diff 1           # entry 1 against the latest entry
uv run lion diff 1 2         # entries 1 and 2
uv run lion diff previous    # the previous entry against the latest
```

`lion diff <old> <new>` compares two stored states without collecting or writing
and reuses the same comparison rule as `status` (including the RAM tolerance).
`<new>` defaults to the latest state.

## Reference resolution

References are resolved in this order:

| Input | Meaning |
| --- | --- |
| `latest` / `head` / `aktuell` | newest entry by `zuletzt_bestaetigt` |
| `previous` / `prev` / `vorherig` | the entry before that |
| a bare number, e.g. `2` | 1-based index from `lion history` (1 = oldest) |
| the compact reference | exact file name without `.toml` |
| a unique prefix, e.g. `2026-10-05T18` | shortest unique match |
| an ISO `erstscan`, e.g. `2026-10-05T18:00:00Z` | normalized for timezone and seconds |

## Errors

- An ambiguous prefix or unknown reference fails with the list of valid
  references.
- Fewer than two stored states is an error.
- `history` and `diff` validate every entry, so a damaged file stops them with
  its path instead of being skipped.

## JSON output

```bash
uv run lion history --json
uv run lion diff previous --json
```

The shapes are documented in
[Data structures](../reference/data-structures.md).
