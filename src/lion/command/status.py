"""The ``lion status`` command: compare without writing.

``status`` is the read-only *command*; *state* is the internal representation it
compares. It never writes to the history; see :mod:`lion.command` for the
``status``/``state`` naming.
"""

import json

import typer

from lion.command import fail
from lion.command.diagnostics import warn_incomplete
from lion.program.diff import diff_collectors, has_structural_change, render
from lion.program.storage import get_data_dir, load_latest
from lion.state.collector import collect_state
from lion.state.registry import COLLECTORS


def run(json_output: bool = False) -> None:
    """Compare the current state with the latest stored state without writing.

    Args:
        json_output: Emit a single JSON object instead of human text.
    """
    try:
        state = collect_state(COLLECTORS)
        latest = load_latest()
    except (OSError, ValueError) as exc:
        fail(exc)

    warn_incomplete(state)
    if latest is None:
        if json_output:
            typer.echo("null")
        else:
            typer.echo("No state stored. Run 'lion scan'.")
        if (get_data_dir() / "scans").is_dir():
            typer.echo(
                "Note: old states under 'scans/' are no longer read; run 'lion scan' to start a new history.",
                err=True,
            )
        return

    diff = diff_collectors(latest.collectors, state)
    if json_output:
        payload: dict[str, object] = {
            "changed": bool(diff),
            "structure_changed": has_structural_change(diff),
            "since": latest.confirmed_at,
            "differences": diff,
        }
        typer.echo(json.dumps(payload))
    elif not diff:
        typer.echo(f"Nothing has changed since the last scan at {latest.confirmed_at}.")
    else:
        if has_structural_change(diff):
            typer.echo("Structure changed.")
        typer.echo(render(diff))
