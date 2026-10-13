# LION documentation

**LION — Linux Operator Nerd** tracks a Linux machine's state with collectors,
compares stored states, and manages the Zsh configuration through the shell
library (`shlib`). Everything runs locally and needs no root. See the
[README](../README.md) for the pitch, the quickstart and the canonical promise.

This documentation follows the [Diátaxis](https://diataxis.fr/) structure.
Command depth stays canonical in `lion <cmd> --help`; the generated CLI
reference reproduces it, and the other pages explain, guide and link.

## Tutorials

- [Getting started](tutorials/getting-started.md) — install LION and run your
  first scan, status, history and diff.

## How-to guides

- [Manage the shell configuration](how-to/manage-shell-config.md) — install,
  use and remove the Zsh shell library.
- [Record a shell session](how-to/record-shell.md) — insert the watch
  integration, record, stop, read the events and classify the blocks.
- [Document a decision without a recording](how-to/document-a-decision.md) —
  create, edit and read a standalone ADR with `lion adr`.
- [Compare stored states](how-to/compare-states.md) — list the history and diff
  two states.
- [Diagnose with doctor](how-to/diagnose-with-doctor.md) — run the read-only
  diagnosis and review its fix script.

## Reference

- [CLI](reference/cli.md) — the generated command reference: usage and options
  for every command, produced from the app by `just docs`.
- [Data structures](reference/data-structures.md) — the stored snapshot, the
  shlib status and the JSON output shapes.
- [Storage](reference/storage.md) — paths, files, locks and compatibility.
- [Wiki (OKF v0.2)](reference/wiki.md) — the derived wiki `scan` regenerates:
  files, frontmatter grammar, palette and determinism.
- [Recordings](reference/recordings.md) — the watch data space, event grammar,
  masking and segment liveness.
- [Records and ADRs](reference/records-and-adrs.md) — the `#-LION:` annotation
  grammar, the fold into one ADR per record and the `lion records` views.
- [Standalone ADRs](reference/adrs.md) — decisions without a recording: the
  document schema, ids, the `lion adr` views and the `doctor` topic.
- [Classifiers](reference/classifiers.md) — the rule file schema, the group
  catalog, the ERE subset and the `lion classify`/`watch R` views.
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
- [Testing strategy](explanation/testing-strategy.md) — the unit, E2E container
  and alpha-test levels, and why the container cannot replace a real machine.

## Decisions

- [0001 — No root, writes confined](decisions/0001-no-root-write-boundary.md)
- [0002 — `status` reads, `scan` writes](decisions/0002-status-read-only-scan-writes.md)
- [0003 — History published with `os.link`](decisions/0003-history-publication-os-link.md)
- [0004 — A single RAM tolerance](decisions/0004-memory-total-tolerance.md)
- [0005 — The CLI reference is generated](decisions/0005-generated-cli-reference.md)
- [0006 — System profile and structural diff](decisions/0006-system-profile-and-structural-diff.md)
- [0007 — `doctor` recommends, the human executes](decisions/0007-doctor-reco-human-executed.md)
- [0008 — A reinstall after uninstall fails loudly](decisions/0008-shlib-reinstall-fail-loud.md)
- [0009 — Config file location and format conventions](decisions/0009-config-file-conventions.md)
- [0010 — English identifiers and messages](decisions/0010-english-identifiers-and-messages.md)
- [0011 — Watch capture architecture: a zsh-side spool](decisions/0011-watch-capture-zsh-spool.md)
- [0012 — Recording layout and shell-segment liveness](decisions/0012-watch-recording-layout-liveness.md)
- [0013 — ADRs as flat annotation lines](decisions/0013-watch-adr-annotations.md)
- [0014 — Watch dog: an `accept-line` guard widget](decisions/0014-watch-dog-accept-line.md)
- [0015 — Classifier rules: read-time data in a POSIX-ERE subset](decisions/0015-classifier-rules-read-time-ere.md)
- [0016 — The wiki is a derived OKF v0.2 artifact written by scan](decisions/0016-wiki-derived-okf-artifact.md)
- [0017 — Standalone ADRs live beside the recordings](decisions/0017-standalone-adrs.md)
