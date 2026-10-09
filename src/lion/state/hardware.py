"""Collector for stable hardware facts (CPU, memory, GPU)."""

import os
from dataclasses import asdict, dataclass
from pathlib import Path

from lion.state.collector import Collector, CollectorResult, CollectorStatus
from lion.state.tools import run_tool

UNKNOWN = "Unknown"
TIMEOUT_SECONDS = 10
_DISPLAY_CLASSES = ("vga compatible controller", "3d controller", "display controller")
SYSFS_DRM = "/sys/class/drm"
SYSFS_PCI = "/sys/bus/pci/devices"

VENDOR_UNKNOWN = "unknown"
VENDOR_NONE = "none"
VENDOR_MIXED = "mixed"

_PCI_VENDORS = {0x10DE: "nvidia", 0x1002: "amd", 0x8086: "intel"}


def _read_gpu_vendor(pci_id: str, name: str) -> str:
    """Read the numeric PCI vendor, falling back to the device description.

    Vendor identity does not depend on a proprietary, open-source or passthrough
    driver being active. Unknown numeric vendors stay unknown.
    """
    try:
        with open(os.path.join(SYSFS_PCI, pci_id, "vendor"), encoding="utf-8") as handle:
            vendor_id = int(handle.read().strip(), 16)
    except (OSError, ValueError):
        description = name.lower()
        if description.startswith("nvidia "):
            return "nvidia"
        if description.startswith(("amd ", "amd/ati ", "advanced micro devices")):
            return "amd"
        if description.startswith("intel "):
            return "intel"
        return VENDOR_UNKNOWN
    return _PCI_VENDORS.get(vendor_id, VENDOR_UNKNOWN)


@dataclass(frozen=True)
class GpuState:
    """One GPU with its PCI identity, kernel driver and readings."""

    pci_id: str
    name: str
    vendor: str
    driver: str
    driver_version: str
    memory_total_bytes: int


@dataclass
class _PciGpu:
    """A display controller found by ``lspci``; its kernel driver is filled while parsing."""

    pci_id: str
    name: str
    driver: str = ""


@dataclass(frozen=True)
class _NvidiaGpu:
    """An ``nvidia-smi`` GPU together with its normalized PCI bus id."""

    pci_id: str
    state: GpuState


@dataclass(frozen=True)
class HardwareState:
    """Hardware facts that change rarely enough to be worth storing."""

    cpu_model: str
    cpu_logical_cores: int
    memory_total_bytes: int
    cuda_version: str
    gpu_vendor: str
    compute_platform: str
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


def _normalize_pci_id(raw: str) -> str:
    """Collapse PCI bus ids from ``lspci`` and ``nvidia-smi`` to one form.

    ``lspci -D`` prints ``0000:2d:00.0`` while ``nvidia-smi`` prints
    ``00000000:2D:00.0``; both normalize to ``0000:2d:00.0``. Keep the
    domain: different domains may contain the same bus/device/function.
    """
    parts = raw.strip().lower().split(":")
    if len(parts) != 3 or not _is_hex(parts[0]):
        return raw.strip().lower()
    return f"{int(parts[0], 16):04x}:{parts[1]}:{parts[2]}"


def _is_hex(value: str) -> bool:
    if not value:
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return True


def _is_pci_slot(token: str) -> bool:
    """Return whether the first token of an ``lspci`` line is a PCI slot."""
    parts = token.split(":")
    if len(parts) != 3 or "." not in parts[2]:
        return False
    device, _, function = parts[2].partition(".")
    return all(_is_hex(part) for part in (parts[0], parts[1], device, function))


def _is_display_device(rest: str) -> bool:
    lower = rest.lower()
    return any(lower.startswith(display_class) for display_class in _DISPLAY_CLASSES)


def _device_description(rest: str) -> str:
    description = rest.partition(":")[2].strip()
    if description.endswith(")"):
        head, separator, tail = description.rpartition("(")
        if separator and tail.rstrip(")").strip().startswith("rev"):
            description = head.strip()
    return description or UNKNOWN


def _collect_pci_gpus(failures: list[str] | None = None) -> list[_PciGpu]:
    """List every display controller via ``lspci``, including non-NVIDIA GPUs."""
    output = run_tool(["lspci", "-D", "-k"], timeout=TIMEOUT_SECONDS, failures=failures)
    if not output:
        return []
    devices: list[_PciGpu] = []
    current: _PciGpu | None = None
    for line in output.splitlines():
        if line[:1] not in (" ", "\t", ""):
            token, _, rest = line.partition(" ")
            current = None
            if _is_pci_slot(token) and _is_display_device(rest):
                current = _PciGpu(pci_id=_normalize_pci_id(token), name=_device_description(rest))
                devices.append(current)
            continue
        if current is not None and line.strip().startswith("Kernel driver in use:"):
            current.driver = line.split(":", 1)[1].strip()
    return devices


def _collect_nvidia_gpus(failures: list[str] | None = None) -> list[_NvidiaGpu]:
    """Query NVIDIA GPUs through ``nvidia-smi``, keyed by PCI bus id."""
    output = run_tool(
        ["nvidia-smi", "--query-gpu=name,driver_version,memory.total,pci.bus_id", "--format=csv,noheader"],
        timeout=TIMEOUT_SECONDS,
        failures=failures,
    )
    if not output:
        return []
    gpus: list[_NvidiaGpu] = []
    for line in output.splitlines():
        fields = [field.strip() for field in line.split(",")]
        if len(fields) != 4:
            continue
        pci_id = _normalize_pci_id(fields[3])
        state = GpuState(
            pci_id=pci_id,
            name=fields[0],
            vendor="nvidia",
            driver="nvidia",
            driver_version=fields[1],
            memory_total_bytes=_parse_mib(fields[2]),
        )
        gpus.append(_NvidiaGpu(pci_id=pci_id, state=state))
    return gpus


def _read_vram_total(device_path: str) -> int:
    """Read ``mem_info_vram_total`` (bytes) from a DRM device, or ``0``."""
    try:
        with open(os.path.join(device_path, "mem_info_vram_total"), encoding="utf-8") as handle:
            raw = handle.read().strip()
    except OSError:
        return 0
    try:
        value = int(raw)
    except ValueError:
        return 0
    return value if value > 0 else 0


def _collect_vram_totals() -> dict[str, int]:
    """Map normalized PCI ids to VRAM bytes from DRM sysfs.

    The ``amdgpu`` driver (and Intel ``xe``/``i915``) expose the total VRAM as
    bytes via ``mem_info_vram_total``, keyed here by PCI slot so it can be
    joined with the ``lspci`` devices.
    """
    totals: dict[str, int] = {}
    try:
        entries = os.scandir(SYSFS_DRM)
    except OSError:
        return totals
    with entries:
        for entry in entries:
            suffix = entry.name.removeprefix("card")
            if entry.name == "card" or not suffix.isdigit():
                continue
            device_path = os.path.join(entry.path, "device")
            pci_id = _normalize_pci_id(os.path.basename(os.path.realpath(device_path)))
            total = _read_vram_total(device_path)
            if total:
                totals[pci_id] = total
    return totals


def _discard_spurious_nvidia_failure(
    failures: list[str] | None, nvidia: list[_NvidiaGpu], devices: list[_PciGpu]
) -> None:
    """Drop a redundant ``nvidia-smi`` failure when PCI proves no NVIDIA card.

    Vendor evidence is read before richer NVIDIA names replace the PCI
    descriptions. The ``tools`` collector separately records whether
    ``nvidia-smi`` exists, so its absence on a non-NVIDIA host is not an error.
    """
    if (
        failures is not None
        and not nvidia
        and not any(_read_gpu_vendor(device.pci_id, device.name) == "nvidia" for device in devices)
        and not any(failure.startswith("lspci:") for failure in failures)
    ):
        failures[:] = [failure for failure in failures if not failure.startswith("nvidia-smi:")]


def _merge_gpus(devices: list[_PciGpu], nvidia: list[_NvidiaGpu], vram_totals: dict[str, int]) -> list[GpuState]:
    """Merge ``lspci`` devices with NVIDIA states by PCI slot, sorted by id."""
    nvidia_by_id = {entry.pci_id: entry.state for entry in nvidia if entry.pci_id}
    gpus: list[GpuState] = []
    matched: set[str] = set()
    for device in devices:
        state = nvidia_by_id.get(device.pci_id)
        if state is not None:
            gpus.append(state)
            matched.add(device.pci_id)
        else:
            gpus.append(
                GpuState(
                    pci_id=device.pci_id,
                    name=device.name,
                    vendor=_read_gpu_vendor(device.pci_id, device.name),
                    driver=device.driver,
                    driver_version="",
                    memory_total_bytes=vram_totals.get(device.pci_id, 0),
                )
            )
    for entry in nvidia:
        if entry.pci_id not in matched:
            gpus.append(entry.state)
    return sorted(gpus, key=lambda gpu: gpu.pci_id)


def _collect_gpus(failures: list[str] | None = None) -> list[GpuState]:
    """Merge ``lspci`` devices with ``nvidia-smi`` details so no GPU is lost.

    ``lspci`` is the source of truth for which GPUs exist, because
    ``nvidia-smi`` only ever reports NVIDIA cards. NVIDIA entries are matched
    to their PCI slot and carry the richer driver and memory data; every other
    card is reported with its kernel driver and its VRAM read from DRM sysfs
    when the driver exposes it.
    """
    nvidia = _collect_nvidia_gpus(failures)
    devices = _collect_pci_gpus(failures)
    _discard_spurious_nvidia_failure(failures, nvidia, devices)
    if not devices:
        return sorted((entry.state for entry in nvidia), key=lambda gpu: gpu.pci_id)
    return _merge_gpus(devices, nvidia, _collect_vram_totals())


def _gpu_vendor(gpus: list[GpuState]) -> str:
    """Summarize the vendors of every detected GPU.

    Returns ``none`` without GPUs, the single shared vendor when they agree,
    ``unknown`` when only unidentified drivers were seen, and ``mixed`` when
    several different vendors are present.
    """
    vendors = {gpu.vendor for gpu in gpus}
    if not vendors:
        return VENDOR_NONE
    if vendors == {VENDOR_UNKNOWN}:
        return VENDOR_UNKNOWN
    if len(vendors) == 1:
        return next(iter(vendors))
    return VENDOR_MIXED


def _compute_platform(gpus: list[GpuState]) -> str:
    """Return a driver-derived hint for later compute diagnostics.

    ``nvidia`` suggests CUDA and ``amdgpu``/``radeon`` suggests ROCm. This
    heuristic proves neither GPU support, installed runtimes nor working compute.
    ``none`` means no matching driver evidence, not proof of no capability.
    """
    drivers = {gpu.driver for gpu in gpus}
    has_nvidia = "nvidia" in drivers
    has_amd = bool(drivers & {"amdgpu", "radeon"})
    if has_nvidia and has_amd:
        return "mixed"
    if has_nvidia:
        return "cuda"
    if has_amd:
        return "rocm"
    return "none"


def _collect_cuda_version(failures: list[str] | None = None) -> str:
    """Read driver-reported CUDA support from the banner, not a toolkit version."""
    output = run_tool(["nvidia-smi"], timeout=TIMEOUT_SECONDS, failures=failures)
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
    failures: list[str] = []
    # Collect the CUDA version before the GPU list: the GPU merge suppresses a
    # redundant ``nvidia-smi`` failure once PCI discovery proves no NVIDIA card.
    cuda_version = _collect_cuda_version(failures)
    gpus = _collect_gpus(failures)
    state = HardwareState(
        cpu_model=_read_cpu_model(),
        cpu_logical_cores=os.cpu_count() or 0,
        memory_total_bytes=_read_memory_total(),
        cuda_version=cuda_version,
        gpu_vendor=_gpu_vendor(gpus),
        compute_platform=_compute_platform(gpus),
        gpu=gpus,
    )
    if state.cpu_model == UNKNOWN:
        failures.append("CPU model unavailable")
    if state.memory_total_bytes == 0:
        failures.append("MemTotal unavailable or invalid")
    return CollectorResult(
        status=CollectorStatus.UNAVAILABLE if failures else CollectorStatus.OK,
        data=asdict(state),
        error="; ".join(dict.fromkeys(failures)),
    )


COLLECTOR = Collector(name="hardware", collect=_collect)
