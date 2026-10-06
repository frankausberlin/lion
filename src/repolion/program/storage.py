"""Persist and load the LION state history.

``scan`` writes here: it creates a new entry, confirms the latest entry, or
appends a new distinct state. ``status`` never writes and only reads.
"""

import os
import tomllib
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Literal, cast

import tomli_w

from repolion.state.model import Snapshot, canonical_collectors

Event = Literal["created", "confirmed", "appended"]


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


def load_latest() -> Snapshot | None:
    """Load and validate every entry, returning the newest by confirmation time.

    Invalid entries fail explicitly: silently skipping them could hide the
    newest state. Equal ``zuletzt_bestaetigt`` values are resolved by filename.
    """
    entries = [(path.name, path, _load_entry(path)) for path in _entry_paths()]
    if not entries:
        return None
    return max(entries, key=lambda item: _selection_key(item[1], item[2].zuletzt_bestaetigt))[2]


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
            path = history_dir / f"{stamp.strftime('%Y-%m-%dT%H-%M-%S.%fZ')}{suffix}.toml"
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
    now = datetime.now(UTC).isoformat()
    latest = _load_head_for_write()
    if latest is not None:
        path, previous = latest
        if previous.canonical_collectors() == canonical_collectors(collectors):
            snapshot = replace(previous, zuletzt_bestaetigt=now)
            _update_head(path, snapshot)
            return SaveOutcome(event="confirmed", path=path, snapshot=snapshot)
    snapshot = Snapshot(erstscan=now, zuletzt_bestaetigt=now, collectors=collectors)
    path = _write_new_file(get_history_dir(), snapshot)
    event: Event = "created" if latest is None else "appended"
    return SaveOutcome(event=event, path=path, snapshot=snapshot)
