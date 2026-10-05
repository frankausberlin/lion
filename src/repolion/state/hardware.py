"""Collector for stable hardware facts (CPU, memory, GPU)."""

import os
from dataclasses import asdict, dataclass
from pathlib import Path

from repolion.state.collector import Collector, CollectorResult, CollectorStatus
from repolion.state.tools import run_tool

UNKNOWN = "Unknown"
TIMEOUT_SECONDS = 10


@dataclass(frozen=True)
class GpuState:
    """One NVIDIA GPU as reported by ``nvidia-smi``."""

    name: str
    driver_version: str
    memory_total_bytes: int


@dataclass(frozen=True)
class HardwareState:
    """Hardware facts that change rarely enough to be worth storing."""

    cpu_model: str
    cpu_logical_cores: int
    memory_total_bytes: int
    cuda_version: str
    gpu: list[GpuState]


def _read_cpu_model() -> str:
    """Read the CPU model from /proc/cpuinfo."""
    path = Path("/proc/cpuinfo")
    if not path.exists():
        return UNKNOWN
    for line in path.read_text(encoding="utf-8").splitlines():
        key, separator, value = line.partition(":")
        if separator and key.strip() == "model name":
            return value.strip() or UNKNOWN
    return UNKNOWN


def _read_memory_total() -> int:
    """Read total memory in bytes from /proc/meminfo."""
    path = Path("/proc/meminfo")
    if not path.exists():
        return 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("MemTotal:"):
            continue
        parts = line.split()
        if len(parts) != 3 or parts[2] != "kB":
            return 0
        try:
            kibibytes = int(parts[1])
        except ValueError:
            return 0
        return max(0, kibibytes) * 1024
    return 0


def _parse_mib(value: str) -> int:
    digits = "".join(character for character in value if character.isdigit())
    return int(digits) * 1024 * 1024 if digits else 0


def _collect_gpus() -> list[GpuState]:
    """Query GPU facts, returning an empty list when nvidia-smi is absent."""
    output = run_tool(
        ["nvidia-smi", "--query-gpu=name,driver_version,memory.total", "--format=csv,noheader"],
        timeout=TIMEOUT_SECONDS,
    )
    if not output:
        return []
    gpus: list[GpuState] = []
    for line in output.splitlines():
        fields = [field.strip() for field in line.split(",")]
        if len(fields) != 3:
            continue
        gpus.append(GpuState(name=fields[0], driver_version=fields[1], memory_total_bytes=_parse_mib(fields[2])))
    return gpus


def _collect_cuda_version() -> str:
    """Best-effort CUDA version parsed from the standard nvidia-smi banner."""
    output = run_tool(["nvidia-smi"], timeout=TIMEOUT_SECONDS)
    if not output:
        return ""
    for line in output.splitlines():
        if "CUDA Version:" not in line:
            continue
        remainder = line.split("CUDA Version:", 1)[1].strip()
        return remainder.split()[0].strip("|") if remainder else ""
    return ""


def _collect() -> CollectorResult:
    """Collect hardware facts; missing tools or files never fail the capture."""
    state = HardwareState(
        cpu_model=_read_cpu_model(),
        cpu_logical_cores=os.cpu_count() or 0,
        memory_total_bytes=_read_memory_total(),
        cuda_version=_collect_cuda_version(),
        gpu=_collect_gpus(),
    )
    return CollectorResult(status=CollectorStatus.OK, data=asdict(state))


COLLECTOR = Collector(name="hardware", collect=_collect)
