# 0015 — Classifier rules: read-time data in a POSIX-ERE subset

- **Status:** accepted
- **Date:** 2026-10-12

## Context

The classifier turns recorded shell input blocks into metadata (groups and
fields). The same rule vocabulary must serve two consumers: `lion classify` /
`lion watch R` in Python today, and the watch dog (E5, ADR 0014) that matches in
a Zsh `accept-line` widget without any Python process in the key path. Rules
must be user-extensible, deterministic and safe: loading a rule file must never
execute anything, and invalid rules must fail loudly instead of guessing.

Python's `re` module and POSIX ERE are close but not identical: `re` has no
POSIX character classes (`[[:space:]]`), while ERE has no backreferences,
non-greedy quantifiers, `\b`/`\d`/`\s` classes or `(?...)` group constructs.

## Decision

Rules are declared as JSON data, never code, with a closed group vocabulary
(`packages`, `services`, `containers`, `shell`, `downloads`, `interruptions`,
`checks`) and one optional rule per id per layer. The bundled defaults live in
`lion/classify/default_rules.json`; a user file at
`$XDG_CONFIG_HOME/lion/classifier.json` overrides rules by `id` and may append
new ones.

Patterns stay inside a documented POSIX-ERE subset with POSIX character
classes; the loader validates the subset loudly and translates the classes to
Python `re` at compile time. Empirically, `re.compile("[[:space:]]")` does not
mean whitespace, so the translation is mandatory, not a convenience. Segment
scope anchors at the segment start after stripping `sudo`/`NAME=value`
prefixes; block scope searches masked lines so quotes, here-doc bodies and
printed text never match.

Classification is derived from the raw recording events on every read: no
writer, no persisted classification, no new event type. The merged rule list
carries a deterministic SHA-256 content hash; `schema_version`, ruleset
`version` and the hash are part of every classification.

## Consequences

- E5 can compile the same validated rule strings with Zsh ERE; there is one
  rule format, not two dialects that could drift apart.
- User extension is data-only and fails loudly: outside the subset, unknown
  groups, unknown fields or bad capture references are errors, not guesses.
- Some convenient regex features (lookarounds, `\d`, non-greedy) are
  unavailable by design; patterns are validated against the subset instead.
- Stable provenance (ruleset version plus hash) makes a classification
  reproducible and debuggable after the rules changed.

## Alternatives

- **Python `re` dialect as the rule format.** Rejected: the dog could not
  compile the same rules in Zsh, and `[[:space:]]` would silently mean the
  wrong thing.
- **Rules as shell code or filters.** Rejected: the rules would run in the
  input path and could not be validated safely.
- **First-match-wins resolution.** Rejected: several rules may legitimately
  contribute to one block; the documented deterministic order plus
  deduplication is more honest.
- **Hard-coded classification rules.** Rejected: the roadmap requires user
  extendability with one schema shared by classifier and dog.
