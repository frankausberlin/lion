"""Collect Linux system information."""

import os
import platform
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SystemInfo:
    """System information collected by LION."""

    distribution: str
    distribution_version: str
    kernel: str
    architecture: str
    hostname: str
    cpu_model: str
    cpu_logical_cores: int
    memory_total_bytes: int


def _read_os_release() -> dict[str, str]:
    """Read distribution metadata using the standard library."""
    try:
        return platform.freedesktop_os_release()
    except OSError:
        return {}


def _read_cpu_model() -> str:
    """Read the CPU model from /proc/cpuinfo."""
    path = Path("/proc/cpuinfo")

    if not path.exists():
        return "Unknown"

    for line in path.read_text(encoding="utf-8").splitlines():
        key, separator, value = line.partition(":")
        if separator and key.strip() == "model name":
            return value.strip() or "Unknown"

    return "Unknown"


def _read_memory_total() -> int:
    """Read total memory in bytes from /proc/meminfo."""
    path = Path("/proc/meminfo")

    if not path.exists():
        return 0

    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("MemTotal:"):
            parts = line.split()
            if len(parts) != 3 or parts[2] != "kB":
                return 0
            try:
                kibibytes = int(parts[1])
            except ValueError:
                return 0
            return max(0, kibibytes) * 1024

    return 0


def scan_system() -> SystemInfo:
    """Collect basic Linux system information."""
    os_release = _read_os_release()

    return SystemInfo(
        distribution=os_release.get("NAME", "Unknown"),
        distribution_version=os_release.get("VERSION_ID", "Unknown"),
        kernel=platform.release(),
        architecture=platform.machine(),
        hostname=platform.node(),
        cpu_model=_read_cpu_model(),
        cpu_logical_cores=os.cpu_count() or 0,
        memory_total_bytes=_read_memory_total(),
    )
