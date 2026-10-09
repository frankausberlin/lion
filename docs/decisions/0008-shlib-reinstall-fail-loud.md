# 0008 — A reinstall after uninstall fails loudly

- **Status:** accepted
- **Date:** 2026-10-10

## Context

`lion shlib uninstall` preserves the *current* configuration rather than the
pre-install state: it consolidates the exports into `~/.zshrc.exports` and
flattens the scripts directly into `~/.zshrc`, while deliberately retaining
`~/.shlib`, the reference copy `~/.zshrc.lock` and the pre-install backups
(`~/.zshrc.before-shlib*`). After an uninstall the same configuration therefore
exists twice — once in `~/.shlib/exports/` plus the flattened rc, and once in
`~/.zshrc.exports` — and the pre-install original
(`~/.shlib/shlibs/00-original-zshrc.sh`) is still on disk.

A naive reinstall would have to decide what to do with those retained artifacts,
and every automatic choice is wrong:

- Adopting the flattened state would fold already-flattened scripts back in and
  **execute the consolidated exports and scripts a second time**.
- Preferring `00-original-zshrc.sh` would silently discard the configuration
  changes made since the first install; preferring the flattened state would
  silently discard the original. The "original" a reinstall should restore is
  genuinely ambiguous.

## Decision

LION **refuses** an `install` while artifacts of a previous installation are
present and changes nothing. It never adopts, rewrites or deletes retained
files to make a reinstall succeed; the user reconciles the retained files first
and then installs again. The refusal is a hard error (exit code `1`) naming the
blocking path, consistent with the fail-loud principle used everywhere else in
`shlib`.

`_require_clean_install_target` (in `src/lion/program/shlib.py`) rejects a
reinstall when any of these exist:

- `~/.zshrc.lock` — the reference copy;
- `~/.zshrc.exports` — the consolidated exports from the uninstall;
- `~/.shlib/shlibs/00-original-zshrc.sh` — the preserved pre-install rc;
- any existing numbered script under `~/.shlib/shlibs/` (`[0-9][0-9]*`);
- a `~/.shlib`, `~/.shlib/exports`, `~/.shlib/shlibs` or `~/.shlib/dash` that is
  a symlink or not a real directory;
- a `~/.shlib/exports/.gitignore` that is a symlink or differs from the
  generated content.

Retained artifacts that are **not** blockers and stay in place: the
`~/.zshrc.before-shlib*` backups (they are private copies and are never
overwritten), a correctly generated `~/.shlib/exports/.gitignore`, and the
process-lock file `~/.shlib.lock` (see [ADR 0002](0002-status-read-only-scan-writes.md)
and the storage reference for the lock's retained-but-harmless contract).

## Consequences

- The behavior is a pure refusal: no partial write, no new backup, and the active
  `.zshrc` is byte-for-byte unchanged. This mirrors the existing precheck
  contracts (`tests/test_shlib.py`).
- A safe reconciliation path exists and is documented in
  [Manage the shell configuration](../how-to/manage-shell-config.md):
  1. Confirm the exports are preserved in `~/.shlib/exports/` (the uninstall
     already consolidated them there), then remove `~/.zshrc.exports`.
  2. Decide deliberately about `~/.shlib/shlibs/00-original-zshrc.sh` and
     `~/.zshrc.lock`: keep a copy if the pre-install state is still needed, then
     remove them so the install target is clean.
  3. Remove or relocate any retained numbered scripts under
     `~/.shlib/shlibs/` (the flattened `.zshrc` keeps their content).
  4. Run `lion shlib install` again.
- There is no recovery that preserves both histories automatically; that is
  intentional. The user is the only party that can say which state is canonical.
- The `~/.shlib.lock` file is never treated as a remnant, so a lingering lock
  does not block the reinstall.

## Alternatives

- **A gentle adopt roundtrip.** Rejected: it would execute consolidated exports
  and flattened scripts a second time and cannot disambiguate the "original"
  between the pre-install backup and the current flattened state.
- **An explicit `install --adopt` flag.** Rejected as unnecessary: it adds a
  second, subtly different install path with the same ambiguity, and every
  automatic adoption rule still has to choose between two valid histories.
  Reconciliation is a one-time, understandable manual step.
