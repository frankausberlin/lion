# Getting started

In this tutorial you install LION from the repository, capture the machine
state for the first time, and see how LION confirms, lists and compares states.
It assumes a Linux machine and a shell (the examples use Bash).

## Prerequisites

- Linux
- Python 3.12 or newer
- [uv](https://docs.astral.sh/uv/)

## Install

Clone the repository and prepare the environment:

```bash
git clone https://github.com/frankausberlin/lion.git
cd lion
uv sync
```

Every command is then run through `uv run lion <command>`. No manual virtual
environment activation or direnv configuration is required.

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
```

After two unchanged scans there is still only **one** history entry: confirming
it does not create a second state. Do not run `diff previous` yet; it requires
at least two entries.

`history` prints a stable reference per entry; `diff` accepts that reference,
an alias such as `previous`, or a 1-based index. When the machine actually
changes (for example after installing a package), the next `scan` appends a new
distinct entry. Once `history` lists at least two entries, compare them:

```bash
uv run lion diff previous latest
```

This shows exactly what changed. No package installation is needed just to
complete this introductory walkthrough.

## Inspect the result as JSON

Every read command has a `--json` form, which is safe for scripts and CI:

```bash
uv run lion scan --json
uv run lion status --json
uv run lion history --json
uv run lion diff previous --json  # only after at least two history entries exist
```

With no stored state, `status --json` prints `null` and exits successfully.

## Where to go next

- [Manage the shell configuration](../how-to/manage-shell-config.md) for the
  `shlib` workflow.
- [Compare stored states](../how-to/compare-states.md) for the full reference
  and history details.
- `lion <command> --help` for the canonical options and examples of a command.
