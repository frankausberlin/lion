"""Shared CLI error helpers for the lion commands."""

from __future__ import annotations

from pathlib import Path
from typing import NoReturn

import typer

from lion.output import display_text
from lion.shlib.errors import ShlibError
from lion.shlib.layout import ShlibLayout


def fail(error: Exception | str, home: Path | None = None, *, exit_code: int = 2) -> NoReturn:
    """Print a user-facing error and exit with ``exit_code``.

    Args:
        error: Error instance or already formatted message.
        home: Optional home directory used to abbreviate the message.
        exit_code: Process exit status.

    Raises:
        typer.Exit: Always, with ``exit_code``.
    """
    message = str(error)
    if home is not None:
        message = display_text(message, home)
    typer.echo(f"error: {message}", err=True)
    raise typer.Exit(exit_code)


def layout_or_exit(home: str | None, *, exit_code: int = 2) -> ShlibLayout:
    """Build the shlib layout or exit with an error.

    Args:
        home: ``--home`` option value or ``None`` for the current user's home.
        exit_code: Process exit status for a layout error.

    Returns:
        The selected layout.

    Raises:
        typer.Exit: If the layout cannot be built.
    """
    try:
        return ShlibLayout.from_option(home)
    except ShlibError as exc:
        fail(exc, exit_code=exit_code)
