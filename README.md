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


## shlib

`lion shlib` installs and removes a zsh configuration layout below `~/.shlib`.

```bash
lion shlib install --dry-run   # validate and show the plan
lion shlib install             # write ~/.shlib and replace ~/.zshrc
lion shlib uninstall           # flatten back into ~/.zshrc, keep a backup
```

Prerequisites are zsh and GNU `diffutils` (`diff`, `cmp`). `install` copies the
original `~/.zshrc` to `~/.shlib/shlibs/00-original-zshrc.sh`, writes a lock
file and replaces `~/.zshrc` with a bootstrap block. Files in
`~/.shlib/exports/` become environment variables; files in `~/.shlib/shlibs/`
named `NN-*.sh` are sourced in sorted order. `uninstall` combines everything
into a flat `~/.zshrc` and keeps the old tree as
`~/.shlib.uninstalled-<timestamp>`; it does not restore the previous file.
Owner-only exports (`chmod 600`, also `400`) are not inlined: `uninstall`
writes them to `secrets.sh` inside the backup (mode `0600`) and sources that
file from the flat `~/.zshrc`. The backup is then part of the shell
configuration; deleting it makes zsh report the missing file.
Use `--force` only when you deliberately accept a modified or unlocked
`~/.zshrc`. See [docs/shlib.md](docs/shlib.md) for the layout, ordering rules
and safety behavior.

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
