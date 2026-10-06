"""Persist and load the LION state history.

``scan`` writes here: it creates a new entry, confirms the latest entry, or
appends a new distinct state. ``status`` never writes and only reads.
"""

import fcntl
import os
import tomllib
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Literal, cast

import tomli_w

from repolion.state.model import Snapshot

Event = Literal["created", "confirmed", "appended"]

#: The compact, file-safe form of an ``erstscan``. Used both to name new entries
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

    ``ref`` is the file name without the ``.toml`` suffix, i.e. the ``erstscan``
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
            raise ValueError("zuletzt_bestaetigt must be a string")
        instant = datetime.fromisoformat(stamp)
        if instant.utcoffset() is None:
            raise ValueError("zuletzt_bestaetigt must include a timezone offset")
    except ValueError as exc:
        raise HistoryError(f"Cannot load state '{path}': {exc}") from exc
    return instant, path.name


def list_entries() -> list[Entry]:
    """Load and validate every entry, oldest first by confirmation time.

    Invalid entries fail explicitly: silently skipping them could hide the
    newest state. Equal ``zuletzt_bestaetigt`` values are resolved by filename,
    matching :func:`load_latest` and the ``scan`` head selection.
    """
    entries = [Entry(ref=path.stem, path=path, snapshot=_load_entry(path)) for path in _entry_paths()]
    entries.sort(key=lambda entry: _selection_key(entry.path, entry.snapshot.zuletzt_bestaetigt))
    return entries


def load_latest() -> Snapshot | None:
    """Load and validate every entry, returning the newest by confirmation time."""
    entries = list_entries()
    return entries[-1].snapshot if entries else None


_LATEST_ALIASES = frozenset({"latest", "head", "aktuell"})
_PREVIOUS_ALIASES = frozenset({"previous", "prev", "vorherig"})


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

    The compact file-name form is primary; the raw ``erstscan`` and its compact
    equivalent make copy-paste from ``lion history`` and ISO input work. ``Z``
    and ``+00:00`` are treated as the same timezone.
    """
    tokens = {entry.ref, entry.snapshot.erstscan}
    compact = _compact_timestamp(entry.snapshot.erstscan)
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
    """Return the entries whose ``erstscan`` is within one second of an ISO input."""
    try:
        parsed = datetime.fromisoformat(reference)
    except ValueError:
        return []
    if parsed.utcoffset() is None:
        return []
    matches: list[Entry] = []
    for entry in entries:
        try:
            instant = datetime.fromisoformat(entry.snapshot.erstscan)
        except ValueError:
            continue
        if instant.utcoffset() is not None and abs((instant - parsed).total_seconds()) < 1:
            matches.append(entry)
    return matches


def _ambiguous(reference: str, matches: list[Entry]) -> HistoryError:
    options = ", ".join(entry.ref for entry in matches)
    return HistoryError(f"Referenz '{reference}' ist mehrdeutig: {options}")


def _unknown(reference: str, entries: list[Entry]) -> HistoryError:
    options = ", ".join(entry.ref for entry in entries)
    return HistoryError(f"Unbekannte Referenz '{reference}'. Gültig: {options}")


def resolve(reference: str) -> Entry:
    """Resolve a compact reference, index, alias, prefix or ISO timestamp.

    Accepted, in order: ``latest``/``head``/``aktuell``, ``previous``/``prev``/
    ``vorherig``, a 1-based index from ``lion history`` (1 = oldest), an exact
    reference, a unique prefix, then a unique ``erstscan`` within one second.

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
        raise HistoryError("Kein Zustand gespeichert. Führe 'lion scan' aus.")
    token = reference.strip()
    if token.endswith(".toml"):
        token = token[: -len(".toml")]
    token = token.strip()
    if not token:
        raise HistoryError("Leere Referenz; nutze 'lion history' für gültige Referenzen.")
    lowered = token.lower()
    if lowered in _LATEST_ALIASES:
        return entries[-1]
    if lowered in _PREVIOUS_ALIASES:
        if len(entries) < 2:
            raise HistoryError("Kein vorheriger Zustand gespeichert.")
        return entries[-2]
    if lowered.isdigit():
        index = int(lowered)
        if not 1 <= index <= len(entries):
            raise HistoryError(f"Index {index} liegt außerhalb von 1..{len(entries)}.")
        return entries[index - 1]
    # Resolve an exact reference before derived tokens: for a same-instant
    # collision pair ``X``/``X~0001`` the compact token derived from the shared
    # ``erstscan`` equals ``X``, which would otherwise make ``X`` ambiguous.
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
        stamp = _parse(path).get("zuletzt_bestaetigt")
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


def _write_new_file(history_dir: Path, snapshot: Snapshot) -> Path:
    """Publish a completed entry via a hard link, never overwriting a file."""
    temporary = _stage(history_dir, snapshot)
    try:
        stamp = datetime.fromisoformat(snapshot.erstscan).astimezone(UTC)
        counter = 0
        while True:
            # "~" sorts after "." (the start of ".toml"), so a collision-suffixed
            # name orders after the base name and the newest-entry tie-break holds.
            suffix = "" if counter == 0 else f"~{counter:04d}"
            path = history_dir / f"{stamp.strftime(COMPACT_TIMESTAMP_FORMAT)}{suffix}.toml"
            try:
                os.link(temporary, path)
            except FileExistsError:
                counter += 1
                continue
            return path
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
            if previous.matches(collectors):
                snapshot = replace(previous, zuletzt_bestaetigt=now)
                _update_head(path, snapshot)
                return SaveOutcome(event="confirmed", path=path, snapshot=snapshot)
        snapshot = Snapshot(erstscan=now, zuletzt_bestaetigt=now, collectors=collectors)
        path = _write_new_file(get_history_dir(), snapshot)
        event: Event = "created" if latest is None else "appended"
        return SaveOutcome(event=event, path=path, snapshot=snapshot)
