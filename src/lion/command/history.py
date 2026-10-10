"""The ``lion history`` command: list stored states without writing.

``history`` is read-only. It lists every validated entry from oldest to newest
and surfaces the stable compact reference used by ``lion diff``.
"""

import json

import typer

from lion.command import fail, reference_json
from lion.program.storage import Entry, list_entries


def _entry_payload(index: int, entry: Entry, latest: bool) -> dict[str, object]:
    """Return the JSON representation of one history entry."""
    payload = reference_json(entry)
    payload["index"] = index
    payload["latest"] = latest
    return payload


def run(json_output: bool = False, limit: int | None = None) -> None:
    """List stored states with their stable reference.

    Args:
        json_output: Emit a single JSON object instead of a table.
        limit: Show only the newest ``limit`` entries; ``None`` shows all. The
            full history is always loaded and validated first, and the selection
            keeps the global 1-based indices, so a limit never renumbers entries
            or hides a damaged older entry.
    """
    try:
        entries = list_entries()
    except (OSError, ValueError) as exc:
        fail(exc)

    if not entries:
        if json_output:
            typer.echo(json.dumps({"entries": []}))
        else:
            typer.echo("No state stored. Run 'lion scan'.")
        return

    total = len(entries)
    shown = entries[-limit:] if limit is not None else entries
    offset = total - len(shown)

    if json_output:
        payload = [
            _entry_payload(offset + position, entry, offset + position == total)
            for position, entry in enumerate(shown, 1)
        ]
        typer.echo(json.dumps({"entries": payload}))
        return

    typer.echo(f"{'#':>3}  {'REF':<28}  CONFIRMED")
    for position, entry in enumerate(shown, start=1):
        index = offset + position
        hint = "  latest" if index == total else ""
        typer.echo(f"{index:>3}  {entry.ref:<28}  {entry.snapshot.confirmed_at}{hint}")
