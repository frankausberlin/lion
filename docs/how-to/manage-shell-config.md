# Manage the shell configuration

LION's shell library (`shlib`) keeps `~/.zshrc` small by moving configuration
into `~/.shlib/`. It manages the current user's home only (no `sudo`) and
requires Zsh for syntax validation. A custom `ZDOTDIR` outside the home
directory is rejected.

The directory layout below `~/.shlib` is:

- `exports/` — one file per environment variable (file name = variable name,
  file content = value).
- `shlibs/` — numbered scripts sourced in order (`00-`, `01-`, ...).
- `dash/` — your own symlinks to configuration files; LION does not populate it.

## Check the status

```bash
uv run lion shlib                  # same as "shlib status"; never writes
uv run lion shlib status --json    # installation, lock, script/export names and warnings
```

`status` reports differences between `~/.zshrc` and the reference copy without
displaying file contents or secret values.

## Install

```bash
uv run lion shlib install
```

Installation backs up the existing `.zshrc` to `.zshrc.before-shlib` (numbered
if already present), preserves its permissions, and moves its configuration into
`~/.shlib/shlibs/00-original-zshrc.sh`. It then writes the new `.zshrc`, which
loads exports first and then the scripts whose names start with two digits in
lexical order.

Installing an already installed library is a no-op.

## Add an environment variable

Create one file per variable below `~/.shlib/exports`. The file name must be a
valid shell identifier and the permissions should stay at `600`:

```bash
printf '%s' 'my-secret-value' > ~/.shlib/exports/MY_SECRET
chmod 600 ~/.shlib/exports/MY_SECRET
```

Whitespace is preserved except trailing newlines, matching shell command
substitution. An `exports/.gitignore` excludes the files from normal Git adds;
do not force-add secrets to Git.

## Add a script

Put numbered scripts into `~/.shlib/shlibs`. They are sourced in lexical order
after the exports:

```bash
ln -s ~/dotfiles/aliases.zsh ~/.shlib/shlibs/20-aliases.sh
```

No extra Powerlevel10k or direnv hooks are inserted; existing setup stays in the
original script. When splitting that script, keep instant-prompt
initialization early and move the direnv hook to the end of your shell
initialization.

## Review changes against the reference

`.zshrc.lock` is a reference copy: shell startup displays differences but does
not prevent edits. After reviewing intentional changes, refresh it:

```bash
cp ~/.zshrc ~/.zshrc.lock
```

## Uninstall

```bash
uv run lion shlib uninstall
```

Uninstall preserves the *current* configuration, not the pre-install state:

- Exports are consolidated into `~/.zshrc.exports` with mode `600` and literal,
  shell-quoted values. `.zshrc` sources this file where the loader previously
  ran.
- Script contents (also through symlinks) are inserted in load order, each with
  a short filename comment. `.zshrc` keeps its original permissions.
- Lines outside the SHLIB markers, including installer additions, remain in
  place.
- Both generated files pass `zsh -f -n` before publication, so syntax
  diagnostics are suppressed to avoid echoing secrets. Files are replaced
  atomically one at a time; if the rc replacement fails, the newly created
  exports file is removed.
- A private `.zshrc.before-shlib-uninstall` backup is retained, as are
  `~/.shlib`, `.zshrc.lock` and the original backups. Existing backups are never
  overwritten.

Review file-relative script logic and top-level `return` statements after
flattening: flattening can change their behavior. To deliberately return to the
pre-Shlib configuration, inspect and restore the original `.zshrc.before-shlib`
backup yourself; later configuration changes will then no longer be active.

## Errors and reconciliation

- Already installed or uninstalled operations are no-ops.
- The documented manual SHLIB block is supported; unknown changes inside that
  block require manual reconciliation.
- An existing `.zshrc.exports`, installation remnants, ambiguous markers, broken
  script symlinks and symlinked output files cause a clear error instead of an
  overwrite, so reinstallation after removal requires reconciling the retained
  files first.
- `install` and `uninstall` hold an exclusive process lock (`~/.shlib.lock`,
  separate from the reference copy `.zshrc.lock`) for the whole operation, so a
  concurrent lifecycle command fails fast instead of interleaving. The lock file
  is retained but is not an installation remnant, and `status` never takes it.
  Do not edit shell configuration during an operation.

Installation and removal never load or execute the user's scripts.
