"""The ``lion classify`` command: read-only classification of recordings.

The command resolves a recording like the other read commands, loads the merged
rule set and renders the derived blocks; it never collects, never writes and
never executes anything. ``preview`` and ``matches_text`` are shared with the
terse ``lion watch R``/``R-`` output.
"""

import json

import typer

from lion.classify import engine, rules
from lion.classify.model import Block, Match
from lion.classify.parser import PARSER_VERSION
from lion.classify.text import sanitize_controls
from lion.command import fail
from lion.program import recording
from lion.program.recording import Recording

__all__ = ["block_line", "matches_text", "preview", "run"]

#: Number of characters of a block shown by ``preview``.
PREVIEW_LENGTH = 40


def preview(text: str) -> str:
    """Return the terminal-safe single-line preview of a block.

    The text is whitespace-collapsed, stripped of control characters and cut
    to the first 40 characters, so recorded text cannot move the cursor.
    """
    return sanitize_controls(" ".join(text.split()))[:PREVIEW_LENGTH]


def matches_text(matches: tuple[Match, ...]) -> str:
    """Return the terse one-line rendering of a block's matches."""
    if not matches:
        return "-"
    parts: list[str] = []
    for match in matches:
        fields = ",".join(f"{name}={sanitize_controls(value)}" for name, value in match.fields.items())
        parts.append(f"{match.group}:{fields}")
    return "; ".join(parts)


def _resolve(reference: str | None) -> Recording | None:
    if reference is not None:
        return recording.resolve_recording(reference)
    active = recording.active_recording()
    if active is not None:
        return active
    records = recording.list_recordings()
    return records[-1] if records else None


def _match_detail(match: Match) -> str:
    fields = ", ".join(f"{name}={sanitize_controls(value)}" for name, value in match.fields.items())
    return f"{match.group}: {fields}"


def _run(reference: str | None, json_output: bool) -> None:
    record = _resolve(reference)
    if record is None:
        if json_output:
            payload: dict[str, object] = {
                "ref": None,
                "ruleset": None,
                "parser_version": PARSER_VERSION,
                "blocks": [],
            }
            typer.echo(json.dumps(payload))
        else:
            typer.echo("No recording stored. Run 'lion watch start'.")
        return
    ruleset = rules.load_ruleset()
    classification = engine.classify_recording(record, ruleset)
    if json_output:
        typer.echo(json.dumps(classification.to_dict()))
        return
    provenance = classification.ruleset
    typer.echo(f"Recording {classification.ref} (ruleset {provenance.version}, hash {provenance.hash[:6]}\u2026)")
    if not classification.blocks:
        typer.echo("  No blocks.")
        return
    for block in classification.blocks:
        typer.echo(f"  #{block.index}  $ {preview(block.text)}")
        if not block.matches:
            typer.echo("        -")
            continue
        for match in block.matches:
            typer.echo(f"        {_match_detail(match)}")


def run(reference: str | None = None, json_output: bool = False) -> None:
    """Run ``lion classify``.

    Args:
        reference: Recording reference; defaults to the active recording, then
            the newest one. Without any recording the command reports that
            instead of failing.
        json_output: Emit a single JSON object instead of human text.
    """
    try:
        _run(reference, json_output)
    except (OSError, ValueError) as exc:
        fail(exc)


def block_line(block: Block) -> str:
    """Return the terse ``R<n>:`` line shared by ``lion watch R`` and ``R-``."""
    return f"R{block.index}: {preview(block.text)}  {matches_text(block.matches)}"
