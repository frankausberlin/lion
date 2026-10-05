"""Persist and load LION system scans."""

import os
import tomllib
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import cast
from uuid import uuid4

import tomli_w

from repolion.paths import get_scans_dir
from repolion.scan import SystemInfo


class ScanError(ValueError):
    """A stored scan cannot be read or validated."""


@dataclass(frozen=True)
class ScanRecord:
    """Complete LION scan."""

    timestamp: str
    system: SystemInfo

    def to_dict(self) -> dict[str, object]:
        """Return the shared TOML and JSON representation."""
        return {"scan": {"timestamp": self.timestamp}, "system": asdict(self.system)}


def save_scan(system_info: SystemInfo) -> Path:
    """Publish a complete UTC scan atomically without overwriting another scan."""
    scans_dir = get_scans_dir()
    scans_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC)
    record = ScanRecord(now.isoformat(), system_info)
    # A hard link publishes the complete file and fails if the destination exists.
    # Both paths are on the same filesystem; temporary files are never scan candidates.
    with NamedTemporaryFile(mode="w", encoding="utf-8", dir=scans_dir, suffix=".tmp", delete=False) as file:
        temporary = Path(file.name)
        try:
            file.write(tomli_w.dumps(record.to_dict()))
            file.flush()
            os.fsync(file.fileno())
            while True:
                path = scans_dir / f"{now.strftime('%Y-%m-%dT%H-%M-%S.%fZ')}-{uuid4().hex}.toml"
                try:
                    os.link(temporary, path)
                except FileExistsError:
                    continue
                return path
        finally:
            temporary.unlink(missing_ok=True)


def _table(value: object) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise ValueError("expected a table")
    return cast(Mapping[str, object], value)


def _text(data: Mapping[str, object], key: str) -> str:
    value = data[key]
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string")
    return value


def _count(data: Mapping[str, object], key: str) -> int:
    value = data[key]
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{key} must be a non-negative integer")
    return value


def load_scan(path: Path) -> ScanRecord:
    """Read and validate a scan, reporting its path on failure."""
    try:
        with path.open("rb") as file:
            data = cast(dict[str, object], tomllib.load(file))
        timestamp = _text(_table(data["scan"]), "timestamp")
        if datetime.fromisoformat(timestamp).utcoffset() is None:
            raise ValueError("timestamp must include a timezone")
        system = _table(data["system"])
        return ScanRecord(
            timestamp=timestamp,
            system=SystemInfo(
                distribution=_text(system, "distribution"),
                distribution_version=_text(system, "distribution_version"),
                kernel=_text(system, "kernel"),
                architecture=_text(system, "architecture"),
                hostname=_text(system, "hostname"),
                cpu_model=_text(system, "cpu_model"),
                cpu_logical_cores=_count(system, "cpu_logical_cores"),
                memory_total_bytes=_count(system, "memory_total_bytes"),
            ),
        )
    except (OSError, ValueError, KeyError) as exc:
        raise ScanError(f"Cannot load scan '{path}': {exc}") from exc


def load_latest_scan() -> ScanRecord | None:
    """Select by stored instant, including legacy scans with local UTC offsets.

    Invalid files fail explicitly: silently skipping them could hide the newest scan.
    Equal timestamps are resolved deterministically by filename.
    """
    scans_dir = get_scans_dir()
    if not scans_dir.exists():
        return None
    records = [(path.name, load_scan(path)) for path in scans_dir.glob("*.toml")]
    if not records:
        return None
    return max(records, key=lambda item: (datetime.fromisoformat(item[1].timestamp), item[0]))[1]
