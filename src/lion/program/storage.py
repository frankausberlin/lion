"""Persist and load the LION state history.

``scan`` writes here: it creates a new entry, confirms the latest entry, or
appends a new distinct state. ``status`` never writes and only reads.
"""

import fcntl
import os
import tempfile
import tomllib
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Literal, cast

import tomli_w

from lion.program.checks import CheckStatus, DoctorContext, Finding
from lion.state.model import Snapshot

Event = Literal["created", "confirmed", "appended"]

#: The compact, file-safe form of an ``created_at``. Used both to name new entries
#: and to resolve references, so the two can never drift apart.
COMPACT_TIMESTAMP_FORMAT = "%Y-%m-%dT%H-%M-%S.%fZ"


def get_data_dir() -> Path:
    """Return the LION data directory."""
    base = Path(
        os.environ.get(
            "XDG_DATA_HOME",
            Path.home() / ".local" / "share",
        )
    )
    return base / "lion"


def get_history_dir() -> Path:
    """Return the LION state history directory."""
    return get_data_dir() / "history"


def get_recos_dir() -> Path:
    """Return the LION recommendation-script directory."""
    return get_data_dir() / "recos"


class HistoryError(ValueError):
    """A stored state cannot be read or validated."""


@dataclass(frozen=True)
class SaveOutcome:
    """Result of persisting a freshly collected state."""

    event: Event
    path: Path
    snapshot: Snapshot


@dataclass(frozen=True)
class Entry:
    """One validated history entry with its stable compact reference.

    ``ref`` is the file name without the ``.toml`` suffix, i.e. the ``created_at``
    in compact form (plus a ``~NNNN`` collision suffix when present). It never
    changes once published, so it is the reference humans type for ``lion diff``.
    """

    ref: str
    path: Path
    snapshot: Snapshot


def _entry_paths() -> list[Path]:
    """Return the history entry paths in a stable order."""
    history_dir = get_history_dir()
    if not history_dir.exists():
        return []
    return sorted(history_dir.glob("*.toml"))


def _parse(path: Path) -> dict[str, object]:
    """Parse one history entry, naming its path on failure."""
    try:
        with path.open("rb") as file:
            return cast("dict[str, object]", tomllib.load(file))
    except (OSError, ValueError) as exc:
        raise HistoryError(f"Cannot load state '{path}': {exc}") from exc


def _load_entry(path: Path) -> Snapshot:
    """Parse and strictly validate one entry, naming its path on failure."""
    try:
        return Snapshot.from_toml_dict(_parse(path))
    except (OSError, ValueError, KeyError) as exc:
        raise HistoryError(f"Cannot load state '{path}': {exc}") from exc


def _selection_key(path: Path, stamp: object) -> tuple[datetime, str]:
    try:
        if not isinstance(stamp, str):
            raise ValueError("confirmed_at must be a string")
        instant = datetime.fromisoformat(stamp)
        if instant.utcoffset() is None:
            raise ValueError("confirmed_at must include a timezone offset")
    except ValueError as exc:
        raise HistoryError(f"Cannot load state '{path}': {exc}") from exc
    return instant, path.name


def list_entries() -> list[Entry]:
    """Load and validate every entry, oldest first by confirmation time.

    Invalid entries fail explicitly: silently skipping them could hide the
    newest state. Equal ``confirmed_at`` values are resolved by filename,
    matching :func:`load_latest` and the ``scan`` head selection.
    """
    entries = [Entry(ref=path.stem, path=path, snapshot=_load_entry(path)) for path in _entry_paths()]
    entries.sort(key=lambda entry: _selection_key(entry.path, entry.snapshot.confirmed_at))
    return entries


def load_latest() -> Snapshot | None:
    """Load and validate every entry, returning the newest by confirmation time."""
    entries = list_entries()
    return entries[-1].snapshot if entries else None


_LATEST_ALIASES = frozenset({"latest", "head"})
_PREVIOUS_ALIASES = frozenset({"previous", "prev"})


def _compact_timestamp(value: str) -> str | None:
    """Return the compact (file-name) form of an ISO timestamp, or ``None``."""
    try:
        instant = datetime.fromisoformat(value)
    except ValueError:
        return None
    if instant.utcoffset() is None:
        return None
    return instant.astimezone(UTC).strftime(COMPACT_TIMESTAMP_FORMAT).lower()


def _reference_tokens(entry: Entry) -> set[str]:
    """Return every accepted spelling of one entry's reference, lowercased.

    The compact file-name form is primary; the raw ``created_at`` and its compact
    equivalent make copy-paste from ``lion history`` and ISO input work. ``Z``
    and ``+00:00`` are treated as the same timezone.
    """
    tokens = {entry.ref, entry.snapshot.created_at}
    compact = _compact_timestamp(entry.snapshot.created_at)
    if compact is not None:
        tokens.add(compact)
    normalized: set[str] = set()
    for token in tokens:
        lowered = token.lower()
        normalized.add(lowered)
        normalized.add(lowered.replace("+00:00", "z"))
    return normalized


def _input_variants(lowered: str) -> set[str]:
    """Return the lowercased input plus its UTC ``Z`` variant."""
    return {lowered, lowered.replace("+00:00", "z")}


def _match_exact(entries: list[Entry], variants: set[str]) -> list[Entry]:
    """Return the entries whose reference equals one of the input variants."""
    return [entry for entry in entries if variants & _reference_tokens(entry)]


def _match_prefix(entries: list[Entry], variants: set[str]) -> list[Entry]:
    """Return the entries whose reference starts with one of the input variants."""
    return [
        entry
        for entry in entries
        if any(token.startswith(variant) for variant in variants for token in _reference_tokens(entry))
    ]


def _match_instant(entries: list[Entry], reference: str) -> list[Entry]:
    """Return the entries whose ``created_at`` is within one second of an ISO input."""
    try:
        parsed = datetime.fromisoformat(reference)
    except ValueError:
        return []
    if parsed.utcoffset() is None:
        return []
    matches: list[Entry] = []
    for entry in entries:
        try:
            instant = datetime.fromisoformat(entry.snapshot.created_at)
        except ValueError:
            continue
        if instant.utcoffset() is not None and abs((instant - parsed).total_seconds()) < 1:
            matches.append(entry)
    return matches


def _ambiguous(reference: str, matches: list[Entry]) -> HistoryError:
    options = ", ".join(entry.ref for entry in matches)
    return HistoryError(f"Reference '{reference}' is ambiguous: {options}")


def _unknown(reference: str, entries: list[Entry]) -> HistoryError:
    options = ", ".join(entry.ref for entry in entries)
    return HistoryError(f"Unknown reference '{reference}'. Valid: {options}")


def _resolve_fuzzy(entries: list[Entry], token: str, lowered: str, reference: str) -> Entry:
    """Resolve a non-alias, non-index reference to a unique entry.

    An exact reference is resolved before derived tokens: for a same-instant
    collision pair ``X``/``X~0001`` the compact token derived from the shared
    ``created_at`` equals ``X``, which would otherwise make ``X`` ambiguous.

    Raises:
        HistoryError: If the reference is missing or ambiguous.
    """
    exact_ref = [entry for entry in entries if entry.ref.lower() == lowered]
    if len(exact_ref) == 1:
        return exact_ref[0]
    if exact_ref:
        raise _ambiguous(reference, exact_ref)
    for matches in (
        _match_exact(entries, _input_variants(lowered)),
        _match_prefix(entries, _input_variants(lowered)),
        _match_instant(entries, token),
    ):
        if len(matches) == 1:
            return matches[0]
        if matches:
            raise _ambiguous(reference, matches)
    raise _unknown(reference, entries)


def resolve(reference: str) -> Entry:
    """Resolve a compact reference, index, alias, prefix or ISO timestamp.

    Accepted, in order: ``latest``/``head``, ``previous``/``prev``, a 1-based
    index from ``lion history`` (1 = oldest), an exact reference, a unique
    prefix, then a unique ``created_at`` within one second.

    Args:
        reference: The user-supplied reference.

    Returns:
        The matching, validated entry.

    Raises:
        HistoryError: If the history is empty, or the reference is missing,
            ambiguous, or out of range.
    """
    entries = list_entries()
    if not entries:
        raise HistoryError("No state stored. Run 'lion scan'.")
    token = reference.strip()
    if token.endswith(".toml"):
        token = token[: -len(".toml")]
    token = token.strip()
    if not token:
        raise HistoryError("Empty reference; use 'lion history' for valid references.")
    lowered = token.lower()
    if lowered in _LATEST_ALIASES:
        return entries[-1]
    if lowered in _PREVIOUS_ALIASES:
        if len(entries) < 2:
            raise HistoryError("No previous state stored.")
        return entries[-2]
    if lowered.isdigit():
        index = int(lowered)
        if not 1 <= index <= len(entries):
            raise HistoryError(f"Index {index} is outside 1..{len(entries)}.")
        return entries[index - 1]
    return _resolve_fuzzy(entries, token, lowered, reference)


def _load_head_for_write() -> tuple[Path, Snapshot] | None:
    """Select the newest entry for writing, validating only the head.

    ``save_state`` does not need the full history: it only compares against and
    possibly refreshes the newest entry. Every entry is still parsed (so an
    unreadable or syntactically invalid file fails with its path), but the
    confirmation timestamps are checked for ordering and recursive collector
    validation runs only for the selected head.
    """
    candidates: list[tuple[tuple[datetime, str], Path]] = []
    for path in _entry_paths():
        stamp = _parse(path).get("confirmed_at")
        candidates.append((_selection_key(path, stamp), path))
    if not candidates:
        return None
    _stamp, path = max(candidates, key=lambda item: item[0])
    return path, _load_entry(path)


def _stage(directory: Path, snapshot: Snapshot) -> Path:
    """Write and fsync a complete entry to a temporary file in ``directory``."""
    directory.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(mode="w", encoding="utf-8", dir=directory, suffix=".tmp", delete=False) as file:
        temporary = Path(file.name)
        try:
            file.write(tomli_w.dumps(snapshot.to_toml_dict()))
            file.flush()
            os.fsync(file.fileno())
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    return temporary


def _link_new(directory: Path, temporary: Path, stamp: datetime, extension: str) -> Path:
    """Publish ``temporary`` via a hard link, never overwriting a file.

    A collision adds a ``~``/``NNNN`` suffix. ``~`` sorts after ``.`` (the start
    of ``.toml``), so a suffixed name orders after the base name and the
    newest-entry tie-break holds.
    """
    counter = 0
    while True:
        suffix = "" if counter == 0 else f"~{counter:04d}"
        path = directory / f"{stamp.strftime(COMPACT_TIMESTAMP_FORMAT)}{suffix}{extension}"
        try:
            os.link(temporary, path)
        except FileExistsError:
            counter += 1
            continue
        return path


def _write_new_file(history_dir: Path, snapshot: Snapshot) -> Path:
    """Publish a completed entry via a hard link, never overwriting a file."""
    temporary = _stage(history_dir, snapshot)
    try:
        stamp = datetime.fromisoformat(snapshot.created_at).astimezone(UTC)
        return _link_new(history_dir, temporary, stamp, ".toml")
    finally:
        temporary.unlink(missing_ok=True)


def _update_head(path: Path, snapshot: Snapshot) -> None:
    """Atomically replace the confirmed head entry (the single allowed mutation)."""
    temporary = _stage(path.parent, snapshot)
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def save_state(collectors: dict[str, dict[str, object]]) -> SaveOutcome:
    """Persist a freshly collected state following the history comparison model.

    Args:
        collectors: Serialized collector sections from ``collect_state``.

    Returns:
        The event, the affected path, and the stored snapshot.
    """
    data_dir = get_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    # Keep this inode permanently: unlinking a lock file can split waiting
    # writers across different locks. Closing the handle releases the lock,
    # including on exceptions. Readers remain read-only and never acquire it.
    with (data_dir / ".history.lock").open("a") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        now = datetime.now(UTC).isoformat()
        latest = _load_head_for_write()
        if latest is not None:
            path, previous = latest
            if datetime.fromisoformat(now) < datetime.fromisoformat(previous.confirmed_at):
                raise HistoryError(
                    "System clock is earlier than the latest confirmation; history unchanged. "
                    "Correct the clock before scanning again."
                )
            if previous.matches(collectors):
                snapshot = replace(previous, confirmed_at=now)
                _update_head(path, snapshot)
                return SaveOutcome(event="confirmed", path=path, snapshot=snapshot)
        snapshot = Snapshot(created_at=now, confirmed_at=now, collectors=collectors)
        path = _write_new_file(get_history_dir(), snapshot)
        event: Event = "created" if latest is None else "appended"
        return SaveOutcome(event=event, path=path, snapshot=snapshot)


@dataclass(frozen=True)
class HistoryInspection:
    """The outcome of inspecting one history entry without raising."""

    path: Path
    snapshot: Snapshot | None = None
    error: str = ""


def inspect_history() -> list[HistoryInspection]:
    """Inspect every history entry without aborting on the first bad one.

    Unlike :func:`list_entries` and :func:`load_latest`, this never raises for a
    single damaged entry: each file is reported with its own status so ``doctor``
    can list every problem instead of stopping at the first.
    """
    results: list[HistoryInspection] = []
    for path in _entry_paths():
        try:
            results.append(HistoryInspection(path=path, snapshot=_load_entry(path)))
        except (OSError, ValueError) as exc:
            results.append(HistoryInspection(path=path, error=str(exc)))
    return results


def _history_order_findings(valid: list[tuple[str, Snapshot]]) -> list[Finding]:
    """Warn when confirmation times are not non-decreasing by file name."""
    previous: datetime | None = None
    for _name, snapshot in sorted(valid, key=lambda item: item[0]):
        instant = datetime.fromisoformat(snapshot.confirmed_at)
        if previous is not None and instant < previous:
            return [
                Finding(
                    topic="history",
                    name="history.order",
                    status=CheckStatus.WARN,
                    message="Confirmation times are not monotonic with file order.",
                    hint="The newest selection can be wrong; check the timestamps.",
                )
            ]
        previous = instant
    return []


def history_checks(_ctx: DoctorContext) -> list[Finding]:
    """Check history integrity and legacy layout without raising."""
    findings: list[Finding] = []
    inspections = inspect_history()
    if not inspections:
        findings.append(
            Finding(
                topic="history",
                name="history.entries",
                status=CheckStatus.SKIP,
                message="No history present.",
                hint="Optional: 'lion scan' captures the first state.",
            )
        )
    else:
        valid: list[tuple[str, Snapshot]] = []
        for inspection in inspections:
            snapshot = inspection.snapshot
            if snapshot is None:
                findings.append(
                    Finding(
                        topic="history",
                        name=f"history.{inspection.path.name}",
                        status=CheckStatus.ERROR,
                        message=f"Damaged entry: {inspection.error}",
                        hint="Inspect, back up or remove the file; LION never skips it.",
                    )
                )
            else:
                valid.append((inspection.path.name, snapshot))
        if valid:
            findings.append(
                Finding(
                    topic="history",
                    name="history.entries",
                    status=CheckStatus.OK,
                    message=f"{len(valid)} valid entries.",
                )
            )
            findings.extend(_history_order_findings(valid))
    if (get_data_dir() / "scans").is_dir():
        findings.append(
            Finding(
                topic="history",
                name="history.scans",
                status=CheckStatus.WARN,
                message="Legacy directory 'scans/' present; it is no longer read.",
                hint="Remove the old files manually after reviewing them.",
            )
        )
    return findings


def _nearest_existing(path: Path) -> Path | None:
    """Return the closest existing ancestor of ``path``, or ``None``."""
    current = path
    while not current.exists():
        if current.parent == current:
            return None
        current = current.parent
    return current


def storage_checks(_ctx: DoctorContext) -> list[Finding]:
    """Check that the data directory and its subdirectories are writable.

    This is read-only: it never creates a directory or a lock file. The nearest
    existing ancestor decides whether ``history/`` and ``recos/`` could be
    created there.
    """
    findings: list[Finding] = []
    data_dir = get_data_dir()
    base = _nearest_existing(data_dir)
    if base is None or not base.is_dir() or not os.access(base, os.W_OK):
        findings.append(
            Finding(
                topic="storage",
                name="storage.data_dir",
                status=CheckStatus.ERROR,
                message=f"Data directory is not writable: {data_dir}",
                hint="Check permissions on $XDG_DATA_HOME (or the user home).",
            )
        )
        return findings
    findings.append(
        Finding(
            topic="storage",
            name="storage.data_dir",
            status=CheckStatus.OK,
            message=f"Datenverzeichnis schreibbar: {base}",
        )
    )
    for label, directory in (("history", get_history_dir()), ("recos", get_recos_dir())):
        if directory.exists():
            writable = directory.is_dir() and os.access(directory, os.W_OK)
        else:
            writable = os.access(base, os.W_OK)
        if writable:
            findings.append(
                Finding(
                    topic="storage",
                    name=f"storage.{label}",
                    status=CheckStatus.OK,
                    message=f"'{label}' is writable.",
                )
            )
        else:
            findings.append(
                Finding(
                    topic="storage",
                    name=f"storage.{label}",
                    status=CheckStatus.ERROR,
                    message=f"'{label}' is not writable: {directory}",
                    hint="Check permissions; LION never creates directories without write permission.",
                )
            )
    return findings


def publish_reco(content: str) -> Path:
    """Publish one recommendation script atomically, never overwriting one.

    The ``recos/`` directory is created on demand with mode ``700``, the script
    is written and fsynced to a temporary file with mode ``700``, and then
    published with a hard link under ``<compact-timestamp>.sh`` (plus a
    ``~NNNN`` suffix on collision). The destination is never overwritten and a
    symlink there is never followed.

    Args:
        content: The complete script text.

    Returns:
        The path of the newly published script.

    Raises:
        OSError: If the directory or the script cannot be written.
    """
    recos_dir = get_recos_dir()
    recos_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    stamp = datetime.now(UTC)
    fd, name = tempfile.mkstemp(prefix=".reco.", suffix=".tmp", dir=recos_dir)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            os.fchmod(stream.fileno(), 0o700)
            _ = stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        return _link_new(recos_dir, temporary, stamp, ".sh")
    finally:
        temporary.unlink(missing_ok=True)
