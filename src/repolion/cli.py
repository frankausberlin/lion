"""Command-line interface for LION.

This module only wires Typer decorators and options; the command bodies live in
:mod:`repolion.command`, one module per command. ``status`` is the read-only
*command*; *state* is the internal representation it compares.
"""

from typing import Annotated

import typer

from repolion.command.scan import run as run_scan
from repolion.command.shlib import Action
from repolion.command.shlib import run as run_shlib
from repolion.command.status import run as run_status

app = typer.Typer(name="lion", help="Linux Operator Nerd.")


@app.command()
def scan(json_output: Annotated[bool, typer.Option("--json", help="Output the result as JSON.")] = False) -> None:
    """Collect the current state and persist it into the history."""
    run_scan(json_output)


@app.command()
def status(json_output: Annotated[bool, typer.Option("--json", help="Output the comparison as JSON.")] = False) -> None:
    """Compare the current state with the latest stored state without writing."""
    run_status(json_output)


@app.command()
def shlib(
    action: Annotated[Action, typer.Argument(help="Shlib operation.")] = Action.STATUS,
    json_output: Annotated[bool, typer.Option("--json", help="Output status as JSON.")] = False,
) -> None:
    """Inspect, install or uninstall the Zsh shell library."""
    run_shlib(action, json_output)
