# Document a decision without a recording

Some decisions are not made in a recorded shell — switching the default browser,
for example. `lion adr` stores such a decision as a **standalone ADR** under
`$XDG_DATA_HOME/lion/adrs/`. This guide creates, edits and reads one; the
schema, ids and JSON shapes live in
[Standalone ADRs](../reference/adrs.md).

## Create a decision

```bash
uv run lion adr add \
  --title "Use Firefox as the default browser" \
  --decision "Switch from Chromium to Firefox." \
  --status accepted
```

`--title`, `--decision` and `--status` are shortcuts; any other field can be set
with a repeatable `--field NAME=VALUE`. At least one field is required, so an
unfinished draft is fine:

```bash
uv run lion adr add --field component desktop
uv run lion adr add --field context "Reconcile with the 2026 browser audit." \
  --recording latest
```

`--recording REF` records which recording the decision belongs to; `doctor`
warns when that reference matches no recording. The command prints the new ref
and path.

## Find it again

```bash
uv run lion adr                 # counters: total, complete, incomplete, edited
uv run lion adr list            # every ADR, oldest first
uv run lion adr show latest     # the newest ADR in detail
uv run lion adr show adr-2026   # a unique prefix also works
```

`lion adr show` resolves `uses`/`supersedes` against the standalone ADRs first
and the recordings second; an unknown token shows as `(unknown)` and is not an
error.

## Edit it

```bash
export EDITOR=vim
uv run lion adr edit latest
```

`edit` opens a TOML template of the current document. Saving **overwrites and
adds** fields and stamps `edited_at`; LION never deletes a field, and there is
no delete command. The `id` and `created_at` must stay unchanged, and the
`[fields]` table must not be emptied — an invalid draft aborts loudly and leaves
the temporary file in place for you to reconcile.

## Check the store

```bash
uv run lion adr status --json   # one JSON object for scripts
uv run lion doctor              # topic 'adr' reports a broken or orphaned store
```

`adr status`, `adr list` and `adr show` never write and never collect a state.

## Related

- [Standalone ADRs](../reference/adrs.md) — the document schema and JSON shapes.
- [Records and ADRs](../reference/records-and-adrs.md) — the recording-bound ADRs.
- [ADR 0017](../decisions/0017-standalone-adrs.md) — the decision.
