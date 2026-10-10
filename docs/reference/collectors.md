# Collectors

Each collector lives with its dataclass in `src/lion/state/<collector>.py` and
produces one section of the state. Every section has the common keys `status`
(`ok`, `unavailable` or `error`) and `error` (a message, empty on success),
followed by the collector's data fields.

The registry (`src/lion/state/registry.py`) runs seven collectors in this order:
`host`, `hardware`, `network`, `packages`, `services`, `containers`, `tools`.

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
| `cuda_version` | Driver-reported CUDA support from the `nvidia-smi` banner, or `""`; not the installed toolkit version. |
| `gpu_vendor` | Derived GPU vendor across all cards: `nvidia`, `amd`, `intel`, `mixed`, `none` or `unknown`. |
| `compute_platform` | Driver-derived compute hint: `cuda`, `rocm`, `mixed` or `none`. |
| `gpu` | List of GPUs, sorted by `pci_id`; see below. |

Each `gpu` entry carries its own identity and driver facts:

| Field | Meaning |
| --- | --- |
| `pci_id` | Normalized PCI bus id (e.g. `0000:01:00.0`), the PCI-slot list identity, not a physical-card identifier. |
| `name` | Device description from `lspci`, or the `nvidia-smi` name when available. |
| `vendor` | Vendor from numeric PCI sysfs metadata, with a branded PCI-description fallback: `nvidia`, `amd`, `intel` or `unknown`. |
| `driver` | Kernel driver module in use (from `lspci`), e.g. `nvidia`, `amdgpu`, `i915`. |
| `driver_version` | Driver version from `nvidia-smi`, or `""` for non-NVIDIA cards. |
| `memory_total_bytes` | VRAM total: from `nvidia-smi` or the DRM sysfs `mem_info_vram_total`, else `0`. |

Display controllers are enumerated with `lspci`; NVIDIA entries are enriched
with driver and memory details from `nvidia-smi`, and other cards get their VRAM
total from the DRM sysfs when the driver exposes it. `gpu_vendor` is
independent of the active driver. `compute_platform` uses driver evidence:
`nvidia` suggests `cuda`, `amdgpu`/`radeon` suggests `rocm`, and both suggest
`mixed`. This is a hint, not proof of supported GPUs, installed runtimes or
working compute. `none` means no matching driver evidence. `doctor` checks those
conditions separately.
Missing `/proc` files use `Unknown`/`0`; when the helper tools are unavailable
the GPU list is empty.

## `network`

Physical network interfaces read from `/sys/class/net` only (no external tools,
no root). Interfaces without a `device` symlink are virtual or ephemeral (`lo`,
`docker0`, `veth*`, bridges) and churn between scans, so they are excluded. No
IP addresses, link state or MTU are recorded: those are runtime facts.

| Field | Meaning |
| --- | --- |
| `interfaces` | List of physical interfaces, sorted by `name`; see below. |

Each `interfaces` entry carries:

| Field | Meaning |
| --- | --- |
| `name` | Interface directory name; the list identity. |
| `mac` | MAC address from the `address` file, or `""` when unreadable. |
| `type` | ARPHRD interface type from the `type` file (e.g. `1` = ether), or `0`. |
| `driver` | Kernel driver module from the `device/driver` symlink, or `""` when unbound. |

A missing `/sys/class/net` (non-Linux) marks the collector `unavailable`.

## `packages`

Installed Debian packages and `apt-mark` selections.

| Field | Meaning |
| --- | --- |
| `installed` | Mapping of package to version, keyed by `name:architecture` when architecture metadata is present. |
| `manual` | Sorted `apt-mark showmanual` selection. |
| `auto` | Sorted `apt-mark showauto` selection. |
| `held` | Sorted `apt-mark showhold` selection. |

Missing Dpkg or `apt-mark` marks the collector `unavailable`.

## `services`

Persistent systemd service unit-file states, read with
`systemctl list-unit-files --type=service` (read-only). Runtime facts such as
failed units or `is-system-running` are deliberately excluded: they churn and
belong to a future runtime collector.

| Field | Meaning |
| --- | --- |
| `units` | List of `*.service` units, sorted by `name`; each `{name, state}`. |

`state` is normalized against the known unit-file states (`enabled`, `disabled`,
`masked`, `static`, ...); anything else becomes `unknown`. `name` is the list
identity. A missing or failing `systemctl` marks the collector `unavailable`.

## `containers`

Container configuration from Docker or Podman (config facts only). The engine is
chosen once: Docker when its `info` succeeds, otherwise Podman; with neither
binary installed the collector stays `ok` with `runtime` `none` and empty lists.
A present engine that fails marks the collector `unavailable`; any section that
was collected is kept.

Every command uses fixed, whitelisted `--format` templates, so secrets in
`Config.Env` or arbitrary labels never reach stdout. `docker inspect` runs as a
second phase over the container names from `docker ps` and never dumps the whole
object. Container ids, state, health, IPs, timestamps and live stats are
dropped, and every list is sorted.

| Field | Meaning |
| --- | --- |
| `runtime` | `docker`, `podman` or `none`. |
| `server_version` | Engine server version, or `""`. |
| `storage_driver` | Engine storage driver, or `""`. |
| `rootless` | Whether the engine reports rootless mode. |
| `images` | List of images, sorted; each `{repository, tag, digest, size_bytes}` (no image id). |
| `volumes` | List of volumes, sorted; each `{name, driver}`. |
| `networks` | List of container networks, sorted; each `{name, driver, scope}`. |
| `containers` | List of containers, sorted by `name`; see below. |

Each `containers` entry carries `name`, `image_ref`, `restart_policy`,
`privileged`, `cap_add`, `cap_drop`, `readonly_rootfs`, `security_opt`, `ports`,
`networks` (names), `mounts`, `compose_project` and `compose_service`.

`ports` is built from `Config.ExposedPorts` and `HostConfig.PortBindings`
(configuration, not the runtime `NetworkSettings.Ports`): each is
`{container_port, protocol, host_port}`, with `host_port` empty for an
exposed-only port. `mounts` is each `{type, name, source, destination,
read_only}`.

List identities are `containers`, `volumes` and `networks` by `name`; `images`
by a computed reference (`repository:tag`, or `repository@digest` when the tag
is empty or `<none>`).

## `tools`

Executable visibility on the current process's `PATH`, measured with `shutil.which`.
This does not prove that a tool runs successfully or that a runtime is installed.
This collector always reports `ok`: a missing tool is data, not a warning.

| Field | Meaning |
| --- | --- |
| `available` | Mapping of normalized name to bool: `lspci`, `nvidia_smi`, `rocm_smi`, `apt_mark`, `systemctl`, `docker`, `podman`, `zsh`. |

The availability is separate from `hardware.compute_platform`, which reports the
driver hint; vendor identity and tool facts distinguish "NVIDIA card present but
`nvidia-smi` missing" from "no NVIDIA hardware at all". Tool visibility also
does not prove an engine is usable: `docker`/`podman` are recorded here, and the
`containers` collector reports separately whether one actually works.

## Adding a collector

New collectors must not abort a capture: report `unavailable` or `error` with a
message and return their data keys regardless. Never persist volatile fields.
The behavioural rules are explained in the
[collector model](../explanation/collectors.md).
