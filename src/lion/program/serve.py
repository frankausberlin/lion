"""The MCP server behind ``lion serve``: a read-only mirror plus ADR writing.

The server is a second view of the same ledger the CLI reads, never a second
implementation: every tool mirrors an existing ``--json`` contract and is
implemented on top of the program layer (storage, recordings, annotations,
standalone ADRs, classifier). No tool ever collects a state - only
``lion_adr_add`` and ``lion_adr_update`` write, and only into the standalone
ADR store - and recorded text is redacted best-effort on the way out
(:mod:`lion.program.redaction`).

``fastmcp`` is imported inside :func:`build_server`, so importing this module
(``doctor``, tests) never pays the fastmcp import price. The prompt defaults
(:mod:`lion.program.prompts`) are read once per build; a broken file fails the
build loudly, which is what ``serve.definition`` reports.
"""

import inspect
import json
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from lion import __version__
from lion.classify import engine as classify_engine
from lion.classify import rules as classify_rules
from lion.classify.model import Classification
from lion.classify.parser import PARSER_VERSION
from lion.program import adrs, annotations, prompts, recording, redaction, storage, wiki
from lion.program.annotations import Adr, AdrIssue
from lion.program.checks import CheckStatus, DoctorContext, Finding
from lion.program.diff import diff_collectors, has_structural_change
from lion.program.recording import Recording, SpoolEvent
from lion.state.registry import COLLECTORS

if TYPE_CHECKING:
    from fastmcp import FastMCP

__all__ = ["INSTRUCTIONS", "build_server", "serve_checks"]

#: The server instructions shown to MCP clients once per session.
INSTRUCTIONS = """LION (Linux Operator Nerd) exposes the stored machine ledger of this host.

- Every tool except lion_adr_add and lion_adr_update is read-only, and no tool
  ever collects a new state: the ledger changes only when the user runs
  'lion scan' or 'lion watch' locally.
- ADRs have two origins: 'standalone' (created with 'lion adr') and
  'recording' (annotations inside a recording). Prefer standalone ADRs for
  durable decisions and always mind the origin when citing one.
- Text taken from recordings and ADR fields is masked best-effort before it is
  returned (set LION_SERVE_REDACT=0 to disable); masking is not a security
  boundary."""

#: The snippet cap of a search hit, in characters.
SNIPPET_LIMIT = 160

#: The newest-first aliases accepted by the read tools.
_LATEST_ALIASES = frozenset({"latest", "head"})

_ADR_ORIGINS = ("standalone", "recording")


# --- shared JSON shapes (mirrors of the CLI --json contracts) ----------------


def _reference_json(entry: storage.Entry) -> dict[str, object]:
    """Return the shared JSON reference of one stored entry (``history``/``diff``)."""
    return {
        "ref": entry.ref,
        "created_at": entry.snapshot.created_at,
        "confirmed_at": entry.snapshot.confirmed_at,
        "path": str(entry.path),
    }


def _segment_json(segment: recording.SegmentInfo) -> dict[str, object]:
    """Return the JSON representation of one segment (``watch list/show``)."""
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


def _recording_json(record: Recording) -> dict[str, object]:
    """Return the JSON representation of one recording (``watch list/show``)."""
    return {
        "ref": record.ref,
        "path": str(record.path),
        "status": record.meta.status,
        "started_at": record.meta.started_at,
        "stopped_at": record.meta.stopped_at,
        "note": redaction.redact(record.meta.note),
        "integration_version": record.meta.integration_version,
        "segments": [_segment_json(segment) for segment in record.segments],
    }


def _instant(value: float) -> str:
    """Return an event epoch time as an ISO 8601 instant."""
    return datetime.fromtimestamp(value, tz=UTC).isoformat()


def _event_json(event: SpoolEvent) -> dict[str, object]:
    """Return the JSON representation of one ordered spool event (redacted)."""
    payload: dict[str, object] = {
        "kind": event.kind,
        "segment": event.segment,
        "time": _instant(event.time),
    }
    if event.kind in ("cmd", "comment"):
        payload["cwd"] = redaction.redact(event.cwd)
        payload["text"] = redaction.redact(event.text)
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


def _issue_json(issue: AdrIssue) -> dict[str, object]:
    """Return the JSON representation of one annotation issue (redacted)."""
    return {
        "reason": issue.reason,
        "segment": issue.segment,
        "line": issue.line,
        "time": issue.time,
        "text": redaction.redact(issue.text),
    }


def _resolved_json(item: annotations.ResolvedRef) -> dict[str, object]:
    """Return the JSON representation of one resolved reference token."""
    return {"ref": item.raw, "resolved": item.resolved}


def _adr_detail_json(adr: Adr, refs: list[str]) -> dict[str, object]:
    """Return the detail shape of ``lion records adrs REF --json`` (redacted)."""
    anchor: dict[str, object] | None = None
    if adr.anchor is not None:
        anchor = {
            "kind": adr.anchor.kind,
            "segment": adr.anchor.segment,
            "line": adr.anchor.line,
            "time": adr.anchor.time,
            "text": redaction.redact(adr.anchor.text),
        }
    return {
        "ref": adr.ref,
        "fields": redaction.redact_fields(adr.fields),
        "status": adr.status,
        "complete": adr.complete,
        "edited": adr.edited,
        "anchor": anchor,
        "auto": {
            "first_at": adr.first_at,
            "last_at": adr.last_at,
            "user": adr.user,
            "host": adr.host,
            "cwd": redaction.redact(adr.cwd),
            "segments": list(adr.segments),
        },
        "uses": [_resolved_json(item) for item in annotations.resolve_ref_list(adr.fields.get("uses", ""), refs)],
        "supersedes": [
            _resolved_json(item) for item in annotations.resolve_ref_list(adr.fields.get("supersedes", ""), refs)
        ],
        "issues": [_issue_json(issue) for issue in adr.issues],
    }


def _classification_json(classification: Classification) -> dict[str, object]:
    """Return the ``lion classify --json`` shape with recorded text redacted."""
    return {
        "ref": classification.ref,
        "ruleset": classification.ruleset.to_dict(),
        "parser_version": classification.parser_version,
        "blocks": [
            {
                "index": block.index,
                "segment": block.segment,
                "time": block.time,
                "text": redaction.redact(block.text),
                "matches": [
                    {
                        "rule_id": match.rule_id,
                        "source": match.source,
                        "group": match.group,
                        "fields": redaction.redact_fields(match.fields),
                        "guard": match.guard,
                    }
                    for match in block.matches
                ],
            }
            for block in classification.blocks
        ],
    }


# --- tools ------------------------------------------------------------------


def lion_history(limit: int | None = None) -> dict[str, object]:
    """List the stored states with their stable references, oldest first.

    Args:
        limit: Show only the newest N entries; omit to list every entry. The
            full history is always validated first, and the selection keeps the
            global 1-based indices.
    """
    entries = storage.list_entries()
    if limit is not None and limit < 1:
        raise ValueError("limit must be a positive integer; omit it to list every entry.")
    total = len(entries)
    shown = entries[-limit:] if limit is not None else entries
    offset = total - len(shown)
    payload = [
        {
            **_reference_json(entry),
            "index": offset + position,
            "latest": offset + position == total,
        }
        for position, entry in enumerate(shown, start=1)
    ]
    return {"entries": payload}


def lion_snapshot(reference: str = "latest") -> dict[str, object]:
    """Return one stored state in full: its reference and the validated snapshot.

    Args:
        reference: A compact reference, 1-based index, unique prefix, ISO
            timestamp or the aliases 'latest'/'previous'.
    """
    entry = storage.resolve(reference)
    return {"ref": entry.ref, "snapshot": entry.snapshot.to_toml_dict()}


def lion_diff(reference: str, second: str | None = None) -> dict[str, object]:
    """Compare two stored states without collecting or writing.

    Args:
        reference: The older state reference (see ``lion_history``).
        second: The newer state reference; defaults to the latest entry.
    """
    if len(storage.list_entries()) < 2:
        raise storage.HistoryError("Fewer than two states stored; run 'lion scan'.")
    old = storage.resolve(reference)
    new = storage.resolve(second) if second is not None else storage.resolve("latest")
    diff = diff_collectors(old.snapshot.collectors, new.snapshot.collectors)
    return {
        "from": _reference_json(old),
        "to": _reference_json(new),
        "changed": bool(diff),
        "structure_changed": has_structural_change(diff),
        "differences": diff,
    }


def lion_recordings(limit: int | None = None) -> dict[str, object]:
    """List the stored shell recordings, oldest first.

    Args:
        limit: Show only the newest N recordings; omit to list every recording.
            Every recording is validated first, and the selection keeps the
            oldest-first order.
    """
    records = recording.list_recordings()
    if limit is not None and limit < 1:
        raise ValueError("limit must be a positive integer; omit it to list every recording.")
    shown = records[-limit:] if limit is not None else records
    return {"recordings": [{"ref": record.ref, **_recording_json(record)} for record in shown]}


def lion_recording(reference: str) -> dict[str, object]:
    """Return one recording with its metadata and every ordered event.

    Recorded command, comment and note texts are redacted best-effort.

    Args:
        reference: A recording reference, unique prefix, 'latest' or 'active'.
    """
    record = recording.resolve_recording(reference)
    events = recording.load_events(record)
    return {"recording": _recording_json(record), "events": [_event_json(event) for event in events]}


def _default_recording(reference: str | None) -> Recording | None:
    """Resolve an optional reference: explicit, else active, else newest."""
    if reference is not None:
        return recording.resolve_recording(reference)
    active = recording.active_recording()
    if active is not None:
        return active
    records = recording.list_recordings()
    return records[-1] if records else None


def lion_classify(reference: str | None = None) -> dict[str, object]:
    """Classify the recorded input blocks of a recording (read-only).

    The classification is derived from the raw events on every call; block
    texts and match fields are redacted best-effort.

    Args:
        reference: Recording reference; defaults to the active recording, then
            the newest one. Without any recording the result carries empty
            blocks instead of failing.
    """
    record = _default_recording(reference)
    if record is None:
        return {"ref": None, "ruleset": None, "parser_version": PARSER_VERSION, "blocks": []}
    ruleset = classify_rules.load_ruleset()
    classification = classify_engine.classify_recording(record, ruleset)
    return _classification_json(classification)


def lion_adr_list(origin: str | None = None, status: str | None = None) -> dict[str, object]:
    """List the ADRs of both origins as one uniform view, oldest first.

    Args:
        origin: Restrict to 'standalone' or 'recording'; omit for both.
        status: Restrict to one status value (proposed, accepted, superseded).
    """
    if origin is not None and origin not in _ADR_ORIGINS:
        raise ValueError(f"Unknown origin '{origin}'; use one of: {', '.join(_ADR_ORIGINS)}.")
    if status is not None and not annotations.is_known_status(status):
        raise ValueError(f"Unknown status '{status}'; use one of: {', '.join(annotations.STATUS_VALUES)}.")
    items: list[dict[str, object]] = []
    if origin in (None, "standalone"):
        items.extend(_standalone_list_item(document) for document in adrs.list_adrs())
    if origin in (None, "recording"):
        items.extend(_recording_list_items())
    if status is not None:
        items = [item for item in items if item["status"] == status]
    items.sort(key=lambda item: (str(item["at"]), str(item["ref"])))
    return {"adrs": items}


def _standalone_list_item(document: adrs.AdrDocument) -> dict[str, object]:
    """Return one standalone ADR as a uniform list item."""
    return {
        "ref": document.ref,
        "origin": "standalone",
        "status": document.status,
        "complete": document.complete,
        "edited": document.edited,
        "title": redaction.redact(document.fields.get("title", "")),
        "at": document.created_at,
    }


def _recording_list_items() -> list[dict[str, object]]:
    """Return every recording ADR as a uniform list item."""
    items: list[dict[str, object]] = []
    for record in recording.list_recordings():
        adr = annotations.adr_for(record)
        if adr is None:
            continue
        items.append(
            {
                "ref": record.ref,
                "origin": "recording",
                "status": adr.status,
                "complete": adr.complete,
                "edited": adr.edited,
                "title": redaction.redact(adr.fields.get("title", "")),
                "at": record.meta.started_at,
            }
        )
    return items


def _standalone_detail(document: adrs.AdrDocument, adr_refs: list[str], recording_refs: list[str]) -> dict[str, object]:
    """Return one standalone ADR in the ``adr show --json`` shape plus ``origin``."""
    uses = adrs.resolve_ref_list(document.fields.get("uses", ""), adr_refs, recording_refs)
    supersedes = adrs.resolve_ref_list(document.fields.get("supersedes", ""), adr_refs, recording_refs)
    return {
        "ref": document.ref,
        "fields": redaction.redact_fields(document.fields),
        "status": document.status,
        "complete": document.complete,
        "edited": document.edited,
        "recording": document.recording,
        "created_at": document.created_at,
        "edited_at": document.edited_at,
        "uses": [_resolved_json(item) for item in uses],
        "supersedes": [_resolved_json(item) for item in supersedes],
        "issues": [_issue_json(issue) for issue in document.issues],
        "origin": "standalone",
    }


def _recording_detail(record: Recording, refs: list[str]) -> dict[str, object]:
    """Return one recording ADR in the ``records adrs REF --json`` shape plus ``origin``."""
    result = annotations.annotations_for(record)
    if result.adr is None:
        return {
            "adr": None,
            "issues": [_issue_json(issue) for issue in result.issues],
            "origin": "recording",
        }
    return {"adr": _adr_detail_json(result.adr, refs), "origin": "recording"}


def _resolve_detail(reference: str) -> dict[str, object]:
    """Resolve a detail reference: ADR ids first, then recording references."""
    token = reference.strip()
    if not token:
        raise adrs.AdrError("Empty reference; use 'lion adr list' for valid references.")
    documents = adrs.list_adrs()
    adr_refs = [document.ref for document in documents]
    recording_refs = _recording_ref_list()
    lowered = token.lower()
    if documents:
        if lowered in _LATEST_ALIASES:
            return _standalone_detail(documents[-1], adr_refs, recording_refs)
        exact = [document for document in documents if document.ref.lower() == lowered]
        if len(exact) == 1:
            return _standalone_detail(exact[0], adr_refs, recording_refs)
        if exact:
            raise adrs.AdrError(f"Reference '{token}' is ambiguous: {_adr_options(exact)}")
        matches = [document for document in documents if document.ref.lower().startswith(lowered)]
        if len(matches) == 1:
            return _standalone_detail(matches[0], adr_refs, recording_refs)
        if matches:
            raise adrs.AdrError(f"Reference '{token}' is ambiguous: {_adr_options(matches)}")
    try:
        record = recording.resolve_recording(token)
    except recording.RecordingError as exc:
        if documents:
            raise adrs.AdrError(
                f"Unknown reference '{token}'. Valid ADRs: {_adr_options(documents)}; "
                f"valid recordings: {_options(recording_refs)}"
            ) from exc
        raise
    return _recording_detail(record, recording_refs)


def _adr_options(documents: list[adrs.AdrDocument]) -> str:
    """Return the comma-separated references of the given ADRs."""
    return _options([document.ref for document in documents])


def _options(refs: list[str]) -> str:
    """Return a comma-separated option list, or a neutral hint when empty."""
    return ", ".join(refs) if refs else "(none)"


def _recording_ref_list() -> list[str]:
    """Return the known recording references; an unreadable space yields none."""
    try:
        return [record.ref for record in recording.list_recordings()]
    except recording.RecordingError:
        return []


def lion_adr_show(reference: str) -> dict[str, object]:
    """Return one ADR in detail: standalone first, then recording-bound.

    The resolution mirrors ``uses``/``supersedes``: exact ADR id, then a unique
    ADR prefix, then a recording reference.

    Args:
        reference: An ADR id, a unique prefix, 'latest', or a recording
            reference carrying an ADR.
    """
    return _resolve_detail(reference)


def _validate_fields(fields: Mapping[str, str]) -> None:
    """Reject field sets that could never be loaded back."""
    for name, value in fields.items():
        if not name.strip() or not value.strip():
            raise adrs.AdrError(f"Invalid ADR field '{name}': names and values must be non-empty.")


def lion_adr_add(fields: dict[str, str], recording: str = "") -> dict[str, object]:
    """Create a new standalone ADR; at least one field is required.

    Args:
        fields: The field set, for example title, decision, component, status.
        recording: Optional recording reference the decision belongs to.
    """
    _validate_fields(fields)
    document = adrs.create_adr(fields, recording_ref=recording)
    return {"ref": document.ref, "path": str(document.path)}


def lion_adr_update(reference: str, fields: dict[str, str], recording: str | None = None) -> dict[str, object]:
    """Update one standalone ADR: overwrite and add fields, never remove any.

    Args:
        reference: The ADR id, a unique prefix or 'latest'.
        fields: The fields to overwrite or add; must be non-empty.
        recording: A new recording reference; omit to keep the current one.
    """
    document = adrs.resolve_adr(reference)
    _validate_fields(fields)
    updated = adrs.update_adr(document, fields, recording_ref=recording)
    return {"ref": updated.ref, "path": str(updated.path), "edited_at": updated.edited_at}


def _match_snippet(text: str, needle: str) -> str | None:
    """Return the first matching line, collapsed, redacted and capped."""
    for line in text.splitlines() or [""]:
        collapsed = " ".join(line.split())
        if needle in collapsed.lower():
            masked = redaction.redact(collapsed)
            if len(masked) > SNIPPET_LIMIT:
                return masked[:SNIPPET_LIMIT] + "\u2026"
            return masked
    return None


def lion_search(query: str, limit: int = 20) -> dict[str, object]:
    """Search the ADRs and recordings, newest first, case-insensitive.

    Every ADR field value (both origins) and the recording notes, command and
    comment texts are searched; snippets are redacted best-effort.

    Args:
        query: The text to find (a case-insensitive substring, per line).
        limit: Maximum number of hits, newest first.
    """
    token = query.strip()
    if not token:
        raise ValueError("Empty query; pass a non-empty search text.")
    if limit < 1:
        raise ValueError("limit must be a positive integer.")
    needle = token.lower()
    hits: list[tuple[str, dict[str, object]]] = []
    for document in adrs.list_adrs():
        for name, value in document.fields.items():
            snippet = _match_snippet(value, needle)
            if snippet is not None:
                hits.append(
                    (
                        document.created_at,
                        {
                            "kind": "adr",
                            "ref": document.ref,
                            "origin": "standalone",
                            "where": f"field:{name}",
                            "snippet": redaction.redact_field(name, snippet),
                        },
                    )
                )
    for record in recording.list_recordings():
        events = recording.load_events(record)
        note = _match_snippet(record.meta.note, needle)
        if note is not None:
            hits.append(
                (record.meta.started_at, {"kind": "recording", "ref": record.ref, "where": "note", "snippet": note})
            )
        adr = annotations.adr_for(record, events)
        if adr is not None:
            at = adr.last_at or record.meta.started_at
            for name, value in adr.fields.items():
                snippet = _match_snippet(value, needle)
                if snippet is not None:
                    hits.append(
                        (
                            at,
                            {
                                "kind": "adr",
                                "ref": record.ref,
                                "origin": "recording",
                                "where": f"field:{name}",
                                "snippet": redaction.redact_field(name, snippet),
                            },
                        )
                    )
        for event in events:
            if event.kind not in ("cmd", "comment"):
                continue
            snippet = _match_snippet(event.text, needle)
            if snippet is not None:
                where = "command" if event.kind == "cmd" else "comment"
                hits.append(
                    (_instant(event.time), {"kind": "recording", "ref": record.ref, "where": where, "snippet": snippet})
                )
    hits.sort(
        key=lambda item: (item[0], str(item[1]["ref"]), str(item[1]["where"]), str(item[1]["snippet"])),
        reverse=True,
    )
    return {"query": token, "hits": [hit for _, hit in hits[:limit]]}


# --- resources --------------------------------------------------------------


def _wiki_page(name: str) -> str:
    """Return one wiki page, or a loud error with the regeneration hint."""
    page = wiki.get_wiki_dir() / name
    try:
        return page.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"Cannot read wiki page '{page}': {exc}; run 'lion scan' to regenerate it.") from exc


def wiki_index_resource() -> str:
    """The derived wiki index written by the latest 'lion scan'."""
    return _wiki_page(wiki.INDEX_PAGE)


def wiki_collector_resource(collector: str) -> str:
    """One derived wiki page: the facts of a single collector.

    Args:
        collector: The collector name in the canonical registry order.
    """
    names = [item.name for item in COLLECTORS]
    if collector not in names:
        raise ValueError(f"Unknown collector '{collector}'. Valid: {', '.join(names)}.")
    return _wiki_page(f"{collector}.md")


def collectors_resource() -> str:
    """The collector registry: names and canonical order, as JSON."""
    return json.dumps({"collectors": [item.name for item in COLLECTORS]})


def classifier_rules_resource() -> str:
    """The merged classifier rule set: hash, groups and rules, as JSON."""
    ruleset = classify_rules.load_ruleset()
    return json.dumps(
        {
            "schema_version": ruleset.schema_version,
            "version": ruleset.version,
            "hash": ruleset.hash,
            "user_path": str(ruleset.user_path),
            "user_loaded": ruleset.user_loaded,
            "groups": sorted(classify_rules.GROUP_FIELDS),
            "rules": [{**rule.to_canonical(), "source": rule.source} for rule in ruleset.rules],
        }
    )


# --- prompts ----------------------------------------------------------------


def _render_review(template: prompts.PromptTemplate, reference: str = "latest", focus: str = "") -> str:
    return template.render({"reference": reference, "focus": focus or "Keep the review concise and actionable."})


def _render_document(template: prompts.PromptTemplate, subject: str, recording: str = "") -> str:
    related = f"Related recording: {recording}" if recording.strip() else "No related recording was named."
    return template.render({"subject": subject, "recording": related})


def _render_recall(template: prompts.PromptTemplate, query: str) -> str:
    return template.render({"query": query})


def _check_prompt_binding(template: prompts.PromptTemplate, fn: Callable[..., str]) -> None:
    """Fail loudly when a renderer does not match the declared arguments."""
    parameters = list(inspect.signature(fn).parameters.values())
    declared = list(template.arguments)
    names = [parameter.name for parameter in parameters]
    expected = [argument.name for argument in declared]
    if names != expected:
        raise prompts.PromptError(
            f"Prompt '{template.name}': renderer parameters {names} do not match the declared arguments "
            f"{expected} ({template.path})"
        )
    for parameter, argument in zip(parameters, declared, strict=True):
        required = parameter.default is inspect.Parameter.empty
        if required != argument.required:
            raise prompts.PromptError(
                f"Prompt '{template.name}': argument '{argument.name}' is declared required={argument.required} "
                f"but the renderer says required={required} ({template.path})"
            )


# --- server -----------------------------------------------------------------


def build_server() -> "FastMCP":
    """Build the LION MCP server with every tool, resource and prompt.

    ``fastmcp`` is imported here so the ordinary CLI never pays its import
    price. The prompt defaults are read once per build; a missing or invalid
    file raises a loud :class:`~lion.program.prompts.PromptError` naming the
    path.
    """
    from fastmcp import FastMCP
    from fastmcp.prompts import FunctionPrompt
    from fastmcp.prompts.base import PromptArgument

    templates = prompts.load_prompt_templates()

    def render_review(reference: str = "latest", focus: str = "") -> str:
        return _render_review(templates["review_latest_changes"], reference, focus)

    def render_document(subject: str, recording: str = "") -> str:
        return _render_document(templates["document_decision"], subject, recording)

    def render_recall(query: str) -> str:
        return _render_recall(templates["recall_knowledge"], query)

    mcp = FastMCP(name="lion", version=__version__, instructions=INSTRUCTIONS, on_duplicate="error")
    for tool in (
        lion_history,
        lion_snapshot,
        lion_diff,
        lion_recordings,
        lion_recording,
        lion_classify,
        lion_adr_list,
        lion_adr_show,
        lion_adr_add,
        lion_adr_update,
        lion_search,
    ):
        _ = mcp.tool(tool)
    _ = mcp.resource("lion://wiki/index", mime_type="text/markdown")(wiki_index_resource)
    _ = mcp.resource("lion://wiki/{collector}", mime_type="text/markdown")(wiki_collector_resource)
    _ = mcp.resource("lion://collectors", mime_type="application/json")(collectors_resource)
    _ = mcp.resource("lion://classifier/rules", mime_type="application/json")(classifier_rules_resource)
    renderers: tuple[tuple[str, Callable[..., str]], ...] = (
        ("review_latest_changes", render_review),
        ("document_decision", render_document),
        ("recall_knowledge", render_recall),
    )
    for name, render in renderers:
        template = templates[name]
        _check_prompt_binding(template, render)
        _ = mcp.add_prompt(
            FunctionPrompt(
                name=template.name,
                description=template.description,
                arguments=[
                    PromptArgument(name=argument.name, description=argument.description, required=argument.required)
                    for argument in template.arguments
                ],
                fn=render,
            )
        )
    return mcp


def serve_checks(_ctx: DoctorContext) -> list[Finding]:
    """Check that the MCP server definition builds (doctor topic ``serve``).

    Building registers every tool, resource and prompt and parses the prompt
    defaults, so a duplicate name or a broken file is a loud ``error`` with the
    underlying message. This is read-only and never starts the server.
    """
    try:
        _ = build_server()
    except Exception as exc:
        return [
            Finding(
                topic="serve",
                name="serve.definition",
                status=CheckStatus.ERROR,
                message=str(exc),
                hint="'lion serve' would fail the same way; fix the prompt defaults or the server definition.",
            )
        ]
    return [
        Finding(
            topic="serve",
            name="serve.definition",
            status=CheckStatus.OK,
            message=f"MCP server builds with {len(prompts.EXPECTED_PROMPTS)} prompt defaults.",
        )
    ]
