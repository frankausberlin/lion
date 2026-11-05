"""Typer sub-app for ``lion dash``."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, NoReturn

import typer

from lion.dash.errors import DashError
from lion.dash.registry import LockResult, list_entries, lock_entry, unlock_entry
from lion.output import display_path, display_text
from lion.shlib.errors import ShlibError
from lion.shlib.layout import ShlibLayout

dash_app = typer.Typer(
    name="dash",
    help="List user-maintained dash links and manage their lock copies.",
    no_args_is_help=True,
)

HomeOption = Annotated[
    str | None,
    typer.Option("--home", help="Home directory to operate on. Defaults to the current user's home."),
]


@dash_app.command("list")
def list_command(home: HomeOption = None) -> None:
    """Show dash entries with lock state, type and target."""
    layout = _layout(home)
    try:
        entries = list_entries(layout)
    except DashError as exc:
        _fail(exc, layout.home)

    typer.echo(f"dash: {display_path(layout.dash, layout.home)}")
    if not entries:
        typer.echo("no entries")
        return
    for entry in entries:
        state = "locked" if entry.locked else "unlocked"
        line = f"{entry.name}: {state}, {entry.kind}"
        if entry.target is not None:
            line += f" -> {display_path(entry.target, layout.home)}"
        typer.echo(line)


@dash_app.command("lock")
def lock_command(
    name: Annotated[str, typer.Argument(help="Direct dash entry name to lock.")],
    home: HomeOption = None,
) -> None:
    """Copy the current target content to locks/<name>.lock."""
    layout = _layout(home)
    try:
        result = lock_entry(layout, name)
    except DashError as exc:
        _fail(exc, layout.home)
    _print_lock_result(result, layout)


@dash_app.command("unlock")
def unlock_command(
    name: Annotated[str, typer.Argument(help="Direct dash entry name to unlock.")],
    home: HomeOption = None,
) -> None:
    """Delete only locks/<name>.lock, never the dash link."""
    layout = _layout(home)
    try:
        removed = unlock_entry(layout, name)
    except DashError as exc:
        _fail(exc, layout.home)
    if removed:
        typer.echo(f"unlocked {name}")
    else:
        typer.echo(f"no lock for {name}")


def _print_lock_result(result: LockResult, layout: ShlibLayout) -> None:
    """Print the lock summary without revealing file contents."""
    prefix = "updated" if result.updated else "locked"
    typer.echo(
        f"{prefix} {result.name}: {display_path(result.target, layout.home)} "
        f"({result.size} bytes, sha256:{result.sha256[:12]})"
    )


def _layout(home: str | None) -> ShlibLayout:
    """Build the shlib layout or exit with an error."""
    try:
        return ShlibLayout.from_option(home)
    except ShlibError as exc:
        _fail(exc)


def _fail(error: Exception, home: Path | None = None) -> NoReturn:
    """Print a user-facing error and exit with status 2."""
    message = str(error)
    if home is not None:
        message = display_text(message, home)
    typer.echo(f"error: {message}", err=True)
    raise typer.Exit(2)
