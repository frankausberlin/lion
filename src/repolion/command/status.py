"""The ``lion status`` command: compare without writing.

``status`` is the read-only *command*; *state* is the internal representation it
compares. It never writes to the history; see :mod:`repolion.command` for the
``status``/``state`` naming.
"""

import json

import typer

from repolion.command import fail
from repolion.program.diff import diff_collectors, render
from repolion.program.storage import get_data_dir, load_latest
from repolion.state.collector import collect_state
from repolion.state.registry import COLLECTORS


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

    if latest is None:
        if json_output:
            typer.echo("null")
        else:
            typer.echo("Kein Zustand gespeichert. Führe 'lion scan' aus.")
        if (get_data_dir() / "scans").is_dir():
            typer.echo(
                "Hinweis: Alte Zustände unter 'scans/' werden nicht mehr gelesen; "
                "führe 'lion scan' für einen neuen Verlauf aus.",
                err=True,
            )
        return

    diff = diff_collectors(latest.collectors, state)
    if json_output:
        payload: dict[str, object] = {
            "geaendert": bool(diff),
            "seit": latest.zuletzt_bestaetigt,
            "unterschiede": diff,
        }
        typer.echo(json.dumps(payload))
    elif not diff:
        typer.echo(f"Seit dem letzten Scan am {latest.zuletzt_bestaetigt} hat sich nichts geändert.")
    else:
        typer.echo(render(diff))
