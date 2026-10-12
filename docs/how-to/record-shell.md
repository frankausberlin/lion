# Record a shell session

`lion watch` records what you type in Zsh — command blocks with their exit
status and duration, plus pure comment lines — and keeps the recording readable
across `exec zsh`, terminal crashes and reboots. Capture happens entirely inside
Zsh; Python never runs in the input path. The record lives in its own data space
and never touches the scan history.

## 1. Insert the shell integration

```bash
uv run lion shell insert
```

The integration goes into the recognized artifact of your setup:

- with the shell library installed: `~/.shlib/shlibs/90-lion-watch.sh`;
- otherwise: a marked `# >>>>>> LION watch` block at the end of `~/.zshrc`.

`insert` never overwrites: it backs up `~/.zshrc` first (`.before-lion-watch`),
validates the result with `zsh -n` and refuses to touch an artifact that already
exists with different content. Check where it lives with:

```bash
uv run lion shell status          # mode, path, modified, warnings
uv run lion shell status --json
```

Open a new shell (or `exec zsh`) afterwards. Loading is silent, so the
Powerlevel10k instant prompt is not disturbed. With Powerlevel10k loaded, a red
dot (`●`) appears in the right prompt while a recording is active; the segment
only reads a small pointer file and never starts a scan or a process.

## 2. Start a recording

```bash
uv run lion watch start --note "debugging the wifi driver"
```

`start` collects the machine state once and stores it as the recording's start
state. Exactly one recording is active per user and machine; a second `start`
fails loudly. While recording, `lion status` appends a short banner, and
`watch status` compares the current machine against the start state:

```bash
uv run lion watch status
uv run lion watch status --json   # recording, segments, changes
```

## 3. Work, then stop

Every shell prompt is a capture point: if recording started after your shell was
already open, it joins with the next prompt. Then:

```bash
uv run lion watch stop
```

`stop` collects the state once more, publishes `state-end.toml` and removes the
pointer. A recording does not need `stop` to stay readable: if the shell exits
(`exec zsh`, SIGHUP) or the machine reboots, segment liveness is derived when
reading and a missing end is never reported as success. See
[Recordings](../reference/recordings.md).

## 4. Read the recording

```bash
uv run lion watch list            # ref, status, event count, size
uv run lion watch show            # the active or latest recording
uv run lion watch show latest --json
uv run lion watch show 2026-10-12T09  # any unique prefix
```

`show` prints the ordered events: commands with their working directory, results
with exit status and duration, comments and exits. `status`, `list` and `show`
are read-only.

## Secrets

Masking is best-effort: assignments whose name looks sensitive (`TOKEN`,
`SECRET`, `PASSWORD`, `PASSWD`, `API_KEY`, `APIKEY`, `CREDENTIAL`) and
`Authorization:` headers are masked to `***` before the event is written. This
is a convenience, not a security boundary. Disable it with `export
LION_WATCH_MASK=0` when the masker gets in your way.

## Remove the integration

```bash
uv run lion shell remove
```

`remove` deletes exactly the recognized artifact; if a foreign file or two
artifacts are found, it refuses and asks you to reconcile. Removing while a
recording is active warns on stderr and proceeds: the record stays readable, new
shells load no hooks any more, and shells that already loaded the integration
keep recording until the recording is stopped or they exit.
