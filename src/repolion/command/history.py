"""The ``lion history`` command: list stored states without writing.

``history`` is read-only. It lists every validated entry from oldest to newest
and surfaces the stable compact reference used by ``lion diff``.
"""

import json

import typer

from repolion.command import fail, reference_json
from repolion.program.storage import Entry, list_entries


def _entry_payload(index: int, entry: Entry, latest: bool) -> dict[str, object]:
    """Return the JSON representation of one history entry."""
    payload = reference_json(entry)
    payload["index"] = index
    payload["aktuell"] = latest
    return payload


def run(json_output: bool = False) -> None:
    """List every stored state with its stable reference.

    Args:
        json_output: Emit a single JSON object instead of a table.
    """
    try:
        entries = list_entries()
    except (OSError, ValueError) as exc:
        fail(exc)

    if not entries:
        if json_output:
            typer.echo(json.dumps({"eintraege": []}))
        else:
            typer.echo("Kein Zustand gespeichert. Führe 'lion scan' aus.")
        return

    if json_output:
        payload = [_entry_payload(index, entry, index == len(entries)) for index, entry in enumerate(entries, 1)]
        typer.echo(json.dumps({"eintraege": payload}))
        return

    typer.echo(f"{'#':>3}  {'REF':<28}  ZULETZT BESTÄTIGT")
    for index, entry in enumerate(entries, start=1):
        hint = "  aktuell" if index == len(entries) else ""
        typer.echo(f"{index:>3}  {entry.ref:<28}  {entry.snapshot.zuletzt_bestaetigt}{hint}")
