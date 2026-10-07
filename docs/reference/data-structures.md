# Data structures

This page documents the persisted snapshot, the shell-library status and the
JSON shapes emitted by the CLI. Field names are shown exactly as they appear on
disk and on stdout.

## Snapshot (`STATE`)

Every stored state is a `Snapshot` persisted as one TOML file. Its keys are:

| Key | Type | Meaning |
| --- | --- | --- |
| `schema_version` | integer | Always `1` for the current format. |
| `erstscan` | string | First observation of this state (ISO 8601 with a UTC offset). |
| `zuletzt_bestaetigt` | string | Last unchanged confirmation (ISO 8601 with a UTC offset). |
| `collectors` | table | One table per collector, each with `status`, `error` and its data. |

Minimal example:

```toml
schema_version = 1
erstscan = "2026-10-05T20:00:00.123456+00:00"
zuletzt_bestaetigt = "2026-10-05T20:00:00.123456+00:00"

[collectors.host]
status = "ok"
error = ""
hostname = "workstation"
```

Validation is strict: `schema_version` must be `1`, both timestamps must carry a
timezone offset, and every collector section must have a known `status`
(`ok`, `unavailable` or `error`). A file that fails validation stops the reading
command with its path.

## Shell library status (`STATE_SHLIB`)

`lion shlib status --json` prints one object:

```jsonc
{
  "installed": false,   // whether the managed block is present in ~/.zshrc
  "lock": "missing",    // "missing" | "unchanged" | "changed" vs. ~/.zshrc.lock
  "shlibs": [],         // names of the numbered scripts in ~/.shlib/shlibs
  "exports": [],        // names of the export files in ~/.shlib/exports
  "warnings": []        // readable warnings, e.g. a missing directory or a wrong mode
}
```

It has no `schema_version`; unlike the snapshot it is a transient report, not a
stored file.

## `scan --json`

```json
{
  "ereignis": "created",
  "pfad": "/home/user/.local/share/lion/history/2026-10-05T20-00-00.123456Z.toml",
  "zustand": {
    "schema_version": 1,
    "erstscan": "2026-10-05T20:00:00.123456+00:00",
    "zuletzt_bestaetigt": "2026-10-05T20:00:00.123456+00:00",
    "collectors": {"host": {"status": "ok", "error": "", "hostname": "workstation"}}
  }
}
```

`ereignis` is `created`, `confirmed` or `appended` (see the
[comparison model](../explanation/comparison-model.md)).

## `status --json`

```json
{
  "geaendert": true,
  "seit": "2026-10-05T20:00:00.123456+00:00",
  "unterschiede": {"host": {"changed": {"hostname": {"old": "a", "new": "b"}}}}
}
```

With no stored state it prints `null` and exits successfully. Operational or
invalid-state errors go to stderr with exit code `1` and no JSON on stdout.

## `history --json`

```json
{
  "eintraege": [
    {
      "index": 1,
      "ref": "2026-10-05T20-00-00.123456Z",
      "erstscan": "2026-10-05T20:00:00.123456+00:00",
      "zuletzt_bestaetigt": "2026-10-05T20:00:00.123456+00:00",
      "pfad": "/home/user/.local/share/lion/history/2026-10-05T20-00-00.123456Z.toml",
      "aktuell": true
    }
  ]
}
```

## `diff --json`

```json
{
  "von": {"ref": "…", "erstscan": "…", "zuletzt_bestaetigt": "…", "pfad": "…"},
  "bis": {"ref": "…", "erstscan": "…", "zuletzt_bestaetigt": "…", "pfad": "…"},
  "geaendert": true,
  "unterschiede": {"host": {"changed": {"hostname": {"old": "a", "new": "b"}}}}
}
```

`von` and `bis` use the same reference shape as one `history` entry (without
`index` and `aktuell`).

### The `unterschiede` shape

`unterschiede` is keyed by collector and only contains collectors that differ.
Each collector carries one or more of:

- `added` — dotted paths present only in the new state,
- `removed` — dotted paths present only in the old state,
- `changed` — dotted paths whose value changed, as `{"old": …, "new": …}`.

A collector that was added or removed wholesale is reported with the single key
`"(collector)"`.

## Config (planned)

A configuration file (`$XDG_CONFIG_HOME/lion/config.toml`) is planned but not
implemented yet.
