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

- Identity (`vendor` from PCI metadata), driver-derived hints
  (`compute_platform`) and PATH visibility (`tools.available`) are distinct.
  A NVIDIA driver suggests `cuda` even when `nvidia-smi` is missing; the hint
  proves neither runtime installation nor working compute.
- A different GPU slot reads as removed + added; a replacement or changed
  VRAM reading on the same `pci_id` reads as a value change. `status`/`diff` flag
  structural changes with
  `structure_changed` and a leading `Structure changed.` line.
- The change is additive: `schema_version` stays `1`, new collectors and fields
  are tolerated by validation, and old snapshots stay readable. The first scan
  after an upgrade appears once as a structural addition.
- `amdgpu`/`radeon` is a ROCm hint, not proof of hardware support, installation
  or working compute. `cuda_version` reports driver-supported CUDA from the
  banner, not an installed toolkit version.
- History equality and diffing share list identities: only unique string
  package selections and unique PCI-slot GPU entries ignore ordering. Other
  lists and ambiguous identities stay atomic, preserving duplicates and types.
- The `tools` collector is always `ok`: a missing tool is data, not a warning.

## Alternatives

- A schema-version bump was rejected: it would hard-reject existing history
  entries for a purely additive change.
- Tool availability inside `hardware` was rejected: tools are cross-cutting and
  `doctor` will check them independently.
- Requiring `nvidia-smi` to declare CUDA capability was rejected: that would
  hide the case of NVIDIA hardware without the tool.
