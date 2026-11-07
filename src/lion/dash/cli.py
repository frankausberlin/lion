"""Typer sub-app for ``lion dash``."""

from __future__ import annotations

from typing import Annotated

import typer

from lion.cli_common import fail, layout_or_exit
from lion.dash.errors import DashError
from lion.dash.registry import LockResult, list_entries, lock_entry, lockable_entry_names, unlock_entry
from lion.output import display_path, short_digest
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
    layout = layout_or_exit(home)
    try:
        entries = list_entries(layout)
    except DashError as exc:
        fail(exc, layout.home)

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


def _complete_lock_name(ctx: typer.Context, incomplete: str) -> list[str]:
    """Suggest direct dash entry names that ``lion dash lock`` accepts."""
    home = ctx.params.get("home")
    try:
        layout = ShlibLayout.from_option(home if isinstance(home, str) else None)
        names = lockable_entry_names(layout)
    except (DashError, ShlibError):
        return []
    return [name for name in names if name.startswith(incomplete)]


@dash_app.command("lock")
def lock_command(
    name: Annotated[
        str,
        typer.Argument(help="Direct dash entry name to lock.", autocompletion=_complete_lock_name),
    ],
    home: HomeOption = None,
) -> None:
    """Copy the current target content to locks/<name>.lock."""
    layout = layout_or_exit(home)
    try:
        result = lock_entry(layout, name)
    except DashError as exc:
        fail(exc, layout.home)
    _print_lock_result(result, layout)


@dash_app.command("unlock")
def unlock_command(
    name: Annotated[str, typer.Argument(help="Direct dash entry name to unlock.")],
    home: HomeOption = None,
) -> None:
    """Delete only locks/<name>.lock, never the dash link."""
    layout = layout_or_exit(home)
    try:
        removed = unlock_entry(layout, name)
    except DashError as exc:
        fail(exc, layout.home)
    if removed:
        typer.echo(f"unlocked {name}")
    else:
        typer.echo(f"no lock for {name}")


def _print_lock_result(result: LockResult, layout: ShlibLayout) -> None:
    """Print the lock summary without revealing file contents."""
    prefix = "updated" if result.updated else "locked"
    typer.echo(
        f"{prefix} {result.name}: {display_path(result.target, layout.home)} "
        f"({result.size} bytes, sha256:{short_digest(result.sha256)})"
    )
