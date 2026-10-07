# Collectors

Each collector lives with its dataclass in `src/lion/state/<collector>.py` and
produces one section of the state. Every section has the common keys `status`
(`ok`, `unavailable` or `error`) and `error` (a message, empty on success),
followed by the collector's data fields.

Version 1 ships three collectors.

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
| `gpu` | List of GPUs, each with `name`, `driver_version` and `memory_total_bytes`. |

Display controllers are enumerated with `lspci`; NVIDIA entries are enriched
with driver and memory details from `nvidia-smi`, and other cards get their VRAM
total from the DRM sysfs when the driver exposes it. Missing `/proc` files use
`Unknown`/`0`; when the helper tools are unavailable the GPU list is empty.

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
