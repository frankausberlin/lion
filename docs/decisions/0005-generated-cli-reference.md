# 0005 — The CLI reference is generated from the application

- **Status:** accepted
- **Date:** 2026-10-08

## Context

`docs/reference/cli.md` repeated command and option data that also lives in the
Typer application: every command, its arguments and its options appear both in
`lion <cmd> --help` and on the page. Hand-maintained, the page drifted from the
code and stated the same facts in a second place, which conflicts with the
single-home rule.

## Decision

`docs/reference/cli.md` is a generated artifact. `just docs`
(`scripts/gen_cli_docs.py`) runs Typer's documentation generator over the app,
prepends a "do not edit" header and writes the page. The source of truth is the
application in `src/lion/cli.py` together with the help texts in
`src/lion/main.py`; the page is never edited by hand.

## Consequences

- The page can no longer disagree with `--help`: both derive from the same app.
- Editing the page by hand is pointless — the next `just docs` overwrites it.
  This is intended, not a bug.
- Regeneration is manual and unguarded, so between two runs the committed page
  can lag the code. This is accepted for the current, small command surface.
- Planned commands do not exist in the app and cannot be generated; they stay a
  hand-written list in the README.
- A defect in the generator would silently drop content, so the page is only as
  complete as the generator.

## Alternatives

- A curated page that links to `--help` and repeats no options was rejected: it
  drifted and stated facts a second time.
- Generating only at release time was rejected: it still needs the generator and
  brings no benefit at the current project stage.
- Empty command stubs for planned features were rejected: they would be dead
  code, appear in `--help` and completion, and freeze undecided interfaces.
