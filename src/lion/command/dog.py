"""The ``lion watch dog`` command: call the guard logic and print.

Enabling, disabling and inspecting the guard live in :mod:`lion.program.dog`;
this module only performs the call, formats the output and turns business
errors into CLI errors. ``status`` is read-only; ``on``/``off`` write.
"""

import json
from collections.abc import Mapping
from typing import cast

import typer

from lion.command import fail
from lion.program import dog as logic
from lion.program import shell as shell_logic

__all__ = ["run"]


def run(action: str = "status", json_output: bool = False) -> None:
    """Run a guard operation (``on``/``off``/``status``) for the current user."""
    try:
        home = shell_logic.get_home()
        if json_output and action != "status":
            raise ValueError("--json is supported for watch dog status only.")
        if action == "on":
            _print_report(logic.enable(home))
        elif action == "off":
            _print_report(logic.disable(home))
        else:
            _print_status(logic.dog_status(home).to_dict(), json_output=json_output)
    except (OSError, ValueError) as exc:
        fail(exc)


def _print_report(report: logic.DogReport) -> None:
    """Print the report messages to stdout and its warnings to stderr."""
    for message in report.messages:
        typer.echo(message)
    for warning in report.warnings:
        typer.echo(f"Warning: {warning}", err=True)


def _print_status(result: Mapping[str, object], *, json_output: bool) -> None:
    """Print the status mapping as JSON or as ``key: value`` text lines."""
    if json_output:
        typer.echo(json.dumps(result))
        return
    for key, value in result.items():
        if key == "warnings":
            for warning in cast("list[object]", value):
                typer.echo(f"warning: {warning}")
        else:
            typer.echo(f"{key}: {value}")
