<!-- Generated file. Do not edit by hand. -->
<!-- Source: src/lion/cli.py. Regenerate with: just docs -->

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
