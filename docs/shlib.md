# shlib for zsh

`lion shlib` manages a small zsh configuration layout below `~/.shlib`. It is
intended for Linux with zsh and GNU `diffutils`; other shells and platforms are
out of scope.

## Quick start

```bash
lion shlib install --dry-run
lion shlib install
# start a new shell, or run: source ~/.zshrc
```

```bash
lion shlib status
lion shlib it --dry-run
lion shlib it
lion shlib uninstall --dry-run
lion shlib uninstall
```

All commands accept `--home PATH` to operate on a throwaway or test home
instead of `Path.home()`. Relative paths are resolved and `~` is expanded.
`install`, `it` and `uninstall` also accept `--dry-run`, which performs the
complete preflight and prints the plan without touching the filesystem.
`--force` exists only for `uninstall`.

## Layout

```text
~/
├── .zshrc                                  # bootstrap block after install
└── .shlib/
    ├── exports/                            # NAME -> file content as value
    ├── locks/
    │   └── shlib-version-of.zshrc          # identical to ~/.zshrc
    └── shlibs/
        └── 00-original-zshrc.sh            # the previous ~/.zshrc
```

`install` refuses to run when `~/.shlib` already exists, including a dangling
symlink, or when `~/.zshrc` is missing, is not a regular file, is a symlink or
already contains the bootstrap marker. Directories are created with mode
`0700` because exports can hold secrets. The copied original keeps its content
and mode. The lock file is mode `0644`.

After `uninstall`, `~/.shlib` is renamed to
`~/.shlib.uninstalled-<timestamp>/`. When owner-only exports exist, that backup
also contains `secrets.sh`; see [Owner-only exports](#owner-only-exports).

## Load order

The bootstrap first checks the lock file, then loads exports, then sources
shlib files:

1. **Lock check.** If the lock exists and differs from `~/.zshrc`, a unified
   `diff` is printed. This is a warning, not a block.
2. **Exports.** Every regular, non-hidden file in `~/.shlib/exports/` is
   exported as a variable named after the file. The value is the file content
   with all trailing newlines removed, exactly like `$(cat FILE)`. Names must
   match `[A-Za-z_][A-Za-z0-9_]*`; dotfiles are ignored. Values are quoted by
   `uninstall` with `shlex.quote`, so spaces, quotes, `$` and multiple lines
   remain valid.
3. **Shlibs.** Regular files in `~/.shlib/shlibs/` whose names start with two
   ASCII digits are sourced in filename order. Symlinks to regular files count,
   matching `[ -f ]` in the bootstrap. Files such as `99-local.sh` are loaded;
   `local.sh`, `1-single.sh` and dotfiles are not.

A file without a final newline is padded before the next section marker when
`uninstall` flattens it, so entries never merge.

## Status

`lion shlib status` reports the state without reading export values or
changing anything:

```text
shlib: ~/.shlib
installation: installed
zshrc: drift (12 appended line(s))
exports: 3 (owner-only: 1)
shlibs: 5
unsorted: yes (128 bytes)
backups: 1
```

`installation` is `installed`, `not installed` or `inconsistent`. `zshrc` is
`ok`, `drift` (with `appended line(s)`, `whitespace only`, `other changes` or
`managed block missing`) or `missing`. The `unsorted` line is yellow as soon
as `~/.shlib/shlibs/99-unsorted.sh` exists, even when it is empty; drift is
yellow as well. `inconsistent` and `not installed` are red. The state words are
always visible, independent of color.

Exit codes: `0` everything is ok, `1` not installed or a warning (drift or
`99-unsorted.sh`), `2` inconsistent or a read error.

Colors are disabled with `--no-color`, by setting `NO_COLOR`, or automatically
when stdout is not a terminal.

## Appended .zshrc content and `lion shlib it`

Content added after the managed bootstrap block stays in `~/.zshrc` and keeps
running. `lion shlib it` copies that tail into
`~/.shlib/shlibs/99-unsorted.sh` so it survives an uninstall; it **never
changes `~/.zshrc`**.

```bash
lion shlib it --dry-run
lion shlib it
```

The command requires that `~/.zshrc` starts exactly with the lock file. If the
lock is missing, the built-in bootstrap is used when `~/.zshrc` starts with it;
otherwise the command aborts. Changes before or inside the managed block are
not moved and also abort. Only regular files in a real shlib tree are accepted.

Each capture is framed by markers:

```zsh
# >>> lion shlib it 2026-11-05T14:26:04Z sha256:<hex>
<tail>
# <<< lion shlib it sha256:<hex>
```

A second run with the identical tail is a no-op. An empty or whitespace-only
tail is a no-op as well. New files are created with mode `0600`; an existing
regular `99-unsorted.sh` keeps its mode, and a group- or world-readable file
triggers a warning. Symlinks and directories at the target path abort.

**The tail still runs from `~/.zshrc` until you remove it there.** During that
time the same code runs twice: once from `99-unsorted.sh` and once from the
`.zshrc` tail. The command prints the line range to remove. Removing
`99-unsorted.sh` later makes the status report `unsorted: no` again.

`99-unsorted.sh` sorts before a later `99-LAST.sh`, because the uppercase `L`
sorts before the lowercase `u`. If a later fallback is needed, choose another
name and update the order deliberately.

## Owner-only exports

An export file whose permission bits grant no access to group or others
(`mode & 0o077 == 0`, for example `0600` or `0400`) is treated as owner-only
when `uninstall` flattens the tree. Such files are not written into
`~/.zshrc`. Their values go into
`~/.shlib.uninstalled-<timestamp>/secrets.sh` with mode `0600`, and the new
`~/.zshrc` sources that file. The original export files also remain in
`backup/exports/`.

Use `chmod 600 FILE` for a file that should be treated as a secret; `0644` is
the normal mode and stays inline. `0640` is also public because group
permission bits are set. The marker applies only to `uninstall`; `install` and
the bootstrap export every file regardless of mode. Detection is mode-based, so
tools that normalize permissions to `0644` can turn a secret back into an
inline export.

The source line has no guard and no fallback. Once `secrets.sh` exists, the
backup directory is part of your shell configuration: keep it, or move the
file deliberately and update the `source` line in `~/.zshrc`. If the file is
missing, zsh reports the failed `source` on every start. `uninstall` reports
the count, path and marker rule, but never the names or values.

## Install behavior

Preflight is read-only. If a step before the final `~/.zshrc` replacement fails,
the newly created `~/.shlib` tree is removed and the original `~/.zshrc` stays
unchanged. The new `~/.zshrc` and the lock file are written through temporary
files and `os.replace`. If the final replacement fails, the prepared tree is
kept and the error names it.

## Uninstall behavior

`uninstall` builds one flat `~/.zshrc` from the exports and shlib files:

```zsh
# Generated by `lion shlib uninstall`.
# This file combines the former shlib exports and shlib files; it does not use the shlib mechanism.
```

The exports section comes first, followed by each shlib file framed with
`# >>> shlibs/<name>` and `# <<< shlibs/<name>`. The file is validated and
written completely before anything is moved. Then `~/.shlib` is renamed to
`~/.shlib.uninstalled-<YYYYMMDDTHHMMSSZ>`; a collision gets `-1`, `-2`, and so
on.

Owner-only exports are split off as described above. The flat file contains
only public exports; `secrets.sh` is written first inside `~/.shlib` with mode
`0600`, so it moves with the rename. The flat file contains a notice and an
active `source "$HOME/.shlib.uninstalled-.../secrets.sh"` line, placed after
the public exports and before the shlib files. Without owner-only exports, no
`secrets.sh` is created and the flat file is byte-identical to the previous
behavior.

The previous `~/.zshrc` is **flattened, not restored**. The backup keeps the
old tree and the flat file contains everything that was loaded, directly or
through the sourced secrets file, but no rollback command is provided. A
missing `~/.zshrc` is created; a missing `shlibs/` directory always aborts
because a damaged tree must not silently produce an empty configuration.

The lock check protects manual edits. If `~/.zshrc` differs from the lock file,
or the lock file is missing while `~/.zshrc` exists, `uninstall` requires
`--force`. `--force` does not bypass unreadable files, symlinks, a damaged
tree, invalid export names or NUL bytes in export values.

## Safety rules and limits

- Only zsh is supported. The bootstrap uses the zsh glob qualifier `(N)`.
- GNU `diff` and `cmp` are required for the lock warning.
- Symlinked `~/.zshrc` files and symlinked `~/.shlib` roots are refused; a
  symlinked tree is never moved.
- Export values cannot contain NUL bytes. Files are handled as UTF-8 with
  `surrogateescape`, so non-UTF-8 shlib content survives the round trip.
- Owner-only detection uses permission bits only: `0600` and `0400` are
  secrets, `0640` and `0644` are public. Tools that normalize modes can change
  the result of a later `uninstall`.
- With owner-only exports the backup is part of the shell configuration.
  Deleting or moving it makes zsh fail to source `secrets.sh` on every start.
  There is no guard, no stable secret path and no migration.
- There is no locking and no migration of an existing `~/.shlib` tree. The
  commands are designed for a single interactive user.
- `--purge`, restore, interactive prompts and shells other than zsh are not
  part of this tool.

See the generated [CLI reference](reference/cli.md) for the exact options.
