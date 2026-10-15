# 0020 — Watch dog E5: config intent, a compiled artifact, config-driven rules

- **Status:** accepted
- **Date:** 2026-10-15

## Context

ADR 0014 fixed the mechanism: the guard is a ZLE `accept-line` widget with a
cancellable double-Enter, loaded as static shell code. E5 ships it, so the open
questions of the prototype had to be decided: where the on/off state lives,
where the patterns come from (the ADR's "JSON rules" wording predates the
classifier rule set of ADR 0015), how the artifact stays in sync with rule
changes, where the guard loads, which rules are critical by default, and how a
pattern that Python's `re` accepts but glibc ERE rejects is caught.

The guard must work without an active recording, and the toggle must be safe:
any failure may leave the guard **inactive**, never half-armed. Loading must
stay silent and process-free in the key path (instant-prompt compatibility).

## Decision

**State is intent, the artifact is the switch.** The on/off state lives as
`[watch] dog` in `$XDG_CONFIG_HOME/lion/config.toml` (ADR 0009; first real
consumer, integer `schema_version = 1`, unknown keys are loud errors). The
derived artifact `$XDG_DATA_HOME/lion/dog/rules.zsh` is the effective switch
for Zsh: no artifact, no guard. `on` writes the config first and the artifact
second (a later failure leaves the guard inactive — fail-safe); `off` removes
the artifact first and flips the config second. `on` is an idempotent refresh
that recompiles the rules; `status` never writes.

**Rules are the classifier's `guard: "confirm"` rules** — one source of truth,
no second rule file. The artifact carries the widened patterns, the parallel
rule ids and the merged rule-set hash as its header; the config layer stays
declarative. The default confirm set is deliberately small (7 rules):
`interruptions.reboot`, `interruptions.shutdown`, `containers.volume.rm`,
`containers.volume.prune`, `downloads.curl.pipe`, `downloads.wget.pipe` and
the new `shell.rm.recursive`, whose match shares the raw-buffer prefix class
`(^|[;&|[:space:]])(sudo[[:space:]]+)?` with the widget.

**Matching is a best-effort ERE search over the raw ZLE buffer.** A leading
`^` is widened to the same prefix class, so `sudo reboot` and `; rm -rf …` are
caught while `firm -rf` is not; a pattern without a leading `^` (the pipelines)
is used unchanged. Only the first flag token of `rm` is recognized: `rm -rf`
matches, `rm -v -rf` stays recognized only best-effort (documented limit).
Over-triggering is harmless (one extra Enter), under-triggering is the honest
limit of a guardrail.

**Compilation is validated once, loudly.** `on` renders the artifact
deterministically (hash header, transformed patterns, ids), checks it with
`zsh -n` and one Zsh process that compiles every pattern via `=~` (zsh reports
a failed `regcomp` on stderr, which is read as failure), refuses control
characters in a pattern, and only then writes atomically with mode `600`. A
compile failure writes nothing and can never damage a working artifact; after a
rule change, `lion doctor` reports the stale artifact as `watch.dog` warn and
`lion watch dog on` heals it.

**Loading goes through the watch integration** (single carrier, ADR 0011):
the delivered script sources the artifact only in an interactive shell when it
is readable, stays silent and never shows the guard when the artifact is
absent. Shells that already loaded the guard keep it until the next shell
starts even after `off` — the documented cost of a process-free key path.

## Consequences

- The double-Enter state is a buffer comparison: the second Enter runs exactly
  the armed text, any edit re-arms; pastes are covered because the widget sees
  the whole buffer.
- Doctor gains `watch.dog` (config/drift/staleness, error on a broken config
  or rule set) and `watch.dog.integration`; a `warn` publishes the usual reco.
- The README, `lion --help` and the how-to state plainly that the guard is a
  guardrail, not a sandbox and not a security boundary.
- A broken `classifier.json` fails `watch dog status` and doctor loudly, like
  `classify` itself; the guard keeps running with its last compiled artifact.
- The widget replaces `accept-line`; foreign wrappers chain through the
  published widget name `lion-dog-accept` (ADR 0014 interop rule).

## Alternatives

- **On/off state in a state file of its own** (for example a marker file next
  to the artifact). Rejected: two sources of intent invite drift; the config
  conventions already exist for exactly this (ADR 0009).
- **The artifact as the only state** (presence equals on). Rejected: no way to
  tell "never enabled" from "enabled but artifact lost", and doctor could not
  report intent-vs-artifact drift.
- **A generated widget function instead of pattern arrays** (one `if` per
  rule). Rejected: the arrays keep rendering and the widget static and small;
  a per-rule `if` would grow the key path with the rule set.
- **Compiling with Python `re` only.** Rejected: Python `re` is not glibc
  ERE; the Zsh compile run is the actual engine's verdict.
- **A wider default confirm set** (all remove-like or download-like rules).
  Rejected: over-triggering trains users to double-Enter blindly; the set is a
  conservative maintainer decision and stays extensible per rule id.
- **Watching the artifact for `off` in already-running shells.** Rejected:
  needs a periodic check (process or timer) in the shell, violating the
  process-free input path; new-shell semantics are sure and cheap.
