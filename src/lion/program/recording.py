"""Record shell sessions: recording data space, lifecycle and liveness.

A recording lives under ``$XDG_DATA_HOME/lion/recordings/`` and is separate from
the scan history. At most one recording is active per user and machine; the
``active`` pointer names it while it is open. Starting and stopping are the only
writers here — every read function never writes.

Shell segments are stamped with the kernel ``boot_id`` and the writing process's
PID and start ticks. Whether a segment is still alive is decided at read time;
a missing ``exit`` event is never treated as success (ADR 0012).
"""

import os
import shutil
import tempfile
import tomllib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import tomli_w

from lion import __version__
from lion.program.checks import CheckStatus, DoctorContext, Finding
from lion.program.storage import COMPACT_TIMESTAMP_FORMAT, get_data_dir
from lion.state.model import Snapshot

#: Schema version of ``meta.toml``.
META_SCHEMA_VERSION = 1

#: Recording statuses stored in ``meta.toml``.
STATUS_OPEN = "open"
STATUS_STOPPED = "stopped"

#: The event-line cap in characters, mirrored by the zsh integration.
EVENT_LINE_CAP = 4096

#: The kernel boot-id path used for segment liveness.
BOOT_ID_PATH = Path("/proc/sys/kernel/random/boot_id")

_LATEST_ALIASES = frozenset({"latest", "head"})
_POINTER_NAME = "active"


class RecordingError(ValueError):
    """A recording cannot be read, validated or mutated."""


def get_recordings_dir() -> Path:
    """Return the LION recordings directory."""
    return get_data_dir() / "recordings"


def get_active_pointer() -> Path:
    """Return the path of the ``active`` pointer file."""
    return get_recordings_dir() / _POINTER_NAME


@dataclass(frozen=True)
class RecordingMeta:
    """The validated metadata of one recording (``meta.toml``)."""

    schema_version: int
    ref: str
    status: str
    started_at: str
    stopped_at: str = ""
    note: str = ""
    integration_version: str = ""

    def to_toml_dict(self) -> dict[str, object]:
        """Return the TOML representation with scalars before optional keys."""
        data: dict[str, object] = {
            "schema_version": self.schema_version,
            "ref": self.ref,
            "status": self.status,
            "started_at": self.started_at,
            "integration_version": self.integration_version,
            "note": self.note,
        }
        if self.stopped_at:
            data["stopped_at"] = self.stopped_at
        return data

    @classmethod
    def from_toml_dict(cls, data: Mapping[str, object]) -> "RecordingMeta":
        """Validate and build metadata from parsed TOML data.

        Raises:
            ValueError: If a required field is missing, mistyped or inconsistent.
        """
        version = data.get("schema_version")
        if not isinstance(version, int) or isinstance(version, bool) or version != META_SCHEMA_VERSION:
            raise ValueError(f"unsupported schema_version: {version!r}")
        ref = _require_str(data, "ref")
        if not ref:
            raise ValueError("ref must not be empty")
        status = _require_str(data, "status")
        if status not in (STATUS_OPEN, STATUS_STOPPED):
            raise ValueError(f"unknown status: {status!r}")
        started_at = _require_str(data, "started_at")
        stopped_at = _require_str(data, "stopped_at") if "stopped_at" in data else ""
        if stopped_at:
            _validate_timestamp(stopped_at, "stopped_at")
        if status == STATUS_STOPPED and not stopped_at:
            raise ValueError("stopped recording requires stopped_at")
        if status == STATUS_OPEN and stopped_at:
            raise ValueError("open recording must not carry stopped_at")
        _validate_timestamp(started_at, "started_at")
        return cls(
            schema_version=version,
            ref=ref,
            status=status,
            started_at=started_at,
            stopped_at=stopped_at,
            note=_require_str(data, "note") if "note" in data else "",
            integration_version=_require_str(data, "integration_version") if "integration_version" in data else "",
        )


@dataclass(frozen=True)
class SpoolEvent:
    """One decoded event line from a segment spool file.

    ``time`` is the event's ``EPOCHREALTIME`` in seconds (for the ``segment``
    header it is the segment start). Only the fields that belong to the kind are
    populated; ``text`` and ``cwd`` are already decoded from zsh quoting.
    """

    kind: str
    segment: str
    line: int
    time: float
    cwd: str = ""
    text: str = ""
    status: int | None = None
    t0: float | None = None
    t1: float | None = None
    boot_id: str = ""
    pid: int | None = None
    start_ticks: int | None = None

    @property
    def executed_seconds(self) -> float | None:
        """Return the duration of a ``result`` event, or ``None`` for others."""
        if self.t0 is None or self.t1 is None:
            return None
        return max(0.0, self.t1 - self.t0)


@dataclass(frozen=True)
class SegmentInfo:
    """One shell segment with its derived liveness and event statistics."""

    name: str
    path: Path
    boot_id: str
    pid: int
    start_ticks: int
    started_at: str
    epoch_start: float
    alive: bool
    end_reason: str
    commands: int
    results: int
    comments: int
    exits: int
    executed_seconds: float
    last_exit_status: int | None
    size_bytes: int


@dataclass(frozen=True)
class Recording:
    """One validated recording with its start/end states and segments."""

    ref: str
    path: Path
    meta: RecordingMeta
    state_start: Snapshot
    state_end: Snapshot | None
    segments: tuple[SegmentInfo, ...] = ()

    @property
    def active(self) -> bool:
        """Return whether the recording is still open."""
        return self.meta.status == STATUS_OPEN


def _require_str(data: Mapping[str, object], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string")
    return value


def _validate_timestamp(value: str, key: str) -> None:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{key} is not a valid timestamp: {value!r}") from exc
    if parsed.utcoffset() is None:
        raise ValueError(f"{key} must include a timezone offset")


def _parse_instant(value: str, path: Path, key: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise RecordingError(f"Cannot load recording '{path}': invalid {key}: {value!r}") from exc
    return parsed


def _epoch_instant(value: float, path: Path) -> datetime:
    try:
        return datetime.fromtimestamp(value, tz=UTC)
    except (OverflowError, OSError, ValueError) as exc:
        raise RecordingError(f"Cannot read segment '{path}': invalid epoch time {value!r}") from exc


def _load_toml(path: Path) -> dict[str, object]:
    try:
        with path.open("rb") as stream:
            return cast("dict[str, object]", tomllib.load(stream))
    except (OSError, ValueError) as exc:
        raise RecordingError(f"Cannot load recording '{path}': {exc}") from exc


def _load_meta(path: Path) -> RecordingMeta:
    data = _load_toml(path)
    try:
        return RecordingMeta.from_toml_dict(data)
    except (ValueError, KeyError) as exc:
        raise RecordingError(f"Cannot load recording metadata '{path}': {exc}") from exc


def _load_snapshot(path: Path) -> Snapshot:
    data = _load_toml(path)
    try:
        return Snapshot.from_toml_dict(data)
    except (ValueError, KeyError) as exc:
        raise RecordingError(f"Cannot load recording '{path}': {exc}") from exc


def unquote_zsh(text: str) -> str:
    r"""Decode the ``${(q)}`` quoted form of one zsh value.

    zsh emits backslash-escaped characters for shell metacharacters and
    ``$'...'`` groups for control characters (``$'\\n'``, ``$'\\t'``, octal
    ``$'\\033'``). A truncated line can end in an incomplete escape; an
    unterminated backslash or ``$'...'`` group is returned literally, so a
    hard-capped event never breaks the reader.

    Args:
        text: The quoted field as written by the zsh hook.

    Returns:
        The decoded value.
    """
    if text == "''":
        return ""
    result: list[str] = []
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if char == "\\":
            if index + 1 < length:
                result.append(text[index + 1])
                index += 2
            else:
                result.append("\\")
                index += 1
        elif char == "$" and index + 1 < length and text[index + 1] == "'":
            end = _closing_quote(text, index + 2)
            if end == -1:
                result.append(text[index:])
                index = length
            else:
                result.append(_decode_dollar_quote(text[index + 2 : end]))
                index = end + 1
        else:
            result.append(char)
            index += 1
    return "".join(result)


def _closing_quote(text: str, start: int) -> int:
    """Return the index of the ``$'...'`` closing quote, or ``-1``."""
    index = start
    while index < len(text):
        if text[index] == "\\":
            index += 2
            continue
        if text[index] == "'":
            return index
        index += 1
    return -1


_DOLLAR_ESCAPES = {
    "n": "\n",
    "t": "\t",
    "r": "\r",
    "a": "\a",
    "b": "\b",
    "f": "\f",
    "v": "\v",
    "e": "\x1b",
    "\\": "\\",
    "'": "'",
    '"': '"',
}


def _decode_dollar_quote(body: str) -> str:
    """Decode the inner text of one ``$'...'`` group."""
    result: list[str] = []
    index = 0
    while index < len(body):
        char = body[index]
        if char != "\\":
            result.append(char)
            index += 1
            continue
        index += 1
        if index >= len(body):
            result.append("\\")
            break
        escaped = body[index]
        if escaped in _DOLLAR_ESCAPES:
            result.append(_DOLLAR_ESCAPES[escaped])
            index += 1
            continue
        if escaped.isdigit():
            digits = ""
            while index < len(body) and len(digits) < 3 and body[index].isdigit():
                digits += body[index]
                index += 1
            result.append(chr(int(digits, 8)))
            continue
        if escaped in "xuU":
            width = {"x": 2, "u": 4, "U": 8}[escaped]
            index += 1
            digits = body[index : index + width]
            if len(digits) == width and all(char in "0123456789abcdefABCDEF" for char in digits):
                result.append(chr(int(digits, 16)))
                index += width
                continue
            result.append("\\" + escaped)
            continue
        result.append(escaped)
        index += 1
    return "".join(result)


def _event(kind: str, segment: str, line: int, fields: list[str], path: Path) -> SpoolEvent:
    """Build one typed event from its decoded field list, or fail loudly."""

    def bad(detail: str) -> RecordingError:
        return RecordingError(f"Cannot read segment '{path}' line {line}: {detail}")

    def as_float(value: str, label: str) -> float:
        try:
            return float(value)
        except ValueError as exc:
            raise bad(f"{label} is not a number: {value!r}") from exc

    def as_int(value: str, label: str) -> int:
        try:
            return int(value)
        except ValueError as exc:
            raise bad(f"{label} is not an integer: {value!r}") from exc

    if kind == "segment":
        if len(fields) != 4:
            raise bad("segment header must carry boot_id, pid, start ticks and start time")
        return SpoolEvent(
            kind=kind,
            segment=segment,
            line=line,
            time=as_float(fields[3], "start time"),
            boot_id=fields[0],
            pid=as_int(fields[1], "pid"),
            start_ticks=as_int(fields[2], "start ticks"),
        )
    if kind == "cmd":
        if len(fields) != 3:
            raise bad("cmd event must carry time, cwd and text")
        return SpoolEvent(
            kind=kind,
            segment=segment,
            line=line,
            time=as_float(fields[0], "time"),
            cwd=unquote_zsh(fields[1]),
            text=unquote_zsh(fields[2]),
        )
    if kind == "comment":
        if len(fields) != 3:
            raise bad("comment event must carry time, cwd and text")
        return SpoolEvent(
            kind=kind,
            segment=segment,
            line=line,
            time=as_float(fields[0], "time"),
            cwd=unquote_zsh(fields[1]),
            text=unquote_zsh(fields[2]),
        )
    if kind == "result":
        if len(fields) != 4:
            raise bad("result event must carry time, status, t0 and t1")
        return SpoolEvent(
            kind=kind,
            segment=segment,
            line=line,
            time=as_float(fields[0], "time"),
            status=as_int(fields[1], "status"),
            t0=as_float(fields[2], "t0"),
            t1=as_float(fields[3], "t1"),
        )
    if kind == "exit":
        if len(fields) != 2:
            raise bad("exit event must carry time and status")
        return SpoolEvent(
            kind=kind,
            segment=segment,
            line=line,
            time=as_float(fields[0], "time"),
            status=as_int(fields[1], "status"),
        )
    raise bad(f"unknown event kind: {kind!r}")


def read_events(segment: Path) -> list[SpoolEvent]:
    """Read and decode every event of one segment file, in file order.

    Args:
        segment: Path of the segment spool file.

    Returns:
        The decoded events, starting with the segment header.

    Raises:
        RecordingError: If the file is empty or a line is malformed, naming the
            file and line.
    """
    try:
        raw = segment.read_bytes().decode("utf-8", errors="replace")
    except OSError as exc:
        raise RecordingError(f"Cannot read segment '{segment}': {exc}") from exc
    lines = raw.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    if not lines or lines == [""]:
        raise RecordingError(f"Cannot read segment '{segment}': empty segment file")
    events: list[SpoolEvent] = []
    for number, line in enumerate(lines, start=1):
        fields = line.split("\t")
        events.append(_event(fields[0], segment.name, number, fields[1:], segment))
    if events[0].kind != "segment":
        raise RecordingError(f"Cannot read segment '{segment}' line 1: first line must be the segment header")
    return events


def _read_boot_id() -> str | None:
    try:
        return BOOT_ID_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        return None


def _process_start_ticks(pid: int) -> int | None:
    """Return field 22 (starttime) of ``/proc/<pid>/stat``, or ``None``."""
    try:
        data = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except OSError:
        return None
    tail = data.rsplit(")", 1)[-1].split()
    if len(tail) < 20:
        return None
    try:
        return int(tail[19])
    except ValueError:
        return None


def _liveness(boot_id: str, pid: int, start_ticks: int, boot_now: str | None) -> tuple[bool, str]:
    """Return whether a stamped segment is still alive, and the end reason.

    This judges a single process stamp only; takeover chains (``exec zsh``,
    a reloaded integration) keep the stamp and are resolved by
    :func:`segments_for`.
    """
    if boot_now is None:
        return False, "unknown"
    if boot_id != boot_now:
        return False, "reboot"
    ticks = _process_start_ticks(pid)
    if ticks is None:
        return False, "gone"
    if ticks != start_ticks:
        return False, "reused"
    return True, ""


def _segment_paths(record_dir: Path) -> list[Path]:
    events_dir = record_dir / "events"
    if not events_dir.exists():
        return []
    if not events_dir.is_dir():
        raise RecordingError(f"Cannot load recording '{events_dir}': not a directory")
    paths: list[Path] = []
    for path in sorted(events_dir.iterdir(), key=lambda item: item.name):
        if path.name.startswith("."):
            continue
        if not path.is_file() or path.is_symlink():
            raise RecordingError(f"Unexpected entry in events directory: {path}")
        paths.append(path)
    return paths


def _read_segment(path: Path, boot_now: str | None) -> SegmentInfo:
    events = read_events(path)
    header = events[0]
    commands = sum(1 for event in events if event.kind == "cmd")
    results = sum(1 for event in events if event.kind == "result")
    comments = sum(1 for event in events if event.kind == "comment")
    exits = sum(1 for event in events if event.kind == "exit")
    executed = sum(event.executed_seconds or 0.0 for event in events)
    last_exit: int | None = None
    for event in events:
        if event.kind == "exit":
            last_exit = event.status
    assert header.pid is not None and header.start_ticks is not None
    alive, reason = _liveness(header.boot_id, header.pid, header.start_ticks, boot_now)
    return SegmentInfo(
        name=path.name,
        path=path,
        boot_id=header.boot_id,
        pid=header.pid,
        start_ticks=header.start_ticks,
        started_at=_epoch_instant(header.time, path).isoformat(),
        epoch_start=header.time,
        alive=alive,
        end_reason=reason,
        commands=commands,
        results=results,
        comments=comments,
        exits=exits,
        executed_seconds=executed,
        last_exit_status=last_exit,
        size_bytes=path.stat().st_size,
    )


def segments_for(record_dir: Path, boot_now: str | None = None) -> tuple[SegmentInfo, ...]:
    """Return the segment infos of one record, ordered by segment start.

    Segments that share one process stamp ``(boot_id, pid, start_ticks)`` form a
    takeover chain: ``exec zsh`` (or a reloaded integration) keeps the process,
    so only the newest segment of such a chain can still receive events. Every
    older segment of the chain is marked ``alive=False`` with the end reason
    ``superseded`` — unconditionally, so the takeover instant stays the stable
    segment end even after the process itself ends.
    """
    if boot_now is None:
        boot_now = _read_boot_id()
    infos = [_read_segment(path, boot_now) for path in _segment_paths(record_dir)]
    infos.sort(key=lambda info: (info.epoch_start, info.name))
    newest: dict[tuple[str, int, int], int] = {}
    for index, info in enumerate(infos):
        newest[(info.boot_id, info.pid, info.start_ticks)] = index
    for index, info in enumerate(infos):
        if newest[(info.boot_id, info.pid, info.start_ticks)] != index:
            infos[index] = replace(info, alive=False, end_reason="superseded")
    return tuple(infos)


def load_recording(ref: str) -> Recording:
    """Load and validate one recording by its exact reference.

    Raises:
        RecordingError: If the directory or one of its parts is missing or invalid.
    """
    path = get_recordings_dir() / ref
    if path.is_symlink() or not path.is_dir():
        raise RecordingError(f"Cannot load recording '{path}': not a recording directory")
    meta = _load_meta(path / "meta.toml")
    if meta.ref != ref:
        raise RecordingError(f"Cannot load recording '{path}': meta ref '{meta.ref}' does not match the directory")
    state_start_path = path / "state-start.toml"
    if not state_start_path.is_file():
        raise RecordingError(f"Cannot load recording '{path}': state-start.toml is missing")
    state_start = _load_snapshot(state_start_path)
    state_end_path = path / "state-end.toml"
    state_end = _load_snapshot(state_end_path) if state_end_path.is_file() else None
    if meta.status == STATUS_STOPPED and state_end is None:
        raise RecordingError(f"Cannot load recording '{path}': stopped recording without state-end.toml")
    return Recording(
        ref=ref,
        path=path,
        meta=meta,
        state_start=state_start,
        state_end=state_end,
        segments=segments_for(path),
    )


def _record_dirs() -> list[Path]:
    recordings = get_recordings_dir()
    if not recordings.exists():
        return []
    dirs: list[Path] = []
    for path in sorted(recordings.iterdir(), key=lambda item: item.name):
        if path.name.startswith(".") or path.name == _POINTER_NAME:
            continue
        if path.is_symlink() or not path.is_dir():
            raise RecordingError(f"Unexpected entry in recordings directory: {path}")
        dirs.append(path)
    return dirs


def list_recordings() -> list[Recording]:
    """Load and validate every recording, oldest first by start time."""
    recordings = [load_recording(path.name) for path in _record_dirs()]
    recordings.sort(key=lambda recording: (recording.meta.started_at, recording.ref))
    return recordings


def _read_pointer() -> str | None:
    path = get_active_pointer()
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise RecordingError(f"Cannot read active pointer '{path}': {exc}") from exc
    stripped = text.strip()
    if not stripped or len(stripped.splitlines()) != 1:
        raise RecordingError(f"Cannot read active pointer '{path}': expected exactly one recording reference")
    return stripped


def active_recording() -> Recording | None:
    """Return the active recording, or ``None`` when no pointer exists.

    Raises:
        RecordingError: If the pointer is malformed or does not lead to an open
            recording.
    """
    ref = _read_pointer()
    if ref is None:
        return None
    recording = load_recording(ref)
    if not recording.active:
        raise RecordingError(
            f"Active pointer '{get_active_pointer()}' references the stopped recording '{ref}'; "
            "reconcile the recording space (see 'lion doctor')"
        )
    return recording


def _ambiguous(reference: str, matches: list[Recording]) -> RecordingError:
    options = ", ".join(recording.ref for recording in matches)
    return RecordingError(f"Reference '{reference}' is ambiguous: {options}")


def resolve_recording(reference: str) -> Recording:
    """Resolve a recording reference: exact, unique prefix, ``latest`` or ``active``.

    Raises:
        RecordingError: If the reference is empty, unknown, ambiguous, or if
            ``latest`` is used without a recording or ``active`` without an
            active recording.
    """
    token = reference.strip().rstrip("/")
    if not token:
        raise RecordingError("Empty reference; use 'lion watch list' for valid references.")
    lowered = token.lower()
    if lowered == "active":
        recording = active_recording()
        if recording is None:
            raise RecordingError("No active recording. Run 'lion watch start'.")
        return recording
    recordings = list_recordings()
    if not recordings:
        raise RecordingError("No recording stored. Run 'lion watch start'.")
    if lowered in _LATEST_ALIASES:
        return recordings[-1]
    exact = [recording for recording in recordings if recording.ref.lower() == lowered]
    if len(exact) == 1:
        return exact[0]
    if exact:
        raise _ambiguous(token, exact)
    matches = [recording for recording in recordings if recording.ref.lower().startswith(lowered)]
    if len(matches) == 1:
        return matches[0]
    if matches:
        raise _ambiguous(token, matches)
    options = ", ".join(recording.ref for recording in recordings)
    raise RecordingError(f"Unknown reference '{token}'. Valid: {options}")


def stage_text(directory: Path, content: str, *, prefix: str = ".lion.", suffix: str = ".tmp") -> Path:
    """Write text to a fresh temporary file in ``directory`` and fsync it.

    The temporary file is mode ``600`` and is returned for the caller to
    publish (``os.replace``/``os.link``) or remove; only a write failure is
    cleaned up here.
    """
    fd, name = tempfile.mkstemp(prefix=prefix, suffix=suffix, dir=directory, text=True)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            os.fchmod(stream.fileno(), 0o600)
            _ = stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return temporary


def _publish_new(directory: Path, content: str, target: Path) -> None:
    """Publish content via a hard link; an existing destination is a loud error."""
    temporary = stage_text(directory, content)
    try:
        os.link(temporary, target)
    except FileExistsError as exc:
        raise RecordingError(f"Refusing to overwrite existing file: {target}") from exc
    finally:
        temporary.unlink(missing_ok=True)


def _write_meta(path: Path, meta: RecordingMeta) -> None:
    """Atomically replace ``meta.toml`` (the single allowed in-place mutation)."""
    temporary = stage_text(path.parent, tomli_w.dumps(meta.to_toml_dict()))
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _create_record_dir(recordings: Path, stamp: datetime) -> Path:
    """Create a fresh record directory, adding a ``~NNNN`` suffix on collision."""
    counter = 0
    while True:
        suffix = "" if counter == 0 else f"~{counter:04d}"
        path = recordings / f"{stamp.strftime(COMPACT_TIMESTAMP_FORMAT)}{suffix}"
        try:
            path.mkdir(mode=0o700)
        except FileExistsError:
            counter += 1
            continue
        return path


def _timestamps_in(record_dir: Path) -> list[datetime]:
    """Return every known timestamp of one record for the clock guard.

    This includes the metadata timestamps, both state snapshots and every
    segment start. Reading is strict: a damaged record fails the guard instead
    of being skipped.
    """
    instants: list[datetime] = []
    meta_path = record_dir / "meta.toml"
    meta = _load_meta(meta_path)
    instants.append(_parse_instant(meta.started_at, meta_path, "started_at"))
    if meta.stopped_at:
        instants.append(_parse_instant(meta.stopped_at, meta_path, "stopped_at"))
    for name in ("state-start.toml", "state-end.toml"):
        path = record_dir / name
        if path.is_file():
            snapshot = _load_snapshot(path)
            instants.append(_parse_instant(snapshot.created_at, path, "created_at"))
            instants.append(_parse_instant(snapshot.confirmed_at, path, "confirmed_at"))
    for segment in _segment_paths(record_dir):
        header = read_events(segment)[0]
        instants.append(_epoch_instant(header.time, segment))
    return instants


def _latest_known_instant() -> datetime | None:
    """Return the latest timestamp known in the recording space, if any."""
    newest: datetime | None = None
    for record_dir in _record_dirs():
        for instant in _timestamps_in(record_dir):
            if newest is None or instant > newest:
                newest = instant
    return newest


def _latest_event_instant(record_dir: Path) -> datetime | None:
    """Return the latest event time inside one record, if any."""
    newest: datetime | None = None
    for segment in _segment_paths(record_dir):
        for event in read_events(segment):
            instant = _epoch_instant(event.time, segment)
            if newest is None or instant > newest:
                newest = instant
    return newest


def _guard_clock(now: datetime, extra: Iterable[datetime] = ()) -> None:
    """Refuse a mutation whose clock precedes the newest known timestamp."""
    candidates = list(extra)
    latest = _latest_known_instant()
    if latest is not None:
        candidates.append(latest)
    if candidates and now < max(candidates):
        raise RecordingError(
            "System clock is earlier than the latest recording timestamp; nothing changed. Correct the clock and retry."
        )


def _snapshot_for(now: datetime, collectors: Mapping[str, Mapping[str, object]]) -> Snapshot:
    stamp = now.isoformat()
    return Snapshot(
        created_at=stamp,
        confirmed_at=stamp,
        collectors={name: dict(section) for name, section in collectors.items()},
    )


def start_recording(collectors: Mapping[str, Mapping[str, object]], note: str = "") -> Recording:
    """Start a recording: create its space, publish the pointer and return it.

    Args:
        collectors: Serialized collector sections from ``collect_state``.
        note: Optional free-text note stored in ``meta.toml``.

    Returns:
        The freshly created, active recording.

    Raises:
        RecordingError: If a pointer already exists, the clock guard rejects the
            mutation, or the space cannot be published.
    """
    recordings = get_recordings_dir()
    recordings.mkdir(mode=0o700, parents=True, exist_ok=True)
    if get_active_pointer().exists() or get_active_pointer().is_symlink():
        raise RecordingError(
            f"An active recording pointer already exists: {get_active_pointer()}. "
            "Stop the recording or reconcile the space (see 'lion doctor')."
        )
    now = datetime.now(UTC)
    _guard_clock(now)
    record_dir = _create_record_dir(recordings, now)
    try:
        meta = RecordingMeta(
            schema_version=META_SCHEMA_VERSION,
            ref=record_dir.name,
            status=STATUS_OPEN,
            started_at=now.isoformat(),
            note=note,
            integration_version=__version__,
        )
        _publish_new(record_dir, tomli_w.dumps(meta.to_toml_dict()), record_dir / "meta.toml")
        snapshot = _snapshot_for(now, collectors)
        _publish_new(record_dir, tomli_w.dumps(snapshot.to_toml_dict()), record_dir / "state-start.toml")
        (record_dir / "events").mkdir(mode=0o700)
        _publish_new(recordings, record_dir.name + "\n", get_active_pointer())
    except BaseException:
        shutil.rmtree(record_dir, ignore_errors=True)
        raise
    return load_recording(record_dir.name)


def stop_recording(collectors: Mapping[str, Mapping[str, object]], note: str = "") -> Recording:
    """Stop the active recording: publish its end state and clear the pointer.

    Args:
        collectors: Serialized collector sections from ``collect_state``.
        note: Optional note; when given it replaces the start note.

    Returns:
        The stopped recording.

    Raises:
        RecordingError: If no recording is active, it is already stopped, the
            clock guard rejects the mutation, or the end state cannot be
            published.
    """
    ref = _read_pointer()
    if ref is None:
        raise RecordingError("No active recording. Run 'lion watch start' first.")
    recording = load_recording(ref)
    if not recording.active:
        raise RecordingError(f"Recording '{ref}' is already stopped; reconcile the space (see 'lion doctor').")
    now = datetime.now(UTC)
    latest_event = _latest_event_instant(recording.path)
    _guard_clock(now, extra=[latest_event] if latest_event is not None else [])
    snapshot = _snapshot_for(now, collectors)
    _publish_new(recording.path, tomli_w.dumps(snapshot.to_toml_dict()), recording.path / "state-end.toml")
    meta = replace(
        recording.meta,
        status=STATUS_STOPPED,
        stopped_at=now.isoformat(),
        note=note or recording.meta.note,
    )
    _write_meta(recording.path / "meta.toml", meta)
    try:
        get_active_pointer().unlink()
    except FileNotFoundError as exc:
        raise RecordingError(
            f"Active pointer vanished while stopping '{ref}'; reconcile the space (see 'lion doctor')."
        ) from exc
    return load_recording(ref)


def load_events(recording: Recording) -> list[SpoolEvent]:
    """Return every event of a recording, ordered chronologically.

    Args:
        recording: A loaded recording.

    Returns:
        The events of every segment ordered by time; the segment name and the
        file line break ties, so each segment header stays first within its
        segment and appears at its start instant.
    """
    events: list[SpoolEvent] = []
    for segment in recording.segments:
        events.extend(read_events(segment.path))
    events.sort(key=lambda event: (event.time, event.segment, event.line))
    return events


@dataclass(frozen=True)
class RecordingSpace:
    """A tolerant inspection of the whole recording space, for ``doctor``."""

    pointer: str | None
    error: str
    recordings: tuple[Recording | None, ...]
    errors: tuple[str, ...]


def inspect_space() -> RecordingSpace:
    """Inspect the recording space without raising."""
    try:
        pointer = _read_pointer()
    except RecordingError as exc:
        return RecordingSpace(pointer=None, error=str(exc), recordings=(), errors=())
    try:
        dirs = _record_dirs()
    except RecordingError as exc:
        return RecordingSpace(pointer=pointer, error=str(exc), recordings=(), errors=())
    recordings: list[Recording | None] = []
    errors: list[str] = []
    for record_dir in dirs:
        try:
            recordings.append(load_recording(record_dir.name))
        except RecordingError as exc:
            recordings.append(None)
            errors.append(str(exc))
    return RecordingSpace(pointer=pointer, error="", recordings=tuple(recordings), errors=tuple(errors))


def watch_checks(_ctx: DoctorContext) -> list[Finding]:
    """Check the recording space without writing (doctor topic ``watch``)."""
    space = inspect_space()
    findings: list[Finding] = []
    if space.error:
        findings.append(
            Finding(
                topic="watch",
                name="watch.space",
                status=CheckStatus.ERROR,
                message=f"Recording space unreadable: {space.error}",
                hint="Inspect $XDG_DATA_HOME/lion/recordings manually.",
            )
        )
        return findings
    for error in space.errors:
        findings.append(
            Finding(
                topic="watch",
                name="watch.record",
                status=CheckStatus.ERROR,
                message=f"Damaged recording: {error}",
                hint="Inspect, back up or remove the record directory; LION never skips it.",
            )
        )
    recordings = [recording for recording in space.recordings if recording is not None]
    if not recordings and not space.errors:
        findings.append(
            Finding(
                topic="watch",
                name="watch.records",
                status=CheckStatus.SKIP,
                message="No recordings present.",
                hint="Optional: 'lion watch start' records a shell session.",
            )
        )
    elif recordings:
        findings.append(
            Finding(
                topic="watch",
                name="watch.records",
                status=CheckStatus.OK,
                message=f"{len(recordings)} recording(s) present.",
            )
        )
    if space.pointer is not None:
        pointed = next((recording for recording in recordings if recording.ref == space.pointer), None)
        if pointed is None:
            findings.append(
                Finding(
                    topic="watch",
                    name="watch.pointer",
                    status=CheckStatus.WARN,
                    message=(
                        f"Active pointer '{get_active_pointer()}' references the missing recording '{space.pointer}'."
                    ),
                    hint="Review the pointer file and remove or repair it manually.",
                )
            )
        elif not pointed.active:
            findings.append(
                Finding(
                    topic="watch",
                    name="watch.pointer",
                    status=CheckStatus.WARN,
                    message=f"Active pointer references the stopped recording '{space.pointer}'.",
                    hint="Remove the stale pointer after reviewing the record.",
                )
            )
        else:
            findings.append(
                Finding(
                    topic="watch",
                    name="watch.active",
                    status=CheckStatus.OK,
                    message=f"Recording '{pointed.ref}' is active since {pointed.meta.started_at}.",
                )
            )
            for segment in pointed.segments:
                state = "open" if segment.alive else f"ended ({segment.end_reason})"
                findings.append(
                    Finding(
                        topic="watch",
                        name=f"watch.segment.{segment.name}",
                        status=CheckStatus.OK,
                        message=f"Segment {segment.name}: {state}.",
                    )
                )
    return findings
