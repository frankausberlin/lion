"""Persist and load LION system scans."""

import tomllib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import tomli_w

from repolion.paths import get_scans_dir
from repolion.scan import SystemInfo


@dataclass(frozen=True)
class ScanRecord:
    """Complete LION scan."""

    timestamp: str
    system: SystemInfo


def save_scan(system_info: SystemInfo) -> Path:
    """Save a system scan as TOML."""
    scans_dir = get_scans_dir()
    scans_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.now().astimezone()

    data = {
        "scan": {
            "timestamp": now.isoformat(),
        },
        "system": {
            "distribution": system_info.distribution,
            "distribution_version": system_info.distribution_version,
            "kernel": system_info.kernel,
            "architecture": system_info.architecture,
            "hostname": system_info.hostname,
            "cpu_model": system_info.cpu_model,
            "cpu_logical_cores": system_info.cpu_logical_cores,
            "memory_total_bytes": system_info.memory_total_bytes,
        },
    }

    filename = now.strftime("%Y-%m-%dT%H-%M-%S%z") + ".toml"
    path = scans_dir / filename

    path.write_text(tomli_w.dumps(data), encoding="utf-8")

    return path


def load_latest_scan() -> ScanRecord | None:
    """Load the newest stored scan."""
    scans_dir = get_scans_dir()

    if not scans_dir.exists():
        return None

    files = sorted(scans_dir.glob("*.toml"), reverse=True)

    if not files:
        return None

    with files[0].open("rb") as file:
        data = tomllib.load(file)

    system = data["system"]

    return ScanRecord(
        timestamp=data["scan"]["timestamp"],
        system=SystemInfo(
            distribution=system["distribution"],
            distribution_version=system["distribution_version"],
            kernel=system["kernel"],
            architecture=system["architecture"],
            hostname=system["hostname"],
            cpu_model=system["cpu_model"],
            cpu_logical_cores=system["cpu_logical_cores"],
            memory_total_bytes=system["memory_total_bytes"],
        ),
    )
