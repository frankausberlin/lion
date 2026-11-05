<!-- Generated file. Do not edit by hand. -->
<!-- Source: the lion Typer app (src/lion/cli.py and sub-apps). Regenerate with: just docs -->

# CLI reference

Command-line interface for lion.

**Usage**:

```console
$ lion [OPTIONS] COMMAND [ARGS]...
```

**Options**:

* `--install-completion`: Install completion for the current shell.
* `--show-completion`: Show completion for the current shell, to copy it or customize the installation.
* `--help`: Show this message and exit.

**Commands**:

* `watch`: Compare locked dash entries with their...
* `doctor`: Diagnose shlib, dash, lock and secret...
* `hello`: Print a greeting.
* `mcp`: Run the MCP server over stdio.
* `shlib`: Install or remove the zsh shlib layout.
* `dash`: List user-maintained dash links and manage...
* `watch-dog`: Manage the shlib file that runs `lion...

## `lion watch`

Compare locked dash entries with their lock copies and print diffs.

**Usage**:

```console
$ lion watch [OPTIONS]
```

**Options**:

* `--home <str>`: Home directory to operate on. Defaults to the current user&#x27;s home.
* `--help`: Show this message and exit.

## `lion doctor`

Diagnose shlib, dash, lock and secret state without printing file contents.

The default run is read-only. ``--fix`` creates missing lion-owned
directories and the redaction key, and removes orphaned dash locks; it
never touches a dash link, a changed target or a suspected secret.

**Usage**:

```console
$ lion doctor [OPTIONS]
```

**Options**:

* `--home <str>`: Home directory to operate on. Defaults to the current user&#x27;s home.
* `--fix`: Apply the scoped fixes to lion-owned artifacts.
* `--dry-run`: Show the fix plan without changing anything.
* `--no-color`: Disable ANSI colors in the report.
* `--help`: Show this message and exit.

## `lion hello`

Print a greeting.

**Usage**:

```console
$ lion hello [OPTIONS]
```

**Options**:

* `--name <str>`: [default: world]
* `--help`: Show this message and exit.

## `lion mcp`

Run the MCP server over stdio.

**Usage**:

```console
$ lion mcp [OPTIONS]
```

**Options**:

* `--help`: Show this message and exit.

## `lion shlib`

Install or remove the zsh shlib layout.

**Usage**:

```console
$ lion shlib [OPTIONS] COMMAND [ARGS]...
```

**Options**:

* `--help`: Show this message and exit.

**Commands**:

* `install`: Install the shlib layout and replace...
* `uninstall`: Flatten the shlib layout into ~/.zshrc and...
* `status`: Show installation, drift and cleanup state...
* `it`: Copy content appended to ~/.zshrc into...

### `lion shlib install`

Install the shlib layout and replace ~/.zshrc with the bootstrap block.

**Usage**:

```console
$ lion shlib install [OPTIONS]
```

**Options**:

* `--home <str>`: Home directory to operate on. Defaults to the current user&#x27;s home.
* `--dry-run`: Run all checks and show the plan without changing anything.
* `--help`: Show this message and exit.

### `lion shlib uninstall`

Flatten the shlib layout into ~/.zshrc and keep the old tree as a backup.

Owner-only export files are written to ``secrets.sh`` inside the backup and
sourced from the flattened ``.zshrc`` instead of being inlined.

**Usage**:

```console
$ lion shlib uninstall [OPTIONS]
```

**Options**:

* `--home <str>`: Home directory to operate on. Defaults to the current user&#x27;s home.
* `--dry-run`: Run all checks and show the plan without changing anything.
* `--force`: Overwrite a .zshrc that differs from the lock file or has no lock file.
* `--help`: Show this message and exit.

### `lion shlib status`

Show installation, drift and cleanup state without changing anything.

**Usage**:

```console
$ lion shlib status [OPTIONS]
```

**Options**:

* `--home <str>`: Home directory to operate on. Defaults to the current user&#x27;s home.
* `--no-color`: Disable ANSI colors in the status output.
* `--help`: Show this message and exit.

### `lion shlib it`

Copy content appended to ~/.zshrc into ~/.shlib/shlibs/99-unsorted.sh.

The captured tail is removed from .zshrc, so it matches the lock file
afterwards. 99-unsorted.sh remains a cleanup file that the user must
resolve and delete.

**Usage**:

```console
$ lion shlib it [OPTIONS]
```

**Options**:

* `--home <str>`: Home directory to operate on. Defaults to the current user&#x27;s home.
* `--dry-run`: Run all checks and show the plan without changing anything.
* `--help`: Show this message and exit.

## `lion dash`

List user-maintained dash links and manage their lock copies.

**Usage**:

```console
$ lion dash [OPTIONS] COMMAND [ARGS]...
```

**Options**:

* `--help`: Show this message and exit.

**Commands**:

* `list`: Show dash entries with lock state, type...
* `lock`: Copy the current target content to...
* `unlock`: Delete only locks/&lt;name&gt;.lock, never the...

### `lion dash list`

Show dash entries with lock state, type and target.

**Usage**:

```console
$ lion dash list [OPTIONS]
```

**Options**:

* `--home <str>`: Home directory to operate on. Defaults to the current user&#x27;s home.
* `--help`: Show this message and exit.

### `lion dash lock`

Copy the current target content to locks/&lt;name&gt;.lock.

**Usage**:

```console
$ lion dash lock [OPTIONS] {name}
```

**Arguments**:

* `name`: Direct dash entry name to lock.  [required]

**Options**:

* `--home <str>`: Home directory to operate on. Defaults to the current user&#x27;s home.
* `--help`: Show this message and exit.

### `lion dash unlock`

Delete only locks/&lt;name&gt;.lock, never the dash link.

**Usage**:

```console
$ lion dash unlock [OPTIONS] {name}
```

**Arguments**:

* `name`: Direct dash entry name to unlock.  [required]

**Options**:

* `--home <str>`: Home directory to operate on. Defaults to the current user&#x27;s home.
* `--help`: Show this message and exit.

## `lion watch-dog`

Manage the shlib file that runs `lion watch` at shell start.

**Usage**:

```console
$ lion watch-dog [OPTIONS] COMMAND [ARGS]...
```

**Options**:

* `--help`: Show this message and exit.

**Commands**:

* `on`: Write the managed watchdog file.
* `off`: Remove the complete managed watchdog file.
* `status`: Report whether the managed watchdog file...

### `lion watch-dog on`

Write the managed watchdog file.

**Usage**:

```console
$ lion watch-dog on [OPTIONS]
```

**Options**:

* `--home <str>`: Home directory to operate on. Defaults to the current user&#x27;s home.
* `--help`: Show this message and exit.

### `lion watch-dog off`

Remove the complete managed watchdog file.

**Usage**:

```console
$ lion watch-dog off [OPTIONS]
```

**Options**:

* `--home <str>`: Home directory to operate on. Defaults to the current user&#x27;s home.
* `--help`: Show this message and exit.

### `lion watch-dog status`

Report whether the managed watchdog file exists.

**Usage**:

```console
$ lion watch-dog status [OPTIONS]
```

**Options**:

* `--home <str>`: Home directory to operate on. Defaults to the current user&#x27;s home.
* `--help`: Show this message and exit.
