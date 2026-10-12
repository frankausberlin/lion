"""The ``lion records`` command: read and edit the ADRs of recordings.

``status``, ``list`` and ``adrs`` are pure readers: they never collect a state
and never write. The annotation grammar itself lives in
:mod:`lion.program.annotations`; this module only resolves references, renders
the views and maps errors to CLI failures.
"""

import json
import os
import shlex
import subprocess
from dataclasses import dataclass

import typer

from lion.command import fail
from lion.command.watch import format_size, record_totals, recording_json
from lion.program import annotations, recording
from lion.program.annotations import Adr, AdrIssue, ResolvedRef
from lion.program.recording import Recording

__all__ = ["run"]


@dataclass(frozen=True)
class _AdrSummary:
    """The ADR counters of the whole recording space."""

    total: int
    complete: int
    incomplete: int
    edited: int
    status: dict[str, int]

    def to_json(self) -> dict[str, object]:
        """Return the counters as the ``records status`` JSON shape."""
        return {
            "total": self.total,
            "complete": self.complete,
            "incomplete": self.incomplete,
            "edited": self.edited,
            "status": self.status,
        }


def _adr_summary(adrs: list[Adr]) -> _AdrSummary:
    status = {name: 0 for name in annotations.STATUS_VALUES}
    for adr in adrs:
        if adr.status in status:
            status[adr.status] += 1
    return _AdrSummary(
        total=len(adrs),
        complete=sum(1 for adr in adrs if adr.complete),
        incomplete=sum(1 for adr in adrs if not adr.complete),
        edited=sum(1 for adr in adrs if adr.edited),
        status=status,
    )


def _adr_label(adr: Adr) -> str:
    """Return the one-word ADR state used in list columns."""
    return adr.status if adr.complete else "incomplete"


def _adr_brief(adr: Adr) -> dict[str, object]:
    """Return the compact ADR object of ``records list --json``."""
    return {
        "status": adr.status,
        "complete": adr.complete,
        "edited": adr.edited,
        "title": adr.fields.get("title", ""),
    }


def _issue_json(issue: AdrIssue) -> dict[str, object]:
    """Return the JSON representation of one annotation issue."""
    return {
        "reason": issue.reason,
        "segment": issue.segment,
        "line": issue.line,
        "time": issue.time,
        "text": issue.text,
    }


def _resolved_json(item: ResolvedRef) -> dict[str, object]:
    """Return the JSON representation of one resolved reference token."""
    return {"ref": item.raw, "resolved": item.resolved}


def _multiline(value: str) -> str:
    return value.replace("\n", "\n    ")


def _refs_text(items: tuple[ResolvedRef, ...]) -> str:
    return ", ".join(f"{item.raw} -> {item.resolved or '(unknown)'}" for item in items)


def _run_status(json_output: bool) -> None:
    active = recording.active_recording()
    records = recording.list_recordings()
    summary = _adr_summary(annotations.adrs_for(records))
    open_count = sum(1 for record in records if record.active)
    if json_output:
        payload = {
            "active": None
            if active is None
            else {
                "ref": active.ref,
                "started_at": active.meta.started_at,
                "note": active.meta.note,
                "segments": len(active.segments),
            },
            "recordings": len(records),
            "open": open_count,
            "adrs": summary.to_json(),
        }
        typer.echo(json.dumps(payload))
        return
    if active is None:
        typer.echo("No active recording.")
    else:
        typer.echo(
            f"Active recording {active.ref} (started {active.meta.started_at}, {len(active.segments)} segment(s))."
        )
    typer.echo(f"Recordings: {len(records)} ({open_count} open).")
    typer.echo(
        f"ADRs: {summary.total} total, {summary.complete} complete, "
        f"{summary.incomplete} incomplete, {summary.edited} edited."
    )
    status = summary.status
    distribution = ", ".join(f"{name} {status[name]}" for name in annotations.STATUS_VALUES)
    typer.echo(f"Status: {distribution}.")


def _run_list(json_output: bool) -> None:
    records = recording.list_recordings()
    if json_output:
        payload: list[dict[str, object]] = []
        for record in records:
            adr = annotations.adr_for(record)
            payload.append({**recording_json(record), "adr": None if adr is None else _adr_brief(adr)})
        typer.echo(json.dumps({"recordings": payload}))
        return
    if not records:
        typer.echo("No recordings stored. Run 'lion watch start'.")
        return
    typer.echo(f"{'#':>3}  {'REF':<30}  {'STATUS':<7}  {'STARTED':<32}  {'EVENTS':>6}  {'SIZE':>9}  ADR")
    for index, record in enumerate(records, start=1):
        events, size = record_totals(record)
        adr = annotations.adr_for(record)
        label = "-" if adr is None else _adr_label(adr)
        typer.echo(
            f"{index:>3}  {record.ref:<30}  {record.meta.status:<7}  {record.meta.started_at:<32}  "
            f"{events:>6}  {format_size(size):>9}  {label}"
        )


def _adr_list_json(record: Recording, adr: Adr) -> dict[str, object]:
    """Return the JSON representation of one ADR in the listing."""
    return {
        "ref": record.ref,
        "status": adr.status,
        "complete": adr.complete,
        "edited": adr.edited,
        "title": adr.fields.get("title", ""),
        "started_at": record.meta.started_at,
    }


def _run_adrs_list(json_output: bool) -> None:
    records = recording.list_recordings()
    entries = [(record, annotations.adr_for(record)) for record in records]
    items = [(record, adr) for record, adr in entries if adr is not None]
    if json_output:
        payload = [_adr_list_json(record, adr) for record, adr in items]
        typer.echo(json.dumps({"adrs": payload}))
        return
    if not items:
        typer.echo("No ADRs recorded.")
        return
    typer.echo(f"{'#':>3}  {'REF':<30}  {'STATUS':<10}  {'TITLE':<40}  STARTED")
    for index, (record, adr) in enumerate(items, start=1):
        title = " ".join(adr.fields.get("title", "").split())
        typer.echo(f"{index:>3}  {record.ref:<30}  {_adr_label(adr):<10}  {title:<40}  {record.meta.started_at}")


def _adr_detail_json(adr: Adr, refs: list[str]) -> dict[str, object]:
    """Return the JSON representation of one ADR detail view."""
    anchor: dict[str, object] | None = None
    if adr.anchor is not None:
        anchor = {
            "kind": adr.anchor.kind,
            "segment": adr.anchor.segment,
            "line": adr.anchor.line,
            "time": adr.anchor.time,
            "text": adr.anchor.text,
        }
    return {
        "ref": adr.ref,
        "fields": dict(adr.fields),
        "status": adr.status,
        "complete": adr.complete,
        "edited": adr.edited,
        "anchor": anchor,
        "auto": {
            "first_at": adr.first_at,
            "last_at": adr.last_at,
            "user": adr.user,
            "host": adr.host,
            "cwd": adr.cwd,
            "segments": list(adr.segments),
        },
        "uses": [_resolved_json(item) for item in annotations.resolve_ref_list(adr.fields.get("uses", ""), refs)],
        "supersedes": [
            _resolved_json(item) for item in annotations.resolve_ref_list(adr.fields.get("supersedes", ""), refs)
        ],
        "issues": [_issue_json(issue) for issue in adr.issues],
    }


def _run_adrs_detail(record: Recording, refs: list[str], json_output: bool) -> None:
    result = annotations.annotations_for(record)
    adr = result.adr
    if json_output:
        if adr is None:
            payload: dict[str, object] = {
                "adr": None,
                "issues": [_issue_json(issue) for issue in result.issues],
            }
        else:
            payload = {"adr": _adr_detail_json(adr, refs)}
        typer.echo(json.dumps(payload))
        return
    if adr is None:
        typer.echo(f"No ADR in recording {record.ref}.")
    else:
        state = "complete" if adr.complete else "incomplete"
        typer.echo(f"ADR {adr.ref} ({adr.status}, {state})")
        for name, value in adr.fields.items():
            if name in ("uses", "supersedes"):
                continue
            typer.echo(f"  {name}: {_multiline(value)}")
        if adr.anchor is not None:
            typer.echo(
                f"  anchor: {adr.anchor.kind} command in {adr.anchor.segment} "
                f"line {adr.anchor.line} at {adr.anchor.time}"
            )
            typer.echo(f"    $ {_multiline(adr.anchor.text)}")
        typer.echo(f"  first_at: {adr.first_at}")
        typer.echo(f"  last_at: {adr.last_at}")
        typer.echo(f"  user: {adr.user}")
        typer.echo(f"  host: {adr.host}")
        typer.echo(f"  cwd: {adr.cwd}")
        typer.echo(f"  segments: {', '.join(adr.segments)}")
        typer.echo(f"  edited: {'true' if adr.edited else 'false'}")
        for name in ("uses", "supersedes"):
            value = adr.fields.get(name)
            if value:
                resolved = annotations.resolve_ref_list(value, refs)
                typer.echo(f"  {name}: {_refs_text(resolved)}")
    if result.issues:
        typer.echo("Unresolved:")
        for issue in result.issues:
            typer.echo(f"  {issue.segment}:{issue.line}  {issue.reason}: {issue.text}")


def _run_adrs(reference: str | None, json_output: bool) -> None:
    if reference is None:
        _run_adrs_list(json_output)
        return
    record = recording.resolve_recording(reference)
    refs = [item.ref for item in recording.list_recordings()]
    _run_adrs_detail(record, refs, json_output)


def _run_edit(reference: str | None) -> None:
    """Open the ADR overlay of a stopped recording in ``$EDITOR`` and store it."""
    if reference is not None:
        record = recording.resolve_recording(reference)
    else:
        active = recording.active_recording()
        record = active if active is not None else recording.resolve_recording("latest")
    if record.active:
        raise annotations.AnnotationError(f"Recording {record.ref} is still open; run 'lion watch stop' first.")
    editor = os.environ.get("EDITOR", "").strip()
    if not editor:
        raise annotations.AnnotationError("No $EDITOR is set; export EDITOR=<editor> and retry.")
    current = annotations.annotations_for(record).adr
    draft = recording.stage_text(
        record.path,
        annotations.overlay_template(current),
        prefix=".adr-edit-",
        suffix=".toml",
    )
    try:
        completed = subprocess.run([*shlex.split(editor), str(draft)], check=False)
    except OSError as exc:
        raise annotations.AnnotationError(
            f"Cannot run $EDITOR '{editor}': {exc}; the draft remains at {draft}."
        ) from exc
    if completed.returncode != 0:
        raise annotations.AnnotationError(
            f"{editor} exited with status {completed.returncode}; the draft remains at {draft}."
        )
    overlay = annotations.parse_overlay_document(draft.read_bytes(), draft)
    target = annotations.write_overlay(record.path, overlay.fields)
    draft.unlink(missing_ok=True)
    typer.echo(f"ADR overlay written: {target}")


def run(action: str = "status", argument: str | None = None, json_output: bool = False) -> None:
    """Run one records operation.

    Args:
        action: ``status``, ``list``, ``adrs`` or ``edit``.
        argument: The recording reference for the ``adrs`` detail view and
            ``edit`` (defaults to the active or newest recording there).
        json_output: Emit a single JSON object instead of human text.
    """
    try:
        if action == "list":
            _run_list(json_output)
        elif action == "adrs":
            _run_adrs(argument, json_output)
        elif action == "edit":
            _run_edit(argument)
        else:
            _run_status(json_output)
    except (OSError, ValueError) as exc:
        fail(exc)
