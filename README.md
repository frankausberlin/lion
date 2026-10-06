# lion

**LION — Linux Operator Nerd** collects the machine state through small
collectors, keeps a history of distinct states, and compares the current state
with the latest stored one for people, scripts, and agents.
The project and CLI are named `lion`; the repository and Python distribution/import
package remain `repolion`.

## Getting started

Requires Linux and Python 3.12 or newer.

```bash
uv sync
uv run lion scan             # collect the current state and save it
uv run lion status           # compare the current state with the latest saved one
uv run lion --help
```

No initialization step is needed. `scan` creates its data directory automatically.

## CLI structure

Each CLI command is defined in `src/repolion/command/<command>.py` (for example
`scan.py` and `status.py`) and exposed through a thin Typer decorator of the same
name in `src/repolion/cli.py`, which only wires options and delegates to the
command's `run` function.

Terminology: **status** is the read-only *command* that compares the current
state with the last stored one, while **state** always refers to the internal
representation (the collected collector mapping and the persisted `Snapshot`).
The two words are not interchangeable.

## Zsh shell library

```bash
lion shlib                  # same as shlib status; never writes
lion shlib status --json    # installation, lock, script/export names and warnings
lion shlib install
lion shlib uninstall
```

Shlib manages `~/.zshrc` for the current user (no sudo). Zsh must be installed
for syntax validation during installation and removal. A custom `ZDOTDIR`
outside the home directory is rejected.

Installation backs up the existing `.zshrc` to `.zshrc.before-shlib` (numbered
if already present), preserves its permissions, and moves its configuration
into `~/.shlib/shlibs/00-original-zshrc.sh`. It creates `exports/`, `shlibs/`
and the optional `dash/` directory below `~/.shlib`. The new `.zshrc` loads
exports first, then scripts whose names start with two digits in lexical order.
Keep export filenames valid shell identifiers and their permissions at `600`.
Export values retain whitespace except trailing newlines, matching shell command
substitution. `exports/.gitignore` excludes the secret files from normal Git adds.
Do not force-add secrets to Git.

`.zshrc.lock` is a reference copy: shell startup displays differences, but does
not prevent edits. `status` reports differences without displaying file contents
or secret values. After reviewing intentional changes, refresh the reference
with `cp ~/.zshrc ~/.zshrc.lock`.

No extra Powerlevel10k or direnv hooks are inserted: existing setup stays in the
original script. When splitting that script, keep instant-prompt initialization
early and move the direnv hook to the end of your shell initialization. `dash/`
is available for your own configuration symlinks; Lion does not populate it.

**Uninstall preserves the current configuration, not the pre-install state:**

- Exports are consolidated into `~/.zshrc.exports` with mode `600` and literal,
  shell-quoted values. `.zshrc` sources this file where the loader previously ran.
- Script contents (also through symlinks) are inserted in load order, each with a
  short filename comment. `.zshrc` keeps its original permissions.
- Lines outside the SHLIB markers, including installer additions, remain in place.
- Both generated files pass `zsh -f -n` before publication. Syntax diagnostics
  are suppressed to avoid echoing secrets. Files are replaced atomically one at a
  time; if rc replacement fails, the newly created exports file is removed.
- A private `.zshrc.before-shlib-uninstall` backup is retained, as are `~/.shlib`,
  `.zshrc.lock` and the original backups. Existing backups are never overwritten.

Review file-relative script logic and top-level `return` statements: flattening
can change their behavior. To deliberately return to the pre-Shlib configuration,
inspect and restore the original `.zshrc.before-shlib` backup yourself; later
configuration changes will then no longer be active.

Already installed/uninstalled operations are no-ops. The documented manual
SHLIB block is supported, while unknown changes inside that block require manual
reconciliation. Existing `.zshrc.exports`, installation remnants, ambiguous
markers, broken script symlinks and symlinked output files cause a clear error
instead of an overwrite. Reinstallation after removal therefore requires
reconciling retained files first. Run lifecycle operations one at a time and do
not edit shell configuration during an operation. Installation/removal never
loads or executes the user's scripts.

## Collectors

Each collector lives with its dataclass in `src/repolion/state/<collector>.py`
and returns a section with a `status` (`ok`, `unavailable`, or `error`), an
`error` message, and its data. A failing collector never aborts the whole
capture: an exception is stored as an `error` section.

Shared types (`CollectorStatus`, `CollectorResult`, `Collector`, and
`collect_state`) live in `src/repolion/state/collector.py`, and
`src/repolion/state/tools.py` provides the shared external-tool runner. The
ordered registry is `src/repolion/state/registry.py`, the persisted `Snapshot`
model is `src/repolion/state/model.py`, `src/repolion/program/storage.py` owns
the history, and `src/repolion/program/diff.py` builds and renders the
comparison.

Version 1 ships three collectors:

- **host** — hostname, distribution/version, kernel, architecture.
- **hardware** — CPU model, logical CPU count, total memory, CUDA version, and
  NVIDIA GPUs. Missing `/proc` files use `Unknown`/`0`; a missing `nvidia-smi`
  yields an empty GPU list. No volatile fields (clocks, temperatures, uptime).
- **packages** — installed Dpkg packages (including held packages), keyed by
  `name:architecture` when architecture metadata is present, plus sorted
  `manual`, `auto`, and `held` selections from `apt-mark`. Missing Dpkg or
  `apt-mark` marks the collector `unavailable`.

## Comparison model

`scan` writes and `status` only reads. A state has two timestamps: `erstscan`
(first observation) and `zuletzt_bestaetigt` (last unchanged confirmation).

- No stored state → a new entry is created (`created`).
- Identical collector data → the latest entry's `zuletzt_bestaetigt` is
  refreshed in place (`confirmed`); no new file is written.
- Any difference → a new entry is appended (`appended`). The previous entry is
  kept, so the history records every distinct state.

Two states are compared using a canonical serialization of the `collectors`
section only; the timestamps do not participate, but each collector's `status`
and `error` do. Returning states are not reactivated.

`status` groups the difference per collector with `+` (added), `-` (removed),
and `~` (changed) lines. If nothing changed, it says so; if no state exists, it
tells you to run `lion scan`.

## JSON output

```bash
uv run lion scan --json
uv run lion status --json
```

`scan --json` outputs a single JSON object on stdout:

```json
{
  "ereignis": "created",
  "pfad": "/home/user/.local/share/lion/history/2026-10-05T20-00-00.123456Z.toml",
  "zustand": {
    "schema_version": 1,
    "erstscan": "2026-10-05T20:00:00.123456+00:00",
    "zuletzt_bestaetigt": "2026-10-05T20:00:00.123456+00:00",
    "collectors": {"host": {"status": "ok", "error": "", "hostname": "workstation"}}
  }
}
```

`status --json` outputs the comparison:

```json
{
  "geaendert": true,
  "seit": "2026-10-05T20:00:00.123456+00:00",
  "unterschiede": {"host": {"changed": {"hostname": {"old": "a", "new": "b"}}}}
}
```

With no stored state, `status --json` outputs `null` and exits successfully.
Operational or invalid-state errors go to stderr with exit code `1` and no JSON
on stdout. Because there is no interactive prompt, non-interactive CI and script
use is safe.

## Storage and compatibility

The state history is TOML files under `$XDG_DATA_HOME/lion/history`, defaulting
to `~/.local/share/lion/history`. New records use UTC timestamps and are named
after their `erstscan`.

New entries are published with a hard link so an existing entry is never
overwritten; only refreshing `zuletzt_bestaetigt` rewrites the latest file, and
it does so atomically via `os.replace`. This requires a filesystem supporting
hard links.

Concurrent scans serialize the complete read/compare/write operation using an
exclusive process lock in `$XDG_DATA_HOME/lion/.history.lock` (under the default
data directory when unset). The lock file remains in place; its lock is released
when the writer closes it or exits. `status` remains read-only and does not lock.

Every `.toml` entry is strictly validated (`schema_version = 1`, UTC offsets on
both timestamps, a valid `status` per collector). Any unreadable or invalid
entry stops `status` with its path in the error message instead of silently
being skipped.

The former `scans/` directory is no longer read and is left untouched; there is
no migration. LION does not silently skip damaged files or fall back to older
records. Existing history with unqualified package names remains readable; the
first scan with architecture-qualified names records this representation change.
The `manual`, `auto`, and `held` lists retain the names returned by `apt-mark`.

## Development

```bash
just test       # tests and coverage; minimum 90%
just lint       # lint, formatting and type check
just fix        # auto-fix lint issues
just check      # full quality gate
```

Tests cover fixture-based collector readings, the history comparison model
(create/confirm/append, dedup on status change, invalid entries), the diff and
rendering, and text/JSON CLI behavior. Collector tools (`nvidia-smi`,
`apt-mark`, Dpkg) are exercised through fixtures and mocks so the suite also
passes on machines without them.

### End-to-end lifecycles

```bash
just test-e2e  # requires Docker with a running daemon and just
```

This builds an Ubuntu 24.04 image with the locked development dependencies and
runs the installed `lion` executable as a subprocess. A locally built `.deb`
(with no dependencies or maintainer scripts) is installed, scanned and purged.
The test checks the complete package diff, create/confirm/append behavior,
read-only status, persisted TOML, and all three historical states, including the
return to the original state. No collectors or package-manager calls are mocked.

The same container also tests Shlib installation and removal with a real Zsh,
including literal secret values, script order, linked scripts, installer additions,
syntax-failure handling and resulting permissions. All shell files live in a
disposable home directory.

Image construction needs network access; the test container runs with networking
disabled, no host mounts and no privileged mode. Only its own package database is
modified. The runner copies command logs, JUnit results and history into a unique
`e2e-artifacts.*` directory before removing the container, even on test failure.
These directories are gitignored and may be deleted after inspection.

Ordinary `pytest`, `just test` and `just check` exclude the `e2e` marker. The test
also skips unless the runner's opt-in flag, Docker marker and root user are
present. Do not run the package lifecycle directly on a workstation. GitHub CI
runs it in a separate job and uploads diagnostics on failure. This covers package
changes; it does not validate physical GPU discovery.

## Release

```bash
just bump patch
git push origin main --tags
```

See [AGENTS.md](AGENTS.md) for AI agent guidelines.
