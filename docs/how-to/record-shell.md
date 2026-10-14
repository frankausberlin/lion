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

`show` prints the events chronologically: commands with their working directory,
results with exit status and duration, comments and exits. When parallel shells
interleave, a continuation line marks the segment of the following events.
`status`, `list` and `show` are read-only.

## Attach an ADR

While recording, `#` comment lines can carry the decision behind what you run:

```zsh
#+LION: adr.title Antidote and Powerlevel10k for the interactive shell
#+LION: adr.component shell
git clone --depth=1 https://github.com/mattmc3/antidote.git ~/.antidote
#-LION: adr.decision Load plugins through Antidote and use Powerlevel10k.
#.LION: adr.status accepted
```

Read them back with `lion records`:

```bash
uv run lion records                 # counters over the recording space
uv run lion records list            # recordings with their ADR state
uv run lion records adrs            # all ADRs, oldest first
uv run lion records adrs latest     # one ADR in detail
uv run lion records adrs latest --json
```

The markers: `#-LION:` refers to the previous command block, `#+LION:` to the
next, `#.LION:` to the last referenced one. `adr.<name> <value>` sets a field,
`adr.<name>` deletes it, `adr` clears the entry, and prose after `#.LION:`
continues the last set field (see the example sequence in
[Records and ADRs](../reference/records-and-adrs.md)). Only full comment lines
count — a marker inside a string or a here-document body is text. `title` and
`decision` make an ADR `complete`; the ADR stays inside the recording and
never enters the scan history.

## Edit an ADR afterwards

`lion records edit` (or `lion records edit <REF>`) opens the ADR of a
**stopped** recording in `$EDITOR` as a TOML overlay and stores it back
atomically:

```bash
export EDITOR=vim
uv run lion records edit latest
```

Adjust the `[fields]` table, save and close. An invalid draft stops loudly and
keeps the temporary file so you can fix it; an empty `[fields]` table removes
the ADR on purpose. The overlay replaces the fields derived from the recorded
annotations — the raw events are never modified — and `lion records adrs
latest` shows `edited: true` afterwards. Only stopped recordings can be
edited; run `lion watch stop` first.

## Classify the input blocks

`lion classify` derives metadata from the recorded blocks — which package
manager ran, which service changed, which download was piped into a shell —
without executing anything and without storing the result:

```bash
uv run lion classify                 # the active, else the newest recording
uv run lion classify latest --json   # rule set, blocks and matches
```

While a recording is active you can read the same classification without
leaving the session; both reads are terse, text-only and never collect:

```zsh
lion watch R      # the ADR fields and every classified block: R1: …, R2: …
lion watch R-     # only the previous block, one line
```

A match is metadata, never proof that a command succeeded. Set your own rules
in `~/.config/lion/classifier.json` (one JSON file, rules override the
built-ins by id); the full schema, the group catalog and the ERE subset live in
[Classifiers](../reference/classifiers.md). Declare your own work rules (for
example "install Debian packages with nala") in
`~/.config/lion/policies.toml`; `lion classify` then evaluates every command
segment and shows `policy <id> <success|fail|conflict>` lines (read-only,
never blocking).

## Comments as captured input

Comments are information here, not noise: pasted setup guides with `#` lines are
part of what a recording keeps. That relies on zsh's
`interactive_comments` option being set (the Deen Lupysta setup does this
early; check with `[[ -o interactive_comments ]]`):

- a standalone comment line becomes one `comment` event and is never executed;
- a comment-only multi-line paste becomes one `comment` event (embedded
  newlines included);
- a pasted block that mixes comments and commands stays **one `cmd` event** with
  the comment lines in its text — the integration never records such a block
  twice.

Without the option, `#` lines are executed and therefore show up as (usually
failing) commands; see [Recordings](../reference/recordings.md).

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
