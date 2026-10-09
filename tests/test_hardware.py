"""Deterministic tests for the hardware collector."""

import subprocess
from pathlib import Path
from typing import cast

import pytest

from lion.state import hardware, tools
from lion.state.collector import CollectorStatus


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
    drm = tmp_path / "drm"
    drm.mkdir(exist_ok=True)
    monkeypatch.setattr(hardware, "SYSFS_DRM", str(drm))
    monkeypatch.setattr(hardware, "SYSFS_PCI", str(tmp_path / "pci"), raising=False)


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
    assert result.data["gpu_vendor"] == "none"
    assert result.data["compute_platform"] == "none"


def test_collect_full_state(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Assemble CPU, memory and every GPU into one section.

    Both the non-NVIDIA card (from ``lspci``) and the NVIDIA cards (enriched
    from ``nvidia-smi``) must be present.
    """
    _patch_proc(monkeypatch, tmp_path, cpuinfo="model name : Fixture CPU\n", meminfo="MemTotal: 2048 kB\n")
    monkeypatch.setattr(hardware.os, "cpu_count", lambda: 12)

    lspci_output = (
        "0000:25:00.0 VGA compatible controller: AMD/ATI Navi 14 [Radeon RX 5500] (rev c5)\n"
        "\tSubsystem: Tul Corporation Device 2401\n"
        "\tKernel driver in use: amdgpu\n"
        "0000:2d:00.0 VGA compatible controller: NVIDIA Corporation GA104 [GeForce RTX 4090] (rev a1)\n"
        "\tKernel driver in use: nvidia\n"
        "0000:3b:00.0 3D controller: NVIDIA Corporation GA102 (rev a1)\n"
        "\tKernel driver in use: nvidia\n"
    )

    def fake_run(command: list[str], **kwargs: object) -> _Completed:
        if any("--query-gpu" in part for part in command):
            return _Completed(
                0,
                "NVIDIA RTX 4090, 560.10, 24564 MiB, 00000000:2D:00.0\nsecond, 1.0, 1024 MiB, 00000000:3B:00.0\n",
            )
        if command[:1] == ["lspci"]:
            return _Completed(0, lspci_output)
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
    assert result.data["gpu_vendor"] == "mixed"
    assert result.data["compute_platform"] == "mixed"
    assert result.data["gpu"] == [
        {
            "pci_id": "0000:25:00.0",
            "name": "AMD/ATI Navi 14 [Radeon RX 5500]",
            "vendor": "amd",
            "driver": "amdgpu",
            "driver_version": "",
            "memory_total_bytes": 0,
        },
        {
            "pci_id": "0000:2d:00.0",
            "name": "NVIDIA RTX 4090",
            "vendor": "nvidia",
            "driver": "nvidia",
            "driver_version": "560.10",
            "memory_total_bytes": 24564 * 1024 * 1024,
        },
        {
            "pci_id": "0000:3b:00.0",
            "name": "second",
            "vendor": "nvidia",
            "driver": "nvidia",
            "driver_version": "1.0",
            "memory_total_bytes": 1024 * 1024 * 1024,
        },
    ]


def test_collect_amd_and_nvidia_without_nvidia_smi(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Without ``nvidia-smi`` every ``lspci`` display controller still appears."""
    _patch_proc(monkeypatch, tmp_path, cpuinfo="", meminfo="")
    lspci_output = (
        "0000:25:00.0 VGA compatible controller: AMD/ATI Navi 14 [Radeon RX 5500] (rev c5)\n"
        "\tKernel driver in use: amdgpu\n"
        "0000:2d:00.0 VGA compatible controller: NVIDIA Corporation GA104 [GeForce RTX 3060 Ti] (rev a1)\n"
        "\tKernel driver in use: nvidia\n"
    )

    def fake_run(command: list[str], **kwargs: object) -> _Completed:
        if command[:1] == ["lspci"]:
            return _Completed(0, lspci_output)
        return _Completed(1, "")

    monkeypatch.setattr(tools.subprocess, "run", fake_run)

    assert hardware.COLLECTOR.collect().data["gpu"] == [
        {
            "pci_id": "0000:25:00.0",
            "name": "AMD/ATI Navi 14 [Radeon RX 5500]",
            "vendor": "amd",
            "driver": "amdgpu",
            "driver_version": "",
            "memory_total_bytes": 0,
        },
        {
            "pci_id": "0000:2d:00.0",
            "name": "NVIDIA Corporation GA104 [GeForce RTX 3060 Ti]",
            "vendor": "nvidia",
            "driver": "nvidia",
            "driver_version": "",
            "memory_total_bytes": 0,
        },
    ]
    result = hardware.COLLECTOR.collect()
    assert result.data["gpu_vendor"] == "mixed"
    assert result.data["compute_platform"] == "mixed"


def test_missing_nvidia_smi_keeps_cuda_hint(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A missing ``nvidia-smi`` hides the CUDA version but not the driver hint."""
    _patch_proc(monkeypatch, tmp_path, cpuinfo="", meminfo="")
    lspci_output = (
        "0000:2d:00.0 VGA compatible controller: NVIDIA Corporation AD102 [GeForce RTX 4090]\n"
        "\tKernel driver in use: nvidia\n"
    )

    def fake_run(command: list[str], **kwargs: object) -> _Completed:
        if command[:1] == ["lspci"]:
            return _Completed(0, lspci_output)
        return _Completed(1, "")

    monkeypatch.setattr(tools.subprocess, "run", fake_run)
    result = hardware.COLLECTOR.collect()
    assert result.data["gpu_vendor"] == "nvidia"
    assert result.data["compute_platform"] == "cuda"
    assert result.data["cuda_version"] == ""


def test_collect_non_nvidia_vram_from_sysfs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Read the AMD VRAM total from the DRM sysfs ``mem_info_vram_total``."""
    _patch_proc(monkeypatch, tmp_path, cpuinfo="", meminfo="")
    slot = tmp_path / "0000:25:00.0"
    slot.mkdir()
    (slot / "mem_info_vram_total").write_text("8573157376\n")
    card = Path(hardware.SYSFS_DRM) / "card2"
    card.mkdir()
    (card / "device").symlink_to(slot)
    lspci_output = (
        "0000:25:00.0 VGA compatible controller: AMD/ATI Navi 14 [Radeon RX 5500] (rev c5)\n"
        "\tKernel driver in use: amdgpu\n"
    )

    def fake_run(command: list[str], **kwargs: object) -> _Completed:
        if command[:1] == ["lspci"]:
            return _Completed(0, lspci_output)
        return _Completed(1, "")

    monkeypatch.setattr(tools.subprocess, "run", fake_run)

    assert hardware.COLLECTOR.collect().data["gpu"] == [
        {
            "pci_id": "0000:25:00.0",
            "name": "AMD/ATI Navi 14 [Radeon RX 5500]",
            "vendor": "amd",
            "driver": "amdgpu",
            "driver_version": "",
            "memory_total_bytes": 8573157376,
        }
    ]


def test_gpu_pci_domains_remain_distinct(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Join NVIDIA and sysfs data without merging equal slots in different domains."""
    _patch_proc(monkeypatch, tmp_path, cpuinfo="", meminfo="")
    for index, domain in enumerate(["0000", "0002"]):
        slot = tmp_path / f"{domain}:01:00.0"
        slot.mkdir()
        (slot / "mem_info_vram_total").write_text(str((index + 1) * 1024))
        card = Path(hardware.SYSFS_DRM) / f"card{index}"
        card.mkdir()
        (card / "device").symlink_to(slot)

    def fake_run(command: list[str], **kwargs: object) -> _Completed:
        if command[0] == "lspci":
            return _Completed(
                0,
                "\n".join(
                    [
                        "0000:01:00.0 VGA compatible controller: AMD first",
                        "0001:01:00.0 VGA compatible controller: NVIDIA card",
                        "0002:01:00.0 VGA compatible controller: AMD second",
                    ]
                ),
            )
        if any("--query-gpu" in part for part in command):
            return _Completed(0, "NVIDIA card, 560.10, 8192 MiB, 00000001:01:00.0\n")
        return _Completed(1, "")

    monkeypatch.setattr(tools.subprocess, "run", fake_run)
    assert hardware.COLLECTOR.collect().data["gpu"] == [
        {
            "pci_id": "0000:01:00.0",
            "name": "AMD first",
            "vendor": "amd",
            "driver": "",
            "driver_version": "",
            "memory_total_bytes": 1024,
        },
        {
            "pci_id": "0001:01:00.0",
            "name": "NVIDIA card",
            "vendor": "nvidia",
            "driver": "nvidia",
            "driver_version": "560.10",
            "memory_total_bytes": 8192 * 1024 * 1024,
        },
        {
            "pci_id": "0002:01:00.0",
            "name": "AMD second",
            "vendor": "amd",
            "driver": "",
            "driver_version": "",
            "memory_total_bytes": 2048,
        },
    ]


def test_malformed_gpu_lines(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Skip malformed GPU lines and treat missing digits as zero bytes."""
    _patch_proc(monkeypatch, tmp_path, cpuinfo="", meminfo="")
    outputs = iter(["only, two\n", "name, driver, no-digits, 00000000:01:00.0\n"])

    def fake_run(command: list[str], **kwargs: object) -> _Completed:
        if command[:1] == ["lspci"] or command == ["nvidia-smi"]:
            return _Completed(1, "")
        return _Completed(0, next(outputs))

    monkeypatch.setattr(tools.subprocess, "run", fake_run)
    assert hardware.COLLECTOR.collect().data["gpu"] == []
    assert hardware.COLLECTOR.collect().data["gpu"] == [
        {
            "pci_id": "0000:01:00.0",
            "name": "name",
            "vendor": "nvidia",
            "driver": "nvidia",
            "driver_version": "driver",
            "memory_total_bytes": 0,
        }
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


@pytest.mark.parametrize(
    ("failure", "message"),
    [
        (FileNotFoundError(), "executable not found"),
        (subprocess.TimeoutExpired("tool", 10), "timed out"),
        (PermissionError("sensitive detail"), "execution or decoding failed"),
    ],
)
def test_tool_failure_diagnostics(monkeypatch: pytest.MonkeyPatch, failure: Exception, message: str) -> None:
    """Keep stable failure causes without exposing subprocess output or arguments."""

    def fail(*args: object, **kwargs: object) -> None:
        raise failure

    monkeypatch.setattr(tools.subprocess, "run", fail)
    failures: list[str] = []
    assert tools.run_tool(["tool", "secret"], timeout=10, failures=failures) is None
    assert failures == [f"tool: {message}"]


def test_nonzero_tool_diagnostic(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exit codes survive while potentially sensitive stdout does not."""

    def run(*args: object, **kwargs: object) -> _Completed:
        return _Completed(7, "secret")

    monkeypatch.setattr(tools.subprocess, "run", run)
    failures: list[str] = []
    assert tools.run_tool(["tool"], timeout=10, failures=failures) is None
    assert failures == ["tool: exit code 7"]


def test_non_nvidia_machine_needs_no_nvidia_tool(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A successful empty PCI enumeration is different from failed discovery."""
    _patch_proc(monkeypatch, tmp_path, cpuinfo="model name: CPU", meminfo="MemTotal: 2048 kB")

    def run(command: list[str], **kwargs: object) -> _Completed:
        if command[0] == "lspci":
            return _Completed(0, "")
        raise FileNotFoundError

    monkeypatch.setattr(tools.subprocess, "run", run)
    assert hardware.COLLECTOR.collect().status == CollectorStatus.OK
    _no_tools(monkeypatch)
    result = hardware.COLLECTOR.collect()
    assert result.status == CollectorStatus.UNAVAILABLE
    assert "lspci: exit code 1" in result.error
    assert result.data["cpu_model"] == "CPU"


@pytest.mark.parametrize("pci_output", ["", "0000:01:00.0 VGA compatible controller: NVIDIA Corporation TU104\n"])
def test_nvidia_banner_failure_survives_unbranded_name(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, pci_output: str
) -> None:
    """A successful NVIDIA query identifies NVIDIA even without a branded name."""
    _patch_proc(monkeypatch, tmp_path, cpuinfo="model name: CPU", meminfo="MemTotal: 2048 kB")

    def run(command: list[str], **kwargs: object) -> _Completed:
        if command[0] == "lspci":
            return _Completed(0, pci_output)
        if len(command) > 1:
            return _Completed(0, "Tesla T4, 550.54, 15360 MiB, 00000000:01:00.0\n")
        raise subprocess.TimeoutExpired(command, 10)

    monkeypatch.setattr(tools.subprocess, "run", run)
    result = hardware.COLLECTOR.collect()
    assert result.status == CollectorStatus.UNAVAILABLE
    assert result.error == "nvidia-smi: timed out"
    assert result.data["gpu"] == [
        {
            "pci_id": "0000:01:00.0",
            "name": "Tesla T4",
            "vendor": "nvidia",
            "driver": "nvidia",
            "driver_version": "550.54",
            "memory_total_bytes": 15360 * 1024 * 1024,
        }
    ]


def _collect_with_lspci(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, lspci_output: str) -> dict[str, object]:
    """Collect hardware from a fixed ``lspci`` output without other tools."""
    _patch_proc(monkeypatch, tmp_path, cpuinfo="model name: CPU", meminfo="MemTotal: 2048 kB")

    def fake_run(command: list[str], **kwargs: object) -> _Completed:
        if command[:1] == ["lspci"]:
            return _Completed(0, lspci_output)
        return _Completed(1, "")

    monkeypatch.setattr(tools.subprocess, "run", fake_run)
    return hardware.COLLECTOR.collect().data


def test_single_amd_gpu_is_rocm_platform(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A lone AMD driver yields a ROCm hint, not a verified capability."""
    data = _collect_with_lspci(
        monkeypatch,
        tmp_path,
        "0000:25:00.0 VGA compatible controller: Advanced Micro Devices Navi\n\tKernel driver in use: amdgpu\n",
    )
    assert data["gpu_vendor"] == "amd"
    assert data["compute_platform"] == "rocm"


def test_unidentified_driver_is_unknown(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A display controller without a recognizable kernel driver stays unknown."""
    data = _collect_with_lspci(
        monkeypatch,
        tmp_path,
        "0000:01:00.0 VGA compatible controller: Contoso Graphics\n\tKernel driver in use: nouveau\n",
    )
    assert data["gpu_vendor"] == "unknown"
    assert data["compute_platform"] == "none"
    assert data["gpu"] == [
        {
            "pci_id": "0000:01:00.0",
            "name": "Contoso Graphics",
            "vendor": "unknown",
            "driver": "nouveau",
            "driver_version": "",
            "memory_total_bytes": 0,
        }
    ]


def test_gpus_are_sorted_by_pci_id(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The GPU list is deterministic regardless of ``lspci`` order."""
    data = _collect_with_lspci(
        monkeypatch,
        tmp_path,
        "0000:3b:00.0 VGA compatible controller: AMD second\n\tKernel driver in use: amdgpu\n"
        "0000:25:00.0 VGA compatible controller: AMD first\n\tKernel driver in use: amdgpu\n",
    )
    assert [entry["pci_id"] for entry in cast("list[dict[str, object]]", data["gpu"])] == [
        "0000:25:00.0",
        "0000:3b:00.0",
    ]


@pytest.mark.parametrize("driver", ["nouveau", "vfio-pci", ""])
def test_vendor_does_not_depend_on_active_driver(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, driver: str) -> None:
    """A NVIDIA PCI description identifies the vendor even without its driver."""
    output = "0000:01:00.0 VGA compatible controller: NVIDIA Corporation GPU\n"
    if driver:
        output += f"\tKernel driver in use: {driver}\n"
    data = _collect_with_lspci(monkeypatch, tmp_path, output)
    gpu = cast("list[dict[str, object]]", data["gpu"])[0]
    assert gpu["vendor"] == "nvidia"
    assert gpu["driver"] == driver
    assert data["gpu_vendor"] == "nvidia"
    assert data["compute_platform"] == "none"


@pytest.mark.parametrize(
    ("vendor_id", "expected"), [("0x10de", "nvidia"), ("0x1002", "amd"), ("0x8086", "intel"), ("0xffff", "unknown")]
)
def test_numeric_pci_vendor_is_independent_of_name_and_driver(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, vendor_id: str, expected: str
) -> None:
    """Numeric PCI vendor evidence wins over a misleading device description."""
    slot = tmp_path / "pci" / "0000:01:00.0"
    slot.mkdir(parents=True)
    (slot / "vendor").write_text(vendor_id + "\n")
    data = _collect_with_lspci(
        monkeypatch, tmp_path, "0000:01:00.0 VGA compatible controller: NVIDIA Corporation GPU\n"
    )
    assert cast("list[dict[str, object]]", data["gpu"])[0]["vendor"] == expected


def test_unreadable_or_invalid_vendor_uses_pci_description(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """An invalid numeric vendor still permits a branded PCI description."""
    slot = tmp_path / "pci" / "0000:01:00.0"
    slot.mkdir(parents=True)
    (slot / "vendor").write_text("not a number")
    data = _collect_with_lspci(monkeypatch, tmp_path, "0000:01:00.0 VGA compatible controller: Intel Corporation GPU\n")
    assert cast("list[dict[str, object]]", data["gpu"])[0]["vendor"] == "intel"
