"""The ``lion shell`` command: call the watch integration logic and print.

Inserting, removing and inspecting the integration live in
:mod:`lion.program.shell`; this module only performs the call, formats the
output and turns business errors into CLI errors.
"""

import json
import subprocess
from collections.abc import Mapping
from typing import cast

import typer

from lion.command import fail
from lion.program import shell as logic

__all__ = ["run"]


def run(action: str = "status", json_output: bool = False) -> None:
    """Run a watch-integration operation on the current user's home directory."""
    try:
        home = logic.get_home()
        if json_output and action != "status":
            raise ValueError("--json is supported for shell status only.")
        if action == "insert":
            _print_report(logic.insert(home))
        elif action == "remove":
            _print_report(logic.remove(home))
        else:
            _print_status(logic.status(home), json_output=json_output)
    except (OSError, ValueError) as exc:
        fail(exc)
    except subprocess.TimeoutExpired:
        fail(ValueError("Zsh syntax validation timed out; no shell configuration changed."))


def _print_report(report: logic.ShellReport) -> None:
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
