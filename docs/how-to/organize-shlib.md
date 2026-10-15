# Organize the shell library

`lion shlib install` preserves your existing `~/.zshrc` as
`~/.shlib/shlibs/00-original-zshrc.sh` and replaces the active file with a small
loader. The loader reads two directories on every new shell:

- `~/.shlib/exports/` — one file per environment variable (file name = variable
  name, file content = value), loaded first.
- `~/.shlib/shlibs/` — scripts whose names start with two digits, sourced in
  lexical order afterwards.

This guide splits the preserved original into those two directories.
[Manage the shell configuration](manage-shell-config.md) covers the installation
details, the reference copy `.zshrc.lock` and the uninstall flow.

## 1. Install the library

Skip this step when `~/.zshrc` already contains the `SHLIB for zsh` block:

```bash
uv run lion shlib install
```

The command backs up `.zshrc` to `.zshrc.before-shlib`, stores its content in
`~/.shlib/shlibs/00-original-zshrc.sh` and inserts the loader block.

## 2. Split the original

Work through the preserved original block by block, and delete each block there
as you move it out, so no setting is defined twice:

```bash
$EDITOR ~/.shlib/shlibs/00-original-zshrc.sh
```

Mind the order while migrating: exports load before every script, so an `export`
line still left in `00-original-zshrc.sh` overrides the same variable's file in
`exports/`. When nothing is left, remove the file:

```bash
rm ~/.shlib/shlibs/00-original-zshrc.sh
```

### Variables to `exports/`

Create one file per variable; the file content is the value:

```bash
printf '%s' 'literal-value' > ~/.shlib/exports/MY_SECRET
chmod 600 ~/.shlib/exports/MY_SECRET
```

- The file name must be a valid shell name (`[A-Za-z_][A-Za-z0-9_]*`); any other
  name makes `lion shlib status` warn and blocks an uninstall.
- Keep the mode at `600`; `status` reports any other mode as a warning, and the
  generated `exports/.gitignore` keeps the files out of Git.
- The value is taken literally — only trailing newlines are dropped, `$` stays
  literal and nothing is expanded. An assignment that needs expansion, for
  example `export PATH="$HOME/bin:$PATH"`, belongs in a numbered script.

### The rest to `shlibs/`

Move PATH additions, aliases, completions, prompt setup and tool hooks into
scripts whose names start with exactly two digits:

```bash
$EDITOR ~/.shlib/shlibs/20-aliases.sh
```

- Sourcing order is lexical: `00-`, `10-`, `20-`, ...; leave gaps (10, 20, 30)
  so a later insert does not renumber anything.
- Only `[0-9][0-9]*` files are sourced; a `README.md` beside them is ignored.
- Load an existing dotfiles script in place instead of copying it:

  ```bash
  ln -s ~/dotfiles/aliases.zsh ~/.shlib/shlibs/20-aliases.sh
  ```

- Leave `90-lion-watch.sh` to LION: `lion shell insert` writes and removes that
  file when the watch integration is in use.

## 3. Keep the ordering constraints

- The Powerlevel10k instant prompt stays at the very top of `~/.zshrc`, above
  the loader block; it is outside the split.
- A hook that must run after everything else (for example the direnv hook) stays
  below the block at the end of `~/.zshrc`: numbered scripts run where the block
  sits.

## 4. Verify

```bash
zsh -n ~/.shlib/shlibs/20-aliases.sh   # parse one new script without running it
uv run lion shlib status               # scripts, exports, warnings, lock state
exec zsh                               # load the organized configuration
uv run lion doctor                     # read-only shlib diagnosis
```

The split is done when `status` lists the new scripts and exports without
warnings and a fresh shell behaves as before. If you also edited `~/.zshrc`
itself, refresh the reference copy with `cp ~/.zshrc ~/.zshrc.lock`
(see [Manage the shell configuration](manage-shell-config.md)).
