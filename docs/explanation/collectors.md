# Collector model

Collectors turn the machine into comparable data. They share one framework and
one contract.

## Shared framework

Shared types (`CollectorStatus`, `CollectorResult`, `Collector` and
`collect_state`) live in `src/lion/state/collector.py`, which imports no
collector module to avoid cycles. `src/lion/state/tools.py` provides the shared
external-tool runner, while `src/lion/state/tooling.py` is the separate `tools`
collector that records tool availability. Each collector keeps its frozen
dataclass next to a private `_collect()` and exports `COLLECTOR`;
`src/lion/state/registry.py` exposes the ordered `COLLECTORS`;
`src/lion/state/model.py` defines the persisted `Snapshot`, its strict
validation and the comparison rules.

## The contract

- **Never abort the capture.** A failing collector must not stop the others. An
  unexpected exception is turned into an `error` section carrying the message,
  and a collector that cannot run reports `unavailable` instead. The section is
  always present with its `status` and `error`.
- **No volatile fields.** Clocks, temperatures and uptime are never persisted;
  they would make every scan look like a change.
- **Missing tools are not errors of the machine.** External tools (`nvidia-smi`,
  `lspci`, `apt-mark`, Dpkg) are environment-dependent. When they are absent,
  the collector degrades (empty GPU list, `unavailable` packages) rather than
  failing the whole scan.
- **Deterministic output.** Collector data is serialized from frozen dataclasses
  in a stable order so equal machines produce equal sections.

Because capture never aborts, a persisted snapshot always contains every
collector section, which is what makes the comparison rules in the
[comparison model](comparison-model.md) well defined.

## Capability versus availability

Two facts are deliberately kept apart:

- **Capability** is derived from hardware and drivers and lives in `hardware`:
  `gpu_vendor` and `compute_platform`. An NVIDIA kernel driver yields `cuda`
  even when `nvidia-smi` is not installed; `amdgpu`/`radeon` yields `rocm` as a
  capability, not proof that ROCm is installed.
- **Availability** is where a tool simply exists on `PATH`, recorded in the
  `tools` collector as `available` booleans. A missing tool is never a warning.

Together they keep "NVIDIA card present, but `nvidia-smi` missing" visible
instead of collapsing it into "no GPU" or "no CUDA".

The data fields of each collector are listed in
[Collectors](../reference/collectors.md).

## Capture diagnostics

External-tool failures retain a stable reason: missing executable, timeout,
nonzero exit code, or execution/decoding failure. Output and arguments are not
included. Hardware keeps available readings, but failed discovery, missing CPU
model or missing/invalid MemTotal mark the section `unavailable`. NVIDIA tooling
is optional when PCI discovery succeeds without NVIDIA and no NVIDIA query
identifies a device. Optional per-device VRAM readings retain the zero fallback.

`scan` and `status` warn on stderr for every non-OK collector, including unchanged
failures and JSON mode. An incomplete capture can still be saved and exits
successfully; its status and error are persisted and affect comparisons.
