"""The ``lion watch`` command: recording lifecycle and reading.

``start`` and ``stop`` collect the machine state once and hand it to
:mod:`lion.program.recording`; ``status`` collects once and compares the active
recording's start state with the fresh capture. ``list``, ``show``, ``R`` and
``R-`` are pure readers: they never collect and never write. ``R`` and ``R-``
render the read-time classification of the active recording (see
:mod:`lion.classify`).
"""

import json
from datetime import UTC, datetime

import typer

from lion.classify import engine as classify_engine
from lion.classify import rules as classify_rules
from lion.classify.model import Classification
from lion.command import fail
from lion.command.classify import block_line
from lion.command.diagnostics import warn_incomplete
from lion.program import annotations, recording
from lion.program import shell as shell_logic
from lion.program.diff import diff_collectors, has_structural_change, render
from lion.program.recording import Recording
from lion.state.collector import collect_state
from lion.state.registry import COLLECTORS

__all__ = ["run"]


def segment_json(segment: recording.SegmentInfo) -> dict[str, object]:
    """Return the shared JSON representation of one segment."""
    return {
        "name": segment.name,
        "path": str(segment.path),
        "pid": segment.pid,
        "started_at": segment.started_at,
        "alive": segment.alive,
        "end_reason": segment.end_reason,
        "commands": segment.commands,
        "results": segment.results,
        "comments": segment.comments,
        "exits": segment.exits,
        "executed_seconds": segment.executed_seconds,
        "size_bytes": segment.size_bytes,
    }


def recording_json(record: recording.Recording) -> dict[str, object]:
    """Return the shared JSON representation of one recording."""
    return {
        "ref": record.ref,
        "path": str(record.path),
        "status": record.meta.status,
        "started_at": record.meta.started_at,
        "stopped_at": record.meta.stopped_at,
        "note": record.meta.note,
        "integration_version": record.meta.integration_version,
        "segments": [segment_json(segment) for segment in record.segments],
    }


def _instant(value: float) -> str:
    return datetime.fromtimestamp(value, tz=UTC).isoformat()


def _event_json(event: recording.SpoolEvent) -> dict[str, object]:
    """Return the JSON representation of one ordered spool event."""
    payload: dict[str, object] = {
        "kind": event.kind,
        "segment": event.segment,
        "time": _instant(event.time),
    }
    if event.kind in ("cmd", "comment"):
        payload["cwd"] = event.cwd
        payload["text"] = event.text
    elif event.kind == "result":
        payload["status"] = event.status
        duration = event.executed_seconds
        payload["duration_ms"] = None if duration is None else round(duration * 1000, 3)
    elif event.kind == "exit":
        payload["status"] = event.status
    elif event.kind == "segment":
        payload["boot_id"] = event.boot_id
        payload["pid"] = event.pid
        payload["start_ticks"] = event.start_ticks
    return payload


def _segment_text(segment: recording.SegmentInfo) -> str:
    state = "open" if segment.alive else f"ended ({segment.end_reason})"
    return (
        f"    {segment.name}: {state} \u2014 {segment.commands} commands, {segment.results} results, "
        f"{segment.comments} comments, {segment.exits} exits, {segment.executed_seconds * 1000:.1f} ms"
    )


def _event_text(event: recording.SpoolEvent) -> str:
    if event.kind == "segment":
        return f"  segment {event.segment}  pid {event.pid}  boot {event.boot_id}"
    stamp = _instant(event.time)
    if event.kind == "cmd":
        return f"  {stamp}  {event.cwd}  $ {_indent(event.text)}"
    if event.kind == "comment":
        return f"  {stamp}  {event.cwd}  {_indent(event.text)}"
    if event.kind == "result":
        duration = event.executed_seconds
        millis = "unknown" if duration is None else f"{duration * 1000:.1f}"
        return f"  {stamp}  -> status {event.status} ({millis} ms)"
    return f"  {stamp}  exit {event.status}"


def _indent(text: str) -> str:
    return text.replace("\n", "\n      ")


def format_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    return f"{size_bytes / 1024:.1f} KiB"


def _collect() -> dict[str, dict[str, object]]:
    state = collect_state(COLLECTORS)
    warn_incomplete(state)
    return state


def _run_status(json_output: bool) -> None:
    record = recording.active_recording()
    if record is None:
        if json_output:
            typer.echo(json.dumps({"active": False}))
        else:
            typer.echo("No active recording.")
        return
    state = _collect()
    diff = diff_collectors(record.state_start.collectors, state)
    if json_output:
        payload: dict[str, object] = {
            "active": True,
            "recording": recording_json(record),
            "changes": diff,
            "structure_changed": has_structural_change(diff),
        }
        typer.echo(json.dumps(payload))
        return
    typer.echo(f"Active recording {record.ref} (started {record.meta.started_at}).")
    if record.meta.note:
        typer.echo(f"  note: {record.meta.note}")
    if record.segments:
        typer.echo("  segments:")
        for segment in record.segments:
            typer.echo(_segment_text(segment))
    else:
        typer.echo("  segments: none yet (no shell event captured).")
    if not diff:
        typer.echo("  No changes since the recording started.")
    else:
        if has_structural_change(diff):
            typer.echo("  Structure changed since the recording started.")
        typer.echo(_indent(render(diff)))


def _run_start(note: str) -> None:
    home = shell_logic.get_home()
    integration = shell_logic.status(home)
    if not integration.get("inserted"):
        raise ValueError("The watch integration is not inserted. Run 'lion shell insert' first, then open a new shell.")
    state = _collect()
    record = recording.start_recording(state, note)
    typer.echo(f"Recording started: {record.ref}")


def _run_stop(note: str) -> None:
    state = _collect()
    record = recording.stop_recording(state, note)
    typer.echo(f"Recording stopped: {record.ref}")
    typer.echo(f"State end: {record.path / 'state-end.toml'}")


def record_totals(record: Recording) -> tuple[int, int]:
    """Return the event total and the byte size of a recording's segments."""
    events = sum(segment.commands + segment.results + segment.comments + segment.exits for segment in record.segments)
    size = sum(segment.size_bytes for segment in record.segments)
    return events, size


def _run_list(json_output: bool) -> None:
    records = recording.list_recordings()
    if json_output:
        payload = [{"ref": record.ref, **recording_json(record)} for record in records]
        typer.echo(json.dumps({"recordings": payload}))
        return
    if not records:
        typer.echo("No recordings stored. Run 'lion watch start'.")
        return
    typer.echo(f"{'#':>3}  {'REF':<30}  {'STATUS':<7}  {'STARTED':<32}  {'EVENTS':>6}  SIZE")
    for index, record in enumerate(records, start=1):
        events, size = record_totals(record)
        typer.echo(
            f"{index:>3}  {record.ref:<30}  {record.meta.status:<7}  {record.meta.started_at:<32}  "
            f"{events:>6}  {format_size(size)}"
        )


def _run_show(reference: str | None, json_output: bool) -> None:
    if reference is None:
        record = recording.active_recording()
        if record is None:
            records = recording.list_recordings()
            if not records:
                if json_output:
                    typer.echo(json.dumps({"recording": None, "events": []}))
                else:
                    typer.echo("No recording stored. Run 'lion watch start'.")
                return
            record = records[-1]
    else:
        record = recording.resolve_recording(reference)
    events = recording.load_events(record)
    if json_output:
        payload = {
            "recording": recording_json(record),
            "events": [_event_json(event) for event in events],
        }
        typer.echo(json.dumps(payload))
        return
    typer.echo(f"Recording {record.ref} ({record.meta.status}, started {record.meta.started_at}).")
    if record.meta.stopped_at:
        typer.echo(f"  stopped: {record.meta.stopped_at}")
    if record.meta.note:
        typer.echo(f"  note: {record.meta.note}")
    for segment in record.segments:
        typer.echo(_segment_text(segment))
    typer.echo("")
    previous_segment: str | None = None
    for event in events:
        if event.kind != "segment" and previous_segment is not None and event.segment != previous_segment:
            typer.echo(f"  \u2026 segment {event.segment}")
        typer.echo(_event_text(event))
        previous_segment = event.segment


def _collapse(value: str) -> str:
    return " ".join(value.split())


def _classification(record: Recording) -> Classification:
    """Classify one recording with the merged rule set (read-only)."""
    return classify_engine.classify_recording(record, classify_rules.load_ruleset())


def _run_overview() -> None:
    """Show the active recording's ADR fields and every classified block."""
    record = recording.active_recording()
    if record is None:
        typer.echo("No active recording.")
        return
    classification = _classification(record)
    adr = annotations.adr_for(record)
    typer.echo(f"Recording {record.ref}")
    if adr is None:
        typer.echo("  adr: -")
    else:
        pairs = ", ".join(f"{name}={_collapse(value)}" for name, value in adr.fields.items())
        typer.echo(f"  adr: {pairs}")
    open_fields = [name for name in annotations.CATALOG if adr is None or name not in adr.fields]
    typer.echo("  open: " + (", ".join(open_fields) if open_fields else "-"))
    for block in classification.blocks:
        typer.echo(block_line(block))


def _run_previous() -> None:
    """Show the terse line of the previous block of the active recording."""
    record = recording.active_recording()
    if record is None:
        typer.echo("No active recording.")
        return
    classification = _classification(record)
    if not classification.blocks:
        typer.echo("No previous block.")
        return
    typer.echo(block_line(classification.blocks[-1]))


def run(action: str = "status", argument: str | None = None, json_output: bool = False) -> None:
    """Run one watch operation.

    Args:
        action: ``status``, ``start``, ``stop``, ``list``, ``show``, ``R`` or
            ``R-``.
        argument: The ``--note`` text for ``start``/``stop`` or the reference
            for ``show``.
        json_output: Emit a single JSON object instead of human text.
    """
    try:
        if action == "start":
            _run_start(argument or "")
        elif action == "stop":
            _run_stop(argument or "")
        elif action == "list":
            _run_list(json_output)
        elif action == "show":
            _run_show(argument, json_output)
        elif action == "R":
            _run_overview()
        elif action == "R-":
            _run_previous()
        else:
            _run_status(json_output)
    except (OSError, ValueError) as exc:
        fail(exc)
