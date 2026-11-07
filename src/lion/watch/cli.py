"""Typer commands for ``lion watch`` and ``lion watch-dog``."""

from __future__ import annotations

from typing import Annotated

import typer

from lion.cli_common import fail, layout_or_exit
from lion.output import display_path
from lion.watch.dog import (
    ON,
    disable,
    enable,
    status,
    watchdog_path,
)
from lion.watch.errors import WatchDogError, WatchError
from lion.watch.findings import collect_findings

HomeOption = Annotated[
    str | None,
    typer.Option("--home", help="Home directory to operate on. Defaults to the current user's home."),
]

watch_dog_app = typer.Typer(
    name="watch-dog",
    help="Manage the shlib file that runs `lion watch` at shell start.",
    no_args_is_help=True,
)


def watch_command(home: HomeOption = None) -> None:
    """Compare locked dash entries with their lock copies and print diffs."""
    layout = layout_or_exit(home)
    try:
        findings = collect_findings(layout)
    except WatchError as exc:
        fail(exc, layout.home)

    exit_code = 0
    for finding in findings:
        for line in finding.lines:
            typer.echo(line)
        if finding.is_error:
            exit_code = 2
    raise typer.Exit(exit_code)


@watch_dog_app.command("on")
def on_command(home: HomeOption = None) -> None:
    """Write the managed watchdog file."""
    layout = layout_or_exit(home)
    try:
        path = enable(layout)
    except WatchDogError as exc:
        fail(exc, layout.home)
    typer.echo(f"wrote {display_path(path, layout.home)}")


@watch_dog_app.command("off")
def off_command(home: HomeOption = None) -> None:
    """Remove the complete managed watchdog file."""
    layout = layout_or_exit(home)
    try:
        removed = disable(layout)
    except WatchDogError as exc:
        fail(exc, layout.home)
    path = watchdog_path(layout)
    if removed:
        typer.echo(f"removed {display_path(path, layout.home)}")
    else:
        typer.echo(f"no watchdog file at {display_path(path, layout.home)}")


@watch_dog_app.command("status")
def status_command(home: HomeOption = None) -> None:
    """Report whether the managed watchdog file exists."""
    layout = layout_or_exit(home)
    try:
        state = status(layout)
    except WatchDogError as exc:
        fail(exc, layout.home)
    typer.echo(f"watchdog: {state}")
    raise typer.Exit(0 if state == ON else 1)
