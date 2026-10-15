# Guard critical input

`lion watch dog` asks for a second Enter before a critical command runs: a ZLE
`accept-line` widget in your Zsh confirms recursive `rm`, reboot/shutdown and a
few more commands from the classifier's `guard: "confirm"` rules. It works
without a recording, runs entirely in the shell (no process in the input path)
and is a guardrail, not a sandbox (ADR 0014, ADR 0020).

## 1. Prepare the shell integration

The guard loads through the watch integration, so insert it once:

```bash
uv run lion shell insert
```

Open a new shell (or `exec zsh`) afterwards. See
[Record a shell session](record-shell.md) for what `insert` writes.

## 2. Arm the guard

```bash
uv run lion watch dog on
```

`on` writes the intent to `~/.config/lion/config.toml` and compiles the
artifact `~/.local/share/lion/dog/rules.zsh` from the merged classifier rule
set (validated with `zsh -n` and a Zsh regex compile run before anything is
written). The guard starts in **new shells**; `on` is an idempotent refresh
after a rule change.

## 3. Confirm, edit, cancel

Type a critical command and press Enter:

1. The first Enter executes nothing; the guard shows the matching rule
   (`LION dog: shell.rm.recursive — Enter again to run, edit to cancel`).
2. Press Enter again with the same text to run it.
3. Edit the buffer instead (any change, including a paste) and the next Enter
   arms the new text again; nothing runs until you confirm it.

Non-critical input and empty Enter pass straight through. The guard applies to
interactive input only and also covers bracketed pastes, because it sees the
whole ZLE buffer.

## 4. Which commands are confirmed

The rules are the classifier's `guard: "confirm"` rules — one source of truth,
no second rule list. The built-in default set (7 rules) covers:

| Rule | Triggers on |
| --- | --- |
| `shell.rm.recursive` | `rm -r`/`-rf`/`-R`/`--recursive`, with optional `sudo` |
| `interruptions.reboot` | `reboot`, `systemctl reboot` |
| `interruptions.shutdown` | `shutdown`, `poweroff`, `halt` |
| `containers.volume.rm` | `docker`/`podman volume rm …` |
| `containers.volume.prune` | `docker`/`podman volume prune` |
| `downloads.curl.pipe` | `curl … | sh`/`bash`/`zsh` |
| `downloads.wget.pipe` | `wget … | sh`/`bash`/`zsh` |

Change the set in `$XDG_CONFIG_HOME/lion/classifier.json` by overriding a rule
by id (for example a `guard: "none"` copy) or adding your own rule with
`guard: "confirm"`; the schema lives in [Classifiers](../reference/classifiers.md).
After a change, run `lion watch dog on` again — the artifact carries the rule
set hash, and `lion doctor` reports a stale artifact as `watch.dog` warn.

Matching is best-effort and documented as such: patterns are searched over the
raw buffer with word-boundary and `sudo` handling (`sudo reboot` and
`; rm -rf …` are caught, `firm -rf` is not), and only the first flag token of
`rm` is recognized — `rm -v -rf` stays unrecognized. Over-triggering costs one
extra Enter; under-triggering is the honest limit of the mechanism.

## 5. Check the state, disarm

```bash
uv run lion watch dog             # text status (same as 'watch dog status')
uv run lion watch dog status --json
uv run lion doctor                # watch.dog reports drift and staleness
uv run lion watch dog off
```

`off` removes the artifact and flips the config back. Shells that already
loaded the guard keep it until they restart — the guard never polls, so a new
shell (or `exec zsh`) is the sure way to disarm. `status` is read-only.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Nothing confirms | `lion watch dog status` (`enabled`, `artifact_present`); integration inserted (`lion shell status`) and the shell was started after `on`. |
| `doctor` reports a stale artifact | Rules changed; run `lion watch dog on`. |
| `status` fails loudly | The config or `classifier.json` is broken; the error names the path. |
| Confirmation appears after `off` | That shell loaded the guard earlier; start a new shell. |
