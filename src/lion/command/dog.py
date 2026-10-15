"""The ``lion watch dog`` command: call the guard logic and print.

Enabling, disabling and inspecting the guard live in :mod:`lion.program.dog`;
this module only performs the call, formats the output and turns business
errors into CLI errors. ``status`` is read-only; ``on``/``off`` write.
"""

from lion.command import fail
from lion.command.output import print_report, print_status
from lion.program import dog as logic
from lion.program import shell as shell_logic

__all__ = ["run"]


def run(action: str = "status", json_output: bool = False) -> None:
    """Run a guard operation (``on``/``off``/``status``) for the current user."""
    try:
        home = shell_logic.get_home()
        if action == "on":
            print_report(logic.enable(home))
        elif action == "off":
            print_report(logic.disable(home))
        else:
            print_status(logic.dog_status(home).to_dict(), json_output=json_output)
    except (OSError, ValueError) as exc:
        fail(exc)
