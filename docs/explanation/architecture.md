# Architecture

## Layering

LION separates the machine-state core from the CLI:

| Layer | Location | Responsibility |
| --- | --- | --- |
| Collector framework | `src/lion/state/` | Collect the machine state, the snapshot model and its comparison rules. |
| Programs | `src/lion/program/` | State history storage, the collector diff, and the shell library. |
| Commands | `src/lion/command/` | One module per command with a single `run` function. |
| CLI | `src/lion/cli.py` | Typer decorators and command wiring only. |
| Help texts | `src/lion/main.py` | The English epilogs and sub-app help. |

Each command is defined in `src/lion/command/<command>.py` (for example
`scan.py`, `status.py`, `history.py` and `diff.py`) and exposed through a thin
Typer decorator of the same name in `cli.py`, which only wires options and
delegates to the command's `run` function. The help texts live in `main.py` and
are imported by `cli.py`, so the two modules stay importable in either order
without a cycle.

The shlib business logic (installation, removal, status, backups, file writes
and syntax validation) lives in `src/lion/program/shlib.py`; `command/shlib.py`
only calls it, prints the result and maps errors to CLI failures.
`program/shlib.py` never imports Typer.

## Write boundary

Only `scan` (to the state history) and `shlib` (to the shell configuration)
write. `status`, `history` and `diff` are strictly read-only. See the
[write boundary](write-boundary.md).

## Terminology: `status` vs. `state`

- **status** is the read-only *command* that compares the current state with the
  last stored one.
- **state** always refers to the internal representation: the collected
  collector mapping and the persisted `Snapshot`.

The two words are not interchangeable. A command group with a `status`
subcommand runs it when invoked without arguments (`lion` is the same as
`lion status`, `lion shlib` the same as `lion shlib status`); a group without
one shows its help. This is detailed in
[CLI conventions](cli-conventions.md).
