"""The ``lion diff`` command: compare two stored states without writing.

``diff`` is read-only. It resolves two stored states through
:func:`lion.program.storage.resolve` and reuses the shared collector diff,
so the RAM tolerance stays consistent with ``status`` and ``scan``.
"""

import json

import typer

from lion.command import fail, reference_json
from lion.program.diff import diff_collectors, has_structural_change, render
from lion.program.storage import HistoryError, list_entries, resolve


def run(reference: str, second: str | None = None, json_output: bool = False) -> None:
    """Compare two stored states without collecting or writing.

    Args:
        reference: The older state reference (see ``lion history``).
        second: The newer state reference; defaults to the latest entry.
        json_output: Emit a single JSON object instead of human text.
    """
    try:
        if len(list_entries()) < 2:
            raise HistoryError("Fewer than two states stored; run 'lion scan'.")
        old = resolve(reference)
        new = resolve(second) if second is not None else resolve("latest")
    except (OSError, ValueError) as exc:
        fail(exc)

    diff = diff_collectors(old.snapshot.collectors, new.snapshot.collectors)
    structural = has_structural_change(diff)
    if json_output:
        payload: dict[str, object] = {
            "from": reference_json(old),
            "to": reference_json(new),
            "changed": bool(diff),
            "structure_changed": structural,
            "differences": diff,
        }
        typer.echo(json.dumps(payload))
        return
    typer.echo(f"Comparing {old.ref} → {new.ref}")
    if structural:
        typer.echo("Structure changed.")
    rendered = render(diff)
    typer.echo(rendered if rendered else "No differences.")
