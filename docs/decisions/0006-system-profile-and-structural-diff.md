# 0006 — System profile and structural diff

- **Status:** accepted
- **Date:** 2026-10-09

## Context

A hardware change such as replacing an AMD GPU with an NVIDIA one, or a missing
tool such as `nvidia-smi`, surfaced as noise: many individual GPU values moved
at once and the diff could not say whether the platform or merely a reading had
changed. GPU identity also conflated the kernel driver module with the driver
version, and tool availability was invisible.

## Decision

Split GPU identity into `pci_id`, `vendor`, `driver` (kernel module) and
`driver_version`, derive a profile (`gpu_vendor`, `compute_platform`) in the
`hardware` collector, and record tool availability as facts in a new `tools`
collector. In the diff, match list entries by identity and mark any
`added`/`removed` as a structural change.

## Consequences

- Capability (`compute_platform`) and availability (`tools.available`) are
  distinct: an NVIDIA driver implies `cuda` even when `nvidia-smi` is missing.
- A GPU swap reads as removed + added; a changed VRAM reading on the same
  `pci_id` reads as a value change. `status`/`diff` flag structural changes with
  `struktur_geaendert` and a leading `Struktur geändert.` line.
- The change is additive: `schema_version` stays `1`, new collectors and fields
  are tolerated by validation, and old snapshots stay readable. The first scan
  after an upgrade appears once as a structural addition.
- `amdgpu`/`radeon` is only an approximation of ROCm capability, not proof that
  ROCm is installed; the CUDA version stays separate in `cuda_version`.
- The `tools` collector is always `ok`: a missing tool is data, not a warning.

## Alternatives

- A schema-version bump was rejected: it would hard-reject existing history
  entries for a purely additive change.
- Tool availability inside `hardware` was rejected: tools are cross-cutting and
  `doctor` will check them independently.
- Requiring `nvidia-smi` to declare CUDA capability was rejected: that would
  hide the case of NVIDIA hardware without the tool.
