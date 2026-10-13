"""The ``lion scan`` command: collect the current state and persist it.

``scan`` writes: it creates a history entry, confirms the latest entry, or
appends a new distinct state, and it regenerates the derived OKF wiki. The
persisted data is the internal *state* (``Snapshot``); see :mod:`lion.command`
for the ``status``/``state`` naming.
"""

import json

import typer

from lion.command import fail
from lion.command.diagnostics import warn_incomplete
from lion.command.overview import render_index
from lion.program.diff import CollectorDiff, diff_collectors
from lion.program.storage import SaveOutcome, load_head_for_write, save_state
from lion.program.wiki import get_wiki_dir, render_pages, write_pages
from lion.state.collector import collect_state
from lion.state.registry import COLLECTORS

_EVENTS = {
    "created": "State created",
    "confirmed": "Timestamp refreshed",
    "appended": "New state saved",
}


def run(json_output: bool = False) -> None:
    """Collect the current state, persist it and regenerate the wiki.

    The stored snapshot is rendered to wiki pages after the save succeeded
    (the reference is only known then). A render or write failure is loud, the
    history entry stays valid and the next scan heals the wiki.

    Args:
        json_output: Emit a single JSON object instead of the human overview.
    """
    try:
        state = collect_state(COLLECTORS)
        baseline = load_head_for_write()
        outcome: SaveOutcome = save_state(state)
        _ = write_pages(render_pages(outcome.snapshot, outcome.path.stem))
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
        return
    typer.echo(f"{_EVENTS[outcome.event]}: {outcome.path}")
    baseline_collectors: dict[str, dict[str, object]] = baseline[1].collectors if baseline is not None else {}
    diff: CollectorDiff | None = None if outcome.event == "created" else diff_collectors(baseline_collectors, state)
    wiki_dir = get_wiki_dir()
    typer.echo(render_index(state, diff, wiki_dir))
    typer.echo(f"wiki: {wiki_dir / 'index.md'}")
