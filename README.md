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

## Release

```bash
just bump patch
git push origin main --tags
```

See [AGENTS.md](AGENTS.md) for AI agent guidelines.
