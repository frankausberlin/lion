# LION documentation

**LION — Linux Operator Nerd** tracks a Linux machine's state with collectors,
compares stored states, and manages the Zsh configuration through the shell
library (`shlib`). Everything runs locally and needs no root. See the
[README](../README.md) for the pitch, the quickstart and the canonical promise.

This documentation follows the [Diátaxis](https://diataxis.fr/) structure. The
options of every command are documented canonically in `lion <cmd> --help`; the
pages below explain, guide and link, they do not duplicate that depth.

## Tutorials

- [Getting started](tutorials/getting-started.md) — install LION and run your
  first scan, status, history and diff.

## How-to guides

- [Manage the shell configuration](how-to/manage-shell-config.md) — install,
  use and remove the Zsh shell library.
- [Compare stored states](how-to/compare-states.md) — list the history and diff
  two states.

## Reference

- [CLI](reference/cli.md) — the command directory and where to find each
  command's options.
- [Data structures](reference/data-structures.md) — the stored snapshot, the
  shlib status and the JSON output shapes.
- [Storage](reference/storage.md) — paths, files, locks and compatibility.
- [Collectors](reference/collectors.md) — the fields each collector records.

## Explanation

- [CLI conventions](explanation/cli-conventions.md) — the status default and
  the help behavior.
- [Architecture](explanation/architecture.md) — layering, CLI structure and the
  `status`/`state` terminology.
- [Collector model](explanation/collectors.md) — why a collector never aborts a
  capture and stores no volatile fields.
- [Comparison model](explanation/comparison-model.md) — `created`, `confirmed`,
  `appended` and the RAM tolerance.
- [Write boundary](explanation/write-boundary.md) — no root, no foreign files.

## Decisions

- [0001 — No root, writes confined](decisions/0001-no-root-write-boundary.md)
- [0002 — `status` reads, `scan` writes](decisions/0002-status-read-only-scan-writes.md)
- [0003 — History published with `os.link`](decisions/0003-history-publication-os-link.md)
- [0004 — A single RAM tolerance](decisions/0004-memory-total-tolerance.md)
