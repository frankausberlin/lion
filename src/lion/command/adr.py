"""The ``lion adr`` command: document standalone decisions.

``status``, ``list`` and ``show`` are pure readers: they never collect a state
and never write. ``add`` and ``edit`` are the only writers; ``edit`` opens a
TOML template in ``$EDITOR`` and replaces the document in place. The document
model, the store and its doctor check live in :mod:`lion.program.adrs`.
"""

import json
import os
import shlex
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass

import typer

from lion.command import fail
from lion.program import adrs, annotations, recording
from lion.program.adrs import AdrDocument
from lion.program.annotations import AdrIssue, ResolvedRef

__all__ = ["run"]


@dataclass(frozen=True)
class _AdrSummary:
    """The standalone ADR counters of the whole store."""

    total: int
    complete: int
    incomplete: int
    edited: int
    status: dict[str, int]

    def to_json(self) -> dict[str, object]:
        """Return the counters as the ``adr status --json`` shape."""
        return {
            "total": self.total,
            "complete": self.complete,
            "incomplete": self.incomplete,
            "edited": self.edited,
            "status": self.status,
        }


def _summary(documents: list[AdrDocument]) -> _AdrSummary:
    status = {name: 0 for name in annotations.STATUS_VALUES}
    for document in documents:
        if document.status in status:
            status[document.status] += 1
    return _AdrSummary(
        total=len(documents),
        complete=sum(1 for document in documents if document.complete),
        incomplete=sum(1 for document in documents if not document.complete),
        edited=sum(1 for document in documents if document.edited),
        status=status,
    )


def _label(document: AdrDocument) -> str:
    """Return the one-word ADR state used in list columns."""
    return document.status if document.complete else "incomplete"


def _issue_json(issue: AdrIssue) -> dict[str, object]:
    """Return the JSON representation of one ADR issue."""
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


def _refs(documents: list[AdrDocument]) -> tuple[list[str], list[str]]:
    """Return the ADR refs and the best-effort recording refs for resolution."""
    adr_refs = [document.ref for document in documents]
    try:
        recording_refs = [record.ref for record in recording.list_recordings()]
    except recording.RecordingError:
        recording_refs = []
    return adr_refs, recording_refs


def _run_status(json_output: bool) -> None:
    documents = adrs.list_adrs()
    summary = _summary(documents)
    if json_output:
        typer.echo(json.dumps(summary.to_json()))
        return
    typer.echo(
        f"ADRs: {summary.total} total, {summary.complete} complete, "
        f"{summary.incomplete} incomplete, {summary.edited} edited."
    )
    status = summary.status
    distribution = ", ".join(f"{name} {status[name]}" for name in annotations.STATUS_VALUES)
    typer.echo(f"Status: {distribution}.")


def _run_list(json_output: bool) -> None:
    documents = adrs.list_adrs()
    if json_output:
        payload = [
            {
                "ref": document.ref,
                "status": document.status,
                "complete": document.complete,
                "edited": document.edited,
                "title": document.fields.get("title", ""),
                "recording": document.recording,
                "created_at": document.created_at,
            }
            for document in documents
        ]
        typer.echo(json.dumps({"adrs": payload}))
        return
    if not documents:
        typer.echo("No ADRs stored.")
        return
    typer.echo(f"{'#':>3}  {'REF':<34}  {'STATUS':<10}  {'TITLE':<40}  CREATED")
    for index, document in enumerate(documents, start=1):
        title = " ".join(document.fields.get("title", "").split())
        typer.echo(f"{index:>3}  {document.ref:<34}  {_label(document):<10}  {title:<40}  {document.created_at}")


def _document_json(document: AdrDocument, adr_refs: list[str], recording_refs: list[str]) -> dict[str, object]:
    """Return the JSON representation of one ADR detail view."""
    uses = adrs.resolve_ref_list(document.fields.get("uses", ""), adr_refs, recording_refs)
    supersedes = adrs.resolve_ref_list(document.fields.get("supersedes", ""), adr_refs, recording_refs)
    return {
        "ref": document.ref,
        "fields": dict(document.fields),
        "status": document.status,
        "complete": document.complete,
        "edited": document.edited,
        "recording": document.recording,
        "created_at": document.created_at,
        "edited_at": document.edited_at,
        "uses": [_resolved_json(item) for item in uses],
        "supersedes": [_resolved_json(item) for item in supersedes],
        "issues": [_issue_json(issue) for issue in document.issues],
    }


def _run_show(reference: str | None, json_output: bool) -> None:
    document = adrs.resolve_adr(reference if reference is not None else "latest")
    adr_refs, recording_refs = _refs(adrs.list_adrs())
    if json_output:
        typer.echo(json.dumps(_document_json(document, adr_refs, recording_refs)))
        return
    state = "complete" if document.complete else "incomplete"
    typer.echo(f"ADR {document.ref} ({document.status}, {state})")
    for name, value in document.fields.items():
        if name in ("uses", "supersedes"):
            continue
        typer.echo(f"  {name}: {_multiline(value)}")
    if document.recording:
        typer.echo(f"  recording: {document.recording}")
    typer.echo(f"  created_at: {document.created_at}")
    if document.edited_at:
        typer.echo(f"  edited_at: {document.edited_at}")
    typer.echo(f"  edited: {'true' if document.edited else 'false'}")
    for name in ("uses", "supersedes"):
        value = document.fields.get(name)
        if value:
            typer.echo(f"  {name}: {_refs_text(adrs.resolve_ref_list(value, adr_refs, recording_refs))}")
    if document.issues:
        typer.echo("Unresolved:")
        for issue in document.issues:
            typer.echo(f"  {issue.segment}:{issue.line}  {issue.reason}: {issue.text}")


def _parse_fields(items: Sequence[str]) -> dict[str, str]:
    """Parse repeated ``NAME=VALUE`` options into a field mapping."""
    fields: dict[str, str] = {}
    for item in items:
        name, separator, value = item.partition("=")
        name = name.strip()
        if not separator or not name or not value:
            raise adrs.AdrError(f"Invalid --field '{item}'; expected NAME=VALUE.")
        fields[name] = value
    return fields


def _run_add(
    *,
    title: str,
    decision: str,
    status: str,
    fields: Sequence[str],
    recording_ref: str,
) -> None:
    values = _parse_fields(fields)
    if title:
        values["title"] = title
    if decision:
        values["decision"] = decision
    if status:
        values["status"] = status
    document = adrs.create_adr(values, recording_ref=recording_ref)
    typer.echo(f"ADR created: {document.ref}")
    typer.echo(f"Path: {document.path}")


def _run_edit(reference: str | None) -> None:
    """Open one ADR in ``$EDITOR`` and write the edited fields back in place."""
    document = adrs.resolve_adr(reference if reference is not None else "latest")
    editor = os.environ.get("EDITOR", "").strip()
    if not editor:
        raise adrs.AdrError("No $EDITOR is set; export EDITOR=<editor> and retry.")
    draft = recording.stage_text(
        document.path.parent,
        adrs.document_template(document),
        prefix=".adr-edit-",
        suffix=".toml",
    )
    try:
        completed = subprocess.run([*shlex.split(editor), str(draft)], check=False)
    except OSError as exc:
        raise adrs.AdrError(f"Cannot run $EDITOR '{editor}': {exc}; the draft remains at {draft}.") from exc
    if completed.returncode != 0:
        raise adrs.AdrError(f"{editor} exited with status {completed.returncode}; the draft remains at {draft}.")
    edited = adrs.parse_adr_document(draft.read_bytes(), draft)
    if edited.ref != document.ref:
        raise adrs.AdrError(f"The ADR id must stay '{document.ref}'; the draft remains at {draft}.")
    if edited.created_at != document.created_at:
        raise adrs.AdrError(f"The created_at must stay '{document.created_at}'; the draft remains at {draft}.")
    updated = adrs.update_adr(document, edited.fields, recording_ref=edited.recording)
    draft.unlink(missing_ok=True)
    typer.echo(f"ADR written: {updated.path}")


def run(
    action: str = "status",
    reference: str | None = None,
    json_output: bool = False,
    *,
    title: str = "",
    decision: str = "",
    status: str = "",
    fields: Sequence[str] = (),
    recording_ref: str = "",
) -> None:
    """Run one ``adr`` operation.

    Args:
        action: ``status``, ``add``, ``edit``, ``list`` or ``show``.
        reference: The ADR reference for ``edit``/``show`` (``latest`` when
            omitted).
        json_output: Emit a single JSON object instead of human text.
        title: ``add``: the decision title.
        decision: ``add``: the decision text.
        status: ``add``: the status value (default ``proposed``).
        fields: ``add``: repeated ``NAME=VALUE`` fields.
        recording_ref: ``add``: an optional recording reference.
    """
    try:
        if action == "add":
            _run_add(
                title=title,
                decision=decision,
                status=status,
                fields=fields,
                recording_ref=recording_ref,
            )
        elif action == "edit":
            _run_edit(reference)
        elif action == "list":
            _run_list(json_output)
        elif action == "show":
            _run_show(reference, json_output)
        else:
            _run_status(json_output)
    except (OSError, ValueError) as exc:
        fail(exc)
