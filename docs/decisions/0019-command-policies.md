# 0019 — Command policies: TOML patterns evaluated per command

- **Status:** accepted
- **Date:** 2026-10-14

## Context

Recordings classify `cmd` blocks into metadata (ADR 0015), but the metadata
does not say whether a command followed the operator's own working rules
("install and remove Debian packages with nala"). The maintainer wants
declarative work rules that flag compliant and violating commands on read,
without blocking anything, without executing anything and without persisting an
evaluation result.

A block can hold several command segments (`apt install foo && nala upgrade`),
and merging the fields of different segments would be unsound: a `fail` hit in
one segment must not be softened by a `success` hit in another. The evaluation
unit is therefore the single command segment, not the block and not the line.

## Decision

Policies are declared as TOML data in `$XDG_CONFIG_HOME/lion/policies.toml`
(default `~/.config/lion/policies.toml`); a missing file or an empty policy
list activates nothing, a present but invalid file fails loudly with its path.
Each policy carries an `id`, a human-readable `text` and the optional criteria
`success` and `fail`. A criterion is a list of patterns over the closed
classifier group/field catalog: the list means OR (also across groups), the
fields inside one pattern mean AND, and a list value means OR over the values;
a missing field never matches, and there is no negation.

- TOML instead of YAML: `tomllib` is stdlib (no new dependency), duplicate
  keys are rejected by the parser itself, and the format matches the config
  conventions (ADR 0009) and the TOML artifacts of the ledger.
- Structured patterns instead of an expression language: without negation,
  every formula over equality and membership is a disjunction of conjunctions,
  so the pattern list is exactly as expressive as the supported operator
  fragment - with no parser, no precedence rules and no unevaluable
  comparisons.
- Evaluation is read-time and read-only: the engine attaches the
  segment-scope matches of each segment to a command and evaluates every
  policy against those matches only; block-scope matches such as
  `curl … | sh` stay block-level and are never evidence. Per policy and
  command the result is `success`, `fail`, `conflict` (both criteria match) or
  no result, with the contributing match indices kept as evidence; results
  appear in file order.
- The existing `--json` contracts stay byte-identical without a policy file;
  with one they gain a top-level `policies` list and a `commands` array per
  block, mirrored by `lion_classify` in `serve`. A policy outcome never
  changes the exit code.

## Consequences

- Work rules stay declarative data; the mechanism cannot execute, block or
  persist anything.
- `lion watch R`/`R-`, `records` and the block-level match contract stay
  untouched; policies are a `lion classify`/`lion_classify` concern.
- No negation (`!=`) in v1: the acceptance cases that depend on missing-field
  semantics disappear structurally; a future negation needs its own decision
  about missing fields.
- Block-scope matches are deliberately not policy evidence; evaluating
  pipelines as a whole would be its own change.
- Unlike the classifier rules, policies carry no content hash: the loaded
  definitions are part of the output, so every result explains itself.

## Alternatives

- **YAML with an expression language (`==`, `in`, `!=`, `&&`, `||`, parens).**
  Rejected: adds a dependency, duplicate-key handling and a parser/precedence
  layer whose only semantic surplus over pattern lists is negation, while the
  cross-group restriction would leave most of the grammar unused.
- **Policies as annotations on classifier rules.** Rejected: the same policy
  text would repeat across rules, a scope restriction (`install`/`remove`
  only) cannot ride on a rule, and recognition vocabulary and work rules have
  different owners.
- **Block-level evaluation.** Rejected: merging the fields of different
  segments would let `nala upgrade` soften `apt install foo`.
- **Policies inside `classifier.json`.** Rejected: that file overrides the
  bundled defaults and requires at least one rule; policies have no defaults
  and must stand alone.
