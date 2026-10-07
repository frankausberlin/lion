# Getting started

In this tutorial you install LION from the repository, capture the machine
state for the first time, and see how LION confirms, lists and compares states.
It assumes a Linux machine and a shell (the examples use Bash).

## Prerequisites

- Linux
- Python 3.12 or newer
- [uv](https://docs.astral.sh/uv/) and [direnv](https://direnv.net/) (or your
  own virtual environment handling)

## Install

Clone the repository and prepare the environment:

```bash
git clone <repo-url>
cd lion
direnv allow     # or: source .venv/bin/activate
uv sync
```

Every command is then run through `uv run lion <command>`.

## Capture the first state

`scan` runs the collectors and stores the result:

```bash
uv run lion scan
```

On an empty history this creates the first entry and prints `Zustand angelegt`
with the path of the new file. No initialization step is needed: `scan` creates
its data directory automatically.

## Compare with the latest state

`status` never writes. It collects the current state and compares it with the
latest stored one:

```bash
uv run lion status
```

Run `scan` a second time and then `status`: an unchanged machine is confirmed in
place (only `zuletzt_bestaetigt` is refreshed), so `status` reports that nothing
changed since the last scan.

## List and compare

```bash
uv run lion history          # every stored state, oldest first, with its reference
uv run lion diff previous    # the previous state against the latest
```

`history` prints a stable reference per entry; `diff` accepts that reference,
an alias such as `previous`, or a 1-based index. When the machine actually
changes (for example after installing a package), the next `scan` appends a new
distinct entry and `lion diff previous latest` shows exactly what changed.

## Inspect the result as JSON

Every read command has a `--json` form, which is safe for scripts and CI:

```bash
uv run lion scan --json
uv run lion status --json
uv run lion history --json
uv run lion diff previous --json
```

With no stored state, `status --json` prints `null` and exits successfully.

## Where to go next

- [Manage the shell configuration](../how-to/manage-shell-config.md) for the
  `shlib` workflow.
- [Compare stored states](../how-to/compare-states.md) for the full reference
  and history details.
- `lion <command> --help` for the canonical options and examples of a command.
