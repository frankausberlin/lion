# lion

## Getting Started

```bash
uv sync
just check
```

## Development

```bash
just test        # run tests
just lint        # lint + type check
just fix         # auto-fix lint issues
just clean       # remove caches and generated artifacts
just rate-design run             # design metrics → .refactor/
just rate-design save <name>     # save an immutable baseline
just rate-design compare <name>  # compare the current run with a baseline
just refactor                    # prepare a refactoring session
just merge       # fast-forward main to a branch from any checkout (no push)
just check       # full quality gate (unit/smoke tests only)
just test-e2e    # Docker E2E suite locally (hint + exit 0 until tests exist)
just test-agent  # provider suite manually (hint + exit 0 until tests exist)
```

`just check` runs unit and smoke tests only. The E2E suite is not part of the
gate: run it locally with `just test-e2e`; CI runs it automatically once test
files exist. Agent tests are manual-only and never run in CI.

## MCP Server

```bash
just run         # run the hello command
just mcp-dev     # open the pinned MCP Inspector and run `lion mcp` (requires Node.js and npx)
```

`just mcp-dev` is a development helper; `just check` does not require Node.js.
It starts the installed project entrypoint, so relative imports inside the
package keep working. The `mcp` subcommand runs the server without writing to
stdout before startup. The smoke tests call the server through the in-memory
`Client` from the SDK.


## Documentation

```bash
just docs         # regenerate docs/reference/cli.md
just docs-check   # verify the generated CLI reference is current
```

## Release

```bash
just bump patch   # 0.1.0 → 0.1.1
git push origin main --tags
```

See [AGENTS.md](AGENTS.md) for AI agent guidelines.
