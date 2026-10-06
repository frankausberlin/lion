"""The ``lion diff`` command: compare two stored states without writing.

``diff`` is read-only. It resolves two stored states through
:func:`repolion.program.storage.resolve` and reuses the shared collector diff,
so the RAM tolerance stays consistent with ``status`` and ``scan``.
"""

import json

import typer

from repolion.command import fail, reference_json
from repolion.program.diff import diff_collectors, render
from repolion.program.storage import HistoryError, list_entries, resolve


def run(reference: str, second: str | None = None, json_output: bool = False) -> None:
    """Compare two stored states without collecting or writing.

    Args:
        reference: The older state reference (see ``lion history``).
        second: The newer state reference; defaults to the latest entry.
        json_output: Emit a single JSON object instead of human text.
    """
    try:
        if len(list_entries()) < 2:
            raise HistoryError("Weniger als zwei Zustände gespeichert; führe 'lion scan' aus.")
        old = resolve(reference)
        new = resolve(second) if second is not None else resolve("latest")
    except (OSError, ValueError) as exc:
        fail(exc)

    diff = diff_collectors(old.snapshot.collectors, new.snapshot.collectors)
    if json_output:
        payload: dict[str, object] = {
            "von": reference_json(old),
            "bis": reference_json(new),
            "geaendert": bool(diff),
            "unterschiede": diff,
        }
        typer.echo(json.dumps(payload))
        return
    typer.echo(f"Vergleich {old.ref} → {new.ref}")
    rendered = render(diff)
    typer.echo(rendered if rendered else "Keine Unterschiede.")
