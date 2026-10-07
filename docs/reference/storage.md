# Storage

## Paths

The state history is TOML files under `$XDG_DATA_HOME/lion/history`, defaulting
to `~/.local/share/lion/history`. New records use UTC timestamps and are named
after their `erstscan` in compact form (`2026-10-05T20-00-00.123456Z`), plus a
`~NNNN` suffix for same-instant collisions.

The writer's lock file lives at `$XDG_DATA_HOME/lion/.history.lock` (under the
default data directory when `XDG_DATA_HOME` is unset).

## Publication and mutation

- New entries are published with a hard link (`os.link`) so an existing entry is
  never overwritten. This requires a filesystem that supports hard links.
- Only refreshing `zuletzt_bestaetigt` rewrites the latest file, and it does so
  atomically via `os.replace`.
- Concurrent scans serialize the complete read/compare/write operation using an
  exclusive process lock on `.history.lock`. The lock file remains in place; its
  lock is released when the writer closes it or exits. `status` stays read-only
  and never takes the lock.

## Clock rollback

If the system clock precedes the latest confirmation, `scan` fails before
modifying history. Correct the clock before retrying. Lion does not synthesize
observation times or change existing references; equal timestamps still use
the collision suffixes described above.

## Validation

Every `.toml` entry is strictly validated: `schema_version = 1`, UTC offsets on
both timestamps, and a valid `status` per collector. Any unreadable or invalid
entry stops `status` with its path in the error message instead of being skipped
silently. `scan` parses every entry but fully validates only the newest head, so
an unreadable or syntactically invalid file still fails.

## Compatibility

- The former `scans/` directory is no longer read and is left untouched; there
  is no migration.
- LION does not silently skip damaged files or fall back to older records.
- Existing history with unqualified package names remains readable; the first
  scan with architecture-qualified names records this representation change.
- The `manual`, `auto` and `held` package lists retain the names returned by
  `apt-mark`.

The persisted format and its fields are documented in
[Data structures](data-structures.md).
