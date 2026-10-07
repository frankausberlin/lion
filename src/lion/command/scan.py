"""The ``lion scan`` command: collect the current state and persist it.

``scan`` writes: it creates a history entry, confirms the latest entry, or
appends a new distinct state. The persisted data is the internal *state*
(``Snapshot``); see :mod:`lion.command` for the ``status``/``state`` naming.
"""

import json

import typer

from lion.command import fail
from lion.program.storage import SaveOutcome, save_state
from lion.state.collector import collect_state
from lion.state.registry import COLLECTORS

_EVENTS = {
    "created": "Zustand angelegt",
    "confirmed": "Zeitstempel aktualisiert",
    "appended": "Neuer Zustand gespeichert",
}


def run(json_output: bool = False) -> None:
    """Collect the current state and persist it into the history.

    Args:
        json_output: Emit a single JSON object instead of a human message.
    """
    try:
        state = collect_state(COLLECTORS)
        outcome: SaveOutcome = save_state(state)
    except (OSError, ValueError) as exc:
        fail(exc)

    if json_output:
        payload: dict[str, object] = {
            "ereignis": outcome.event,
            "pfad": str(outcome.path),
            "zustand": outcome.snapshot.to_toml_dict(),
        }
        typer.echo(json.dumps(payload))
    else:
        typer.echo(f"{_EVENTS[outcome.event]}: {outcome.path}")
