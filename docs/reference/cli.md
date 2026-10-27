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

* `hello`: Print a greeting.
* `mcp`: Run the MCP server over stdio.
* `shlib`: Install or remove the zsh shlib layout.

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

**Usage**:

```console
$ lion shlib uninstall [OPTIONS]
```

**Options**:

* `--home <str>`: Home directory to operate on. Defaults to the current user&#x27;s home.
* `--dry-run`: Run all checks and show the plan without changing anything.
* `--force`: Overwrite a .zshrc that differs from the lock file or has no lock file.
* `--help`: Show this message and exit.
