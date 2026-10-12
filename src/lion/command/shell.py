"""The ``lion shell`` command: call the watch integration logic and print.

Inserting, removing and inspecting the integration live in
:mod:`lion.program.shell`; this module only performs the call, formats the
output and turns business errors into CLI errors.
"""

import json
import subprocess
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
            report = logic.insert(home)
            for message in report.messages:
                typer.echo(message)
            for warning in report.warnings:
                typer.echo(f"Warning: {warning}", err=True)
        elif action == "remove":
            report = logic.remove(home)
            for message in report.messages:
                typer.echo(message)
            for warning in report.warnings:
                typer.echo(f"Warning: {warning}", err=True)
        else:
            result = logic.status(home)
            if json_output:
                typer.echo(json.dumps(result))
            else:
                for key, value in result.items():
                    if key == "warnings":
                        for warning in cast("list[object]", value):
                            typer.echo(f"warning: {warning}")
                    else:
                        typer.echo(f"{key}: {value}")
    except (OSError, ValueError) as exc:
        fail(exc)
    except subprocess.TimeoutExpired:
        fail(ValueError("Zsh syntax validation timed out; no shell configuration changed."))
