# CLI conventions

Two mechanics are shared by every command group.

## Status default

A command group that has a `status` subcommand runs it when invoked without
arguments:

- `lion` is the same as `lion status`.
- `lion shlib` is the same as `lion shlib status`.

A group without a `status` subcommand shows its help instead, so no operation
runs by accident.

This is only about *which* command runs. It does not change what that command
does: `status` is read-only. The earlier appendix claim that `status` becomes
the new last state was wrong — writing happens in `scan` (see the
[comparison model](comparison-model.md)).

## Help behavior

| Invocation | Result |
| --- | --- |
| `lion --help` | Help for LION. |
| `lion <command> --help` | Help for that command. |
| `lion <command>` | Help for the command if it has no `status` subcommand. |

`lion --version` is eager: it prints the version from the single source in
`src/lion/__init__.py` and exits before any collector runs. Every command has a
detailed `--help` with its own examples, which is the canonical command depth.
