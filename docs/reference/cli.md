<!-- Generated file. Do not edit by hand. -->
<!-- Source: src/lion/cli.py and src/lion/main.py. Regenerate with: just docs -->

# CLI reference

Linux Operator Nerd: collect, store and compare the machine state.

**Usage**:

```console
$ lion [OPTIONS] COMMAND [ARGS]...
```

**Options**:

* `--version`: Show the version and exit.
* `--install-completion`: Install completion for the current shell.
* `--show-completion`: Show completion for the current shell, to copy it or customize the installation.
* `--help`: Show this message and exit.

Running 'lion' without a command is the same as 'lion status'.

Common commands:

  lion                 compare the current state with the latest stored one
  lion scan            collect and store the current state
  lion history         list stored states with their stable references
  lion diff 1 2        compare two stored states
  lion doctor          diagnose the machine and write a reviewable fix script
  lion shlib           manage the Zsh shell library (~/.shlib)
  lion shell           manage the watch shell integration (~/.zshrc, ~/.shlib)
  lion watch           record shell sessions and read the recordings
  lion records         read and edit the ADRs attached to recordings
  lion adr             document standalone decisions (ADRs)
  lion classify        classify recorded input blocks (read-only)
  lion --version       print the installed version

Run 'lion <command> --help' for details on a command.


**Commands**:

* `scan`: Collect the current state and store it in...
* `status`: Compare the current state with the latest...
* `history`: List stored states with their stable...
* `diff`: Compare two stored states without...
* `doctor`: Run a read-only diagnosis and write a...
* `classify`: Classify the recorded input blocks of a...
* `shlib`: Manage the Zsh shell library (~/.shlib).
* `shell`: Manage the watch shell integration (~/.zshrc, ~/.shlib).
* `watch`: Record shell sessions and read the recordings.
* `records`: Read and edit the ADRs attached to recordings.
* `adr`: Document standalone decisions (ADRs).

## `lion scan`

Collect the current state and store it in the history.

**Usage**:

```console
$ lion scan [OPTIONS]
```

**Options**:

* `--json`: Output the result as JSON.
* `--help`: Show this message and exit.

The first run creates a history entry, an unchanged run refreshes its
timestamp, and a changed state appends a new entry. Only 'scan' writes to the
history. Every run also regenerates the derived OKF v0.2 wiki under
$XDG_DATA_HOME/lion/wiki/ (index.md plus one page per collector); never edit it
by hand.

Examples:

  lion scan            collect and store the current state
  lion scan --json     emit the result as a single JSON object


## `lion status`

Compare the current state with the latest stored one (read-only).

**Usage**:

```console
$ lion status [OPTIONS]
```

**Options**:

* `--json`: Output the comparison as JSON.
* `--help`: Show this message and exit.

Read-only: it never changes the history or the wiki. Running 'lion' without a
command is the same as 'lion status'. The 'wiki:' line below the diff points at
the wiki written by the latest 'scan'; it can be minimally older than the fresh
collection.

Examples:

  lion status          show what changed since the latest stored state
  lion status --json   emit the comparison as a single JSON object


## `lion history`

List stored states with their stable references.

**Usage**:

```console
$ lion history [OPTIONS]
```

**Options**:

* `--json`: Output the entries as JSON.
* `--limit <int range>`: Show only the newest N entries (oldest first within the selection).  [x&gt;=1]
* `--help`: Show this message and exit.

Entries are listed oldest first. The REF column is the stable reference accepted
by 'lion diff'. '--limit N' shows only the newest N entries while keeping the
global indices.

Examples:

  lion history             list stored states, oldest first
  lion history --limit 5   show only the five newest states
  lion history --json      emit the listing as a single JSON object


## `lion diff`

Compare two stored states without collecting or writing.

**Usage**:

```console
$ lion diff [OPTIONS] {reference} [second]
```

**Arguments**:

* `reference`: Older state reference (see &#x27;lion history&#x27;).  [required]
* `second`: Newer state reference; defaults to the latest.

**Options**:

* `--json`: Output the comparison as JSON.
* `--help`: Show this message and exit.

Read-only. A reference is a 1-based index from 'lion history', an alias
('latest', 'previous'), the compact reference, a unique prefix or an ISO
timestamp. The second reference defaults to the latest state.

Examples:

  lion diff 1          compare entry 1 with the latest entry
  lion diff 1 2        compare entries 1 and 2
  lion diff previous   compare the previous entry with the latest


## `lion doctor`

Run a read-only diagnosis and write a reviewable fix script.

**Usage**:

```console
$ lion doctor [OPTIONS]
```

**Options**:

* `--json`: Output the findings as JSON.
* `--show`: Also print the generated reco script (text mode only).
* `--help`: Show this message and exit.

Read-only. 'doctor' runs a fresh collection and checks the collectors, the external
tools, the history, the data directory, the wiki and the shell library. It never
stores that state and never runs a fix.

Findings use 'ok', 'warn', 'error' and 'skip'. If anything is 'warn' or 'error',
exactly one reviewable script is written under $XDG_DATA_HOME/lion/recos/ for
you to read and run yourself; LION never runs it.

Examples:

  lion doctor          show the diagnosis and the reco script path
  lion doctor --show   also print the reco script to stdout
  lion doctor --json   emit the findings as a single JSON object


## `lion classify`

Classify the recorded input blocks of a recording into groups and fields with
the declarative rule set: built-in defaults plus the optional user file
$XDG_CONFIG_HOME/lion/classifier.json.

The groups are packages, services, containers, shell, downloads, interruptions
and checks. A match is metadata derived from the input text, never a claim that
a command ran or succeeded; unsupported shell syntax yields no matches.

**Usage**:

```console
$ lion classify [OPTIONS] [reference]
```

**Arguments**:

* `reference`: Recording reference; defaults to the active or newest recording.

**Options**:

* `--json`: Output the classification as JSON.
* `--help`: Show this message and exit.

Read-only: it never collects a state, never changes a recording and never
executes anything. The classification is derived from the raw events on every
run; the JSON carries the rule set version and hash plus one object per block.

Examples:

  lion classify                the active, else the newest recording
  lion classify latest         one recording by reference
  lion classify --json         emit the classification as a single JSON object

Inside an active recording, 'lion watch R' lists the blocks tersely and
'lion watch R-' shows only the previous block.


## `lion shlib`

Manage the Zsh shell library that keeps ~/.zshrc small by moving configuration
into ~/.shlib/:

  exports/   one file per environment variable (name = variable, content = value)
  shlibs/    numbered scripts sourced in order (00-, 01-, ...)
  dash/      your own symlinks to important configuration files

**Usage**:

```console
$ lion shlib [OPTIONS] COMMAND [ARGS]...
```

**Options**:

* `--help`: Show this message and exit.

Running 'lion shlib' without an operation shows the status.

Examples:

  lion shlib            show the current status (same as 'lion shlib status')
  lion shlib install    install into ~/.zshrc and back up the current file
  lion shlib uninstall  flatten scripts into ~/.zshrc and
                        exports into ~/.zshrc.exports

Link a configuration file into dash:

  ln -s ~/.config/app/settings.toml ~/.shlib/dash/app_settings.toml

Opt a dash file into content monitoring with the LIONSTATE_ prefix:

  ln -s ~/.config/app/settings.toml ~/.shlib/dash/LIONSTATE_app_settings.toml

Load an existing script without moving it:

  ln -s ~/dotfiles/aliases.zsh ~/.shlib/shlibs/20-aliases.sh


**Commands**:

* `status`: Show whether the shell library is...
* `install`: Install the shell library and back up the...
* `uninstall`: Flatten scripts into ~/.zshrc and exports...

### `lion shlib status`

Show whether the shell library is installed and report its state.

**Usage**:

```console
$ lion shlib status [OPTIONS]
```

**Options**:

* `--json`: Output status as JSON.
* `--help`: Show this message and exit.

### `lion shlib install`

Install the shell library and back up the current ~/.zshrc.

**Usage**:

```console
$ lion shlib install [OPTIONS]
```

**Options**:

* `--help`: Show this message and exit.

### `lion shlib uninstall`

Flatten scripts into ~/.zshrc and exports into ~/.zshrc.exports.

**Usage**:

```console
$ lion shlib uninstall [OPTIONS]
```

**Options**:

* `--help`: Show this message and exit.

## `lion shell`

Manage the watch shell integration that records your Zsh input blocks into the
active recording:

  insert   install the integration (a script under ~/.shlib/shlibs when shlib
           is installed, otherwise a marked block in ~/.zshrc)
  remove   remove the recognized artifact again
  status   show mode, path, modified and warnings (read-only)

The integration loads silently and never starts a process in the input path.

**Usage**:

```console
$ lion shell [OPTIONS] COMMAND [ARGS]...
```

**Options**:

* `--help`: Show this message and exit.

Running 'lion shell' without an operation shows the status.

Examples:

  lion shell            show the current status (same as 'lion shell status')
  lion shell insert     install the integration and back up ~/.zshrc
  lion shell remove     remove the integration again

After 'lion shell insert', open a new shell or run 'exec zsh'. Existing shells
pick up start and stop with their next prompt.


**Commands**:

* `status`: Show whether the watch integration is...
* `insert`: Insert the watch integration without...
* `remove`: Remove the watch integration again.

### `lion shell status`

Show whether the watch integration is inserted and where.

**Usage**:

```console
$ lion shell status [OPTIONS]
```

**Options**:

* `--json`: Output status as JSON.
* `--help`: Show this message and exit.

### `lion shell insert`

Insert the watch integration without overwriting anything.

**Usage**:

```console
$ lion shell insert [OPTIONS]
```

**Options**:

* `--help`: Show this message and exit.

### `lion shell remove`

Remove the watch integration again.

**Usage**:

```console
$ lion shell remove [OPTIONS]
```

**Options**:

* `--help`: Show this message and exit.

## `lion watch`

Record shell sessions: start a recording, work in any Zsh, stop it later and
read the captured events:

  start    begin a recording (requires &#x27;lion shell insert&#x27;)
  stop     end the active recording; optional --note TEXT
  status   show the active recording and a diff since its start (read-only)
  list     list stored recordings with event counts and size (read-only)
  show     print the events of one recording (read-only)
  R        the active recording&#x27;s ADR fields and classified blocks (read-only)
  R-       the previous block&#x27;s one-line classification (read-only)

Events are captured in zsh; Python never runs in the input path.

**Usage**:

```console
$ lion watch [OPTIONS] COMMAND [ARGS]...
```

**Options**:

* `--help`: Show this message and exit.

One recording is active per user and machine. A recording survives shell
restarts, 'exec zsh' and reboots: segment liveness is derived from the kernel
boot id plus the writing shell's PID and start time, and a missing exit event
is never treated as success.

Examples:

  lion watch start --note "debugging wifi"
  lion watch status            what changed since the recording started
  lion watch list              stored recordings with events and size
  lion watch show latest       print the recorded events
  lion watch R                 ADR fields and blocks of the active recording
  lion watch R-                the previous block in one line
  lion watch stop --note "done"

Secret masking is best-effort; set LION_WATCH_MASK=0 to opt out.


**Commands**:

* `status`: Show the active recording and what changed...
* `start`: Start a recording; requires &#x27;lion shell...
* `stop`: Stop the active recording and store its...
* `list`: List stored recordings with their event...
* `show`: Print the events of one recording...
* `R`: Show the active recording&#x27;s ADR fields and...
* `R-`: Show the classified previous block of the...

### `lion watch status`

Show the active recording and what changed since it started.

**Usage**:

```console
$ lion watch status [OPTIONS]
```

**Options**:

* `--json`: Output status as JSON.
* `--help`: Show this message and exit.

### `lion watch start`

Start a recording; requires &#x27;lion shell insert&#x27;.

**Usage**:

```console
$ lion watch start [OPTIONS]
```

**Options**:

* `--note <str>`: Free-text note stored with the recording.
* `--help`: Show this message and exit.

### `lion watch stop`

Stop the active recording and store its end state.

**Usage**:

```console
$ lion watch stop [OPTIONS]
```

**Options**:

* `--note <str>`: Free-text note stored with the recording.
* `--help`: Show this message and exit.

### `lion watch list`

List stored recordings with their event counts and size.

**Usage**:

```console
$ lion watch list [OPTIONS]
```

**Options**:

* `--json`: Output the list as JSON.
* `--help`: Show this message and exit.

### `lion watch show`

Print the events of one recording (read-only).

**Usage**:

```console
$ lion watch show [OPTIONS] [reference]
```

**Arguments**:

* `reference`: Recording reference (&#x27;latest&#x27;, &#x27;active&#x27;, a unique prefix); defaults to active or latest.

**Options**:

* `--json`: Output the recording as JSON.
* `--help`: Show this message and exit.

### `lion watch R`

Show the active recording&#x27;s ADR fields and every classified block.

**Usage**:

```console
$ lion watch R [OPTIONS]
```

**Options**:

* `--help`: Show this message and exit.

### `lion watch R-`

Show the classified previous block of the active recording.

**Usage**:

```console
$ lion watch R- [OPTIONS]
```

**Options**:

* `--help`: Show this message and exit.

## `lion records`

Read and edit the decisions (ADRs) attached to recordings as flat ``#-LION:``,
``#+LION:`` and ``#.LION:`` annotation lines:

  status   overview of the recording space and the ADR counters (read-only)
  list     recordings like &#x27;lion watch list&#x27; plus an ADR column (read-only)
  adrs     list all ADRs; with REF show one ADR in detail (read-only)
  edit     edit the ADR overlay (adr.toml) of a stopped recording in $EDITOR

Annotations stay inside the recording; the read commands only read them, and
&#x27;edit&#x27; writes the record&#x27;s single adr.toml overlay.

**Usage**:

```console
$ lion records [OPTIONS] COMMAND [ARGS]...
```

**Options**:

* `--help`: Show this message and exit.

Running 'lion records' without an operation shows the status. Read commands never
collect a state and never write.

The markers '#-LION:' (previous block), '#+LION:' (next block) and '#.LION:'
(ditto) count only as full comment lines outside strings and here-documents.
Fields are 'adr.<name> <value>', 'adr.<name>' deletes a field and 'adr' clears
the entry; '#.LION:' text without an 'adr' token continues the last field.

Examples:

  lion records                 recording space and ADR counters
  lion records list            recordings with event counts and ADR state
  lion records adrs            all ADRs, oldest first
  lion records adrs latest     the newest recording's ADR in detail
  lion records edit latest     edit the ADR overlay of a stopped recording


**Commands**:

* `status`: Show the recording space and the ADR...
* `list`: List stored recordings with their ADR...
* `adrs`: List all ADRs, or show one ADR in detail...
* `edit`: Edit the ADR overlay of a stopped...

### `lion records status`

Show the recording space and the ADR counters (read-only).

**Usage**:

```console
$ lion records status [OPTIONS]
```

**Options**:

* `--json`: Output status as JSON.
* `--help`: Show this message and exit.

### `lion records list`

List stored recordings with their ADR state (read-only).

**Usage**:

```console
$ lion records list [OPTIONS]
```

**Options**:

* `--json`: Output the list as JSON.
* `--help`: Show this message and exit.

### `lion records adrs`

List all ADRs, or show one ADR in detail when REF is given.

**Usage**:

```console
$ lion records adrs [OPTIONS] [reference]
```

**Arguments**:

* `reference`: Recording reference for the detail view; without it all ADRs are listed.

**Options**:

* `--json`: Output the ADR(s) as JSON.
* `--help`: Show this message and exit.

### `lion records edit`

Edit the ADR overlay of a stopped recording in $EDITOR.

**Usage**:

```console
$ lion records edit [OPTIONS] [reference]
```

**Arguments**:

* `reference`: Recording reference; defaults to the active, else the newest recording.

**Options**:

* `--help`: Show this message and exit.

## `lion adr`

Document standalone decisions (ADRs) that exist without a recording, such as
switching the default browser. Each ADR is a TOML document under
$XDG_DATA_HOME/lion/adrs/:

  status   overview of the ADR store and the counters (read-only)
  add      create a new ADR; at least one field is required
  edit     edit one ADR in $EDITOR (overwrites and adds fields)
  list     list stored ADRs (read-only)
  show     show one ADR in detail (read-only)

The fields, the completeness rule (title + decision) and the status values are
the same as for recording ADRs; &#x27;lion records&#x27; keeps the recording-bound view.

**Usage**:

```console
$ lion adr [OPTIONS] COMMAND [ARGS]...
```

**Options**:

* `--help`: Show this message and exit.

Running 'lion adr' without an operation shows the status. Read commands never
collect a state and never write. There is no delete command: 'edit' overwrites
and adds fields, and LION never removes a field. A REF is the id, a unique
prefix or 'latest'.

Examples:

  lion adr                          the ADR counters
  lion adr add --title "Use X" --decision "Because Y" --status accepted
  lion adr add --field component shell --recording latest
  lion adr list                     all ADRs, oldest first
  lion adr show latest              one ADR in detail
  lion adr edit latest              edit an ADR in $EDITOR


**Commands**:

* `status`: Show the standalone ADR counters (read-only).
* `add`: Create a new standalone ADR; at least one...
* `edit`: Edit one ADR in $EDITOR (overwrites and...
* `list`: List stored ADRs, oldest first (read-only).
* `show`: Show one standalone ADR in detail...

### `lion adr status`

Show the standalone ADR counters (read-only).

**Usage**:

```console
$ lion adr status [OPTIONS]
```

**Options**:

* `--json`: Output status as JSON.
* `--help`: Show this message and exit.

### `lion adr add`

Create a new standalone ADR; at least one field is required.

**Usage**:

```console
$ lion adr add [OPTIONS]
```

**Options**:

* `--title <str>`: The decision title.
* `--decision <str>`: The decision text.
* `--status <str>`: The status value (proposed, accepted, superseded).
* `--field <str>`: Add a field as NAME=VALUE (repeatable).
* `--recording <str>`: Reference a recording the decision belongs to.
* `--help`: Show this message and exit.

### `lion adr edit`

Edit one ADR in $EDITOR (overwrites and adds fields).

**Usage**:

```console
$ lion adr edit [OPTIONS] [reference]
```

**Arguments**:

* `reference`: ADR reference (unique prefix, exact id); defaults to latest.

**Options**:

* `--help`: Show this message and exit.

### `lion adr list`

List stored ADRs, oldest first (read-only).

**Usage**:

```console
$ lion adr list [OPTIONS]
```

**Options**:

* `--json`: Output the list as JSON.
* `--help`: Show this message and exit.

### `lion adr show`

Show one standalone ADR in detail (read-only).

**Usage**:

```console
$ lion adr show [OPTIONS] [reference]
```

**Arguments**:

* `reference`: ADR reference (&#x27;latest&#x27;, a unique prefix); defaults to latest.

**Options**:

* `--json`: Output the ADR as JSON.
* `--help`: Show this message and exit.
