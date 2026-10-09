# 0007 — `doctor` recommends, the human executes

- **Status:** accepted
- **Date:** 2026-10-09

## Context

`lion doctor` diagnoses the machine and finds things that need fixing: a missing
external tool, a damaged history entry, a shell library that drifted. A tool
that fixes such problems itself would need to install packages, edit system
files or change the user's shell — exactly the writes that
[ADR 0001](0001-no-root-write-boundary.md) keeps out of LION's scope. At the
same time, "explain the problem and stop" leaves the user to translate a
diagnosis into commands by hand, which is error-prone for privileged steps.

## Decision

`doctor` may write one reviewable shell script, the **reco**, into its own
directory (`$XDG_DATA_HOME/lion/recos/`), and never executes it. The script can
contain the complete fix, including `sudo` and package-manager commands, because
a human reads and runs it; LION only generates it.

## Consequences

- ADR 0001 stays intact: the reco is a write into LION's own directory, so no
  foreign file is touched. ADR 0001 governs *write targets*; this ADR governs
  *recommendation versus execution*.
- The canonical promise ("no root, modifies no system files") still holds
  literally: LION never runs a privileged command and never edits a system file.
- The reco is the correct place for privileged remediation, and it is the only
  artifact a `warn`/`error` run creates. A clean run stays 100% read-only.
- The generated commands are built from LION's own constants (a distro-aware
  package name), never from tool output or environment values, so no secret or
  injection can reach a command line. Messages and hints are emitted only as
  comment lines.
- Retention is out of scope for v1: the directory accumulates like the history.

## Alternatives

- **`doctor --fix`.** Rejected: it would contradict ADR 0001 by running
  privileged and system-modifying commands from inside LION.
- **No artifact, only printed advice.** Rejected: the user has to reconstruct
  commands, and a script can be reviewed, diffed and versioned instead.
- **A structured task list instead of shell.** Rejected as heavier than needed;
  a commented shell script is directly reviewable and runnable.
