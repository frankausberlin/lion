# Standalone ADRs

> Referenced by [ADR 0017](../decisions/0017-standalone-adrs.md).

Not every decision is made inside a recorded shell. `lion adr` documents
**standalone ADRs** — decisions that exist on their own, such as switching the
default browser — as TOML documents under `$XDG_DATA_HOME/lion/adrs/`. They
share the field catalog, the completeness rule and the status values with the
recording-bound ADRs ([Records and ADRs](records-and-adrs.md)); `lion records`
keeps the recording-bound view and is unchanged. Command usage lives in
`lion adr --help`.

## Files and ids

- One document per decision at `$XDG_DATA_HOME/lion/adrs/<id>.toml`, in a
  directory created on demand with mode `700`.
- The id is `adr-` plus the compact UTC creation timestamp
  (`adr-2026-10-13T12-00-00.000000Z`), the same format the recording
  directories use. A same-instant collision adds a `~NNNN` suffix
  (`…Z~0001`), so the base name sorts first and the newest-entry tie-break is
  correct.
- New documents are published with a hard link (never overwriting) as mode
  `600`; edits replace the document in place (`os.replace`, mode `600`).
- `REF` resolves like everywhere in LION: an exact id, a unique prefix or
  `latest`.

## Schema 1

```toml
schema_version = 1
id = "adr-2026-10-13T12-00-00.000000Z"
created_at = "2026-10-13T12:00:00.000000+00:00"
edited_at = "2026-10-13T12:05:00.000000+00:00"   # only after an edit
recording = "2026-10-13T11-00-00.000000Z"          # optional recording reference

[fields]
title = "Use Firefox as the default browser"
decision = "Switch from Chromium to Firefox."
status = "accepted"
```

- `schema_version` must be `1`; `id` must match the file name; `created_at` (and
  `edited_at`, when present) must be timestamps with an offset; `recording`,
  when present, must be a non-empty recording reference.
- `[fields]` is a non-empty table of non-empty strings. Field names are
  arbitrary; the catalog order is `title`, `decision`, `component`, `scope`,
  `context`, `consequences`, `alternatives`, `revisit_when`, `uses`,
  `supersedes`, `status`, with extra fields after them.
- `title` plus `decision` make the ADR `complete`. `status` defaults to
  `proposed`, also accepts `accepted` and `superseded`, and any other value is
  kept verbatim and reported as an issue — never a fatal error.

Reading is strict: a damaged document fails loudly with its path in `lion adr
list`, `lion adr show` and `doctor` — it is never skipped.

## Editing, never deleting

`lion adr edit [REF]` opens a TOML template of the current document in
`$EDITOR` (fields in catalog order, unset catalog fields commented). Saving
**overwrites and adds** fields and stamps `edited_at`; there is no delete
command and LION never removes a field. The `id` and `created_at` must stay
unchanged. Strict validation applies: an invalid draft (including an emptied
`[fields]` table) aborts loudly, the temporary file stays in the store with its
path in the message, and the document is not written.

Unlike the recording overlay (`records edit`), where an empty `[fields]` table
means "deliberately no ADR", a standalone ADR always has at least one field.

## `lion adr`

| Command | Behavior |
| --- | --- |
| `adr` / `adr status` | Counters: `total`, `complete`, `incomplete`, `edited` and the status distribution. Read-only, never collects. |
| `adr add` | Create a new ADR. Flags `--title`, `--decision`, `--status`, `--field NAME=VALUE` (repeatable) and `--recording REF`; at least one field is required. Prints the ref and the path. |
| `adr edit [REF]` | Edit one ADR in `$EDITOR` (see above). |
| `adr list` | List stored ADRs, oldest first (`#`, REF, STATUS, TITLE, CREATED). |
| `adr show [REF]` | Show one ADR in detail (`REF` defaults to `latest`). |

The detail view prints the header `ADR <ref> (<status>, complete|incomplete)`,
the fields in catalog order, `recording`/`created_at`/`edited_at` when present,
and `uses`/`supersedes` resolved against the standalone ADRs first and the
recordings second (`(unknown)` when there is no match — never an error).

### JSON shapes

`adr status --json`:

```json
{"total": 1, "complete": 1, "incomplete": 0, "edited": 0,
 "status": {"proposed": 0, "accepted": 1, "superseded": 0}}
```

`adr list --json`: `{"adrs": [{"ref", "status", "complete", "edited", "title",
"recording", "created_at"}]}`.

`adr show --json`:

```json
{"ref": "…", "fields": {"title": "…"}, "status": "accepted",
 "complete": true, "edited": false, "recording": "",
 "created_at": "…", "edited_at": "",
 "uses": [{"ref": "…", "resolved": "…"}], "supersedes": [],
 "issues": [{"reason": "…", "segment": "…", "line": 0, "time": "…", "text": "…"}]}
```

`adr status`, `adr list` and `adr show` never write and never touch the scan
history or the recordings; the JSON contracts of `scan`, `status`, `watch` and
`records` are unchanged.

## doctor

The read-only `doctor` topic `adr` carries the check `adr.storage`: a missing
store is `ok` with an empty list; a path that is not a directory, an unreadable
store or an unexpected entry is an `error` naming the path; a broken or invalid
document is an `error` and is never skipped; a `recording` reference that
matches no recording is a `warn`.
