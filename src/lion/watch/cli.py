"""Typer commands for ``lion watch`` and ``lion watch-dog``."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, NoReturn

import typer

from lion.output import display_path, display_text
from lion.shlib.errors import ShlibError
from lion.shlib.layout import ShlibLayout
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
    layout = _layout(home)
    try:
        findings = collect_findings(layout)
    except WatchError as exc:
        _fail(exc, layout.home)

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
    layout = _layout(home)
    try:
        path = enable(layout)
    except WatchDogError as exc:
        _fail(exc, layout.home)
    typer.echo(f"wrote {display_path(path, layout.home)}")


@watch_dog_app.command("off")
def off_command(home: HomeOption = None) -> None:
    """Remove the complete managed watchdog file."""
    layout = _layout(home)
    try:
        removed = disable(layout)
    except WatchDogError as exc:
        _fail(exc, layout.home)
    path = watchdog_path(layout)
    if removed:
        typer.echo(f"removed {display_path(path, layout.home)}")
    else:
        typer.echo(f"no watchdog file at {display_path(path, layout.home)}")


@watch_dog_app.command("status")
def status_command(home: HomeOption = None) -> None:
    """Report whether the managed watchdog file exists."""
    layout = _layout(home)
    try:
        state = status(layout)
    except WatchDogError as exc:
        _fail(exc, layout.home)
    typer.echo(f"watchdog: {state}")
    raise typer.Exit(0 if state == ON else 1)


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
