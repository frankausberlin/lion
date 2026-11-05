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
lion shlib status              # show installation, drift and cleanup state
lion shlib it --dry-run        # show appended content that would be moved
lion shlib it                  # move appended content into 99-unsorted.sh
lion shlib uninstall           # flatten back into ~/.zshrc, keep a backup
```

Prerequisites are zsh and GNU `diffutils` (`diff`, `cmp`). `install` copies the
original `~/.zshrc` to `~/.shlib/shlibs/00-original-zshrc.sh`, writes a lock
file and replaces `~/.zshrc` with a bootstrap block. Files in
`~/.shlib/exports/` become environment variables; files in `~/.shlib/shlibs/`
named `NN-*.sh` are sourced in sorted order. `uninstall` combines everything
into a flat `~/.zshrc` and keeps the old tree as
`~/.shlib.uninstalled-<timestamp>`; it does not restore the previous file.
`it` copies content appended after the managed block into
`~/.shlib/shlibs/99-unsorted.sh` and removes it from `~/.zshrc`, so the file
matches the lock file again. The captured content then runs only from
`99-unsorted.sh`.

`00-original-zshrc.sh` and `99-unsorted.sh` are temporary cleanup files, not
permanent parts of the tree. The target state is a shlib tree without either
file. `lion shlib status` stays yellow and exits `1` while one of them exists;
it only prints a hint. You decide how to resolve their content into sorted
`NN-*.sh` files and then delete them. `lion shlib` never deletes these files for
you.

Owner-only exports (`chmod 600`, also `400`) are not inlined: `uninstall`
writes them to `secrets.sh` inside the backup (mode `0600`) and sources that
file from the flat `~/.zshrc`. The backup is then part of the shell
configuration; deleting it makes zsh report the missing file.

## dash and watch

`~/.shlib/dash` is a register of user-maintained symlinks to configuration
files that should be watched for drift. Lion never creates or deletes these
links.

```bash
lion dash list                 # show entries, type, target and lock state
lion dash lock gitconfig       # copy the current content to locks/gitconfig.lock
lion dash unlock gitconfig     # delete only the lock copy
lion watch                     # print unified diffs for locked entries that changed
lion watch-dog on              # run `lion watch` at shell start via shlibs/
lion watch-dog status          # on (exit 0) or off (exit 1)
lion watch-dog off             # remove the managed watchdog file
```

Drift is a normal result: `lion watch` exits `0` and prints diffs; scan and
read errors exit `2`. A clean state and a missing dash tree are silent, so a
flattened `.zshrc` after `uninstall` does not complain on every shell start.
Lock copies use mode `0600` and can contain secrets; `lion watch` prints their
content locally. See [docs/dash.md](docs/dash.md) for details and limits.
Use `--force` only when you deliberately accept a modified or unlocked
`~/.zshrc`. See [docs/shlib.md](docs/shlib.md) for the layout, ordering rules
and safety behavior.

## Diagnosis and redaction

`lion doctor` checks the shlib tree, dash register, locks and suspected secrets
without printing file contents, export values or diff bodies.

```bash
lion doctor             # read-only report
lion doctor --dry-run   # show the scoped fix plan
lion doctor --fix       # create missing dirs/key and remove orphaned locks
```

Exit codes are `0` (clean), `1` (warning) and `2` (error or inconsistent
state). `--fix` never deletes or retargets a dash link, never repairs a
non-regular lock and never edits a suspected secret. Secret detection is
heuristic: confidence is a percentage for prioritization, not a calibrated
probability, and a low value means "not detected", never "clean".

Masks use `[REDACTED:<kind>:<fingerprint>]` with an HMAC-SHA256 fingerprint
from the per-installation key `~/.shlib/.redaction-key` (mode `0600`). Without
a key the token has no fingerprint. Fingerprints are not comparable after a
reinstall, on another machine or after key rotation.

See [docs/doctor.md](docs/doctor.md) for the checks and limits and
[docs/roadmap.md](docs/roadmap.md) for the next steps (MCP `diagnose` and the
masked dash content tool).

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
