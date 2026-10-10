# Diagnose with doctor

`lion doctor` runs a read-only diagnosis of the machine and the LION data, and —
only when something needs attention — writes one reviewable fix script for you to
run yourself.

## Run the diagnosis

```bash
uv run lion doctor
```

`doctor` performs a fresh collection (it does not reuse or change `scan` history)
and checks five topics:

| Topic | What it checks |
| --- | --- |
| `collectors` | every canonical collector produced a usable section |
| `tools` | the external tools required for the detected profile |
| `history` | the stored states are valid and in order |
| `storage` | the data directory, `history/` and `recos/` are writable |
| `shlib` | the shell library installation is consistent |

Each finding is one of four states:

- **`ok`** — checked and fine.
- **`warn`** — a problem you can fix, e.g. a missing required tool. The exit code
  stays `0`.
- **`error`** — a defect in LION's own data, e.g. a damaged history entry or a
  non-writable data directory. The exit code becomes `1`.
- **`skip`** — not applicable, e.g. the shell library is not installed or a tool
  is optional for this profile. Neutral; it never affects the exit code.

The tool policy is profile-dependent: `lspci` is always required, `nvidia-smi`
whenever at least one detected GPU is NVIDIA (including a mixed AMD+NVIDIA
machine), `apt-mark` only on the Debian family, and `rocm-smi`/`zsh` are never
required by a plain doctor run.

The `shlib` check inspects the managed block, the `~/.zshrc.lock` reference copy
and any `dash/` entries independently. A missing reference copy and a broken
`dash` symlink are warnings even when the block is absent; files retained by a
regular uninstall stay allowed. A custom `ZDOTDIR` is reported only when a
managed installation exists; without one it is a neutral `skip`.

## Review and run the fix script

If any finding is `warn` or `error`, `doctor` writes exactly one script under
`$XDG_DATA_HOME/lion/recos/` and prints the path:

```bash
uv run lion doctor
# ...
# Behebungsskript: /home/you/.local/share/lion/recos/2026-10-09T12-00-00.123456Z.sh

uv run lion doctor --show   # also print the script to stdout
```

Print or open the file, read it completely, and only then decide what to run.
LION never runs it and changes nothing itself. The script may contain the full
fix, including `sudo` and package-manager commands, but every command is built
from LION's own constants — no tool output or environment value is ever
interpolated.

A clean run (only `ok`/`skip`) writes no script at all, so `doctor` stays fully
read-only.

## Use it from scripts

```bash
uv run lion doctor --json
```

`--json` emits one JSON object on stdout and ignores `--show`. Its keys
(`status`, `checked`, `findings`, `summary`, `reco_path`) are German,
consistent with `status --json` and `scan --json`; `reco_path` is `null` when no
script was written.

## Related

- [Write boundary](../explanation/write-boundary.md) — why the script is a
  recommendation, not an action.
- [ADR 0007](../decisions/0007-doctor-reco-human-executed.md) — the decision.
- [Storage](../reference/storage.md) — the `recos/` layout and permissions.
