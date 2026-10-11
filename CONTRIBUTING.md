# Contributing to lion

## Development Setup

```bash
git clone https://github.com/frankausberlin/lion.git
cd lion
uv sync           # install all dependencies
```

## Workflow

1. Create a branch, preferably in a separate worktree:
   `git worktree add ../lion-feature -b feat/my-feature`
2. Make your changes
3. Run the quality gate: `just check`
4. Commit: `git add -A && git commit -m "feat: ..."`
5. From the main checkout, merge locally: `just merge feat/my-feature`
   (fast-forward only; it removes the branch's worktree and deletes the branch)
6. Publishing is the maintainer's step: review local `main`
   (`git log --oneline origin/main..main`, `git diff origin/main..main`) and
   `git push origin main`

## Quality Gate

```bash
just lint         # ruff + basedpyright
just typecheck    # type checking only
just test         # pytest with coverage
just check        # full gate (lint + typecheck + tests)
just fix          # auto-fix formatting and lint issues
```

## Architecture

- `src/lion/state/collector.py` — shared `CollectorStatus`, `CollectorResult`,
  `Collector`, and `collect_state`; keep it free of collector imports.
- `src/lion/state/tools.py` — shared `run_tool` helper for external tools.
- `src/lion/state/tooling.py` — the `tools` collector of tool availability.
- `src/lion/state/<collector>.py` — one collector per file (`host`, `hardware`,
  `network`, `packages`, `services`, `containers`, `tooling`), each with its
  frozen dataclass, a private `_collect()`, and a `COLLECTOR` export.
- `src/lion/state/registry.py` — ordered `COLLECTORS` tuple.
- `src/lion/state/model.py` — `Snapshot`, strict validation, and the shared
  `collectors_equal`/`value_equal` comparison rules (including the RAM tolerance);
  `src/lion/state/comparison.py` holds the list identities used by equality and
  diffing.
- `src/lion/program/storage.py` — history persistence (`list_entries`,
  `resolve`, `load_latest`, `save_state`).
- `src/lion/program/diff.py` — `diff_collectors`, `has_structural_change` and
  terminal `render` (lists match by identity; `added`/`removed` are structural).
- `src/lion/program/doctor.py` — the read-only check framework (`checks.py` holds
  the shared types; the checks are owned by the data modules).
- `src/lion/command/<command>.py` — one module per command (`scan` writes the
  history; `status`, `history` and `diff` read only; `doctor` only publishes a
  reco; `shlib` manages the shell library); `cli.py` keeps the Typer decorators.

New collectors must not abort a capture: report `unavailable` or `error` with a
message and return their data keys regardless. Never persist volatile fields.

## Testing

External tools (`lspci`, `nvidia-smi`, `apt-mark`, `systemctl`, `docker`,
`podman`, Dpkg) are exercised through fixtures and mocks so the suite passes
without them. Keep `just check` green, which includes
coverage of at least 90%.

## Documentation

Public documentation lives in [`docs/`](docs/index.md) and follows
[Diátaxis](https://diataxis.fr/): `tutorials/`, `how-to/`, `reference/` and
`explanation/`. Place new content by intent and keep every statement in exactly
one place. Accepted design decisions are recorded as ADRs under
[`docs/decisions/`](docs/decisions/0000-template.md), one decision per file.

Public docs, code, docstrings, `--help` texts and commit messages are English.
Only the maintainer-local planning documents (untracked under `ignore/`) and the
conversation with the maintainer are German.

Command options stay canonical in `lion <cmd> --help`. The CLI reference
[`docs/reference/cli.md`](docs/reference/cli.md) is generated from the Typer
app; never edit it by hand, and regenerate it after CLI changes:

```bash
just docs         # regenerate the page
just docs-check   # verify it matches the app (also part of `just check`)
```

## Conventions

- **Python version:** 3.12
- **Typing:** Strict — public functions must have type annotations
- **Docstrings:** Google style
- **Imports:** Sorted by ruff (isort-compatible)
- **Line length:** 120 characters

## Versioning

Never edit version numbers manually. Use:

```bash
just bump patch   # bugfix: 0.1.0 → 0.1.1
just bump minor   # feature: 0.1.1 → 0.2.0
just bump major   # breaking: 0.2.0 → 1.0.0
```

See [AGENTS.md](AGENTS.md) for AI agent guidelines.
