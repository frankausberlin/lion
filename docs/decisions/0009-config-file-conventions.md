# 0009 — Config file location and format conventions

- **Status:** accepted
- **Date:** 2026-10-10

## Context

LION needs no configuration today: defaults are compiled in, commands read no
config, and every tunable so far belongs to the user's shell
(`~/.shlib`) rather than to LION. Later phases (shell integration, recordings,
the guard) will introduce settings that must persist and that a user may want to
change. Deciding the config's location and format *now*, before any reader
exists, keeps a later field from dragging a rushed path or format decision with
it and prevents drift between an implementation and the documentation.

## Decision

If and when a config file is needed, its conventions are fixed as follows.

- **Location:** `$XDG_CONFIG_HOME/lion/config.toml`, with the standard fallback
  to `~/.config/lion/config.toml` when `XDG_CONFIG_HOME` is unset. This matches
  the XDG base-directory convention LION already follows for its data
  (`$XDG_DATA_HOME/lion/`).
- **Format:** TOML, UTF-8.
- **Versioning:** a mandatory, strictly integer `schema_version`, starting at
  `1`. It is a version marker, not a feature flag; a missing or non-integer
  value is invalid.
- **Missing file:** built-in defaults apply; absence is not an error and never
  writes a file.
- **Unreadable or invalid file:** a loud error. LION never silently ignores or
  partially applies a broken config.
- **Fields:** a field is added only together with its real consumer; there is no
  speculative field. Every field is documented in `docs/reference/`.

## Consequences

- **No implementation in Phase 1.** There is no config reader, no config file
  and no CLI change. This ADR freezes the convention only; the first consumer
  arrives with the Phase 4/5 feature that needs a setting.
- Fixing location and format early avoids a migration once a file exists, and
  the `schema_version` field makes any future layout change explicit.
- Because the file is optional and defaults are compiled in, LION keeps working
  unchanged on a machine that has no config.
- The loud-error rule extends the fail-loud principle already used for history
  entries and shlib prechecks to configuration.

## Alternatives

- **Ship a minimal config skeleton now.** Rejected: it has no consumer in
  Phase 1, so it would be speculative work and dead surface.
- **Define the format together with the first field.** Rejected: it couples a
  settled, low-risk decision (where and how) to an as-yet-unknown feature design
  and reopens the location/format question under time pressure later.
