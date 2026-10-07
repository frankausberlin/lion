# Collector model

Collectors turn the machine into comparable data. They share one framework and
one contract.

## Shared framework

Shared types (`CollectorStatus`, `CollectorResult`, `Collector` and
`collect_state`) live in `src/lion/state/collector.py`, which imports no
collector module to avoid cycles. `src/lion/state/tools.py` provides the shared
external-tool runner. Each collector keeps its frozen dataclass next to a
private `_collect()` and exports `COLLECTOR`; `src/lion/state/registry.py`
exposes the ordered `COLLECTORS`; `src/lion/state/model.py` defines the
persisted `Snapshot`, its strict validation and the comparison rules.

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

The data fields of each collector are listed in
[Collectors](../reference/collectors.md).
