# Configuration

`$XDG_CONFIG_HOME/lion/config.toml` (default `~/.config/lion/config.toml`) is
optional: a missing file means the built-in defaults, and LION never creates it
implicitly — only `lion watch dog on`/`off` write it (atomically, mode `600`).
A present but invalid file is a loud error naming the path; LION never applies a
broken config partially (ADR 0009).

## Schema (`schema_version = 1`)

| Key | Type | Meaning |
| --- | --- | --- |
| `schema_version` | integer | Always `1`; mandatory. |
| `watch.dog` | boolean | Guard intent: confirm critical input before it runs; default `false`. |

Everything else is rejected: unknown top-level keys, unknown `[watch]` keys,
non-integer schema versions and wrongly typed values are errors that name the
path. Example:

```toml
schema_version = 1

[watch]
dog = true
```

## Watch dog

`lion watch dog on|off|status` toggles the guard (ADR 0014, ADR 0020). The
state is the intent in the config; the effective switch for Zsh is the derived
artifact:

- `$XDG_DATA_HOME/lion/dog/rules.zsh` — the compiled guard rules: the
  classifier's `guard: "confirm"` rules as widened ERE patterns plus their rule
  ids, with the merged rule-set hash in the header. Written by `watch dog on`,
  mode `600`.

`on` writes the config first and the artifact second (a failure leaves the
guard inactive — fail-safe); `off` removes the artifact first. `on` is an
idempotent refresh that recompiles after a rule change. The guard loads through
the watch integration in interactive shells only; see
[Guard critical input](../how-to/guard-critical-input.md) for the user journey.

## `lion watch dog status --json`

| Key | Type | Meaning |
| --- | --- | --- |
| `enabled` | boolean | config intent (`watch.dog`). |
| `config_path` | string | the config file path. |
| `config_present` | boolean | whether the config file exists. |
| `rules_hash` | string | SHA-256 of the merged classifier rule set. |
| `confirm_rules` | integer | number of `guard: "confirm"` rules. |
| `artifact_path` | string | the compiled artifact path. |
| `artifact_present` | boolean | whether the artifact exists. |
| `artifact_hash` | string or null | rule-set hash of the artifact, `null` when absent or unreadable. |
| `artifact_stale` | boolean | artifact present but not matching `rules_hash` (including an unreadable header). |
| `integration_mode` | string | `shlib`, `zshrc` or `unknown` (unreadable integration state). |
| `integration_inserted` | boolean | whether the watch integration is present. |
| `warnings` | array | actionable drift warnings: missing/stale artifact while enabled, artifact present while disabled, missing integration. |

`status` is read-only and validates loudly: a broken config or an invalid
`classifier.json` fails with its path. Doctor reports the same state as the
`watch` topic findings `watch.dog` (config/drift/staleness) and
`watch.dog.integration`.
