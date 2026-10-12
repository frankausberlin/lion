# 0013 — Annotation grammar v0 (`#_LION:`)

- **Status:** proposed (v0 draft from E1; grammar to be finalized with E3)
- **Date:** 2026-10-12

## Context

Installation guides want to attach *decisions* and *steps* to the input they
describe, so that a recording can be read back as "why" and "what" rather than
as a raw command list. The marker has to live inside ordinary shell input and
must not change what the shell executes. A block of pasted input may contain
strings, here-documents and shell metacharacters (`;`, `&&`, `|`), and a marker
that appears inside such a construct is text, not an annotation.

The E1 prototype confirmed two facts that shape the grammar: `preexec` never
fires for pure comment lines or empty lines (only `zshaddhistory` sees them),
and pasted multi-line input arrives as one buffer with embedded newlines.

## Decision

Annotations are written as comment lines marked with the prefix `#_LION:`.

- `decision.begin` / `decision.end` and `step.begin` / `step.end` carry readable
  fields: `id`, `title`, `component`, `scope`, `context`, `decision`,
  `consequences`, `alternatives`, `revisit_when`, `supersedes`.
- Parsing uses a **small, deliberately bounded** zsh mini-parser implemented in
  Python (quotes, here-documents, `;`/`&&`/`|`). A marker inside a string or a
  here-document body is **not** an annotation; a construct the parser cannot
  resolve stays unresolved (it is neither silently accepted nor guessed).
- The date and the local identity are filled in automatically. Origin
  (repository, commit, file) is recorded **only when it is actually known**.
- A decision, its intended implementation and the observed outcome are kept
  separately retrievable; the decision status is independent of whether the
  implementation succeeded.

Deliberately left open for v0 (to be resolved in E3): whether an annotation is
its own input block or sits inside a pasted block; how a missing end marker is
treated; and what happens when an `id` is repeated.

## Consequences

- Because `preexec` does not see comment lines, the annotation path has to read
  them from `zshaddhistory` (ADR 0011), or accept them only inside blocks that
  `preexec` already captured.
- The bounded parser is intentionally incomplete: for unclear constructs it
  produces no annotation rather than a wrong one, which keeps the fail-loud
  posture consistent with the rest of LION.
- Keeping decisions, implementations and outcomes separate means a failed
  installation does not erase the decision that led to it.

## Alternatives

- **A separate annotation file or sidecar.** Rejected: the annotation has to
  travel with the shell input to stay honest about what was actually run.
- **A full shell parser.** Rejected: too large, and the goal is a metadata
  marker, not shell execution semantics.
- **Markers recognised by simple substring search.** Rejected: it would
  misclassify markers inside strings and here-documents (`echo "#_LION: …"`),
  reproducing exactly the false-positive class the design forbids.
