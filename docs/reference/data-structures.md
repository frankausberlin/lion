# Data structures

This page documents the persisted snapshot, the shell-library status and the
JSON shapes emitted by the CLI. Field names are shown exactly as they appear on
disk and on stdout.

## Snapshot (`STATE`)

Every stored state is a `Snapshot` persisted as one TOML file. Its keys are:

| Key | Type | Meaning |
| --- | --- | --- |
| `schema_version` | integer | Always `2` for the current format. |
| `created_at` | string | First observation of this state (ISO 8601 with a UTC offset). |
| `confirmed_at` | string | Last unchanged confirmation (ISO 8601 with a UTC offset). |
| `collectors` | table | One table per collector, each with `status`, `error` and its data. |

Minimal example:

```toml
schema_version = 2
created_at = "2026-10-05T20:00:00.123456+00:00"
confirmed_at = "2026-10-05T20:00:00.123456+00:00"

[collectors.host]
status = "ok"
error = ""
hostname = "workstation"
```

Validation is strict: `schema_version` must be `2`, both timestamps must carry a
timezone offset, and every collector section must have a known `status`
(`ok`, `unavailable` or `error`). A file that fails validation stops the reading
command with its path. Schema-1 files that still use the German field names
(`erstscan`, `zuletzt_bestaetigt`) are rejected with a migration hint (ADR 0010).

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
  "event": "created",
  "path": "/home/user/.local/share/lion/history/2026-10-05T20-00-00.123456Z.toml",
  "state": {
    "schema_version": 2,
    "created_at": "2026-10-05T20:00:00.123456+00:00",
    "confirmed_at": "2026-10-05T20:00:00.123456+00:00",
    "collectors": {"host": {"status": "ok", "error": "", "hostname": "workstation"}}
  }
}
```

`event` is `created`, `confirmed` or `appended` (see the
[comparison model](../explanation/comparison-model.md)).

## `status --json`

```json
{
  "changed": true,
  "structure_changed": false,
  "since": "2026-10-05T20:00:00.123456+00:00",
  "differences": {"host": {"changed": {"hostname": {"old": "a", "new": "b"}}}}
}
```

`structure_changed` is `true` when any collector, key or list entry was added
or removed (see [The `differences` shape](#the-differences-shape)).

With no stored state it prints `null` and exits successfully. Operational or
invalid-state errors go to stderr with exit code `1` and no JSON on stdout.

## `history --json`

```json
{
  "entries": [
    {
      "index": 1,
      "ref": "2026-10-05T20-00-00.123456Z",
      "created_at": "2026-10-05T20:00:00.123456+00:00",
      "confirmed_at": "2026-10-05T20:00:00.123456+00:00",
      "path": "/home/user/.local/share/lion/history/2026-10-05T20-00-00.123456Z.toml",
      "latest": true
    }
  ]
}
```

## `diff --json`

```json
{
  "from": {"ref": "…", "created_at": "…", "confirmed_at": "…", "path": "…"},
  "to": {"ref": "…", "created_at": "…", "confirmed_at": "…", "path": "…"},
  "changed": true,
  "structure_changed": false,
  "differences": {"host": {"changed": {"hostname": {"old": "a", "new": "b"}}}}
}
```

`from` and `to` use the same reference shape as one `history` entry (without
`index` and `latest`).

### The `differences` shape

`differences` is keyed by collector and only contains collectors that differ.
Each collector carries one or more of:

- `added` — dotted paths present only in the new state,
- `removed` — dotted paths present only in the old state,
- `changed` — dotted paths whose value changed, as `{"old": …, "new": …}`.

A collector that was added or removed wholesale is reported with the single key
`"(collector)"`.

Every unordered list with a stable identity is compared per entry, using
bracket keys such as `gpu[0000:01:00.0]` or `interfaces[eth0]`:
`hardware.gpu` (by `pci_id`), `network.interfaces`, `services.units`,
`containers.containers`, `containers.volumes` and `containers.networks` (by
`name`), `containers.images` (by the computed reference), and the package
selections `packages.manual`, `packages.auto` and `packages.held` (by value). A
new slot appears as `removed` + `added`; a replacement in the same slot appears
as changed fields. Other lists, duplicate identities and malformed entries
retain their complete ordered value under `changed`.

Any `added` or `removed` entry makes the change *structural*; a diff with only
`changed` entries is a pure value change, echoed as `structure_changed` and the
leading `Structure changed.` line in text output.

## `doctor --json`

```json
{
  "status": "error",
  "checked": ["collectors", "tools", "history", "storage", "shlib"],
  "findings": [
    {"topic": "tools", "name": "tools.nvidia_smi", "status": "warn",
     "message": "…", "hint": "…", "commands": ["sudo …"]}
  ],
  "summary": {"ok": 5, "warn": 2, "error": 1, "skip": 3},
  "reco_path": "/home/user/.local/share/lion/recos/2026-10-09T12-00-00.123456Z.sh"
}
```

`status` is the aggregate `ok`/`warn`/`error` (`skip` is neutral). Each finding
has a stable `name`, its `topic`, one of the four statuses, a `message`, an
optional `hint` and any `commands` that would be executable. `reco_path` is
`null` when nothing was written (a clean run). See
[Diagnose with doctor](../how-to/diagnose-with-doctor.md).

## Config (planned)

A configuration file (`$XDG_CONFIG_HOME/lion/config.toml`) is planned but not
implemented yet.
