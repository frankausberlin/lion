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
  lion --version       print the installed version

Run 'lion <command> --help' for details on a command.


**Commands**:

* `scan`: Collect the current state and store it in...
* `status`: Compare the current state with the latest...
* `history`: List stored states with their stable...
* `diff`: Compare two stored states without...
* `doctor`: Run a read-only diagnosis and write a...
* `shlib`: Manage the Zsh shell library (~/.shlib).

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
history.

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

Read-only: it never changes the history. Running 'lion' without a command is
the same as 'lion status'.

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
tools, the history, the data directory and the shell library. It never stores
that state and never runs a fix.

Findings use 'ok', 'warn', 'error' and 'skip'. If anything is 'warn' or 'error',
exactly one reviewable script is written under $XDG_DATA_HOME/lion/recos/ for
you to read and run yourself; LION never runs it.

Examples:

  lion doctor          show the diagnosis and the reco script path
  lion doctor --show   also print the reco script to stdout
  lion doctor --json   emit the findings as a single JSON object


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
