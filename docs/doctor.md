# `lion doctor`

`lion doctor` is the read-only diagnosis for a shlib/dash/watch installation.
It reports structural drift, unusable dash entries, orphaned locks and
suspected secrets. Findings never contain file contents, diff bodies, export
values, lock values or secret values. Only path, line, key name, kind and a
heuristic confidence are printed.

Secret detection is **best effort** and deliberately incomplete. A clean
security section means "nothing suspected was found", not "the tree is safe".

```bash
lion doctor
lion doctor --dry-run   # show the fix plan, change nothing
lion doctor --fix       # repair scoped, lion-owned structural problems
lion doctor --deep      # add the optional Gitleaks stage
```

All commands accept `--home PATH` for a throwaway home. `--no-color`, the
`NO_COLOR` environment variable and a non-TTY stdout disable ANSI colors, like
`lion shlib status`.

## Output

The report is grouped into `shlib`, `dash` and `security`, followed by a
summary:

```text
shlib:
  warning shlib.zshrc_drift: ~/.zshrc differs from the lock file (2 appended line(s)) [manual]
    hint: run `lion shlib it`
dash:
  warning dash.broken_link: dash entry 'conf' is a broken link [manual]
    hint: rm -- ~/.shlib/dash/conf
security:
  no suspected secrets found (best effort)
doctor: 0 fixable, 1 manual
```

Every finding line names the severity, the stable code, the subject and one of
`fixable`, `manual` or `info`. Hints are plain text and are never executed.
A clean `shlib` or `dash` section prints `ok`. A clean `security` section
prints `no suspected secrets found (best effort)`.

## Exit codes

| Exit | Meaning |
| --- | --- |
| `0` | no warnings or errors (info findings do not count) |
| `1` | at least one warning, including "not installed" |
| `2` | an error, an inconsistent tree or a read failure |

`--dry-run` exits `0` after printing the plan, even when the diagnosed state
contains errors. `--fix` re-collects after applying the plan and exits with the
remaining state; a failed action forces exit `2`.

## Checks

### shlib

- `shlib.not_installed`: warning; run `lion shlib install`.
- `shlib.inconsistent`: error, one finding per status issue.
- `shlib.zshrc_drift`: warning; appended content points at `lion shlib it`,
  other drift stays manual.
- `shlib.cleanup_original` / `shlib.cleanup_unsorted`: warning; resolve the
  file into sorted `NN-*.sh` files and delete it yourself.
- `shlib.missing_dir`: fixable. Missing `locks/`, `exports/`, `dash/` and
  `shlibs/` are created with mode `0700`. A missing `shlibs/` is an error and
  explicitly says that its content is not restored.
- `shlib.invalid_dir`: error for a symlinked or non-directory subdirectory.
  It is never followed.
- `shlib.invalid_name`: regular files in `shlibs/` that the bootstrap does not
  source. Names ending in `.sh` or starting with one digit are warnings, other
  helper files are info. Nothing is renamed.
- `exports.invalid_name`: warning for export file names that are not valid
  shell identifiers.

### dash and locks

- `dash.broken_link`: warning with a manual `rm -- ~/.shlib/dash/<name>` hint.
- `dash.not_a_link`, `dash.directory`, `dash.other`: warning; `lock` and
  `watch` cannot use these entries.
- `dash.unlocked`: info with a count, never a warning.
- `dash.locked_changed`: warning for a locked entry that changed. The report
  never includes the watch diff.
- `dash.too_large`: warning for a locked entry whose content exceeds the scan
  limit; doctor does not read it.
- `dash.lock_not_regular`: error for a lock that is a symlink or not a regular
  file.
- `locks.orphan`: warning, fixable; a regular `*.lock` without a matching dash
  entry. Classification is skipped while `dash/` is missing.

### Redaction key

- `redaction.key_missing`: warning, fixable.
- `redaction.key_invalid`: error for a symlinked, non-regular, unreadable or
  wrongly sized key. Doctor never follows or replaces it.

### Security

- `exports.possible_secret`: a public export (`mode & 0o077 != 0`) with a
  suspected secret is a warning; an owner-only export gets an info finding.
  The suggestion is review plus `chmod 600`.
- `shlibs.possible_secret`: warning. `chmod 600` on a shlib file is not a
  sufficient fix because `uninstall` inlines shlib files; move the value to an
  owner-only export or a future secret mechanism.
- `scan.skipped`: info for binary, oversized or unreadable files.

Secret scanning reads at most 1 MiB per file and skips files with NUL bytes or
invalid UTF-8. It scans exports and shlib files only; `.zshrc`, locks and dash
targets are out of scope.

### Secret detection stages

Detection runs in three stages. Stages 1 and 2 are built in; stage 3 is
optional and never runs automatically.

1. **Prefix rules.** A literal provider prefix such as `ghp_`, `AKIA` or
   `sk-proj-` is searched only in value regions: after `=`/`:` and inside
   single or double quotes. A naked token such as `cmd ghp_...` is left to
   stage 3. The longest matching prefix wins. The candidate needs a minimum
   tail length, must not be a placeholder and must not consist of one repeated
   character.
2. **Context rules.** Sensitive key names (`PASSWORD`, `TOKEN`, `SECRET`,
   `API_KEY`, …), PEM private key blocks, JWT shapes and URL userinfo
   passwords. References (`$VAR`, `${...}`, `$(...)`, backticks), paths,
   URLs and placeholders are skipped. There is no free entropy detection.
3. **Gitleaks.** Only with `--deep`. If Gitleaks is missing, the run continues
   with stages 1 and 2 and reports `gitleaks.unavailable` as info. Gitleaks
   older than 8.0.0, invalid output, a non-zero exit or a timeout becomes a
   warning and does not abort the run. Only rule id and positions are used;
   Gitleaks `Secret` and `Match` fields and its stderr are never shown.

Overlaps are merged. Prefix, provider and URL findings win against Gitleaks,
which wins against sensitive-assignment and file-name findings. At equal
priority the longer span wins.

### Prefix overlay

The prefix list is `~/.shlib/secret-prefixes.txt`, a user-maintained overlay on
the built-in defaults. The file is never sourced by the shell. Missing file:
the defaults apply. An unreadable, symlinked, oversized (above 64 KiB),
non-UTF-8 or NUL-containing file reports `prefixes.invalid` and falls back to
the defaults.

Format, one entry per line:

```text
prefix [kind] [min_tail] [max_tail]
```

- `#` starts a comment line; blank lines are ignored.
- `kind` defaults to `prefixed-secret`, `min_tail` to `20`, `max_tail` to
  unlimited (`-` is also accepted for unlimited).
- `!prefix` disables an existing entry; an unknown prefix is a warning.
- A user entry replaces the same built-in prefix without a warning.
- A duplicate user prefix keeps the last line and warns on that line.
- Matching is case-sensitive and the longest prefix wins.
- Messages name only the line number and the problem, never the line content.

`lion list-prefixes` shows the effective list, including active/disabled state
and the source (`builtin` or `user`). It is read-only:

```bash
lion list-prefixes
lion list-prefixes --home /tmp/test-home
```

Configuration problems are printed as `warning: ...: line N: ...` on stderr;
the command still exits `0`.

- `prefixes.invalid`: warning for a prefix-file or prefix-line problem.
- `gitleaks.unavailable`: info, only with `--deep`.
- `gitleaks.unsupported`, `gitleaks.error`: warning.

## Fix scope

`--fix` only touches lion-owned artifacts:

1. create missing real subdirectories with mode `0700`;
2. create `~/.shlib/.redaction-key` with mode `0600` and exactly 32 random
   bytes, using exclusive creation;
3. delete orphaned locks with the existing `unlock_entry` helper.

It never creates `~/.shlib` itself, never deletes or retargets a dash link,
never touches a changed locked target, never repairs a non-regular lock and
never edits a suspected secret. All fixes are refused when `~/.shlib` is
missing, symlinked or not a directory.

When the plan creates a missing `shlibs/`, it prints that the original shlib
content is not restored. The created directory is empty by design.

When `dash/` was missing, the first `--fix` creates it and deliberately does
not delete any locks in the same run. Pre-existing locks are classified as
orphaned only on a later explicit `--fix`; the first run prints that note.

## Relation to `lion shlib status`

`doctor` reuses the same status snapshot but adds dash, lock, name and security
checks. `status` remains the short, colorized installation view. A fresh
installation is yellow in both commands because `00-original-zshrc.sh` exists
until you resolve and delete it.

## Redaction confidence and fingerprints

Secret detection is heuristic and **not calibrated**. It combines prefix and
provider rules (AWS, GitHub, GitLab, OpenAI/Anthropic, Slack, Stripe, Google,
PyPI, npm, Hugging Face, Docker, JWT, PEM), sensitive key names and URL
userinfo. The reported confidence is an integer percentage for prioritization;
low confidence means "not detected", never "clean". There is no completeness
claim: a value with an unknown name and no recognizable prefix can be missed.

Masks use `[REDACTED:<kind>:<fingerprint>]`, or `[REDACTED:<kind>]` when no key
is available. The fingerprint is

```text
HMAC-SHA256(key, value)[:12]
```

with a per-installation random key in `~/.shlib/.redaction-key` (mode `0600`,
32 bytes). The same value yields the same token; a changed value yields a
different token. There is no plain SHA-256 fallback and no length or prefix in
the mask. Fingerprints are local to one installation: after `uninstall` and a
new install, on another machine, or after key rotation they are not comparable.

## MCP metadata rule

MCP tools and resources may expose metadata only: paths, line numbers, names,
kinds, counts and file modes. They never expose file contents, values, diffs or
masked snippets. `Finding` has no value field, and the future `diagnose` tool
must reuse `collect_diagnosis` unchanged. Gitleaks is an optional detection
stage, not a security boundary.

## Limits

- No `--json`, no masked snippets, no MCP value access in this step.
- No automatic repair of dash links, names, changed targets or secrets.
- No secret scanning of `.zshrc`, locks or dash targets in this step.
- Gitleaks is not a project or runtime dependency and never runs without
  `--deep`.
- No cross-platform behavior; the layout remains Linux/zsh oriented.
