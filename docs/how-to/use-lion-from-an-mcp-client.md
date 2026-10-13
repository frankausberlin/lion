# Use LION from an MCP client

`lion serve` runs an MCP server on stdio so an agent harness can read the
stored ledger — states, recordings, ADRs and classifications — and create
standalone ADRs. This guide wires a client, verifies the connection and covers
the common pitfalls; the full tool catalog lives in
[MCP server (`lion serve`)](../reference/mcp.md).

## 1. Check that the server starts

```bash
lion serve --help    # the command exists
lion doctor          # topic 'serve' reports serve.definition ok
```

`lion serve` blocks and speaks JSON-RPC on stdout; run it only from a client,
not interactively.

## 2. Register the client

Point the client at the `lion` executable. For Claude Desktop
(`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "lion": { "command": "lion", "args": ["serve"] }
  }
}
```

For Claude Code:

```bash
claude mcp add lion -- lion serve
```

If the client does not see `lion` on its `PATH`, use the absolute path to the
console script (`/path/to/lion/.venv/bin/lion`) or `uv`:

```json
{
  "mcpServers": {
    "lion": {
      "command": "uv",
      "args": ["run", "--project", "/path/to/lion", "lion", "serve"]
    }
  }
}
```

## 3. Give the server its data directory

The server resolves `$XDG_DATA_HOME` (and `$HOME`) when it starts. Most clients
inherit a small environment allowlist, so a **custom** `XDG_DATA_HOME` must be
passed explicitly:

```json
{
  "mcpServers": {
    "lion": {
      "command": "lion",
      "args": ["serve"],
      "env": { "XDG_DATA_HOME": "/home/me/.local/share" }
    }
  }
}
```

If the agent reports an empty history although `lion history` shows entries,
this is the first thing to check.

## 4. Verify from the client

Ask the agent to call `lion_history`. A healthy answer carries the same entries
as `lion history --json`; a missing ledger answers with a loud error naming
`run 'lion scan'` — expected until the first scan. Useful first calls:

- `lion_history` / `lion_snapshot` — the stored states.
- `lion_recordings` / `lion_recording REF` — the shell recordings.
- `lion_adr_list` — both ADR origins in one view.
- `lion_search QUERY` — the whole ledger, newest first.

Then read `lion://wiki/index` as a resource for the derived system wiki.

## 5. Decide about redaction

Recorded text and ADR values are masked best-effort before they reach the
agent (`NAME=***`, `Authorization: ***`). If you deliberately want the raw
text in the agent channel, set the switch in the client environment:

```json
"env": { "LION_SERVE_REDACT": "0" }
```

Masking is best-effort, not a security boundary — keep real secrets out of
recordings regardless.

## Troubleshooting

| Symptom | Cause to check |
| --- | --- |
| Server exits immediately | A broken prompt default: `lion doctor` reports `serve.definition` with the file path. |
| Empty history in the client, full history in the shell | The client's minimal environment lacks `XDG_DATA_HOME`; pass it in `env`. |
| Client cannot start the command | `lion` is not on the client's `PATH`; use the absolute script path or `uv`. |
| `Unknown collector` on a wiki resource | Collector names are `host`, `hardware`, `network`, `packages`, `services`, `containers`, `tools`, `dash`. |
| Wiki resource fails with `run 'lion scan'` | The wiki is derived: run `lion scan` on the machine first. |
| Agent sees `***` where the shell shows text | Redaction is on; see step 5. | |

## Related

- [MCP server (`lion serve`)](../reference/mcp.md) — tools, resources, prompts
  and payload shapes.
- [Recordings](../reference/recordings.md) — what a recording contains.
- [Standalone ADRs](../reference/adrs.md) — the write path behind
  `lion_adr_add`/`lion_adr_update`.
- [ADR 0018](../decisions/0018-mcp-serve-read-mirror.md) — the decision.
