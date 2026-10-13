# MCP server (`lion serve`)

`lion serve` runs an [MCP](https://modelcontextprotocol.io/) server on **stdio**
for agent harnesses (editors, agent CLIs, desktop clients). It is a second view
of the same ledger the CLI reads — never a second implementation: every tool is
built on the same program layer and mirrors an existing `--json` contract.

- **No tool ever collects a state.** The ledger changes only through `lion
  scan`, `lion watch` and the two ADR write tools below.
- **Read-only except two tools.** `lion_adr_add` and `lion_adr_update` write
  only into `$XDG_DATA_HOME/lion/adrs/`; everything else reads.
- **Best-effort redaction.** Text taken from recordings and ADR fields is
  masked on the way out; set `LION_SERVE_REDACT=0` to disable it (see
  [Redaction](#redaction)).
- **No network, no config file, no HTTP transport** in this version.

The server start reads and validates the [prompt defaults](#prompts) once; a
broken file is a loud error and `lion serve` exits non-zero. `lion doctor`
reports the same build under the `serve` topic (`serve.definition`).

## Client configuration

The server is a local stdio command; a client starts `lion serve` as a
subprocess. Common shapes:

```jsonc
// Claude Desktop: claude_desktop_config.json
{
  "mcpServers": {
    "lion": { "command": "lion", "args": ["serve"] }
  }
}
```

```bash
# Claude Code
claude mcp add lion -- lion serve
```

If `lion` is not on the client's `PATH`, use the absolute path
(`/path/to/lion/.venv/bin/lion`) or `uv`:

```jsonc
{
  "mcpServers": {
    "lion": {
      "command": "uv",
      "args": ["run", "--project", "/path/to/lion", "lion", "serve"]
    }
  }
}
```

> **Environment.** The server resolves `$XDG_DATA_HOME` (and `$HOME`) when it
> starts. Some clients inherit only a small environment allowlist; if you use a
> custom `XDG_DATA_HOME`, pass it explicitly in the client's `env` block:
> `"env": { "XDG_DATA_HOME": "/home/me/.local/share" }`.

stdout carries pure JSON-RPC; every diagnostic goes to stderr. The server never
prints a banner and never touches the network.

### Interactive inspection

For a quick manual look at the tools, resources and prompts, the official
[MCP Inspector](https://github.com/modelcontextprotocol/inspector) can drive
the server without any repo dependency:

```bash
npx @modelcontextprotocol/inspector lion serve
```

It is a manual tool, not part of the test suite: automated coverage lives in
`tests/test_serve.py` (in-memory contracts), `tests/test_serve_stdio.py` (raw
JSON-RPC and SDK clients) and the opt-in agentic suite (`just test-agent`, see
[tests/agentic/README.md](../../tests/agentic/README.md)).

## Tools

All payloads mirror the corresponding CLI `--json` contract. Errors are
returned as MCP tool errors carrying the program layer's message (including
paths and hints such as `run 'lion scan'`), never as silent empty results.

| Tool | Mirrors | Arguments |
| --- | --- | --- |
| `lion_history` | `lion history --json` | `limit` (optional, newest N) |
| `lion_snapshot` | stored entry document | `reference` (default `latest`) |
| `lion_diff` | `lion diff --json` | `reference`, `second` (optional, default latest) |
| `lion_recordings` | `lion watch list --json` | `limit` (optional, newest N) |
| `lion_recording` | `lion watch show REF --json` | `reference` |
| `lion_classify` | `lion classify --json` | `reference` (optional: active, then newest) |
| `lion_adr_list` | `lion adr list` + `lion records adrs` | `origin`, `status` (optional) |
| `lion_adr_show` | `lion adr show REF --json` / `lion records adrs REF --json` | `reference` |
| `lion_adr_add` | `lion adr add` | `fields` (mapping), `recording` (optional) |
| `lion_adr_update` | `lion adr edit` semantics | `reference`, `fields`, `recording` (optional) |
| `lion_search` | — (new, over both sources) | `query`, `limit` (default 20) |

### History and snapshots

`lion_history` returns `{"entries": [...]}` with `ref`, `created_at`,
`confirmed_at`, `path`, the global 1-based `index` and the `latest` flag,
oldest first. `limit` trims the already validated list to the newest N entries
without renumbering (`limit` must be positive).

`lion_snapshot` returns `{"ref": ..., "snapshot": {...}}` with the complete
validated snapshot (`schema_version`, `created_at`, `confirmed_at`,
`collectors`; see [Data structures](data-structures.md)). Without a stored
state the error names the hint `run 'lion scan'`.

`lion_diff` returns the `lion diff --json` shape: `from`, `to`, `changed`,
`structure_changed` and `differences`. Fewer than two stored states is a loud
error.

### Recordings and classification

`lion_recordings` lists the recordings oldest first (`limit` trims to the
newest N). `lion_recording` returns the metadata plus every ordered event of
one recording (`latest`/`active` and unique prefixes resolve; see
[Recordings](recordings.md)).

`lion_classify` derives the classification from the raw events on every call
(nothing is persisted); without a recording the result carries
`{"ref": null, "ruleset": null, "blocks": []}` instead of failing. The
[classifier reference](classifiers.md) owns the block and match shapes.

### ADRs of both origins

`lion_adr_list` merges the standalone ADRs and the recording-bound ADRs into
one uniform, oldest-first view:

```json
{
  "adrs": [
    {
      "ref": "2026-10-13T12-00-00.000000Z",
      "origin": "recording",
      "status": "accepted",
      "complete": true,
      "edited": false,
      "title": "Antidote for the shell",
      "at": "2026-10-13T12:00:00+00:00"
    }
  ]
}
```

`origin` is `standalone` or `recording`; `status` filters on the shared status
values (`proposed`, `accepted`, `superseded`). `lion_adr_show` resolves ADR ids
first (exact, then unique prefix, then `latest`), then recording references —
the same order `uses`/`supersedes` use. A standalone ADR keeps the `lion adr
show --json` shape plus `origin`; a recording ADR keeps the `lion records adrs
REF --json` shape plus `origin`.

`lion_adr_add` and `lion_adr_update` are the only writing tools. They use the
same store and rules as the CLI ([Standalone ADRs](adrs.md)): field names,
field values and a given `recording` reference must be non-empty, at least one
field is required, `update` overwrites and adds but never removes a field, and
every update stamps `edited_at`. The full document is validated before it is
published, so an invalid write is a loud error and never creates or replaces a
document. They return `{"ref", "path"}` (plus `edited_at` for updates).

### Search

`lion_search` searches every ADR field value of both origins and the recording
notes, command and comment texts. Matching is a case-insensitive substring per
line; hits are ordered newest first and capped by `limit`:

```json
{
  "query": "firefox",
  "hits": [
    {
      "kind": "adr",
      "ref": "adr-2026-10-13T12-00-00.000000Z",
      "origin": "standalone",
      "where": "field:decision",
      "snippet": "Firefox stays the default browser"
    }
  ]
}
```

`where` is `field:<name>` for ADRs, `note`, `command` or `comment` for
recordings; recording hits carry no `origin`. Snippets are redacted and capped;
the mask runs over the complete value before the snippet is derived, so a
secret spanning several lines keeps its masking context.

## Resources

| URI | MIME type | Content |
| --- | --- | --- |
| `lion://wiki/index` | `text/markdown` | The derived wiki index written by the latest `scan`. |
| `lion://wiki/{collector}` | `text/markdown` | One collector page of the derived wiki. |
| `lion://collectors` | `application/json` | `{"collectors": [...]}` in canonical registry order. |
| `lion://classifier/rules` | `application/json` | The merged rule set: `hash`, `version`, `groups` and every rule with `group`/`id`/`match`/`scope`/`emit`/`guard`/`source`. |

An unknown collector is a resource error listing the valid names. A missing
wiki page is a resource error with the hint `run 'lion scan' to regenerate it`;
`doctor`'s `wiki.freshness` remains the place that judges staleness.

## Prompts

Exactly three defaults ship with the package (`src/lion/program/prompt_defaults/`),
read once at server start:

| Prompt | Arguments |
| --- | --- |
| `review_latest_changes` | `reference` (optional), `focus` (optional) |
| `document_decision` | `subject` (required), `recording` (optional) |
| `recall_knowledge` | `query` (required) |

Each default is one Markdown file with a fixed TOML frontmatter (`name`,
`description`, `[[arguments]]` with `name`/`description`/`required`) and a body
whose `{placeholders}` name declared arguments. There is no hot reload and no
user override folder in this version; a missing, extra, unreadable or invalid
file fails the server start loudly with the file path.

## Redaction

Recorded text and ADR values can carry secrets. The server masks them
best-effort before they leave the process, mirroring the zsh watch mask
([Recordings](recordings.md)):

- Trigger keywords (case-insensitive): `token`, `secret`, `password`, `passwd`,
  `api_key`, `apikey`, `credential`, `authorization`.
- `NAME=VALUE` assignments whose name carries a keyword (bare, single- or
  double-quoted values) become `NAME=***`.
- A mapped field (an ADR field) whose **name** carries a keyword is masked as a
  whole (`***`), the mapping form of the same rule; a keyword-free name still
  masks assignments inside its value.
- `Authorization:` header values become `Authorization: ***`.

The filter applies to the recording details, the recording list notes, the
classification blocks and matches, the ADR fields, the ADR list titles, the
anchors and issues, and the search snippets. Stored bytes are never modified;
snapshots, wiki pages and the classifier rule set are not filtered (they carry
collector facts, not shell input). Set `LION_SERVE_REDACT=0` in the server
environment to return the raw text.

**Masking is best-effort, not a security boundary** — exactly like the zsh-side
mask. It is a display filter for the agent channel.

## Invariants

- `lion_history`, `lion_snapshot`, `lion_diff`, `lion_recordings`,
  `lion_recording`, `lion_classify`, `lion_adr_list`, `lion_adr_show`,
  `lion_search`, every resource and every prompt are read-only.
- Only `lion_adr_add` and `lion_adr_update` write, only below
  `$XDG_DATA_HOME/lion/adrs/`, with the same collision, mode (`600`) and
  no-overwrite rules as the CLI.
- No tool collects a state, starts a recording, edits a recording overlay or
  executes a recommended script.
- The tool payloads equal the CLI `--json` payloads for the same ledger; the
  test suite pins this equivalence.
