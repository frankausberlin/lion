"""Command-line interface for LION.

This module only wires Typer decorators and options; the command bodies live in
:mod:`repolion.command`, one module per command, and the help texts live in
:mod:`repolion.main`. ``status`` is the read-only *command*; *state* is the
internal representation it compares.

Help rule: a command group that has a ``status`` subcommand runs it when invoked
without arguments (``lion`` becomes ``lion status``, ``lion shlib`` becomes
``lion shlib status``). Groups without a ``status`` subcommand keep the default
and show their help instead.
"""

from typing import Annotated

import typer

from repolion.command.diff import run as run_diff
from repolion.command.history import run as run_history
from repolion.command.scan import run as run_scan
from repolion.command.shlib import Action
from repolion.command.shlib import run as run_shlib
from repolion.command.status import run as run_status
from repolion.main import (
    DIFF_EPILOG,
    HISTORY_EPILOG,
    ROOT_EPILOG,
    SCAN_EPILOG,
    SHLIB_EPILOG,
    SHLIB_HELP,
    STATUS_EPILOG,
)

app = typer.Typer(
    name="lion",
    help="Linux Operator Nerd: collect, store and compare the machine state.",
    epilog=ROOT_EPILOG,
    invoke_without_command=True,
    no_args_is_help=False,
)


@app.callback()
def root(ctx: typer.Context) -> None:
    """Run ``lion status`` when no other command is given."""
    if ctx.invoked_subcommand is None:
        run_status(False)


@app.command(epilog=SCAN_EPILOG)
def scan(json_output: Annotated[bool, typer.Option("--json", help="Output the result as JSON.")] = False) -> None:
    """Collect the current state and store it in the history."""
    run_scan(json_output)


@app.command(epilog=STATUS_EPILOG)
def status(json_output: Annotated[bool, typer.Option("--json", help="Output the comparison as JSON.")] = False) -> None:
    """Compare the current state with the latest stored one (read-only)."""
    run_status(json_output)


@app.command(epilog=HISTORY_EPILOG)
def history(json_output: Annotated[bool, typer.Option("--json", help="Output the entries as JSON.")] = False) -> None:
    """List stored states with their stable references."""
    run_history(json_output)


@app.command(epilog=DIFF_EPILOG)
def diff(
    reference: Annotated[str, typer.Argument(help="Older state reference (see 'lion history').")],
    second: Annotated[str | None, typer.Argument(help="Newer state reference; defaults to the latest.")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Output the comparison as JSON.")] = False,
) -> None:
    """Compare two stored states without collecting or writing."""
    run_diff(reference, second, json_output)


shlib_app = typer.Typer(
    name="shlib",
    help=SHLIB_HELP,
    short_help="Manage the Zsh shell library (~/.shlib).",
    epilog=SHLIB_EPILOG,
    invoke_without_command=True,
    no_args_is_help=False,
)


@shlib_app.callback()
def shlib_root(ctx: typer.Context) -> None:
    """Run ``lion shlib status`` when no operation is given."""
    if ctx.invoked_subcommand is None:
        run_shlib(Action.STATUS, False)


@shlib_app.command("status")
def shlib_status(json_output: Annotated[bool, typer.Option("--json", help="Output status as JSON.")] = False) -> None:
    """Show whether the shell library is installed and report its state."""
    run_shlib(Action.STATUS, json_output)


@shlib_app.command("install")
def shlib_install() -> None:
    """Install the shell library and back up the current ~/.zshrc."""
    run_shlib(Action.INSTALL, False)


@shlib_app.command("uninstall")
def shlib_uninstall() -> None:
    """Flatten scripts into ~/.zshrc and exports into ~/.zshrc.exports."""
    run_shlib(Action.UNINSTALL, False)


app.add_typer(shlib_app)
