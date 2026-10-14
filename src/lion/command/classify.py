"""The ``lion classify`` command: read-only classification of recordings.

The command resolves a recording like the other read commands, loads the merged
rule set plus the optional policy file, and renders the derived blocks; it
never collects, never writes and never executes anything. With loaded policies
(ADR 0019) every command segment is evaluated against the declared work rules
and shown with its own matches; block-scope matches stay block-level and are
never policy evidence. ``preview`` and ``matches_text`` are shared with the
terse ``lion watch R``/``R-`` output, which stays policy-free.
"""

import json
from collections.abc import Mapping

import typer

from lion.classify import engine, policies, rules
from lion.classify.model import Block, Command, Match, PolicyResult
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


def _command_text(command: Command) -> str:
    """Return the terminal-safe single-line text of one command."""
    return sanitize_controls(" ".join(command.text.split()))


def _policy_line(result: PolicyResult, texts: Mapping[str, str]) -> str:
    """Return one ``policy <id> <status>: <text>`` line, terminal-safe."""
    text = sanitize_controls(" ".join(texts.get(result.policy_id, "").split()))
    return f"policy {sanitize_controls(result.policy_id)} {result.status}: {text}"


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
    configured = policies.load_policies()
    classification = engine.classify_recording(record, ruleset, policies=configured)
    if json_output:
        typer.echo(json.dumps(classification.to_dict()))
        return
    provenance = classification.ruleset
    typer.echo(f"Recording {classification.ref} (ruleset {provenance.version}, hash {provenance.hash[:6]}\u2026)")
    if not classification.blocks:
        typer.echo("  No blocks.")
        return
    scopes = {rule.id: rule.scope for rule in ruleset.rules}
    policy_texts = {policy.id: policy.text for policy in classification.policies}
    for block in classification.blocks:
        typer.echo(f"  #{block.index}  $ {preview(block.text)}")
        if not classification.policies:
            if not block.matches:
                typer.echo("        -")
                continue
            for match in block.matches:
                typer.echo(f"        {_match_detail(match)}")
            continue
        if not block.commands:
            typer.echo("        -")
            continue
        for match in block.matches:
            if scopes.get(match.rule_id) == rules.SCOPE_BLOCK:
                typer.echo(f"        {_match_detail(match)}")
        for command in block.commands:
            typer.echo(f"    command {command.index}: {_command_text(command)}")
            if command.matches:
                for match in command.matches:
                    typer.echo(f"      {_match_detail(match)}")
            else:
                typer.echo("      -")
            for result in command.policy_results:
                typer.echo(f"      {_policy_line(result, policy_texts)}")


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
