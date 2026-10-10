"""The ``lion scan`` command: collect the current state and persist it.

``scan`` writes: it creates a history entry, confirms the latest entry, or
appends a new distinct state. The persisted data is the internal *state*
(``Snapshot``); see :mod:`lion.command` for the ``status``/``state`` naming.
"""

import json

import typer

from lion.command import fail
from lion.command.diagnostics import warn_incomplete
from lion.program.storage import SaveOutcome, save_state
from lion.state.collector import collect_state
from lion.state.registry import COLLECTORS

_EVENTS = {
    "created": "State created",
    "confirmed": "Timestamp refreshed",
    "appended": "New state saved",
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

    warn_incomplete(state)
    if json_output:
        payload: dict[str, object] = {
            "event": outcome.event,
            "path": str(outcome.path),
            "state": outcome.snapshot.to_toml_dict(),
        }
        typer.echo(json.dumps(payload))
    else:
        typer.echo(f"{_EVENTS[outcome.event]}: {outcome.path}")
