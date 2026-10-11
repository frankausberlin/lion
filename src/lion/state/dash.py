"""Collector for the content hashes of the opted-in ``~/.shlib/dash`` links.

``dash`` holds user-created symlinks to important configuration files; the
``LIONSTATE_`` name prefix is the explicit opt-in marker for content
monitoring. Only links that resolve to a regular file are hashed, and only the
SHA-256 digest is stored, never the file content. Broken links, directory
targets and regular files stay unmonitored; ``doctor``'s shlib dash checks
report those defects separately.
"""

import hashlib
from dataclasses import asdict, dataclass
from pathlib import Path

from lion.state.collector import Collector, CollectorResult, CollectorStatus

#: Only dash links with this name prefix are content-monitored.
PREFIX = "LIONSTATE_"

#: Resolved relative to the current user's home on every collection.
DASH_DIRECTORY = Path(".shlib") / "dash"


@dataclass(frozen=True)
class DashFile:
    """One monitored dash link and the SHA-256 digest of its target content."""

    name: str
    hash: str


@dataclass(frozen=True)
class DashState:
    """The monitored dash links, sorted by name."""

    files: list[DashFile]


def _hash_file(path: Path) -> str:
    """Return the SHA-256 hex digest of a file's content."""
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _collect() -> CollectorResult:
    """Hash every opted-in dash link; a missing directory is simply empty.

    A link that exists but cannot be read marks the collector ``unavailable``
    with a stable message while the hashes collected so far are kept (the
    hardware precedent: incomplete discovery is ``unavailable``, not ``error``).
    """
    directory = Path.home() / DASH_DIRECTORY
    if not directory.exists():
        return CollectorResult(status=CollectorStatus.OK, data=asdict(DashState(files=[])))
    if not directory.is_dir():
        return CollectorResult(
            status=CollectorStatus.UNAVAILABLE,
            data=asdict(DashState(files=[])),
            error="dash path is not a directory",
        )
    try:
        entries = sorted(directory.iterdir(), key=lambda entry: entry.name)
    except OSError:
        return CollectorResult(
            status=CollectorStatus.UNAVAILABLE,
            data=asdict(DashState(files=[])),
            error="Cannot list ~/.shlib/dash",
        )

    failures: list[str] = []
    files: list[DashFile] = []
    for entry in entries:
        if not entry.name.startswith(PREFIX) or not entry.is_symlink() or not entry.is_file():
            continue
        try:
            digest = _hash_file(entry)
        except OSError:
            failures.append(f"Unreadable dash entry: {entry.name}")
            continue
        files.append(DashFile(name=entry.name, hash=digest))
    return CollectorResult(
        status=CollectorStatus.UNAVAILABLE if failures else CollectorStatus.OK,
        data=asdict(DashState(files=files)),
        error="; ".join(dict.fromkeys(failures)),
    )


COLLECTOR = Collector(name="dash", collect=_collect)
