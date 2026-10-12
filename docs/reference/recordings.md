# Recordings

> Referenced by [ADR 0011](../decisions/0011-watch-capture-zsh-spool.md) and
> [ADR 0012](../decisions/0012-watch-recording-layout-liveness.md).

`lion watch` records shell input blocks in their own data space, separate from
the scan history. This page documents the paths, the on-disk formats and the
liveness rules. Command usage lives in `lion watch --help` and
[Record a shell session](../how-to/record-shell.md).

## Paths

Recordings live under `$XDG_DATA_HOME/lion/recordings`, defaulting to
`~/.local/share/lion/recordings`:

```text
recordings/
  active                         # one line: the ref of the open recording
  <ref>/                         # created_at in compact UTC form (+ ~NNNN on collision)
    meta.toml                    # schema_version, ref, status, started_at, …, note
    state-start.toml             # Snapshot at start (same model as the history)
    state-end.toml               # Snapshot at stop, published once
    events/
      100-452123-4096.log        # one file per shell segment
```

The record directory uses mode `700`, every file `600`. `active` exists only
while a recording is open; `start` refuses to run while it exists and never
overwrites it (it is published with `os.link`). New records and states are never
overwritten: a same-instant record directory receives a `~NNNN` suffix.

`meta.toml` fields:

| Key | Type | Meaning |
| --- | --- | --- |
| `schema_version` | integer | Always `1` for the current format. |
| `ref` | string | The record's directory name. |
| `status` | string | `open` or `stopped`. |
| `started_at` | string | Start instant (ISO 8601 with a UTC offset). |
| `stopped_at` | string | Stop instant; present exactly for `stopped`. |
| `note` | string | Free text from `--note` (the stop note replaces the start note). |
| `integration_version` | string | The LION version that started the recording. |

## Event grammar

The zsh hooks append **one TAB-separated, escaped line per event**. Each field
is escaped with the zsh parameter expansion `${(q)value}`; a raw TAB therefore
never appears inside a field. Escaping and masking use no subshell and no
external process. The line kinds are:

| Line | Fields after the kind |
| --- | --- |
| `segment` | `boot_id`, `pid`, shell start ticks, segment start (`EPOCHREALTIME`) |
| `cmd` | time, cwd, command text (`preexec` `$1`, falling back to `$3`) |
| `result` | time, exit status, `t0`, `t1` (both `EPOCHREALTIME` snapshots) |
| `comment` | time, cwd, comment text (pure comment lines via `zshaddhistory`) |
| `exit` | time, exit status (`zshexit`; a hint, never required) |

`${(q)}` emits backslash-escaped metacharacters and `$'...'` groups for control
characters (`$'\n'`, `$'\t'`, octal `$'\033'`). A field is capped at 4096
characters; when the cap cuts into an escape, the reader returns the incomplete
tail literally instead of failing. Empty lines and non-comment history lines are
not recorded.

Secret masking is best-effort and documented as such (ADR 0011): values of
assignments whose name contains `TOKEN`, `SECRET`, `PASSWORD`, `PASSWD`,
`API_KEY`, `APIKEY` or `CREDENTIAL` become `***`, as do `Authorization:` header
values. Set `LION_WATCH_MASK=0` to opt out. The masker can be fooled (for
example by concatenated quoted strings); it is a convenience, not a security
boundary.

## Segment liveness

A recording can outlive its shell (`exec zsh`, SIGHUP, crash, reboot), so a
segment is not closed by an event but derived when reading. Each segment is
stamped with the kernel `boot_id` plus the writing process's PID and start ticks
(`/proc/<pid>/stat`, field 22). While reading, the segment is `alive` only when
all three still match; otherwise it is ended with a reason:

| Reason | Meaning |
| --- | --- |
| `reboot` | The boot id differs from the current one. |
| `gone` | The PID no longer exists. |
| `reused` | The PID exists but belongs to a different process (start ticks differ). |
| `unknown` | The boot id cannot be read. |

A missing `exit` event is therefore never treated as success, and a segment that
wrote its `exit` event but crashed later still ends by liveness. Normal `exit`
runs `zshexit`, `exec zsh` and SIGKILL do not; the `exit` event is only a
fast-path hint.

## Pointer lag between shells

A shell applies `start`/`stop` with its next prompt: the hooks refresh the
pointer in `precmd`. At most the block that is typed in that window can still
land in a just-stopped record; everything after it is dropped. Starting works
the same way — an already-open shell joins with its next prompt. A pointer that
is late, stale or unreadable never breaks the prompt; capture simply stays off.

## Clock guard and failure behavior

`watch start` and `watch stop` refuse to mutate anything when the system clock
precedes the newest known timestamp of the recording space (all `meta.toml`
times, both state snapshots and every segment start); `stop` additionally guards
against the last event of its own record. Nothing is written in that case.

Damaged records are always loud: readers name the file and line, and never skip
an entry silently. `lion doctor` reports the recording space without writing
(topic `watch`).

## Read-only commands

`watch status`, `watch list` and `watch show` never write; `lion status` only
appends a text banner while a recording is active, and even a damaged recording
space degrades to a stderr warning there instead of failing the comparison.
