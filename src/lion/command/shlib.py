"""The ``lion shlib`` command: call the shlib logic, print, and map errors.

Installation, removal, status, backups, file writes and syntax validation live in
:mod:`lion.program.shlib`; this module only performs the call, formats the
output, and turns business errors into CLI errors.
"""

import json
import subprocess

import typer

from lion.command import fail
from lion.program import shlib as logic
from lion.program.shlib import Action

__all__ = ["Action", "run"]


def run(action: Action = Action.STATUS, json_output: bool = False) -> None:
    """Run a shlib operation on the current user's home directory."""
    try:
        home = logic.get_home()
        if json_output and action != Action.STATUS:
            raise ValueError("--json is supported for shlib status only.")
        if action == Action.INSTALL:
            for message in logic.install(home):
                typer.echo(message)
        elif action == Action.UNINSTALL:
            for message in logic.uninstall(home):
                typer.echo(message)
        else:
            result = logic.status(home)
            if json_output:
                typer.echo(json.dumps(result))
            else:
                for key, value in result.items():
                    typer.echo(f"{key}: {value}")
    except (OSError, ValueError) as exc:
        fail(exc)
    except subprocess.TimeoutExpired:
        fail(ValueError("Zsh syntax validation timed out; no shell configuration changed."))
