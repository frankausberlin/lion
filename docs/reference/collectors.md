# Collectors

Each collector lives with its dataclass in `src/lion/state/<collector>.py` and
produces one section of the state. Every section has the common keys `status`
(`ok`, `unavailable` or `error`) and `error` (a message, empty on success),
followed by the collector's data fields.

Version 1 ships four collectors.

## `host`

Static identity of the host and its operating system.

| Field | Meaning |
| --- | --- |
| `hostname` | Node name. |
| `distribution` | Distribution name from `/etc/os-release` (`NAME`). |
| `distribution_version` | Distribution version (`VERSION_ID`). |
| `kernel` | Kernel release. |
| `architecture` | Machine architecture. |

Missing values are stored as `Unknown`.

## `hardware`

Stable hardware facts. No volatile fields (clocks, temperatures, uptime).

| Field | Meaning |
| --- | --- |
| `cpu_model` | CPU model from `/proc/cpuinfo`. |
| `cpu_logical_cores` | Number of logical CPUs. |
| `memory_total_bytes` | `MemTotal` in bytes. This is the only field compared with a tolerance; see the [comparison model](../explanation/comparison-model.md). |
| `cuda_version` | CUDA version parsed from the `nvidia-smi` banner, or `""`. |
| `gpu_vendor` | Derived GPU vendor across all cards: `nvidia`, `amd`, `intel`, `mixed`, `none` or `unknown`. |
| `compute_platform` | Derived compute capability from the drivers: `cuda`, `rocm`, `mixed` or `none`. |
| `gpu` | List of GPUs, sorted by `pci_id`; see below. |

Each `gpu` entry carries its own identity and driver facts:

| Field | Meaning |
| --- | --- |
| `pci_id` | Normalized PCI bus id (e.g. `0000:01:00.0`), the list identity. |
| `name` | Device description from `lspci`, or the `nvidia-smi` name when available. |
| `vendor` | Vendor from the kernel driver: `nvidia`, `amd`, `intel` or `unknown`. |
| `driver` | Kernel driver module in use (from `lspci`), e.g. `nvidia`, `amdgpu`, `i915`. |
| `driver_version` | Driver version from `nvidia-smi`, or `""` for non-NVIDIA cards. |
| `memory_total_bytes` | VRAM total: from `nvidia-smi` or the DRM sysfs `mem_info_vram_total`, else `0`. |

Display controllers are enumerated with `lspci`; NVIDIA entries are enriched
with driver and memory details from `nvidia-smi`, and other cards get their VRAM
total from the DRM sysfs when the driver exposes it. `gpu_vendor` and
`compute_platform` are derived from the kernel drivers, not from tool
availability: an NVIDIA driver implies `cuda` even when `nvidia-smi` is missing,
and `amdgpu`/`radeon` implies `rocm` (capability, not installed software).
Missing `/proc` files use `Unknown`/`0`; when the helper tools are unavailable
the GPU list is empty.

## `tools`

Availability of the external tools LION relies on, measured with `shutil.which`.
This collector always reports `ok`: a missing tool is data, not a warning.

| Field | Meaning |
| --- | --- |
| `available` | Mapping of normalized name to bool: `lspci`, `nvidia_smi`, `rocm_smi`, `apt_mark`, `zsh`. |

The availability is separate from `hardware.compute_platform`, which reports the
hardware capability; the two together distinguish "NVIDIA card present but
`nvidia-smi` missing" from "no NVIDIA hardware at all".

## `packages`

Installed Debian packages and `apt-mark` selections.

| Field | Meaning |
| --- | --- |
| `installed` | Mapping of package to version, keyed by `name:architecture` when architecture metadata is present. |
| `manual` | Sorted `apt-mark showmanual` selection. |
| `auto` | Sorted `apt-mark showauto` selection. |
| `held` | Sorted `apt-mark showhold` selection. |

Missing Dpkg or `apt-mark` marks the collector `unavailable`.

## Adding a collector

New collectors must not abort a capture: report `unavailable` or `error` with a
message and return their data keys regardless. Never persist volatile fields.
The behavioural rules are explained in the
[collector model](../explanation/collectors.md).
