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
    """Read /etc/os-release."""
    path = Path("/etc/os-release")

    if not path.exists():
        return {}

    result: dict[str, str] = {}

    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" not in line:
            continue

        key, value = line.split("=", 1)
        result[key] = value.strip().strip('"')

    return result


def _read_cpu_model() -> str:
    """Read the CPU model from /proc/cpuinfo."""
    path = Path("/proc/cpuinfo")

    if not path.exists():
        return "Unknown"

    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("model name"):
            return line.split(":", 1)[1].strip()

    return "Unknown"


def _read_memory_total() -> int:
    """Read total memory in bytes from /proc/meminfo."""
    path = Path("/proc/meminfo")

    if not path.exists():
        return 0

    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("MemTotal:"):
            kibibytes = int(line.split()[1])
            return kibibytes * 1024

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
