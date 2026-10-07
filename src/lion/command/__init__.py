"""Implementations behind the LION CLI commands.

Each CLI command has a module here with the same name (``scan``, ``status``,
``history``, ``diff``, ``shlib``) and a single ``run`` function;
:mod:`lion.cli` keeps only the Typer decorators and delegates to those
functions. Only ``scan`` and ``shlib`` write; ``status``, ``history`` and
``diff`` are read-only.

Naming: ``status`` is the read-only *command* that compares states, while
*state* always refers to the internal representation (the collected collector
mapping and the persisted ``Snapshot``). Keep the two words distinct.
"""

from typing import NoReturn

import typer

from lion.program.storage import Entry


def fail(exc: OSError | ValueError) -> NoReturn:
    """Report a command failure on stderr and exit with code 1."""
    typer.echo(f"Error: {exc}", err=True)
    raise typer.Exit(code=1) from exc


def reference_json(entry: Entry) -> dict[str, object]:
    """Return the shared JSON reference for one stored entry.

    ``history`` and ``diff`` both describe an entry with this shape; keep it in
    one place so the two commands cannot drift apart.
    """
    return {
        "ref": entry.ref,
        "erstscan": entry.snapshot.erstscan,
        "zuletzt_bestaetigt": entry.snapshot.zuletzt_bestaetigt,
        "pfad": str(entry.path),
    }
