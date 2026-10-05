"""Deterministic tests for the hardware collector."""

import subprocess
from pathlib import Path

import pytest

from repolion.state import hardware, tools
from repolion.state.collector import CollectorStatus


class _Completed:
    """Minimal stand-in for :class:`subprocess.CompletedProcess`."""

    def __init__(self, returncode: int, stdout: str) -> None:
        self.returncode = returncode
        self.stdout = stdout


def _patch_proc(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    cpuinfo: str | None,
    meminfo: str | None,
) -> None:
    cpu = tmp_path / "cpuinfo"
    mem = tmp_path / "meminfo"
    if cpuinfo is not None:
        cpu.write_text(cpuinfo)
    if meminfo is not None:
        mem.write_text(meminfo)
    mapping = {"/proc/cpuinfo": cpu, "/proc/meminfo": mem}

    def fake_path(name: str) -> Path:
        return mapping[name]

    monkeypatch.setattr(hardware, "Path", fake_path)


def _no_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(command: list[str], **kwargs: object) -> _Completed:
        return _Completed(1, "")

    monkeypatch.setattr(tools.subprocess, "run", fake_run)


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("processor : 0\nmodel name\t: Example CPU\n", "Example CPU"),
        ("model name without separator", "Unknown"),
        ("model name: \n", "Unknown"),
        ("Hardware: ARM board", "Unknown"),
        ("", "Unknown"),
    ],
)
def test_cpu_text(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    content: str,
    expected: str,
) -> None:
    """Read the CPU model from fixture text."""
    _patch_proc(monkeypatch, tmp_path, cpuinfo=content, meminfo="")
    _no_tools(monkeypatch)
    assert hardware.COLLECTOR.collect().data["cpu_model"] == expected


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("MemTotal:       16384 kB\nMemFree: 100 kB", 16384 * 1024),
        ("MemTotal:", 0),
        ("MemTotal: nope kB", 0),
        ("MemTotal: -1 kB", 0),
        ("MemTotal: 16 MB", 0),
        ("MemFree: 100 kB", 0),
        ("", 0),
    ],
)
def test_memory_text(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    content: str,
    expected: int,
) -> None:
    """Validate units, missing values and malformed memory readings."""
    _patch_proc(monkeypatch, tmp_path, cpuinfo="", meminfo=content)
    _no_tools(monkeypatch)
    assert hardware.COLLECTOR.collect().data["memory_total_bytes"] == expected


def test_missing_proc_files(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Keep documented unknown values when proc files are absent."""
    _patch_proc(monkeypatch, tmp_path, cpuinfo=None, meminfo=None)
    _no_tools(monkeypatch)
    result = hardware.COLLECTOR.collect()
    assert result.data["cpu_model"] == "Unknown"
    assert result.data["memory_total_bytes"] == 0
    assert result.data["gpu"] == []
    assert result.data["cuda_version"] == ""


def test_collect_full_state(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Assemble CPU, memory and GPU facts into one section."""
    _patch_proc(monkeypatch, tmp_path, cpuinfo="model name : Fixture CPU\n", meminfo="MemTotal: 2048 kB\n")
    monkeypatch.setattr(hardware.os, "cpu_count", lambda: 12)

    def fake_run(command: list[str], **kwargs: object) -> _Completed:
        if any("--query-gpu" in part for part in command):
            return _Completed(0, "NVIDIA RTX 4090, 560.10, 24564 MiB\nsecond, 1.0, 1024\n")
        if command == ["nvidia-smi"]:
            return _Completed(0, "| NVIDIA-SMI 560.10  Driver Version: 560.10  CUDA Version: 12.6  |")
        return _Completed(1, "")

    monkeypatch.setattr(tools.subprocess, "run", fake_run)

    result = hardware.COLLECTOR.collect()

    assert result.status == CollectorStatus.OK
    assert result.data["cpu_model"] == "Fixture CPU"
    assert result.data["cpu_logical_cores"] == 12
    assert result.data["memory_total_bytes"] == 2048 * 1024
    assert result.data["cuda_version"] == "12.6"
    assert result.data["gpu"] == [
        {"name": "NVIDIA RTX 4090", "driver_version": "560.10", "memory_total_bytes": 24564 * 1024 * 1024},
        {"name": "second", "driver_version": "1.0", "memory_total_bytes": 1024 * 1024 * 1024},
    ]


def test_malformed_gpu_lines(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Skip malformed GPU lines and treat missing digits as zero bytes."""
    _patch_proc(monkeypatch, tmp_path, cpuinfo="", meminfo="")
    outputs = iter(["only, two\n", "name, driver, no-digits\n"])

    def fake_run(command: list[str], **kwargs: object) -> _Completed:
        if command == ["nvidia-smi"]:
            return _Completed(1, "")
        return _Completed(0, next(outputs))

    monkeypatch.setattr(tools.subprocess, "run", fake_run)
    assert hardware.COLLECTOR.collect().data["gpu"] == []
    assert hardware.COLLECTOR.collect().data["gpu"] == [
        {"name": "name", "driver_version": "driver", "memory_total_bytes": 0}
    ]


def test_cuda_banner_edges(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A malformed or absent CUDA banner yields an empty version."""
    _patch_proc(monkeypatch, tmp_path, cpuinfo="", meminfo="")
    banners = iter(["CUDA Version:   |", "no banner here"])

    def fake_run(command: list[str], **kwargs: object) -> _Completed:
        if command == ["nvidia-smi"]:
            return _Completed(0, next(banners))
        return _Completed(1, "")

    monkeypatch.setattr(tools.subprocess, "run", fake_run)
    assert hardware.COLLECTOR.collect().data["cuda_version"] == ""
    assert hardware.COLLECTOR.collect().data["cuda_version"] == ""


def test_empty_tool_output(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Empty helper output is treated like a missing tool."""
    _patch_proc(monkeypatch, tmp_path, cpuinfo="", meminfo="")

    def fake_run(command: list[str], **kwargs: object) -> _Completed:
        return _Completed(0, "")

    monkeypatch.setattr(tools.subprocess, "run", fake_run)
    result = hardware.COLLECTOR.collect()
    assert result.data["gpu"] == []
    assert result.data["cuda_version"] == ""


def test_tool_oserror(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A missing binary is reported as unavailable."""
    _patch_proc(monkeypatch, tmp_path, cpuinfo="", meminfo="")

    def fake_run(command: list[str], **kwargs: object) -> _Completed:
        raise OSError("missing")

    monkeypatch.setattr(tools.subprocess, "run", fake_run)
    assert hardware.COLLECTOR.collect().data["gpu"] == []


def test_tool_timeout(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A timed-out helper is reported as unavailable."""
    _patch_proc(monkeypatch, tmp_path, cpuinfo="", meminfo="")

    def fake_run(command: list[str], **kwargs: object) -> _Completed:
        raise subprocess.TimeoutExpired(["nvidia-smi"], 1)

    monkeypatch.setattr(tools.subprocess, "run", fake_run)
    assert hardware.COLLECTOR.collect().data["gpu"] == []
