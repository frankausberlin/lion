# Wiki (OKF v0.2)

Every `lion scan` regenerates a derived wiki about the system under
`$XDG_DATA_HOME/lion/wiki`, defaulting to `~/.local/share/lion/wiki`. The
stored data stays the history; the wiki is a pure render of the snapshot that
was just stored, so it always describes the newest state after a scan. It is
never edited by hand and never merged — the next scan overwrites it. See
[ADR 0016](../decisions/0016-wiki-derived-okf-artifact.md) for the rationale.

## Files

The directory holds `index.md` plus one page per registered collector in
registry order: `host.md`, `hardware.md`, `network.md`, `packages.md`,
`services.md`, `containers.md`, `tools.md`, `dash.md`. All files are UTF-8 with
LF line endings and a trailing newline; there are no subdirectories. A scan
adds or replaces these files and cleans nothing up in v1.

## Frontmatter

Each page starts with frontmatter in a fixed key order; every value fits on one
line:

```markdown
---
okf_version: "0.2"
snapshot_ref: 2026-10-13T12-00-00.123456Z
collector: host
status: ok
color: blue
title: Host
---
```

`index.md` omits `collector` and `status`:

```markdown
---
okf_version: "0.2"
snapshot_ref: 2026-10-13T12-00-00.123456Z
color: slate
title: LION system wiki
---
```

`okf_version` is the OKF subset version. `snapshot_ref` is the history file
name without `.toml` (including a possible `~NNNN` collision suffix) and
identifies the state the page was rendered from; `lion doctor` compares it with
the newest history reference. `status` is the collector's status (`ok`,
`unavailable` or `error`); a missing section renders as `unavailable`.

## Palette

`color` carries an identity token, never a hex value; the token-to-hex mapping
is this table and only relevant to wiki keys and future UI accents (the CLI
semantic colors are separate):

| Token | Hex | Identity |
| --- | --- | --- |
| slate | `#64748B` | index |
| blue | `#3B82F6` | host |
| violet | `#8B5CF6` | hardware |
| teal | `#14B8A6` | network |
| amber | `#F59E0B` | packages |
| green | `#22C55E` | services |
| orange | `#F97316` | containers |
| cyan | `#06B6D4` | tools |
| rose | `#F43F5E` | dash |

## Body

`index.md` names the snapshot's `created_at` and `confirmed_at` as facts and
lists the collector pages under `## Collectors` as relative links in registry
order:

```markdown
# LION system wiki

- **created_at**: 2026-10-13T12:00:00+00:00
- **confirmed_at**: 2026-10-13T12:00:00+00:00

## Collectors

- [host](host.md)
- [hardware](hardware.md)
```

Every collector page carries `# <Title>` and `## Facts` with one line per field
of its snapshot section, in section order:

- A scalar renders as `- **<field>**: <value>`. Booleans are `true`/`false`,
  `null` is `null`, numbers are plain, and strings collapse every whitespace
  run to one space (an empty string is `""`), so a value can never break the
  line or the frontmatter.
- A non-empty list renders as `- **<field>**:` plus one sub-bullet per entry:
  a table entry as `key=value, …` (nested lists and mappings become compact
  single-line JSON), a plain entry as its value. An empty list is `(none)`.
- A mapping renders as compact single-line JSON.
- `status` is omitted (it lives in the frontmatter) and `error` appears only
  when it is non-empty.

## Determinism, healing and the read-only boundary

`render_pages(snapshot, ref)` is a pure function: the same snapshot always
produces byte-identical pages, and no generation timestamp is embedded. The
writer creates the directory on demand and replaces each page via a temporary
file, `fsync` and `os.replace`, collector pages first and `index.md` last; old
files are not removed. A render or write failure exits `1` and names the
affected path — the history entry stays valid and the next scan heals the wiki.
A crash between two pages can leave a mixed state until the next scan.

`scan` is the only writer. `status` shows the shared collector index and, when
`index.md` exists, points at it read-only; it can be minimally older than the
fresh collection. `lion doctor` reports `wiki.freshness` (`ok`/`warn`/`skip`)
from the history reference and the index frontmatter and never writes. The
`scan --json` and `status --json` contracts are unchanged by the wiki.

## See also

- [`lion scan`](cli.md#lion-scan) regenerates the wiki; [`lion status`](cli.md#lion-status)
  shows the shared index and the `wiki:` path.
- [Storage](storage.md) — the history the pages reference.
- [Collectors](collectors.md) — the fields that appear as facts.
