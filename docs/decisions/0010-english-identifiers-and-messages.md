# 0010 — English identifiers and messages

- **Status:** accepted
- **Date:** 2026-10-10

## Context

LION's early prototypes carried German placeholder names into the data
contracts and the CLI output: persisted snapshot keys (`erstscan`,
`zuletzt_bestaetigt`), JSON output keys (`ereignis`, `geaendert`,
`struktur_geaendert`, …), German resolve aliases (`aktuell`, `vorherig`) and
German user-facing messages. The repository language rule is English, and the
contracts are public: they are documented in `docs/`, consumed by tests and
future agent tooling (MCP in Phase 6). A mixed-language contract is confusing
to maintain and violates that rule. The maintainer decided to migrate
everything to English now, before any external consumer exists.

## Decision

All identifiers and user-visible strings are English: JSON/TOML field names,
CLI messages, doctor findings and the generated `reco.sh` content. German is
confined to the maintainer conversation and the untracked `ignore/` documents.

The persisted snapshot schema bumps from `1` to `2`, with this mapping:

| Schema-1 (German) | Schema-2 (English) | Where |
| --- | --- | --- |
| `erstscan` | `created_at` | snapshot TOML/JSON |
| `zuletzt_bestaetigt` | `confirmed_at` | snapshot TOML/JSON |
| `ereignis` | `event` | `scan --json` |
| `pfad` | `path` | `scan --json`, reference JSON |
| `zustand` | `state` | `scan --json` |
| `geaendert` | `changed` | `status --json`, `diff --json` |
| `struktur_geaendert` | `structure_changed` | `status --json`, `diff --json` |
| `seit` | `since` | `status --json` |
| `unterschiede` | `differences` | `status --json`, `diff --json` |
| `von` / `bis` | `from` / `to` | `diff --json` |
| `aktuell` | `latest` | `history --json` |
| `eintraege` | `entries` | `history --json` |
| `geprueft` | `checked` | `doctor --json` |
| `befunde` | `findings` | `doctor --json` |
| `zusammenfassung` | `summary` | `doctor --json` |
| `reco_pfad` | `reco_path` | `doctor --json` |
| aliases `aktuell`, `vorherig` | dropped (use `latest`/`head`, `previous`/`prev`) | references |

Schema-1 history entries that still use the German keys are rejected with a
loud, actionable error naming the file, the offending keys and their new
names. Read commands never rewrite the history (ADR 0003 write boundary), so
migration is manual: archive or delete the affected entries and run
`lion scan` to start a fresh history. This mirrors the earlier `scans/` →
`history/` clean break.

## Consequences

- Breaking for stored history and for any consumer of the JSON contracts.
  Acceptable pre-1.0 and before external consumers exist.
- The mapping table above is the audit trail; references to the old names in
  earlier ADRs were updated to the current names so the docs stay navigable.
- Testing gets simpler: one language in assertions and one expected output.
- A future schema change should keep the rule: bump `schema_version`, reject
  old files with a named migration hint, never silently translate.

## Alternatives

- **Read-side compatibility shim** accepting both key sets and translating on
  read: permanent complexity in the strict validation path, and a silent
  migration would still be impossible because read commands must not write.
- **Keep German display strings** for the maintainer only: two output
  languages to test and document, with drift between them.
- **Automatic migration on `scan`**: would rewrite published history entries,
  violating the `os.link` never-overwrite invariant (ADR 0003).
