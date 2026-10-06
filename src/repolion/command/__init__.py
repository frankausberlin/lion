"""Implementations behind the LION CLI commands.

Each CLI command has a module here with the same name (``scan``, ``status``) and
a single ``run`` function; :mod:`repolion.cli` keeps only the Typer decorators
and delegates to those functions.

Naming: ``status`` is the read-only *command* that compares states, while
*state* always refers to the internal representation (the collected collector
mapping and the persisted ``Snapshot``). Keep the two words distinct.
"""

from typing import NoReturn

import typer


def fail(exc: OSError | ValueError) -> NoReturn:
    """Report a command failure on stderr and exit with code 1."""
    typer.echo(f"Error: {exc}", err=True)
    raise typer.Exit(code=1) from exc
