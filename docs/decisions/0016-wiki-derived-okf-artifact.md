# 0016 — The wiki is a derived OKF v0.2 artifact written by scan

- **Status:** accepted
- **Date:** 2026-10-13

## Context

LION stores every state in the history and compares states on read. The machine
knowledge should also exist as a readable artifact: a wiki a human can browse,
and later phases (MCP `serve`, the Web UI) can consume as data. The earlier
roadmap planned a `wiki` command; the maintainer's planning session replaced it
with "wiki on scan", so the artifact can never drift from the newest stored
state.

The requirements asked for an OKF-compliant artifact (OKF partially, v0.2), a
flat structure, resolved relative links, identity colors and a generated index,
with no history, recording or diff pages in v1.

## Decision

Every `scan` regenerates a derived directory artifact under
`$XDG_DATA_HOME/lion/wiki`: `index.md` plus one page per registered collector
(`host.md` … `dash.md`) in registry order. `scan` is the only writer; `status`,
`history`, `diff`, `records`, `classify` and `doctor` never write it. The
`doctor` check `wiki.freshness` only reads the newest history reference and the
index frontmatter.

The format is OKF v0.2 as a documented subset:

- Frontmatter with a fixed key order (`okf_version`, `snapshot_ref`,
  [`collector`, `status`,] `color`, `title`) and one-line values only.
- `color` carries an identity token from the public 9-token palette (token to
  hex lives in the [reference page](../reference/wiki.md)); pages never embed
  hex values, so a palette change cannot touch rendered content.
- `snapshot_ref` is the history file name without `.toml` and stands in for a
  content hash: it identifies the state a page was rendered from.
- Relative links from `index.md` to the collector pages; no subdirectories.
- The body is plain Markdown: `# <Title>` and `## Facts` with one
  `- **<field>**: <value>` line per snapshot field.

Rendering is deterministic: `render_pages(snapshot, ref)` is a pure function of
the validated snapshot and the reference, so the same snapshot yields
byte-identical files. No generation timestamp is embedded; only snapshot-owned
values such as `created_at`/`confirmed_at` appear. `write_pages` writes each
page via a temporary file, `fsync` and `os.replace` — collector pages first,
`index.md` last — and v1 cleans up no old files.

**Save before render.** The planning session fixed "render in full in memory,
then publish the snapshot, then write the wiki". `save_state` determines
`created_at`, `confirmed_at`, the reference and a possible `~NNNN` collision
suffix only while publishing under the history lock, so the frontmatter
`snapshot_ref` cannot be known before the save. The implemented order is
therefore `collect_state → save_state → render_pages → write_pages`. The
guarantees stay: rendering is pure (a render error is a programming error), the
wiki is written only after a successful save, render and write errors are loud
with a path and exit `1`, the history entry stays valid, and the next `scan`
heals. A crash between two page writes can leave a mixed state; the next scan
heals it.

The requirement terms map as follows: "flat" is the directory without
subdirectories; `changed_color` arrives with the Web UI (the CLI already
computes the per-collector change for its index); `suggested_*` fields stay
reserved for the v2/Web-UI mapping — v1 pages carry no suggestion fields.

## Consequences

- The wiki cannot drift from the newest stored state: the writer that stores the
  state regenerates it, and `lion doctor` reports staleness.
- The CLI text output gains the shared collector index and the `wiki:` path
  (see [Wiki (OKF v0.2)](../reference/wiki.md)); the JSON contracts of
  `scan --json` and `status --json` stay unchanged.
- Hand-editing is pointless (overwritten on the next scan) and merging is
  meaningless (derived); the reference page documents this.
- A wiki write failure after the history save leaves a valid snapshot with a
  stale or missing wiki; `doctor` warns and the next scan heals — there is no
  rollback.

## Alternatives

- **A `wiki` command.** Rejected: it would need its own freshness discipline and
  could drift from the history.
- **Persisted page content or a content hash.** Rejected: everything is
  derived; the reference already identifies the state.
- **Hex colors in the pages.** Rejected: a palette change would rewrite every
  page.
- **Rendering inside the history transaction.** Rejected: the save owns the
  reference; rendering afterwards keeps the commit path small and keeps the
  wiki healable.
- **Full OKF with per-directory index files and cross-record links.** Rejected
  for v1: the flat page set is enough for the CLI and the planned consumers.
