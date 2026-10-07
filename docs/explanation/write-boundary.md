# Write boundary

LION observes a machine; it does not manage it. Every write stays inside LION's
own directories and the shell startup files it was explicitly asked to manage.

The canonical one-line promise lives in the [README](../../README.md); this page
explains what it means in practice.

## The rules

- **No root, no system files.** LION never asks for `sudo` and never repairs or
  rewrites files it does not own.
- **Golden rule for writes.** A writing command writes only into its own
  directories. Repairs never touch foreign files.
- **Exactly one shell insertion.** LION inserts exactly one marked block into
  the shell startup file — or, when the shell library is active, a script below
  `~/.shlib/`. That keeps a single writer per file.
- **Fail loud.** No silent omissions. Every stored file is validated; a damaged
  entry stops the command with its path instead of being skipped.
- **Read/write separation.** Read-only commands never write. In practice only
  `scan` (state history) and `shlib` (shell configuration) write.
- **Deterministic.** No volatile fields (clocks, temperatures, uptime) are
  persisted.
- **No secrets in output.** Redaction and restrictive permissions where needed;
  export files are created with mode `600`.

## Where the writes land

- State history: `$XDG_DATA_HOME/lion/history` (default
  `~/.local/share/lion/history`), plus the writer lock `.history.lock` — see
  [Storage](../reference/storage.md).
- Shell library: `~/.shlib/`, the managed block in `~/.zshrc`, the reference
  copy `~/.zshrc.lock`, and the backups it creates — see
  [Manage the shell configuration](../how-to/manage-shell-config.md).

## What this does not promise

LION is not a security boundary. It is a deterministic, local observer; it does
not sandbox commands or enforce policy on the system.
