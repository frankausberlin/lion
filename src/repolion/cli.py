"""Command-line interface for LION."""

import json
from typing import Annotated, NoReturn

import typer

from repolion.diff import diff_collectors, render
from repolion.paths import get_data_dir
from repolion.state.collector import collect_state
from repolion.state.registry import COLLECTORS
from repolion.storage import load_latest, save_state

app = typer.Typer(name="lion", help="Linux Operator Nerd.")

_EVENTS = {
    "created": "Zustand angelegt",
    "confirmed": "Zeitstempel aktualisiert",
    "appended": "Neuer Zustand gespeichert",
}


def _fail(exc: OSError | ValueError) -> NoReturn:
    typer.echo(f"Error: {exc}", err=True)
    raise typer.Exit(code=1) from exc


@app.command()
def scan(json_output: Annotated[bool, typer.Option("--json", help="Output the result as JSON.")] = False) -> None:
    """Collect the current state and persist it into the history."""
    try:
        state = collect_state(COLLECTORS)
        outcome = save_state(state)
    except (OSError, ValueError) as exc:
        _fail(exc)

    if json_output:
        payload: dict[str, object] = {
            "ereignis": outcome.event,
            "pfad": str(outcome.path),
            "zustand": outcome.snapshot.to_toml_dict(),
        }
        typer.echo(json.dumps(payload))
    else:
        typer.echo(f"{_EVENTS[outcome.event]}: {outcome.path}")


@app.command()
def status(json_output: Annotated[bool, typer.Option("--json", help="Output the comparison as JSON.")] = False) -> None:
    """Compare the current state with the latest stored state without writing."""
    try:
        state = collect_state(COLLECTORS)
        latest = load_latest()
    except (OSError, ValueError) as exc:
        _fail(exc)

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
