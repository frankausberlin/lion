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
            "struktur_geaendert": has_structural_change(diff),
            "seit": latest.zuletzt_bestaetigt,
            "unterschiede": diff,
        }
        typer.echo(json.dumps(payload))
    elif not diff:
        typer.echo(f"Seit dem letzten Scan am {latest.zuletzt_bestaetigt} hat sich nichts geändert.")
    else:
        if has_structural_change(diff):
            typer.echo("Struktur geändert.")
        typer.echo(render(diff))
