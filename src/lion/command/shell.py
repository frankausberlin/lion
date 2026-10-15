"""The ``lion shell`` command: call the watch integration logic and print.

Inserting, removing and inspecting the integration live in
:mod:`lion.program.shell`; this module only performs the call, formats the
output and turns business errors into CLI errors.
"""

import subprocess

from lion.command import fail
from lion.command.output import print_report, print_status
from lion.program import shell as logic

__all__ = ["run"]


def run(action: str = "status", json_output: bool = False) -> None:
    """Run a watch-integration operation on the current user's home directory."""
    try:
        home = logic.get_home()
        if json_output and action != "status":
            raise ValueError("--json is supported for shell status only.")
        if action == "insert":
            print_report(logic.insert(home))
        elif action == "remove":
            print_report(logic.remove(home))
        else:
            print_status(logic.status(home), json_output=json_output)
    except (OSError, ValueError) as exc:
        fail(exc)
    except subprocess.TimeoutExpired:
        fail(ValueError("Zsh syntax validation timed out; no shell configuration changed."))
