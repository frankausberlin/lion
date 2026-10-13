"""Standalone ADRs: decisions that exist without a recording.

An ADR is a TOML document under ``$XDG_DATA_HOME/lion/adrs/<id>.toml``. The id
is ``adr-`` plus the compact UTC timestamp used for recording directories (from
:mod:`lion.program.storage`, so the two formats cannot drift); a collision adds
a ``~NNNN`` suffix. New documents are published with ``os.link`` and edits
replace the document in place with ``os.replace``, both at mode ``600``.

The field catalog, the completeness rule (``title`` + ``decision``) and the
status rule (``proposed`` default, unknown values reported as an issue) come
from :mod:`lion.program.annotations`, the single source shared with the
recording overlay. A standalone ADR never mixes with the scan history or the
recordings; ``uses``/``supersedes`` resolve against the ADR store first and the
recording references second, and an unresolved token displays as ``(unknown)``
(never an error).
"""

import os
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import tomli_w

from lion.program import annotations, recording
from lion.program.annotations import AdrIssue, ResolvedRef
from lion.program.checks import CheckStatus, DoctorContext, Finding
from lion.program.storage import COMPACT_TIMESTAMP_FORMAT, get_data_dir

__all__ = [
    "ADR_PREFIX",
    "ADR_SCHEMA_VERSION",
    "AdrDocument",
    "AdrError",
    "adr_checks",
    "create_adr",
    "document_template",
    "get_adrs_dir",
    "list_adrs",
    "load_adr",
    "parse_adr_document",
    "resolve_adr",
    "resolve_ref_list",
    "update_adr",
    "write_adr",
]

#: The only ADR schema version the reader accepts.
ADR_SCHEMA_VERSION = 1

#: The id prefix that separates a standalone ADR from a recording reference.
ADR_PREFIX = "adr-"

_LATEST_ALIASES = frozenset({"latest", "head"})


class AdrError(ValueError):
    """A standalone ADR cannot be read, validated or mutated."""


@dataclass(frozen=True)
class AdrDocument:
    """One validated standalone ADR.

    ``fields`` is ordered (catalog order, then extras) and never empty; ``status``
    defaults to ``proposed`` and ``complete`` follows the shared ``title`` +
    ``decision`` rule. ``edited`` is derived from ``edited_at``.
    """

    ref: str
    path: Path
    created_at: str
    fields: Mapping[str, str]
    recording: str = ""
    edited_at: str = ""
    issues: tuple[AdrIssue, ...] = ()

    @property
    def status(self) -> str:
        """Return the ADR status, defaulting to ``proposed``."""
        return self.fields.get("status") or annotations.STATUS_PROPOSED

    @property
    def complete(self) -> bool:
        """Return whether the ADR carries both a title and a decision."""
        return annotations.is_complete(self.fields)

    @property
    def edited(self) -> bool:
        """Return whether the ADR has been edited since it was created."""
        return bool(self.edited_at)

    def to_toml_dict(self) -> dict[str, object]:
        """Return the TOML representation with scalars before the fields table."""
        data: dict[str, object] = {
            "schema_version": ADR_SCHEMA_VERSION,
            "id": self.ref,
            "created_at": self.created_at,
        }
        if self.edited_at:
            data["edited_at"] = self.edited_at
        if self.recording:
            data["recording"] = self.recording
        data["fields"] = dict(self.fields)
        return data


def get_adrs_dir() -> Path:
    """Return the LION standalone ADR directory."""
    return get_data_dir() / "adrs"


def _invalid(path: Path, message: str) -> AdrError:
    return AdrError(f"Invalid ADR document: {path}: {message}")


def _require_timestamp(data: Mapping[str, object], key: str, path: Path) -> str:
    """Return a validated timestamp string, or fail loudly naming the file."""
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise _invalid(path, f"{key} must be a non-empty string")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise _invalid(path, f"{key} is not a valid timestamp: {value!r}") from exc
    if parsed.utcoffset() is None:
        raise _invalid(path, f"{key} must include a timezone offset")
    return value


def parse_adr_document(raw: bytes, path: Path) -> AdrDocument:
    """Parse and strictly validate one standalone ADR document.

    The id is the document's ``id`` field (the file name is checked separately by
    :func:`load_adr`). The ``[fields]`` table must exist and be non-empty; every
    value is a non-empty string. An unknown ``status`` stays verbatim and becomes
    a visible :class:`AdrIssue`, never a fatal error. Any structural violation is
    a loud :class:`AdrError` naming ``path``.
    """
    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise _invalid(path, str(exc)) from exc
    version = data.get("schema_version")
    if isinstance(version, bool) or version != ADR_SCHEMA_VERSION:
        raise _invalid(path, f"schema_version must be {ADR_SCHEMA_VERSION}")
    ref = data.get("id")
    if not isinstance(ref, str) or not ref.strip():
        raise _invalid(path, "id must be a non-empty string")
    created_at = _require_timestamp(data, "created_at", path)
    edited_at = _require_timestamp(data, "edited_at", path) if "edited_at" in data else ""
    recording_ref = ""
    if "recording" in data:
        value = data.get("recording")
        if not isinstance(value, str) or not value.strip():
            raise _invalid(path, "recording must be a non-empty string")
        recording_ref = value
    fields_raw = data.get("fields")
    if not isinstance(fields_raw, dict):
        raise _invalid(path, "missing [fields] table")
    if not fields_raw:
        raise _invalid(path, "[fields] must carry at least one field")
    fields: dict[str, str] = {}
    for name, value in cast("dict[str, object]", fields_raw).items():
        if not isinstance(value, str) or not value.strip():
            raise _invalid(path, f"field '{name}' must be a non-empty string")
        fields[name] = value
    ordered = annotations.order_fields(fields)
    issues: tuple[AdrIssue, ...] = ()
    status = ordered.get("status")
    if status and not annotations.is_known_status(status):
        issues = (
            AdrIssue(
                reason=annotations.REASON_UNKNOWN_STATUS,
                marker=".",
                segment=path.name,
                line=0,
                time=edited_at or created_at,
                text=f"adr.status {status}",
            ),
        )
    return AdrDocument(
        ref=ref,
        path=path,
        created_at=created_at,
        fields=ordered,
        recording=recording_ref,
        edited_at=edited_at,
        issues=issues,
    )


def load_adr(path: Path) -> AdrDocument:
    """Load and validate one ADR document by its path.

    The document's ``id`` must match the file name without ``.toml``. Only a
    regular file is accepted; a directory or a symlink is a loud defect naming
    the path.

    Raises:
        AdrError: If the file is missing, not a regular file, or invalid.
    """
    if path.is_symlink() or not path.is_file():
        raise AdrError(f"Cannot load ADR '{path}': not a regular file")
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise AdrError(f"Cannot load ADR '{path}': {exc}") from exc
    document = parse_adr_document(raw, path)
    if document.ref != path.stem:
        raise AdrError(f"Cannot load ADR '{path}': id '{document.ref}' does not match the file name")
    return document


def _adr_paths() -> list[Path]:
    """Return the ADR document paths in a stable order.

    A missing directory is an empty store. A path that is not a directory, an
    unreadable directory, or any unexpected entry (a subdirectory, a symlink or
    a non-``.toml`` file) fails loudly instead of being skipped.
    """
    directory = get_adrs_dir()
    if not directory.exists() and not directory.is_symlink():
        return []
    if directory.is_symlink() or not directory.is_dir():
        raise AdrError(f"ADR store is not a directory: {directory}")
    try:
        entries = sorted(directory.iterdir(), key=lambda item: item.name)
    except OSError as exc:
        raise AdrError(f"Cannot read ADR store '{directory}': {exc}") from exc
    paths: list[Path] = []
    for entry in entries:
        if entry.name.startswith("."):
            continue
        if entry.is_symlink() or not entry.is_file() or entry.suffix != ".toml":
            raise AdrError(f"Unexpected entry in ADR store: {entry}")
        paths.append(entry)
    return paths


def list_adrs() -> list[AdrDocument]:
    """Load and validate every ADR, oldest first by creation time.

    Every entry is validated; an invalid one fails loudly with its path.
    """
    documents = [load_adr(path) for path in _adr_paths()]
    documents.sort(key=lambda document: (document.created_at, document.ref))
    return documents


def _ambiguous(reference: str, matches: list[AdrDocument]) -> AdrError:
    options = ", ".join(document.ref for document in matches)
    return AdrError(f"Reference '{reference}' is ambiguous: {options}")


def resolve_adr(reference: str) -> AdrDocument:
    """Resolve an ADR reference: exact, unique prefix or ``latest``.

    Raises:
        AdrError: If the reference is empty, unknown, ambiguous, or if no ADR
            is stored.
    """
    token = reference.strip()
    if not token:
        raise AdrError("Empty reference; use 'lion adr list' for valid references.")
    documents = list_adrs()
    if not documents:
        raise AdrError("No ADRs stored. Run 'lion adr add'.")
    lowered = token.lower()
    if lowered in _LATEST_ALIASES:
        return documents[-1]
    exact = [document for document in documents if document.ref.lower() == lowered]
    if len(exact) == 1:
        return exact[0]
    if exact:
        raise _ambiguous(token, exact)
    matches = [document for document in documents if document.ref.lower().startswith(lowered)]
    if len(matches) == 1:
        return matches[0]
    if matches:
        raise _ambiguous(token, matches)
    options = ", ".join(document.ref for document in documents)
    raise AdrError(f"Unknown reference '{token}'. Valid: {options}")


def _render(
    *,
    ref: str,
    created_at: str,
    edited_at: str,
    recording_ref: str,
    fields: Mapping[str, str],
) -> str:
    """Render one ADR document as TOML text."""
    data: dict[str, object] = {
        "schema_version": ADR_SCHEMA_VERSION,
        "id": ref,
        "created_at": created_at,
    }
    if edited_at:
        data["edited_at"] = edited_at
    if recording_ref:
        data["recording"] = recording_ref
    data["fields"] = annotations.order_fields(fields)
    return tomli_w.dumps(data)


def _instant(now: datetime | None) -> datetime:
    return now if now is not None else datetime.now(UTC)


def create_adr(fields: Mapping[str, str], recording_ref: str = "", *, now: datetime | None = None) -> AdrDocument:
    """Create and publish a new standalone ADR, never overwriting one.

    The id is ``adr-`` plus the compact UTC timestamp of ``now``; a collision
    adds a ``~NNNN`` suffix. The document is staged (mode ``600``) and published
    with ``os.link``.

    Args:
        fields: The field set; at least one non-empty field is required.
        recording_ref: Optional recording reference the decision belongs to.
        now: The creation instant; defaults to the current UTC time.

    Returns:
        The freshly published ADR.

    Raises:
        AdrError: If no field is given or the store cannot be published.
    """
    ordered = annotations.order_fields(fields)
    if not ordered:
        raise AdrError("An ADR needs at least one field; pass --title, --decision or --field NAME=VALUE.")
    directory = get_adrs_dir()
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    instant = _instant(now).astimezone(UTC)
    stamp = instant.strftime(COMPACT_TIMESTAMP_FORMAT)
    created_at = instant.isoformat()
    counter = 0
    while True:
        suffix = "" if counter == 0 else f"~{counter:04d}"
        ref = f"{ADR_PREFIX}{stamp}{suffix}"
        target = directory / f"{ref}.toml"
        temporary = recording.stage_text(
            directory,
            _render(ref=ref, created_at=created_at, edited_at="", recording_ref=recording_ref, fields=ordered),
        )
        try:
            os.link(temporary, target)
        except FileExistsError:
            counter += 1
            continue
        finally:
            temporary.unlink(missing_ok=True)
        return load_adr(target)


def write_adr(
    document: AdrDocument,
    *,
    fields: Mapping[str, str],
    recording_ref: str,
    edited_at: str,
) -> AdrDocument:
    """Atomically replace one ADR document in place (mode ``600``).

    The id and ``created_at`` stay; ``fields``, ``recording`` and ``edited_at``
    are written as given. A failure leaves no partial file behind.
    """
    ordered = annotations.order_fields(fields)
    if not ordered:
        raise AdrError("An ADR needs at least one field.")
    content = _render(
        ref=document.ref,
        created_at=document.created_at,
        edited_at=edited_at,
        recording_ref=recording_ref,
        fields=ordered,
    )
    temporary = recording.stage_text(document.path.parent, content)
    try:
        os.replace(temporary, document.path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return load_adr(document.path)


def update_adr(
    document: AdrDocument,
    fields: Mapping[str, str],
    *,
    recording_ref: str | None = None,
    now: datetime | None = None,
) -> AdrDocument:
    """Overwrite and add fields in place, stamping ``edited_at``.

    There is no delete operation: a field that is not given keeps its value. A
    genuinely empty field set is rejected loudly. ``recording_ref`` updates the
    recording reference; ``None`` keeps the current one.

    Args:
        document: The ADR to update.
        fields: The fields to overwrite or add; must be non-empty.
        recording_ref: A new recording reference, or ``None`` to keep it.
        now: The edit instant; defaults to the current UTC time.
    """
    if not fields:
        raise AdrError("An ADR needs at least one field; nothing to update.")
    merged = dict(document.fields)
    merged.update(fields)
    edited_at = _instant(now).astimezone(UTC).isoformat()
    value = document.recording if recording_ref is None else recording_ref
    return write_adr(document, fields=merged, recording_ref=value, edited_at=edited_at)


def _toml_field(name: str, value: str) -> str:
    """Render one field as a TOML line with proper key/value escaping."""
    return tomli_w.dumps({name: value}).strip()


def document_template(document: AdrDocument) -> str:
    """Return the editable document template shown by ``lion adr edit``.

    Current fields appear as active lines in catalog order (extras after them);
    unset catalog fields appear as comments. Saving overwrites and adds fields;
    LION never deletes a field.
    """
    lines = [
        "# LION ADR — edit the [fields] table and save.",
        "# Saving overwrites and adds fields; LION never deletes an ADR or a field.",
        "# Catalog: " + ", ".join(annotations.CATALOG) + ".",
        "# status: " + " | ".join(annotations.STATUS_VALUES) + ".",
        f"schema_version = {ADR_SCHEMA_VERSION}",
        f'id = "{document.ref}"',
        f'created_at = "{document.created_at}"',
    ]
    if document.recording:
        lines.append(_toml_field("recording", document.recording))
    else:
        lines.append('# recording = ""')
    lines.append("")
    lines.append("[fields]")
    current = dict(document.fields)
    for name in annotations.CATALOG:
        if name in current:
            lines.append(_toml_field(name, current[name]))
        else:
            lines.append(f'# {name} = ""')
    extras = {name: value for name, value in current.items() if name not in annotations.CATALOG}
    if extras:
        lines.append("")
        lines.append("# Additional fields:")
        for name, value in extras.items():
            lines.append(_toml_field(name, value))
    return "\n".join(lines) + "\n"


def _resolve_token(token: str, adr_refs: Sequence[str], recording_refs: Sequence[str]) -> str | None:
    """Resolve one token: exact first, then unique prefix, ADRs before recordings."""
    lowered = token.lower()
    for refs in (adr_refs, recording_refs):
        exact = [ref for ref in refs if ref.lower() == lowered]
        if len(exact) == 1:
            return exact[0]
        if exact:
            return None
    for refs in (adr_refs, recording_refs):
        matches = [ref for ref in refs if ref.lower().startswith(lowered)]
        if len(matches) == 1:
            return matches[0]
        if matches:
            return None
    return None


def resolve_ref_list(
    value: str,
    adr_refs: Sequence[str],
    recording_refs: Sequence[str],
) -> tuple[ResolvedRef, ...]:
    """Resolve ``uses``/``supersedes`` tokens against ADRs, then recordings.

    Matching is exact or unique-prefix, case-insensitively. An unresolved token
    displays as ``(unknown)``; it is never an error.
    """
    return tuple(
        ResolvedRef(raw=token, resolved=_resolve_token(token, adr_refs, recording_refs))
        for token in annotations.parse_ref_list(value)
    )


def adr_checks(_ctx: DoctorContext) -> list[Finding]:
    """Check the standalone ADR store without writing (doctor topic ``adr``).

    A missing directory is ``ok`` with an empty store. A path that is not a
    directory, an unreadable store or an unexpected entry is an ``error`` naming
    the path. A broken or invalid document is an ``error`` and is never skipped.
    A ``recording`` reference that matches no recording is a ``warn``.
    """
    directory = get_adrs_dir()
    if not directory.exists() and not directory.is_symlink():
        return [
            Finding(
                topic="adr",
                name="adr.storage",
                status=CheckStatus.OK,
                message="No ADRs present.",
                hint="Optional: 'lion adr add' documents a decision.",
            )
        ]
    if directory.is_symlink() or not directory.is_dir():
        return [
            Finding(
                topic="adr",
                name="adr.storage",
                status=CheckStatus.ERROR,
                message=f"ADR store is not a directory: {directory}",
                hint="Inspect $XDG_DATA_HOME/lion/adrs manually.",
            )
        ]
    try:
        paths = _adr_paths()
    except AdrError as exc:
        return [
            Finding(
                topic="adr",
                name="adr.storage",
                status=CheckStatus.ERROR,
                message=str(exc),
                hint="Inspect $XDG_DATA_HOME/lion/adrs manually.",
            )
        ]
    findings: list[Finding] = []
    documents: list[AdrDocument] = []
    for path in paths:
        try:
            documents.append(load_adr(path))
        except AdrError as exc:
            findings.append(
                Finding(
                    topic="adr",
                    name=f"adr.{path.stem}",
                    status=CheckStatus.ERROR,
                    message=str(exc),
                    hint="Fix or remove the document; LION never skips it.",
                )
            )
    try:
        recording_refs: set[str] | None = {record.ref for record in recording.list_recordings()}
    except recording.RecordingError:
        recording_refs = None
    for document in documents:
        if document.recording and recording_refs is not None and document.recording not in recording_refs:
            findings.append(
                Finding(
                    topic="adr",
                    name=f"adr.{document.ref}",
                    status=CheckStatus.WARN,
                    message=f"References unknown recording '{document.recording}'.",
                    hint="Review the 'recording' field of the ADR.",
                )
            )
    if not findings:
        if documents:
            findings.append(
                Finding(
                    topic="adr",
                    name="adr.storage",
                    status=CheckStatus.OK,
                    message=f"{len(documents)} ADR(s) present.",
                )
            )
        else:
            findings.append(
                Finding(
                    topic="adr",
                    name="adr.storage",
                    status=CheckStatus.OK,
                    message="No ADRs present.",
                )
            )
    return findings
